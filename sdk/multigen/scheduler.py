"""
Scheduling and triggers for Multigen — batch workloads and event-driven execution.

Features
--------
- ``Schedule``          — base class for timing strategies
- ``IntervalSchedule``  — fire every N seconds
- ``CronSchedule``      — cron-expression-like schedule (minute/hour/day/weekday)
- ``OnceSchedule``      — fire once at a specific time
- ``Trigger``           — event-based trigger (fire on named event)
- ``ScheduledJob``      — job definition (callable + schedule + metadata)
- ``JobResult``         — outcome of a scheduled run
- ``Scheduler``         — async event loop that drives all scheduled jobs
- ``InMemoryJobStore``  — in-process job registry
- ``SQLiteJobStore``    — durable SQLite-backed job store

Usage::

    from multigen.scheduler import (
        Scheduler, ScheduledJob, IntervalSchedule, CronSchedule,
        Trigger, JobResult,
    )

    scheduler = Scheduler()

    # Every 5 minutes
    @scheduler.job(schedule=IntervalSchedule(seconds=300), name="refresh-cache")
    async def refresh_cache():
        ...

    # Cron: every day at 02:00
    @scheduler.job(schedule=CronSchedule(hour=2, minute=0), name="nightly-report")
    async def nightly_report():
        ...

    # Event-driven trigger
    trigger = Trigger("user.signup")
    @scheduler.on(trigger)
    async def send_welcome_email(event_data):
        ...

    await scheduler.start()
    # later…
    await scheduler.emit("user.signup", {"user_id": "42"})
    await scheduler.stop()
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Schedule base ──────────────────────────────────────────────────────────────

class Schedule:
    """Base class for job schedules. Subclasses implement ``next_run(now)``."""

    def next_run(self, now: float) -> float:
        """Return the Unix timestamp of the next scheduled run after *now*."""
        raise NotImplementedError

    def is_due(self, now: float, last_run: Optional[float]) -> bool:
        """Return True if the job should run now."""
        if last_run is None:
            return True
        return now >= self.next_run(last_run)


# ── IntervalSchedule ───────────────────────────────────────────────────────────

class IntervalSchedule(Schedule):
    """
    Fire the job every *seconds* seconds.

    Usage::

        schedule = IntervalSchedule(seconds=60)      # every minute
        schedule = IntervalSchedule(minutes=5)       # every 5 minutes
        schedule = IntervalSchedule(hours=1)         # every hour
    """

    def __init__(
        self,
        seconds: float = 0,
        minutes: float = 0,
        hours: float = 0,
    ) -> None:
        self.interval = seconds + minutes * 60 + hours * 3600
        if self.interval <= 0:
            raise ValueError("IntervalSchedule interval must be > 0")

    def next_run(self, now: float) -> float:
        return now + self.interval

    def is_due(self, now: float, last_run: Optional[float]) -> bool:
        if last_run is None:
            return True
        return (now - last_run) >= self.interval

    def __repr__(self) -> str:
        return f"IntervalSchedule({self.interval}s)"


# ── CronSchedule ──────────────────────────────────────────────────────────────

class CronSchedule(Schedule):
    """
    Cron-like schedule with minute/hour/day-of-month/weekday fields.

    Use ``None`` for "any" (wildcard).  Weekdays: 0=Monday … 6=Sunday.

    Usage::

        CronSchedule(hour=2, minute=0)              # every day at 02:00
        CronSchedule(hour=9, minute=30, weekday=0)  # Mondays at 09:30
        CronSchedule(minute=0)                      # every hour on the hour
    """

    def __init__(
        self,
        minute: Optional[int] = None,
        hour: Optional[int] = None,
        day: Optional[int] = None,
        weekday: Optional[int] = None,
    ) -> None:
        self.minute = minute
        self.hour = hour
        self.day = day
        self.weekday = weekday

    def _matches(self, now_struct) -> bool:
        if self.minute is not None and now_struct.tm_min != self.minute:
            return False
        if self.hour is not None and now_struct.tm_hour != self.hour:
            return False
        if self.day is not None and now_struct.tm_mday != self.day:
            return False
        if self.weekday is not None and now_struct.tm_wday != self.weekday:
            return False
        return True

    def is_due(self, now: float, last_run: Optional[float]) -> bool:
        """Due if the current minute matches and we haven't fired this minute."""
        t = time.localtime(now)
        if not self._matches(t):
            return False
        if last_run is None:
            return True
        # Allow at most one fire per minute
        return (now - last_run) >= 55

    def next_run(self, now: float) -> float:
        # Approximate: advance one minute at a time (simplified)
        t = now + 60
        for _ in range(60 * 24 * 7):
            if self._matches(time.localtime(t)):
                return t
            t += 60
        return now + 3600  # fallback

    def __repr__(self) -> str:
        parts = []
        if self.minute is not None:
            parts.append(f"min={self.minute}")
        if self.hour is not None:
            parts.append(f"hr={self.hour}")
        if self.day is not None:
            parts.append(f"day={self.day}")
        if self.weekday is not None:
            parts.append(f"wd={self.weekday}")
        return f"CronSchedule({', '.join(parts) or '*'})"


# ── OnceSchedule ──────────────────────────────────────────────────────────────

class OnceSchedule(Schedule):
    """Fire the job exactly once at *run_at* (Unix timestamp or seconds from now)."""

    def __init__(self, run_at: Optional[float] = None, delay_s: float = 0) -> None:
        self.run_at = run_at if run_at is not None else time.time() + delay_s
        self._fired = False

    def is_due(self, now: float, last_run: Optional[float]) -> bool:
        if self._fired:
            return False
        return now >= self.run_at

    def next_run(self, now: float) -> float:
        return self.run_at

    def mark_fired(self) -> None:
        self._fired = True

    def __repr__(self) -> str:
        return f"OnceSchedule(at={self.run_at})"


# ── Trigger ────────────────────────────────────────────────────────────────────

@dataclass
class Trigger:
    """
    Named event trigger — fires a job when an event is emitted.

    Usage::

        trigger = Trigger("payment.completed")
        @scheduler.on(trigger)
        async def handle_payment(event_data):
            ...

        await scheduler.emit("payment.completed", {"order_id": "x"})
    """

    event_name: str
    filter_fn: Optional[Callable[[Dict[str, Any]], bool]] = None

    def matches(self, event_name: str, event_data: Dict[str, Any]) -> bool:
        if self.event_name != event_name and self.event_name != "*":
            return False
        if self.filter_fn is not None:
            return bool(self.filter_fn(event_data))
        return True


# ── JobResult ─────────────────────────────────────────────────────────────────

@dataclass
class JobResult:
    """Outcome of a single job execution."""

    job_name: str
    run_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    output: Any = None
    error: Optional[str] = None
    trigger_event: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.error is None

    @property
    def duration_ms(self) -> Optional[float]:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at) * 1000


# ── ScheduledJob ──────────────────────────────────────────────────────────────

@dataclass
class ScheduledJob:
    """
    A job definition: callable + schedule (or trigger) + metadata.

    Parameters
    ----------
    fn          The async callable to invoke.
    name        Unique job name.
    schedule    ``Schedule`` instance (None → trigger-only job).
    trigger     ``Trigger`` instance (None → schedule-only job).
    enabled     Whether the job is active.
    max_retries Retry count on failure.
    timeout_s   Per-run timeout.
    on_error    Optional error handler ``async (exc, result) -> None``.
    """

    fn: Callable
    name: str
    schedule: Optional[Schedule] = None
    trigger: Optional[Trigger] = None
    enabled: bool = True
    max_retries: int = 0
    timeout_s: float = 300.0
    on_error: Optional[Callable] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Runtime state
    last_run: Optional[float] = field(default=None, repr=False)
    run_count: int = field(default=0, repr=False)
    error_count: int = field(default=0, repr=False)

    def is_due(self, now: float) -> bool:
        if not self.enabled or self.schedule is None:
            return False
        return self.schedule.is_due(now, self.last_run)

    def stats(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "enabled": self.enabled,
            "run_count": self.run_count,
            "error_count": self.error_count,
            "last_run": self.last_run,
        }


# ── Scheduler ─────────────────────────────────────────────────────────────────

class Scheduler:
    """
    Async scheduler that drives scheduled jobs and event-triggered jobs.

    Usage::

        scheduler = Scheduler(tick_interval=1.0)

        @scheduler.job(IntervalSchedule(seconds=10), name="heartbeat")
        async def heartbeat():
            print("alive")

        await scheduler.start()
        await asyncio.sleep(60)
        await scheduler.stop()
    """

    def __init__(self, tick_interval: float = 1.0) -> None:
        self.tick_interval = tick_interval
        self._jobs: Dict[str, ScheduledJob] = {}
        self._results: List[JobResult] = []
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue = asyncio.Queue()

    # ── Registration ──

    def job(
        self,
        schedule: Schedule,
        name: Optional[str] = None,
        max_retries: int = 0,
        timeout_s: float = 300.0,
        enabled: bool = True,
        on_error: Optional[Callable] = None,
    ) -> Callable:
        """Decorator to register a scheduled job."""
        def decorator(fn: Callable) -> Callable:
            job_name = name or fn.__name__
            self._jobs[job_name] = ScheduledJob(
                fn=fn,
                name=job_name,
                schedule=schedule,
                enabled=enabled,
                max_retries=max_retries,
                timeout_s=timeout_s,
                on_error=on_error,
            )
            return fn
        return decorator

    def on(
        self,
        trigger: Trigger,
        name: Optional[str] = None,
        timeout_s: float = 300.0,
    ) -> Callable:
        """Decorator to register an event-triggered job."""
        def decorator(fn: Callable) -> Callable:
            job_name = name or fn.__name__
            self._jobs[job_name] = ScheduledJob(
                fn=fn,
                name=job_name,
                trigger=trigger,
                timeout_s=timeout_s,
            )
            return fn
        return decorator

    def add_job(self, job: ScheduledJob) -> None:
        self._jobs[job.name] = job

    def remove_job(self, name: str) -> bool:
        if name in self._jobs:
            del self._jobs[name]
            return True
        return False

    def enable(self, name: str) -> None:
        if name in self._jobs:
            self._jobs[name].enabled = True

    def disable(self, name: str) -> None:
        if name in self._jobs:
            self._jobs[name].enabled = False

    # ── Event emission ──

    async def emit(self, event_name: str, event_data: Optional[Dict[str, Any]] = None) -> int:
        """
        Emit a named event.  All matching trigger-jobs are queued for execution.

        Returns the number of jobs triggered.
        """
        data = event_data or {}
        triggered = 0
        for job in self._jobs.values():
            if job.trigger is not None and job.trigger.matches(event_name, data):
                await self._event_queue.put((job, data, event_name))
                triggered += 1
        return triggered

    # ── Execution ──

    async def _run_job(
        self,
        job: ScheduledJob,
        event_data: Optional[Dict[str, Any]] = None,
        event_name: Optional[str] = None,
    ) -> JobResult:
        result = JobResult(job_name=job.name, trigger_event=event_name)
        last_exc: Optional[Exception] = None

        for attempt in range(job.max_retries + 1):
            try:
                if event_data is not None:
                    coro = job.fn(event_data) if asyncio.iscoroutinefunction(job.fn) else asyncio.get_event_loop().run_in_executor(None, job.fn, event_data)
                else:
                    coro = job.fn() if asyncio.iscoroutinefunction(job.fn) else asyncio.get_event_loop().run_in_executor(None, job.fn)
                output = await asyncio.wait_for(coro, timeout=job.timeout_s)
                result.output = output
                result.finished_at = time.time()
                job.run_count += 1
                job.last_run = result.started_at
                break
            except asyncio.TimeoutError as exc:
                last_exc = exc
                result.error = f"TimeoutError: exceeded {job.timeout_s}s"
            except Exception as exc:
                last_exc = exc
                result.error = f"{type(exc).__name__}: {exc}"
                if attempt < job.max_retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))

        if result.error:
            job.error_count += 1
            result.finished_at = time.time()
            if job.on_error is not None:
                try:
                    r = job.on_error(last_exc, result)
                    if asyncio.iscoroutine(r):
                        await r
                except Exception:
                    pass
            logger.error("[Scheduler] Job '%s' failed: %s", job.name, result.error)
        else:
            logger.debug("[Scheduler] Job '%s' completed in %.0fms", job.name, result.duration_ms or 0)

        self._results.append(result)
        return result

    # ── Main loop ──

    async def _loop(self) -> None:
        while self._running:
            now = time.time()
            # Schedule-based jobs
            for job in list(self._jobs.values()):
                if job.schedule is not None and job.is_due(now):
                    if isinstance(job.schedule, OnceSchedule):
                        job.schedule.mark_fired()
                    asyncio.create_task(self._run_job(job))

            # Event-triggered jobs (non-blocking drain)
            while not self._event_queue.empty():
                try:
                    job, event_data, event_name = self._event_queue.get_nowait()
                    asyncio.create_task(self._run_job(job, event_data, event_name))
                except asyncio.QueueEmpty:
                    break

            await asyncio.sleep(self.tick_interval)

    async def start(self) -> None:
        """Start the scheduler loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("[Scheduler] Started with %d jobs", len(self._jobs))

    async def stop(self) -> None:
        """Gracefully stop the scheduler loop."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[Scheduler] Stopped")

    async def run_now(self, job_name: str) -> Optional[JobResult]:
        """Manually trigger a job by name immediately."""
        job = self._jobs.get(job_name)
        if job is None:
            return None
        return await self._run_job(job)

    # ── Inspection ──

    def list_jobs(self) -> List[ScheduledJob]:
        return list(self._jobs.values())

    def stats(self) -> Dict[str, Any]:
        return {
            "running": self._running,
            "total_jobs": len(self._jobs),
            "total_results": len(self._results),
            "jobs": {name: job.stats() for name, job in self._jobs.items()},
        }

    def recent_results(self, n: int = 20) -> List[JobResult]:
        return self._results[-n:]

    def __repr__(self) -> str:
        return f"Scheduler(jobs={len(self._jobs)}, running={self._running})"


__all__ = [
    "CronSchedule",
    "IntervalSchedule",
    "JobResult",
    "OnceSchedule",
    "Schedule",
    "ScheduledJob",
    "Scheduler",
    "Trigger",
]
