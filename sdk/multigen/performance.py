"""
performance.py — Agent Performance Optimization Toolkit for Multigen SDK

Provides profiling, batching, connection pooling, lazy evaluation, rate limiting,
and a combined optimizer for async agent workloads.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Generic,
    List,
    Optional,
    Tuple,
    TypeVar,
)

__all__ = [
    "ExecutionProfile",
    "AgentProfiler",
    "BatchExecutor",
    "ConnectionPool",
    "LazyValue",
    "lazy",
    "RateLimiter",
    "ExecutionOptimizer",
]

T = TypeVar("T")

# ---------------------------------------------------------------------------
# 1. ExecutionProfile
# ---------------------------------------------------------------------------


@dataclass
class ExecutionProfile:
    """Aggregated performance data for a single named agent."""

    agent_name: str
    call_count: int = 0
    total_ms: float = 0.0
    min_ms: float = float("inf")
    max_ms: float = 0.0
    error_count: int = 0
    _samples: List[float] = field(default_factory=list, repr=False)

    @property
    def avg_ms(self) -> float:
        """Average execution time in milliseconds."""
        if self.call_count == 0:
            return 0.0
        return self.total_ms / self.call_count

    @property
    def p95_ms(self) -> float:
        """95th-percentile execution time in milliseconds."""
        if not self._samples:
            return 0.0
        sorted_samples = sorted(self._samples)
        idx = max(0, int(len(sorted_samples) * 0.95) - 1)
        return sorted_samples[idx]

    def record(self, elapsed_ms: float) -> None:
        """Record a single successful call timing."""
        self.call_count += 1
        self.total_ms += elapsed_ms
        self._samples.append(elapsed_ms)
        if elapsed_ms < self.min_ms:
            self.min_ms = elapsed_ms
        if elapsed_ms > self.max_ms:
            self.max_ms = elapsed_ms

    def record_error(self) -> None:
        """Increment the error counter."""
        self.error_count += 1

    def to_dict(self) -> dict:
        """Serialise the profile to a plain dictionary."""
        return {
            "agent_name": self.agent_name,
            "call_count": self.call_count,
            "total_ms": round(self.total_ms, 3),
            "min_ms": round(self.min_ms, 3) if self.min_ms != float("inf") else None,
            "max_ms": round(self.max_ms, 3),
            "avg_ms": round(self.avg_ms, 3),
            "p95_ms": round(self.p95_ms, 3),
            "error_count": self.error_count,
        }


# ---------------------------------------------------------------------------
# 2. AgentProfiler
# ---------------------------------------------------------------------------


class AgentProfiler:
    """Decorator / context-manager profiler that records per-agent timings.

    Usage as a decorator::

        profiler = AgentProfiler()

        @profiler.profile("my_agent")
        async def call_agent(prompt: str) -> str:
            ...

    Usage as a context manager::

        async with profiler.profile_ctx("my_agent"):
            result = await some_agent(prompt)
    """

    def __init__(self) -> None:
        self._profiles: Dict[str, ExecutionProfile] = {}

    def _get_or_create(self, agent_name: str) -> ExecutionProfile:
        if agent_name not in self._profiles:
            self._profiles[agent_name] = ExecutionProfile(agent_name=agent_name)
        return self._profiles[agent_name]

    def profile(self, agent_name: str) -> Callable:
        """Decorator that wraps an async function and records its timing."""

        def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                prof = self._get_or_create(agent_name)
                start = time.perf_counter()
                try:
                    result = await fn(*args, **kwargs)
                    elapsed_ms = (time.perf_counter() - start) * 1000.0
                    prof.record(elapsed_ms)
                    return result
                except Exception:
                    prof.record_error()
                    raise

            wrapper.__name__ = getattr(fn, "__name__", "wrapped")
            wrapper.__qualname__ = getattr(fn, "__qualname__", "wrapped")
            return wrapper

        return decorator

    @asynccontextmanager
    async def profile_ctx(self, agent_name: str):
        """Async context manager variant of the profiler."""
        prof = self._get_or_create(agent_name)
        start = time.perf_counter()
        try:
            yield prof
            elapsed_ms = (time.perf_counter() - start) * 1000.0
            prof.record(elapsed_ms)
        except Exception:
            prof.record_error()
            raise

    def get_profile(self, agent_name: str) -> Optional[ExecutionProfile]:
        """Return the profile for *agent_name*, or ``None`` if not yet recorded."""
        return self._profiles.get(agent_name)

    def all_profiles(self) -> Dict[str, ExecutionProfile]:
        """Return a shallow copy of all profiles keyed by agent name."""
        return dict(self._profiles)

    def reset(self, agent_name: Optional[str] = None) -> None:
        """Clear profiling data.  Pass *agent_name* to clear only one entry."""
        if agent_name is None:
            self._profiles.clear()
        elif agent_name in self._profiles:
            del self._profiles[agent_name]

    def report(self) -> str:
        """Return a formatted ASCII table of all profiles sorted by total_ms desc."""
        profiles = sorted(
            self._profiles.values(), key=lambda p: p.total_ms, reverse=True
        )
        if not profiles:
            return "No profiling data available."

        headers = ["Agent", "Calls", "Total(ms)", "Avg(ms)", "Min(ms)", "Max(ms)", "P95(ms)", "Errors"]
        rows: List[List[str]] = []
        for p in profiles:
            min_val = f"{p.min_ms:.2f}" if p.min_ms != float("inf") else "—"
            rows.append([
                p.agent_name,
                str(p.call_count),
                f"{p.total_ms:.2f}",
                f"{p.avg_ms:.2f}",
                min_val,
                f"{p.max_ms:.2f}",
                f"{p.p95_ms:.2f}",
                str(p.error_count),
            ])

        col_widths = [
            max(len(h), max((len(r[i]) for r in rows), default=0))
            for i, h in enumerate(headers)
        ]

        def fmt_row(row: List[str]) -> str:
            return "| " + " | ".join(cell.ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"

        sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
        lines = [sep, fmt_row(headers), sep]
        for row in rows:
            lines.append(fmt_row(row))
        lines.append(sep)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# 3. BatchExecutor
# ---------------------------------------------------------------------------


class BatchExecutor:
    """Collects async calls and executes them in batches for efficiency.

    Example::

        async with BatchExecutor(batch_size=8) as executor:
            futures = [executor.submit(my_fn, arg) for arg in items]
            results = await asyncio.gather(*futures)
    """

    def __init__(
        self,
        batch_size: int = 16,
        flush_interval: float = 0.05,
        max_concurrency: int = 8,
    ) -> None:
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.max_concurrency = max_concurrency

        self._pending: List[Tuple[Callable, tuple, dict, asyncio.Future]] = []
        self._semaphore: Optional[asyncio.Semaphore] = None
        self._flush_task: Optional[asyncio.Task] = None
        self._closed = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _ensure_semaphore(self) -> asyncio.Semaphore:
        if self._semaphore is None:
            self._semaphore = asyncio.Semaphore(self.max_concurrency)
        return self._semaphore

    async def _run_one(
        self,
        fn: Callable,
        args: tuple,
        kwargs: dict,
        future: asyncio.Future,
    ) -> None:
        sem = self._ensure_semaphore()
        async with sem:
            try:
                result = await fn(*args, **kwargs)
                if not future.done():
                    future.set_result(result)
            except Exception as exc:
                if not future.done():
                    future.set_exception(exc)

    async def _auto_flush_loop(self) -> None:
        """Periodically flush the pending batch."""
        while not self._closed:
            await asyncio.sleep(self.flush_interval)
            if self._pending:
                await self.flush()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def submit(self, fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> asyncio.Future:
        """Add *fn* to the pending batch and return a Future for its result."""
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending.append((fn, args, kwargs, future))
        if len(self._pending) >= self.batch_size:
            loop.create_task(self.flush())
        return future

    async def flush(self) -> List[Any]:
        """Execute all currently pending calls and return their results."""
        if not self._pending:
            return []

        batch, self._pending = self._pending, []
        tasks = [
            asyncio.create_task(self._run_one(fn, args, kwargs, fut))
            for fn, args, kwargs, fut in batch
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        results: List[Any] = []
        for _, _, _, fut in batch:
            if fut.done() and not fut.cancelled() and fut.exception() is None:
                results.append(fut.result())
        return results

    async def __aenter__(self) -> "BatchExecutor":
        self._closed = False
        self._flush_task = asyncio.create_task(self._auto_flush_loop())
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self._closed = True
        if self._flush_task is not None:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
        # Flush any remaining items
        if self._pending:
            await self.flush()


# ---------------------------------------------------------------------------
# 4. ConnectionPool
# ---------------------------------------------------------------------------


class ConnectionPool(Generic[T]):
    """Generic async connection pool.

    Example::

        async def make_client() -> MyClient:
            return await MyClient.connect(url)

        pool: ConnectionPool[MyClient] = ConnectionPool(factory=make_client, max_size=5)

        async with pool.acquire() as client:
            await client.query(...)
    """

    def __init__(
        self,
        factory: Callable[[], Awaitable[T]],
        max_size: int = 10,
        min_size: int = 1,
    ) -> None:
        self._factory = factory
        self._max_size = max_size
        self._min_size = min_size

        self._pool: asyncio.Queue[T] = asyncio.Queue(maxsize=max_size)
        self._all_connections: List[T] = []
        self._in_use: int = 0
        self._initialised = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    async def _initialise(self) -> None:
        async with self._lock:
            if self._initialised:
                return
            for _ in range(self._min_size):
                conn = await self._factory()
                self._all_connections.append(conn)
                await self._pool.put(conn)
            self._initialised = True

    async def _ensure_initialised(self) -> None:
        if not self._initialised:
            await self._initialise()

    # ------------------------------------------------------------------
    # Pool stats
    # ------------------------------------------------------------------

    @property
    def size(self) -> int:
        """Total number of connections managed by the pool."""
        return len(self._all_connections)

    @property
    def available(self) -> int:
        """Number of connections currently idle in the pool."""
        return self._pool.qsize()

    @property
    def in_use(self) -> int:
        """Number of connections currently checked out."""
        return self._in_use

    # ------------------------------------------------------------------
    # Acquire / release
    # ------------------------------------------------------------------

    async def _get_or_create(self) -> T:
        """Return an idle connection or create a new one if capacity allows."""
        try:
            return self._pool.get_nowait()
        except asyncio.QueueEmpty:
            pass

        if len(self._all_connections) < self._max_size:
            conn = await self._factory()
            self._all_connections.append(conn)
            return conn

        # Pool exhausted — wait for a connection to be released.
        return await self._pool.get()

    @asynccontextmanager
    async def acquire(self):
        """Async context manager that yields a connection and releases it on exit."""
        await self._ensure_initialised()
        conn = await self._get_or_create()
        self._in_use += 1
        try:
            yield conn
        finally:
            self._in_use -= 1
            await self.release(conn)

    async def release(self, conn: T) -> None:
        """Return *conn* to the pool for reuse."""
        try:
            self._pool.put_nowait(conn)
        except asyncio.QueueFull:
            # Pool is full; discard the connection
            if hasattr(conn, "close"):
                try:
                    await conn.close()  # type: ignore[attr-defined]
                except Exception:
                    pass
            if conn in self._all_connections:
                self._all_connections.remove(conn)

    async def close_all(self) -> None:
        """Close all connections in the pool."""
        # Drain idle connections first
        while not self._pool.empty():
            try:
                self._pool.get_nowait()
            except asyncio.QueueEmpty:
                break

        for conn in list(self._all_connections):
            if hasattr(conn, "close"):
                try:
                    result = conn.close()  # type: ignore[attr-defined]
                    if asyncio.iscoroutine(result):
                        await result
                except Exception:
                    pass
        self._all_connections.clear()
        self._initialised = False


# ---------------------------------------------------------------------------
# 5. LazyEvaluator
# ---------------------------------------------------------------------------

_UNSET = object()


class LazyValue(Generic[T]):
    """Wraps a zero-argument callable and evaluates it at most once.

    Example::

        lv = LazyValue(lambda: expensive_computation())
        result = await lv.get()
        assert lv.is_evaluated
    """

    def __init__(self, fn: Callable[[], Any]) -> None:
        self._fn = fn
        self._result: Any = _UNSET
        self._lock: Optional[asyncio.Lock] = None

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def is_evaluated(self) -> bool:
        """``True`` if the wrapped callable has already been evaluated."""
        return self._result is not _UNSET

    async def get(self) -> T:
        """Evaluate the wrapped callable on first call; return cached value thereafter."""
        if self._result is not _UNSET:
            return self._result  # type: ignore[return-value]

        async with self._get_lock():
            # Double-checked locking
            if self._result is not _UNSET:
                return self._result  # type: ignore[return-value]

            result = self._fn()
            if asyncio.iscoroutine(result):
                result = await result
            self._result = result

        return self._result  # type: ignore[return-value]

    def invalidate(self) -> None:
        """Clear the cached result so the callable is re-evaluated on next ``get()``."""
        self._result = _UNSET


def lazy(fn: Callable[[], Any]) -> LazyValue:
    """Decorator / factory that wraps *fn* in a :class:`LazyValue`."""
    return LazyValue(fn)


# ---------------------------------------------------------------------------
# 6. RateLimiter
# ---------------------------------------------------------------------------


class RateLimiter:
    """Token-bucket rate limiter for agent calls.

    Example::

        limiter = RateLimiter(rate=10.0, burst=20)

        async def call_agent():
            await limiter.acquire()
            return await agent(prompt)
    """

    def __init__(self, rate: float, burst: int = 1) -> None:
        if rate <= 0:
            raise ValueError("rate must be > 0")
        self.rate = rate          # tokens per second
        self.burst = burst        # maximum token accumulation
        self._tokens: float = float(burst)
        self._last_refill: float = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(float(self.burst), self._tokens + elapsed * self.rate)
        self._last_refill = now

    async def acquire(self, tokens: int = 1) -> None:
        """Block until *tokens* are available in the bucket."""
        if tokens > self.burst:
            raise ValueError(f"tokens ({tokens}) cannot exceed burst ({self.burst})")
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                # Calculate how long to wait for enough tokens
                deficit = tokens - self._tokens
                wait_time = deficit / self.rate
            await asyncio.sleep(wait_time)

    def try_acquire(self, tokens: int = 1) -> bool:
        """Non-blocking attempt.  Returns ``True`` if tokens were granted."""
        if tokens > self.burst:
            return False
        self._refill()
        if self._tokens >= tokens:
            self._tokens -= tokens
            return True
        return False

    @property
    def available_tokens(self) -> float:
        """Current token level (approximate, not thread-safe without lock)."""
        self._refill()
        return self._tokens


# ---------------------------------------------------------------------------
# 7. ExecutionOptimizer
# ---------------------------------------------------------------------------


class ExecutionOptimizer:
    """Combines :class:`AgentProfiler`, :class:`BatchExecutor`, and
    :class:`RateLimiter` into a single convenience wrapper.

    Example::

        optimizer = ExecutionOptimizer(
            profiler=AgentProfiler(),
            batch_executor=BatchExecutor(batch_size=8),
            rate_limiter=RateLimiter(rate=50.0, burst=10),
        )

        optimized_fn = optimizer.optimize(raw_agent_fn, agent_name="my_agent")
        result = await optimized_fn(prompt)

        print(optimizer.stats())
    """

    def __init__(
        self,
        profiler: Optional[AgentProfiler] = None,
        batch_executor: Optional[BatchExecutor] = None,
        rate_limiter: Optional[RateLimiter] = None,
    ) -> None:
        self.profiler = profiler
        self.batch_executor = batch_executor
        self.rate_limiter = rate_limiter

    def optimize(
        self,
        fn: Callable[..., Awaitable[Any]],
        agent_name: str,
    ) -> Callable[..., Awaitable[Any]]:
        """Wrap *fn* with all configured optimizations.

        Applied in order: rate limiter → profiler → batch routing.
        """
        profiler = self.profiler
        batch_executor = self.batch_executor
        rate_limiter = self.rate_limiter

        async def optimized(*args: Any, **kwargs: Any) -> Any:
            # 1. Rate limiting
            if rate_limiter is not None:
                await rate_limiter.acquire()

            # 2. Profiling wrapper
            if profiler is not None:
                async with profiler.profile_ctx(agent_name):
                    if batch_executor is not None:
                        future = batch_executor.submit(fn, *args, **kwargs)
                        return await future
                    else:
                        return await fn(*args, **kwargs)
            else:
                if batch_executor is not None:
                    future = batch_executor.submit(fn, *args, **kwargs)
                    return await future
                else:
                    return await fn(*args, **kwargs)

        optimized.__name__ = f"optimized_{agent_name}"
        optimized.__qualname__ = f"optimized_{agent_name}"
        return optimized

    def stats(self) -> dict:
        """Return combined statistics from all configured components."""
        result: dict = {}

        if self.profiler is not None:
            result["profiler"] = {
                name: profile.to_dict()
                for name, profile in self.profiler.all_profiles().items()
            }

        if self.batch_executor is not None:
            result["batch_executor"] = {
                "batch_size": self.batch_executor.batch_size,
                "flush_interval": self.batch_executor.flush_interval,
                "max_concurrency": self.batch_executor.max_concurrency,
                "pending_count": len(self.batch_executor._pending),
            }

        if self.rate_limiter is not None:
            result["rate_limiter"] = {
                "rate": self.rate_limiter.rate,
                "burst": self.rate_limiter.burst,
                "available_tokens": round(self.rate_limiter.available_tokens, 4),
            }

        return result
