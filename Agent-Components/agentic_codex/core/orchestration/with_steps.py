"""Linear step runner with batching, parallelism, conditionals, retries, and circuit breakers."""
from __future__ import annotations

import asyncio
import inspect
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from typing import Callable, List, Mapping, Optional, Sequence

from ..agent import Context
from ..schemas import AgentStep
from ..observability.metrics import record_counter, record_latency


StepFn = Callable[[Context], AgentStep]


@dataclass
class StepSpec:
    """Declarative definition of a step."""

    name: str
    fn: StepFn
    condition: Optional[Callable[[Context], bool]] = None
    branch: Optional[Callable[[Context], Sequence["StepSpec"]]] = None
    batch_key: Optional[str] = None
    parallel: bool = False
    isolate_state: bool = False
    retry: int = 0
    backoff: float = 0.1
    timeout: Optional[float] = None
    circuit_breaker_key: Optional[str] = None
    failure_threshold: int = 3
    wait_seconds: float = 0.0
    mode: str = "sync"  # sync | async (coroutine) | thread


def _clone_context(ctx: Context) -> Context:
    return Context(
        goal=ctx.goal,
        scratch=dict(ctx.scratch),
        registry=ctx.registry,
        inbox=list(ctx.inbox),
        memory=dict(ctx.memory),
        stores=dict(ctx.stores),
        policies=dict(ctx.policies),
        components=ctx.components,
        llm=ctx.llm,
        tools=ctx.tools,
    )


def _execute_step(spec: StepSpec, ctx: Context) -> AgentStep:
    result = spec.fn(ctx)
    if inspect.isawaitable(result):
        return asyncio.run(result)
    return result


def _run_step_with_retry(spec: StepSpec, ctx: Context) -> AgentStep:
    attempts = spec.retry + 1
    last_err: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            if spec.wait_seconds:
                time.sleep(spec.wait_seconds)
            if spec.timeout:
                with ThreadPoolExecutor(max_workers=1) as pool:
                    future = pool.submit(_execute_step, spec, ctx)
                    return future.result(timeout=spec.timeout)
            return _execute_step(spec, ctx)
        except FuturesTimeoutError as exc:
            last_err = exc
        except Exception as exc:  # pragma: no cover - defensive
            last_err = exc
        if attempt < attempts:
            time.sleep(spec.backoff * attempt)
    raise last_err or RuntimeError(f"Step {spec.name} failed after retries.")


def _check_circuit(spec: StepSpec, ctx: Context) -> None:
    if not spec.circuit_breaker_key:
        return
    failures = ctx.scratch.setdefault("_circuit_breakers", {}).get(spec.circuit_breaker_key, 0)
    if failures >= spec.failure_threshold:
        raise RuntimeError(f"Circuit open for step {spec.name}")


def _note_failure(spec: StepSpec, ctx: Context) -> None:
    if not spec.circuit_breaker_key:
        return
    breakers = ctx.scratch.setdefault("_circuit_breakers", {})
    breakers[spec.circuit_breaker_key] = breakers.get(spec.circuit_breaker_key, 0) + 1


def run_steps(
    steps: Sequence[StepSpec],
    context: Context,
    *,
    max_parallel: int = 4,
) -> List[AgentStep]:
    """Execute a series of steps with optional batch/parallel/conditional controls."""

    token = context.components.get("cancel_token") if isinstance(context.components, Mapping) else None
    logger = (
        context.components.get("logger")
        if isinstance(context.components, Mapping) and "logger" in context.components
        else None
    )
    results: List[AgentStep] = []
    for spec in steps:
        if getattr(token, "cancelled", False):
            break
        if spec.condition and not spec.condition(context):
            context.scratch.setdefault("with_steps_skipped", []).append(spec.name)
            continue

        _check_circuit(spec, context)
        if logger:
            logger.log("with_steps.start", step=spec.name)

        if spec.batch_key:
            items = list(context.scratch.get(spec.batch_key, []))
            batch_results: List[AgentStep] = []
            if spec.parallel and items:
                with ThreadPoolExecutor(max_workers=min(max_parallel, len(items))) as pool:
                    futures = []
                    for item in items:
                        item_ctx = _clone_context(context) if spec.isolate_state else context
                        item_ctx.scratch["batch_item"] = item
                        futures.append(pool.submit(_run_step_with_retry, spec, item_ctx))
                    for future in futures:
                        batch_results.append(future.result())
            else:
                for item in items:
                    item_ctx = _clone_context(context) if spec.isolate_state else context
                    item_ctx.scratch["batch_item"] = item
                    batch_results.append(_run_step_with_retry(spec, item_ctx))
            results.extend(batch_results)
            for res in batch_results:
                if res.out_messages:
                    context.push_message(res.out_messages[-1])
                context.scratch.update(res.state_updates)
            continue

        try:
            start = time.perf_counter()
            result = _run_step_with_retry(spec, _clone_context(context) if spec.isolate_state else context)
            record_latency(context.scratch, "with_steps.latency", time.perf_counter() - start, step=spec.name)
            record_counter(context.scratch, "with_steps.success", 1, step=spec.name)
        except Exception:
            record_counter(context.scratch, "with_steps.error", 1, step=spec.name)
            _note_failure(spec, context)
            raise

        results.append(result)
        if result.out_messages:
            context.push_message(result.out_messages[-1])
        context.scratch.update(result.state_updates)
        if result.stop:
            if logger:
                logger.log("with_steps.finish", step=spec.name, stop=True)
            break
        if spec.branch:
            branch_steps = list(spec.branch(context))
            if branch_steps:
                branch_results = run_steps(branch_steps, context, max_parallel=max_parallel)
                results.extend(branch_results)
        if logger:
            logger.log("with_steps.finish", step=spec.name, stop=result.stop if 'result' in locals() else False)

    return results


__all__ = ["StepSpec", "run_steps"]
