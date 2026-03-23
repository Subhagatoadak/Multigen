"""
multigen.chain
==============
Sequential agent pipeline with branching, retry, and middleware support.

Usage::

    from multigen.chain import Chain
    from multigen.agent import LLMAgent, FunctionAgent

    result = await Chain([
        FunctionAgent("tokenize",  fn=tokenize),
        LLMAgent("summarise",      prompt="Summarise: {output}"),
        FunctionAgent("validate",  fn=validate_json),
    ]).run({"text": "A long article..."})

    # Named steps + context passing
    result = await (
        Chain()
        .step(FunctionAgent("fetch", fn=fetch_docs),   name="docs")
        .step(LLMAgent("analyze", prompt="Analyze: {docs.output}"), name="analysis")
        .step(LLMAgent("report",  prompt="Write report: {analysis.response}"), name="report")
        .with_middleware(logging_middleware)
        .with_error_handler(on_error)
        .run({"query": "market trends 2025"})
    )
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Dict, List, Optional, Union

from .agent import BaseAgent

logger = logging.getLogger(__name__)

Context = Dict[str, Any]
Middleware = Callable[[Context, Callable[[Context], Coroutine]], Coroutine]


@dataclass
class StepResult:
    name: str
    output: Dict[str, Any]
    latency_ms: float
    error: Optional[str] = None
    skipped: bool = False


@dataclass
class ChainResult:
    status: str          # "completed" | "failed" | "partial"
    steps: List[StepResult]
    context: Context
    total_latency_ms: float
    error: Optional[str] = None

    @property
    def final_output(self) -> Any:
        for step in reversed(self.steps):
            if not step.error and not step.skipped:
                return step.output
        return {}

    def __getitem__(self, name: str) -> Any:
        for s in self.steps:
            if s.name == name:
                return s.output
        raise KeyError(name)


class Chain:
    """Sequential agent pipeline.

    Features:
    - Named steps with context propagation (each step sees all prior outputs)
    - Conditional step execution (``when`` predicate)
    - Per-step timeout
    - Global error handler
    - Middleware stack (logging, tracing, metrics)
    - ``|`` operator for fluent composition: ``chain1 | chain2``
    - ``+`` operator: append a single agent
    """

    def __init__(self, agents: Optional[List[BaseAgent]] = None) -> None:
        self._steps: List[Dict[str, Any]] = []
        self._middlewares: List[Middleware] = []
        self._error_handler: Optional[Callable[[Exception, Context, str], Coroutine]] = None
        self._on_step: Optional[Callable[[str, Dict], None]] = None

        for agent in (agents or []):
            self.step(agent)

    # ── Fluent builder ────────────────────────────────────────────────────

    def step(
        self,
        agent: BaseAgent,
        *,
        name: Optional[str] = None,
        when: Optional[Callable[[Context], bool]] = None,
        timeout: Optional[float] = None,
        output_key: Optional[str] = None,
    ) -> "Chain":
        """Add a step to the chain."""
        self._steps.append({
            "agent":      agent,
            "name":       name or agent.name,
            "when":       when,
            "timeout":    timeout,
            "output_key": output_key or agent.name,
        })
        return self

    def with_middleware(self, mw: Middleware) -> "Chain":
        self._middlewares.append(mw)
        return self

    def with_error_handler(self, handler: Callable) -> "Chain":
        self._error_handler = handler
        return self

    def on_step_complete(self, callback: Callable[[str, Dict], None]) -> "Chain":
        self._on_step = callback
        return self

    def __or__(self, other: Union["Chain", BaseAgent]) -> "Chain":
        """``chain1 | chain2`` — concatenate two chains."""
        new = Chain()
        new._steps = self._steps.copy()
        new._middlewares = self._middlewares.copy()
        new._error_handler = self._error_handler
        if isinstance(other, Chain):
            new._steps.extend(other._steps)
        else:
            new.step(other)
        return new

    def __add__(self, agent: BaseAgent) -> "Chain":
        return self.step(agent)

    # ── Execution ────────────────────────────────────────────────────────

    async def run(self, ctx: Optional[Context] = None) -> ChainResult:
        context: Context = dict(ctx or {})
        results: List[StepResult] = []
        chain_start = time.perf_counter()

        for spec in self._steps:
            agent: BaseAgent = spec["agent"]
            name: str = spec["name"]
            when: Optional[Callable] = spec["when"]
            timeout: Optional[float] = spec["timeout"]
            output_key: str = spec["output_key"]

            # Conditional skip
            if when is not None:
                try:
                    should_run = when(context)
                except Exception as e:
                    logger.warning("Chain step %s: when() raised %s — skipping", name, e)
                    should_run = False
                if not should_run:
                    results.append(StepResult(name=name, output={}, latency_ms=0, skipped=True))
                    continue

            step_start = time.perf_counter()
            try:
                coro = self._run_with_middleware(agent, context)
                if timeout:
                    output = await asyncio.wait_for(coro, timeout=timeout)
                else:
                    output = await coro
                latency_ms = (time.perf_counter() - step_start) * 1000

                context[output_key] = output
                sr = StepResult(name=name, output=output, latency_ms=round(latency_ms, 2))
                results.append(sr)

                if self._on_step:
                    self._on_step(name, output)

            except Exception as exc:
                latency_ms = (time.perf_counter() - step_start) * 1000
                sr = StepResult(name=name, output={}, latency_ms=round(latency_ms, 2), error=str(exc))
                results.append(sr)

                if self._error_handler:
                    try:
                        await self._error_handler(exc, context, name)
                    except Exception:
                        pass

                return ChainResult(
                    status="failed",
                    steps=results,
                    context=context,
                    total_latency_ms=round((time.perf_counter() - chain_start) * 1000, 2),
                    error=f"Step {name!r} failed: {exc}",
                )

        return ChainResult(
            status="completed",
            steps=results,
            context=context,
            total_latency_ms=round((time.perf_counter() - chain_start) * 1000, 2),
        )

    async def _run_with_middleware(self, agent: BaseAgent, ctx: Context) -> Dict[str, Any]:
        if not self._middlewares:
            return await agent(ctx)

        async def base(c: Context) -> Dict[str, Any]:
            return await agent(c)

        call = base
        for mw in reversed(self._middlewares):
            prev = call
            async def wrapped(c: Context, _mw=mw, _prev=prev) -> Dict[str, Any]:
                return await _mw(c, _prev)
            call = wrapped

        return await call(ctx)

    # ── Introspection ────────────────────────────────────────────────────

    def __repr__(self) -> str:
        names = " → ".join(s["name"] for s in self._steps)
        return f"Chain([{names}])"

    @property
    def step_names(self) -> List[str]:
        return [s["name"] for s in self._steps]


# ─── Pipeline alias + middleware helpers ─────────────────────────────────────

Pipeline = Chain   # alias for semantic clarity


async def logging_middleware(ctx: Context, next_fn: Callable) -> Dict[str, Any]:
    """Built-in middleware: logs each step invocation."""
    logger.info("Chain step starting | ctx_keys=%s", list(ctx.keys()))
    result = await next_fn(ctx)
    logger.info("Chain step done | output_keys=%s", list(result.keys()))
    return result


async def tracing_middleware(ctx: Context, next_fn: Callable) -> Dict[str, Any]:
    """Built-in middleware: injects trace_id into context."""
    import uuid
    if "trace_id" not in ctx:
        ctx["trace_id"] = str(uuid.uuid4())
    return await next_fn(ctx)
