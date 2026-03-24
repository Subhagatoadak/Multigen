"""
Workflow-level resilience: deadline management, retry scheduling, backoff.

Problems solved
---------------
- Workflows run forever with no time bound
- Single-step retries don't account for total workflow budget
- No escalation path when SLAs are breached
- Exponential backoff is re-implemented ad-hoc everywhere

Classes
-------
- ``RetryPolicy``        — configurable exponential backoff with jitter
- ``RetryResult``        — outcome of a retried call (attempts, final error/output)
- ``Deadline``           — a point-in-time budget with remaining-time helpers
- ``DeadlineGuard``      — wraps any async callable, raises on deadline breach
- ``EscalationFn``       — type alias for escalation callbacks
- ``DeadlineManager``    — tracks deadlines for multiple concurrent workflow runs
- ``WorkflowRetry``      — full-workflow retry loop with RetryPolicy + Deadline

Usage::

    from multigen.resilience import (
        RetryPolicy, Deadline, DeadlineGuard, WorkflowRetry,
    )

    policy = RetryPolicy(max_retries=4, base_delay=0.5, max_delay=30.0)

    # Wrap a single coroutine call
    async def my_call():
        ...
    result = await policy.call(my_call)

    # Workflow with a 5-minute deadline
    deadline = Deadline(budget_s=300)
    guard = DeadlineGuard(deadline, on_breach=lambda d: print("SLA breached!"))
    result = await guard.run(my_workflow_fn, ctx)

    # Combine both
    wr = WorkflowRetry(policy=policy, deadline=deadline)
    result = await wr.run(my_workflow_fn, ctx)
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── RetryPolicy ───────────────────────────────────────────────────────────────

@dataclass
class RetryResult:
    """Outcome of a retried call."""

    output: Any
    attempts: int
    total_delay_s: float
    error: Optional[Exception] = None

    @property
    def success(self) -> bool:
        return self.error is None


class RetryPolicy:
    """
    Configurable exponential backoff retry policy.

    Delay formula:  ``min(max_delay, base_delay * (multiplier ** attempt) + jitter)``

    Parameters
    ----------
    max_retries     Maximum number of retry attempts (0 = no retry).
    base_delay      Initial delay in seconds.
    multiplier      Backoff multiplier (default 2 → doubles each attempt).
    max_delay       Cap on delay (seconds).
    jitter          Max random jitter added to each delay (seconds).
    retryable_on    Tuple of exception types to retry on (default: all exceptions).

    Usage::

        policy = RetryPolicy(max_retries=3, base_delay=1.0, max_delay=30.0)
        result = await policy.call(flaky_coroutine_fn)
        print(result.attempts, result.output)
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        multiplier: float = 2.0,
        max_delay: float = 60.0,
        jitter: float = 0.5,
        retryable_on: Tuple[type, ...] = (Exception,),
        on_retry: Optional[Callable[[int, Exception, float], None]] = None,
    ) -> None:
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.multiplier = multiplier
        self.max_delay = max_delay
        self.jitter = jitter
        self.retryable_on = retryable_on
        self._on_retry = on_retry

    def delay_for(self, attempt: int) -> float:
        """Return the sleep duration for *attempt* (0-indexed)."""
        delay = self.base_delay * (self.multiplier ** attempt)
        delay = min(self.max_delay, delay)
        delay += random.uniform(0, self.jitter)
        return delay

    async def call(
        self,
        fn: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> RetryResult:
        """
        Call *fn* with retry.  *fn* may be a coroutine function or a sync callable.
        """
        last_exc: Optional[Exception] = None
        total_delay = 0.0

        for attempt in range(self.max_retries + 1):
            try:
                if asyncio.iscoroutinefunction(fn):
                    output = await fn(*args, **kwargs)
                else:
                    loop = asyncio.get_event_loop()
                    output = await loop.run_in_executor(None, lambda: fn(*args, **kwargs))
                return RetryResult(output=output, attempts=attempt + 1, total_delay_s=total_delay)
            except self.retryable_on as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    delay = self.delay_for(attempt)
                    total_delay += delay
                    if self._on_retry:
                        self._on_retry(attempt + 1, exc, delay)
                    logger.debug(
                        "RetryPolicy: attempt %d/%d failed (%s), retrying in %.1fs",
                        attempt + 1, self.max_retries + 1, exc, delay,
                    )
                    await asyncio.sleep(delay)

        return RetryResult(
            output=None,
            attempts=self.max_retries + 1,
            total_delay_s=total_delay,
            error=last_exc,
        )

    def __repr__(self) -> str:
        return (f"RetryPolicy(max_retries={self.max_retries}, "
                f"base_delay={self.base_delay}, max_delay={self.max_delay})")


# ── Deadline ──────────────────────────────────────────────────────────────────

class Deadline:
    """
    A fixed-budget time limit for a workflow run.

    Usage::

        d = Deadline(budget_s=120)        # 2-minute budget
        d.remaining_s()                   # seconds left
        d.is_expired()                    # True / False
        d.assert_not_expired("step-3")    # raises DeadlineError if expired
    """

    def __init__(self, budget_s: float) -> None:
        self.budget_s = budget_s
        self._start = time.monotonic()
        self._deadline = self._start + budget_s

    def remaining_s(self) -> float:
        return max(0.0, self._deadline - time.monotonic())

    def elapsed_s(self) -> float:
        return time.monotonic() - self._start

    def is_expired(self) -> bool:
        return time.monotonic() >= self._deadline

    def assert_not_expired(self, context: str = "") -> None:
        if self.is_expired():
            msg = f"DeadlineError: budget {self.budget_s}s exceeded"
            if context:
                msg += f" at '{context}'"
            raise DeadlineError(msg)

    def fraction_used(self) -> float:
        return min(1.0, self.elapsed_s() / self.budget_s)

    def __repr__(self) -> str:
        return f"Deadline(budget={self.budget_s}s, remaining={self.remaining_s():.1f}s)"


class DeadlineError(Exception):
    """Raised when a workflow exceeds its time budget."""


# ── DeadlineGuard ─────────────────────────────────────────────────────────────

class DeadlineGuard:
    """
    Wraps an async callable and enforces a ``Deadline``.

    On breach: the task is cancelled and *on_breach* is called (if provided)
    before raising ``DeadlineError``.

    Usage::

        guard = DeadlineGuard(
            Deadline(budget_s=60),
            on_breach=lambda d: alert_oncall(d.elapsed_s()),
        )
        result = await guard.run(my_agent, ctx)
    """

    def __init__(
        self,
        deadline: Deadline,
        on_breach: Optional[Callable[["Deadline"], Any]] = None,
    ) -> None:
        self._deadline = deadline
        self._on_breach = on_breach

    async def run(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        remaining = self._deadline.remaining_s()
        if remaining <= 0:
            await self._handle_breach()
            raise DeadlineError(f"Deadline already expired before call")
        try:
            if asyncio.iscoroutinefunction(fn):
                coro = fn(*args, **kwargs)
            else:
                loop = asyncio.get_event_loop()
                coro = loop.run_in_executor(None, lambda: fn(*args, **kwargs))
            return await asyncio.wait_for(coro, timeout=remaining)
        except asyncio.TimeoutError:
            await self._handle_breach()
            raise DeadlineError(
                f"Deadline exceeded: {self._deadline.budget_s}s budget exhausted "
                f"after {self._deadline.elapsed_s():.1f}s"
            )

    async def _handle_breach(self) -> None:
        if self._on_breach is not None:
            result = self._on_breach(self._deadline)
            if asyncio.iscoroutine(result):
                await result


# ── DeadlineManager ───────────────────────────────────────────────────────────

class DeadlineManager:
    """
    Tracks deadlines for multiple concurrent workflow runs.

    Usage::

        mgr = DeadlineManager(default_budget_s=300)
        d = mgr.create("run-abc")
        await mgr.guard("run-abc").run(my_agent, ctx)
        mgr.cancel("run-abc")
    """

    def __init__(self, default_budget_s: float = 300.0) -> None:
        self.default_budget_s = default_budget_s
        self._deadlines: Dict[str, Deadline] = {}
        self._on_breach: Optional[Callable] = None

    def on_breach(self, fn: Callable) -> None:
        """Set a global breach handler called for any deadline breach."""
        self._on_breach = fn

    def create(self, run_id: str, budget_s: Optional[float] = None) -> Deadline:
        d = Deadline(budget_s or self.default_budget_s)
        self._deadlines[run_id] = d
        return d

    def get(self, run_id: str) -> Optional[Deadline]:
        return self._deadlines.get(run_id)

    def guard(self, run_id: str) -> DeadlineGuard:
        d = self._deadlines.get(run_id)
        if d is None:
            d = self.create(run_id)
        return DeadlineGuard(d, on_breach=self._on_breach)

    def cancel(self, run_id: str) -> None:
        self._deadlines.pop(run_id, None)

    def expired_runs(self) -> List[str]:
        return [rid for rid, d in self._deadlines.items() if d.is_expired()]

    def status(self) -> Dict[str, Any]:
        return {
            rid: {
                "remaining_s": d.remaining_s(),
                "elapsed_s": d.elapsed_s(),
                "expired": d.is_expired(),
            }
            for rid, d in self._deadlines.items()
        }


# ── WorkflowRetry ─────────────────────────────────────────────────────────────

@dataclass
class WorkflowRetryResult:
    """Outcome of a full-workflow retry run."""

    output: Any
    attempts: int
    total_elapsed_s: float
    error: Optional[Exception] = None
    deadline_breached: bool = False

    @property
    def success(self) -> bool:
        return self.error is None and not self.deadline_breached


class WorkflowRetry:
    """
    Full-workflow retry loop combining ``RetryPolicy`` + ``Deadline``.

    Unlike ``RetryPolicy`` (which retries a single function call), this class
    reruns the *entire workflow* on failure, respecting an overall time budget.

    Usage::

        wr = WorkflowRetry(
            policy=RetryPolicy(max_retries=3, base_delay=2.0),
            deadline=Deadline(budget_s=120),
            on_breach=lambda: notify_oncall(),
        )
        result = await wr.run(my_chain_fn, ctx={"query": "..."})
    """

    def __init__(
        self,
        policy: Optional[RetryPolicy] = None,
        deadline: Optional[Deadline] = None,
        on_breach: Optional[Callable] = None,
        on_retry: Optional[Callable[[int, Exception], None]] = None,
    ) -> None:
        self._policy   = policy   or RetryPolicy()
        self._deadline = deadline
        self._on_breach = on_breach
        self._on_retry  = on_retry

    async def run(
        self,
        fn: Callable,
        *args: Any,
        **kwargs: Any,
    ) -> WorkflowRetryResult:
        start = time.monotonic()
        last_exc: Optional[Exception] = None
        attempts = 0

        for attempt in range(self._policy.max_retries + 1):
            # Deadline check before each attempt
            if self._deadline is not None and self._deadline.is_expired():
                if self._on_breach:
                    r = self._on_breach()
                    if asyncio.iscoroutine(r):
                        await r
                return WorkflowRetryResult(
                    output=None,
                    attempts=attempts,
                    total_elapsed_s=time.monotonic() - start,
                    deadline_breached=True,
                    error=DeadlineError("Deadline expired before attempt"),
                )

            attempts += 1
            try:
                timeout = self._deadline.remaining_s() if self._deadline else None
                if asyncio.iscoroutinefunction(fn):
                    coro = fn(*args, **kwargs)
                else:
                    loop = asyncio.get_event_loop()
                    coro = loop.run_in_executor(None, lambda: fn(*args, **kwargs))

                if timeout is not None:
                    output = await asyncio.wait_for(coro, timeout=timeout)
                else:
                    output = await coro

                return WorkflowRetryResult(
                    output=output,
                    attempts=attempts,
                    total_elapsed_s=time.monotonic() - start,
                )
            except asyncio.TimeoutError:
                if self._on_breach:
                    r = self._on_breach()
                    if asyncio.iscoroutine(r):
                        await r
                return WorkflowRetryResult(
                    output=None,
                    attempts=attempts,
                    total_elapsed_s=time.monotonic() - start,
                    deadline_breached=True,
                    error=DeadlineError("Deadline timeout"),
                )
            except Exception as exc:
                last_exc = exc
                if attempt < self._policy.max_retries:
                    delay = self._policy.delay_for(attempt)
                    if self._on_retry:
                        self._on_retry(attempt + 1, exc)
                    logger.warning(
                        "WorkflowRetry attempt %d/%d failed: %s — retrying in %.1fs",
                        attempt + 1, self._policy.max_retries + 1, exc, delay,
                    )
                    await asyncio.sleep(delay)

        return WorkflowRetryResult(
            output=None,
            attempts=attempts,
            total_elapsed_s=time.monotonic() - start,
            error=last_exc,
        )


__all__ = [
    "Deadline",
    "DeadlineError",
    "DeadlineGuard",
    "DeadlineManager",
    "RetryPolicy",
    "RetryResult",
    "WorkflowRetry",
    "WorkflowRetryResult",
]
