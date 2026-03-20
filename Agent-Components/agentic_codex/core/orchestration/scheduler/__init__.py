"""Scheduling utilities for local and async execution."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Awaitable, Callable, Iterable, Sequence


def run_local(funcs: Iterable[Callable[[], None]], max_workers: int = 4) -> None:
    """Execute callables using a thread pool."""

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        list(pool.map(lambda fn: fn(), funcs))


def run_serial(funcs: Iterable[Callable[[], None]]) -> None:
    """Execute a sequence of callables in order."""

    for fn in funcs:
        fn()


async def run_async(tasks: Sequence[Callable[[], Awaitable[None]]], *, concurrency: int | None = None) -> None:
    """Execute awaitable-producing callables with optional concurrency limit."""

    semaphore = asyncio.Semaphore(concurrency or len(tasks))

    async def _runner(fn: Callable[[], Awaitable[None]]) -> None:
        async with semaphore:
            await fn()

    await asyncio.gather(*(_runner(task) for task in tasks))


__all__ = ["run_local", "run_serial", "run_async"]
