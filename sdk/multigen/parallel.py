"""
multigen.parallel
=================
Parallel, fan-out/fan-in, MapReduce, and scatter/gather patterns.

Usage::

    from multigen.parallel import Parallel, FanOut, MapReduce, Race

    # Run all agents concurrently, collect all outputs
    result = await Parallel([
        LLMAgent("finance_analyst",  prompt="Analyse finance: {topic}"),
        LLMAgent("tech_analyst",     prompt="Analyse technology: {topic}"),
        LLMAgent("risk_analyst",     prompt="Analyse risk: {topic}"),
    ]).run({"topic": "AI regulation 2025"})

    # Fan-out: split list input, process each item concurrently, aggregate
    result = await FanOut(
        item_agent=LLMAgent("process", prompt="Process: {item}"),
        input_key="documents",
    ).run({"documents": [doc1, doc2, doc3]})

    # Race: return the first result that completes
    result = await Race([agent_a, agent_b, agent_c]).run(ctx)

    # MapReduce
    result = await MapReduce(
        mapper=FunctionAgent("parse", fn=parse),
        reducer=FunctionAgent("merge", fn=merge),
        input_key="records",
    ).run({"records": [...]})
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from .agent import BaseAgent, AgentOutput

logger = logging.getLogger(__name__)
Context = Dict[str, Any]


@dataclass
class ParallelResult:
    status: str                           # "completed" | "partial" | "failed"
    outputs: Dict[str, AgentOutput]       # agent.name → output
    errors: Dict[str, str]                # agent.name → error message
    total_latency_ms: float
    fastest_agent: Optional[str] = None

    @property
    def all_succeeded(self) -> bool:
        return not self.errors

    def merged(self) -> AgentOutput:
        """Shallow-merge all outputs into one dict."""
        merged: AgentOutput = {}
        for out in self.outputs.values():
            merged.update(out)
        return merged


# ─── Parallel (all-concurrent) ───────────────────────────────────────────────

class Parallel:
    """Run all agents concurrently and collect every output.

    Parameters
    ----------
    agents:
        List of agents to run.
    concurrency:
        Maximum simultaneous inflight agents (None = unlimited).
    fail_fast:
        If True, cancel remaining tasks on first failure.
    timeout:
        Per-agent wall-clock timeout in seconds.
    """

    def __init__(
        self,
        agents: List[BaseAgent],
        *,
        concurrency: Optional[int] = None,
        fail_fast: bool = False,
        timeout: Optional[float] = None,
    ) -> None:
        self._agents = agents
        self._concurrency = concurrency
        self._fail_fast = fail_fast
        self._timeout = timeout

    async def run(self, ctx: Optional[Context] = None) -> ParallelResult:
        context: Context = dict(ctx or {})
        start = time.perf_counter()
        sem = asyncio.Semaphore(self._concurrency) if self._concurrency else None

        async def run_one(agent: BaseAgent) -> tuple[str, AgentOutput]:
            async def _call():
                return await agent(context)
            if sem:
                async with sem:
                    if self._timeout:
                        return agent.name, await asyncio.wait_for(_call(), self._timeout)
                    return agent.name, await _call()
            if self._timeout:
                return agent.name, await asyncio.wait_for(_call(), self._timeout)
            return agent.name, await _call()

        if self._fail_fast:
            tasks = [asyncio.create_task(run_one(a)) for a in self._agents]
            outputs: Dict[str, AgentOutput] = {}
            errors:  Dict[str, str] = {}
            try:
                for coro in asyncio.as_completed(tasks):
                    name, result = await coro
                    outputs[name] = result
            except Exception as exc:
                for t in tasks:
                    t.cancel()
                return ParallelResult(
                    status="failed", outputs=outputs, errors={"_": str(exc)},
                    total_latency_ms=round((time.perf_counter() - start) * 1000, 2),
                )
        else:
            results = await asyncio.gather(*[run_one(a) for a in self._agents], return_exceptions=True)
            outputs = {}
            errors  = {}
            for agent, r in zip(self._agents, results):
                if isinstance(r, BaseException):
                    errors[agent.name] = str(r)
                else:
                    outputs[r[0]] = r[1]

        status = "completed" if not errors else ("partial" if outputs else "failed")
        fastest = min(outputs, key=lambda k: outputs[k].get("_latency_ms", float("inf"))) if outputs else None

        return ParallelResult(
            status=status,
            outputs=outputs,
            errors=errors,
            total_latency_ms=round((time.perf_counter() - start) * 1000, 2),
            fastest_agent=fastest,
        )

    def __repr__(self) -> str:
        return f"Parallel([{', '.join(a.name for a in self._agents)}])"


# ─── FanOut ──────────────────────────────────────────────────────────────────

class FanOut:
    """Split a list input across multiple parallel agent invocations.

    Each item in context[input_key] is processed independently by item_agent,
    then all results are collected into context[output_key].

    Optionally, a reduce_fn aggregates the list into a single output.
    """

    def __init__(
        self,
        item_agent: BaseAgent,
        *,
        input_key: str = "items",
        output_key: str = "results",
        concurrency: Optional[int] = None,
        reduce_fn: Optional[Callable[[List[AgentOutput]], AgentOutput]] = None,
    ) -> None:
        self._agent = item_agent
        self._input_key = input_key
        self._output_key = output_key
        self._concurrency = concurrency
        self._reduce_fn = reduce_fn

    async def run(self, ctx: Optional[Context] = None) -> Dict[str, Any]:
        context: Context = dict(ctx or {})
        items = context.get(self._input_key, [])
        if not isinstance(items, list):
            items = [items]

        sem = asyncio.Semaphore(self._concurrency) if self._concurrency else None

        async def process(item: Any, idx: int) -> AgentOutput:
            item_ctx = {**context, "item": item, "item_index": idx}
            if sem:
                async with sem:
                    return await self._agent(item_ctx)
            return await self._agent(item_ctx)

        start = time.perf_counter()
        results = await asyncio.gather(*[process(item, i) for i, item in enumerate(items)], return_exceptions=True)

        processed = [r if not isinstance(r, BaseException) else {"error": str(r)} for r in results]
        errors = [str(r) for r in results if isinstance(r, BaseException)]

        out: Dict[str, Any] = {
            self._output_key: processed,
            "item_count":     len(items),
            "error_count":    len(errors),
            "latency_ms":     round((time.perf_counter() - start) * 1000, 2),
        }

        if self._reduce_fn:
            out["reduced"] = self._reduce_fn(processed)

        return out


# ─── MapReduce ───────────────────────────────────────────────────────────────

class MapReduce:
    """Classic MapReduce over a list of records.

    Map phase:  ``mapper`` runs on each record concurrently.
    Reduce phase: ``reducer`` receives the list of mapped outputs.
    """

    def __init__(
        self,
        mapper: BaseAgent,
        reducer: BaseAgent,
        *,
        input_key: str = "records",
        concurrency: Optional[int] = None,
    ) -> None:
        self._mapper = mapper
        self._reducer = reducer
        self._input_key = input_key
        self._concurrency = concurrency

    async def run(self, ctx: Optional[Context] = None) -> AgentOutput:
        context: Context = dict(ctx or {})
        records = context.get(self._input_key, [])
        fan_out = FanOut(self._mapper, input_key=self._input_key, concurrency=self._concurrency)
        mapped = await fan_out.run(context)
        reduce_ctx = {**context, "records": mapped["results"], "map_results": mapped}
        return await self._reducer(reduce_ctx)


# ─── Race ────────────────────────────────────────────────────────────────────

class Race:
    """Return the first successful agent output; cancel all others.

    Useful for latency hedging (send same request to multiple agents,
    use whichever responds first).
    """

    def __init__(self, agents: List[BaseAgent], *, timeout: Optional[float] = None) -> None:
        self._agents = agents
        self._timeout = timeout

    async def run(self, ctx: Optional[Context] = None) -> AgentOutput:
        context: Context = dict(ctx or {})
        tasks = {asyncio.create_task(a(context)): a.name for a in self._agents}

        try:
            done, pending = await asyncio.wait(
                tasks.keys(),
                return_when=asyncio.FIRST_COMPLETED,
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            for t in tasks:
                t.cancel()
            raise TimeoutError(f"Race: no agent responded within {self._timeout}s")

        for t in pending:
            t.cancel()

        if done:
            task = next(iter(done))
            winner = tasks[task]
            try:
                return {"winner": winner, **task.result()}
            except Exception as exc:
                raise RuntimeError(f"Race winner {winner} failed: {exc}") from exc

        raise RuntimeError("Race: no agent completed")


# ─── Batch ───────────────────────────────────────────────────────────────────

class Batch:
    """Process items in batches of size N, each batch run in parallel."""

    def __init__(self, agent: BaseAgent, batch_size: int = 10, input_key: str = "items") -> None:
        self._agent = agent
        self._batch_size = batch_size
        self._input_key = input_key

    async def run(self, ctx: Optional[Context] = None) -> Dict[str, Any]:
        context: Context = dict(ctx or {})
        items: List[Any] = context.get(self._input_key, [])
        all_results = []

        for i in range(0, len(items), self._batch_size):
            batch = items[i:i + self._batch_size]
            batch_results = await asyncio.gather(
                *[self._agent({**context, "item": item, "item_index": i + j}) for j, item in enumerate(batch)],
                return_exceptions=True,
            )
            all_results.extend(batch_results)

        return {
            "results":     [r if not isinstance(r, BaseException) else {"error": str(r)} for r in all_results],
            "total":       len(items),
            "batch_size":  self._batch_size,
            "batch_count": (len(items) + self._batch_size - 1) // self._batch_size,
        }
