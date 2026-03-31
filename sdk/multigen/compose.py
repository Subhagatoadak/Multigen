"""Composable step primitives with operator overloading."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional


@dataclass
class StepResult:
    """Result of a single step execution."""

    name: str
    output: Any
    status: str  # "success" | "failed" | "skipped"
    error: Optional[str] = None
    duration_ms: float = 0.0


class Step:
    """
    Single composable execution unit.

    Operator overloading::

        step_a >> step_b          # sequential pipeline (StepSequence)
        step_a | step_b           # parallel execution (ParallelStep)
        step_a & step_b           # fan-in: run both, merge outputs (FanInStep)

    Usage::

        fetch = Step(lambda ctx: {"data": ctx["url"]}, name="fetch", retry=2, timeout=5)
        clean = Step(lambda ctx: {"clean": ctx["data"].strip()}, name="clean")
        pipeline = fetch >> clean
        results = await pipeline.run({"url": "hello world"})
    """

    def __init__(
        self,
        fn: Callable,
        name: Optional[str] = None,
        retry: int = 0,
        timeout: Optional[float] = None,
        condition: Optional[Callable[[Dict[str, Any]], bool]] = None,
        on_error: Optional[Callable[[Exception, Dict[str, Any]], Any]] = None,
    ) -> None:
        self.fn = fn
        self.name = name or getattr(fn, "__name__", "step")
        self.retry = retry
        self.timeout = timeout
        self.condition = condition
        self.on_error = on_error

    async def run(self, ctx: Dict[str, Any]) -> StepResult:
        if self.condition and not self.condition(ctx):
            return StepResult(name=self.name, output=None, status="skipped")

        start = time.perf_counter()
        for attempt in range(self.retry + 1):
            try:
                coro = self.fn(ctx)
                if self.timeout:
                    result = await asyncio.wait_for(
                        coro if asyncio.iscoroutine(coro) else asyncio.coroutine(lambda: coro)(),
                        timeout=self.timeout,
                    )
                else:
                    result = await coro if asyncio.iscoroutine(coro) else coro
                return StepResult(
                    name=self.name,
                    output=result,
                    status="success",
                    duration_ms=(time.perf_counter() - start) * 1000,
                )
            except Exception as exc:
                if attempt < self.retry:
                    await asyncio.sleep(0.1 * (attempt + 1))
                    continue
                if self.on_error:
                    fallback = self.on_error(exc, ctx)
                    return StepResult(
                        name=self.name,
                        output=fallback,
                        status="success",
                        duration_ms=(time.perf_counter() - start) * 1000,
                    )
                return StepResult(
                    name=self.name,
                    output=None,
                    status="failed",
                    error=str(exc),
                    duration_ms=(time.perf_counter() - start) * 1000,
                )

    # ── Operator overloading ─────────────────────────────────────────────────

    def __rshift__(self, other: "Step") -> "StepSequence":
        """step_a >> step_b  →  sequential pipeline."""
        if isinstance(other, StepSequence):
            return StepSequence([self] + other.steps)
        return StepSequence([self, other])

    def __or__(self, other: "Step") -> "ParallelStep":
        """step_a | step_b  →  parallel execution."""
        if isinstance(other, ParallelStep):
            return ParallelStep([self] + other.steps)
        return ParallelStep([self, other])

    def __and__(self, other: "Step") -> "FanInStep":
        """step_a & step_b  →  fan-in (run both, merge outputs)."""
        return FanInStep([self, other])


class StepSequence(Step):
    """
    Sequential chain: each step's output is merged into the context for the next step.

    Usage::

        pipeline = fetch >> clean >> analyze
        results = await pipeline.run({"input": "raw text"})
        # returns List[StepResult]
    """

    def __init__(self, steps: List[Step]) -> None:
        self.steps = steps
        self.name = " >> ".join(s.name for s in steps)

    async def run(self, ctx: Dict[str, Any]) -> List[StepResult]:  # type: ignore[override]
        results: List[StepResult] = []
        current = dict(ctx)
        for step in self.steps:
            result = await step.run(current)
            results.append(result)
            if result.status == "success" and result.output is not None:
                if isinstance(result.output, dict):
                    current.update(result.output)
                else:
                    current[result.name] = result.output
        return results

    def __rshift__(self, other: "Step") -> "StepSequence":
        if isinstance(other, StepSequence):
            return StepSequence(self.steps + other.steps)
        return StepSequence(self.steps + [other])


class ParallelStep(Step):
    """
    Run multiple steps concurrently against the same context.

    Usage::

        parallel = fetch_a | fetch_b | fetch_c
        results = await parallel.run(ctx)
        # returns List[StepResult]
    """

    def __init__(self, steps: List[Step], max_concurrent: int = 0) -> None:
        self.steps = steps
        self.max_concurrent = max_concurrent
        self.name = " | ".join(s.name for s in steps)

    async def run(self, ctx: Dict[str, Any]) -> List[StepResult]:  # type: ignore[override]
        if self.max_concurrent and self.max_concurrent < len(self.steps):
            sem = asyncio.Semaphore(self.max_concurrent)

            async def limited(step: Step) -> StepResult:
                async with sem:
                    return await step.run(ctx)

            return list(await asyncio.gather(*[limited(s) for s in self.steps]))
        return list(await asyncio.gather(*[s.run(ctx) for s in self.steps]))

    def __or__(self, other: "Step") -> "ParallelStep":
        if isinstance(other, ParallelStep):
            return ParallelStep(self.steps + other.steps, self.max_concurrent)
        return ParallelStep(self.steps + [other], self.max_concurrent)


class FanInStep(Step):
    """
    Run multiple steps in parallel, then merge all dict outputs into one result.

    Usage::

        merged = analyst & critic & formatter
        result = await merged.run(ctx)
        # returns StepResult with merged dict output
    """

    def __init__(self, steps: List[Step]) -> None:
        self.steps = steps
        self.name = " & ".join(s.name for s in steps)

    async def run(self, ctx: Dict[str, Any]) -> StepResult:  # type: ignore[override]
        results = await asyncio.gather(*[s.run(ctx) for s in self.steps])
        merged: Dict[str, Any] = {}
        errors: List[str] = []
        for r in results:
            if r.status == "failed":
                errors.append(r.error or r.name)
            elif r.output is not None:
                if isinstance(r.output, dict):
                    merged.update(r.output)
                else:
                    merged[r.name] = r.output
        return StepResult(
            name=self.name,
            output=merged,
            status="failed" if errors else "success",
            error="; ".join(errors) if errors else None,
        )


class BranchStep(Step):
    """
    Conditional routing: run *true_branch* or *false_branch* based on a predicate.

    Usage::

        branch = BranchStep(
            condition=lambda ctx: ctx.get("score", 0) > 0.8,
            true_branch=approve_step,
            false_branch=review_step,
        )
        result = await branch.run(ctx)
    """

    def __init__(
        self,
        condition: Callable[[Dict[str, Any]], bool],
        true_branch: Step,
        false_branch: Optional[Step] = None,
        name: str = "branch",
    ) -> None:
        self.condition = condition
        self.true_branch = true_branch
        self.false_branch = false_branch
        self.name = name

    async def run(self, ctx: Dict[str, Any]) -> StepResult:  # type: ignore[override]
        if self.condition(ctx):
            return await self.true_branch.run(ctx)
        if self.false_branch:
            return await self.false_branch.run(ctx)
        return StepResult(name=self.name, output=None, status="skipped")


class LoopStep(Step):
    """
    Repeat a step until *until* returns True or *max_iterations* is reached.

    Usage::

        loop = LoopStep(
            step=refine_step,
            until=lambda ctx, results: ctx.get("confidence", 0) > 0.9,
            max_iterations=5,
        )
        results = await loop.run(ctx)
        # returns List[StepResult] (one per iteration)
    """

    def __init__(
        self,
        step: Step,
        until: Callable[[Dict[str, Any], List[StepResult]], bool],
        max_iterations: int = 10,
        name: str = "loop",
    ) -> None:
        self.step = step
        self.until = until
        self.max_iterations = max_iterations
        self.name = name

    async def run(self, ctx: Dict[str, Any]) -> List[StepResult]:  # type: ignore[override]
        results: List[StepResult] = []
        current = dict(ctx)
        for _ in range(self.max_iterations):
            result = await self.step.run(current)
            results.append(result)
            if result.status == "success" and isinstance(result.output, dict):
                current.update(result.output)
            if self.until(current, results):
                break
        return results


class Compose:
    """
    Factory helpers for building composed step graphs without operator syntax.

    Usage::

        pipeline = Compose.sequence(fetch, clean, analyze)
        par = Compose.parallel(agent_a, agent_b, agent_c, max_concurrent=2)
        branch = Compose.branch(lambda ctx: ctx["ok"], pass_step, fail_step)
        loop = Compose.loop(refine, until=lambda ctx, _: ctx["done"], max_iterations=8)
    """

    @staticmethod
    def step(fn: Callable, name: Optional[str] = None, **kwargs: Any) -> Step:
        return Step(fn, name=name, **kwargs)

    @staticmethod
    def sequence(*steps: Step) -> StepSequence:
        return StepSequence(list(steps))

    @staticmethod
    def parallel(*steps: Step, max_concurrent: int = 0) -> ParallelStep:
        return ParallelStep(list(steps), max_concurrent=max_concurrent)

    @staticmethod
    def fan_in(*steps: Step) -> FanInStep:
        return FanInStep(list(steps))

    @staticmethod
    def branch(
        condition: Callable,
        true_branch: Step,
        false_branch: Optional[Step] = None,
        name: str = "branch",
    ) -> BranchStep:
        return BranchStep(condition, true_branch, false_branch, name=name)

    @staticmethod
    def loop(
        step: Step,
        until: Callable,
        max_iterations: int = 10,
        name: str = "loop",
    ) -> LoopStep:
        return LoopStep(step, until, max_iterations=max_iterations, name=name)


__all__ = [
    "BranchStep",
    "Compose",
    "FanInStep",
    "LoopStep",
    "ParallelStep",
    "Step",
    "StepResult",
    "StepSequence",
]
