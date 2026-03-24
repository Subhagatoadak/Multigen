"""
Token streaming, parallel branch aggregation, and partial result rendering.

Problems solved
---------------
- Users wait 10–30 s for a full LLM response with no intermediate feedback
- No streaming aggregation across parallel branches
- No partial result rendering (progress events, live display)

Classes
-------
- ``StreamToken``       — a single streaming chunk (text, role, metadata)
- ``StreamBuffer``      — thread-safe accumulator for a stream
- ``StreamingAgent``    — wraps any async-generator agent for streaming output
- ``ParallelStreamer``  — fans out to N agents and merges token streams
- ``PartialResult``     — incremental result event emitted during execution
- ``PartialResultBus``  — pub/sub bus for partial results
- ``StreamAggregator``  — collects N parallel streams into one ordered stream

Usage::

    from multigen.streaming import (
        StreamToken, StreamingAgent, ParallelStreamer,
        PartialResultBus, StreamAggregator,
    )

    # Wrap a streaming LLM
    agent = StreamingAgent("writer", stream_fn=my_llm_stream)
    async for token in agent.stream({"prompt": "Tell me a story"}):
        print(token.text, end="", flush=True)

    # Merge N parallel streams
    streamer = ParallelStreamer([agent_a, agent_b])
    async for token in streamer.stream(ctx):
        print(f"[{token.source}] {token.text}")

    # Partial result bus
    bus = PartialResultBus()
    bus.subscribe("my_run", lambda pr: print(pr.content))
    bus.emit("my_run", PartialResult(run_id="my_run", step="step1", content="..."))
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, AsyncIterator, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── StreamToken ────────────────────────────────────────────────────────────────

@dataclass
class StreamToken:
    """A single streaming token / chunk."""
    text: str
    source: str = ""           # agent name / branch label
    is_final: bool = False     # True for the last token of a stream
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.monotonic)

    def __str__(self) -> str:
        return self.text


# ── StreamBuffer ───────────────────────────────────────────────────────────────

class StreamBuffer:
    """
    Thread-safe buffer that accumulates tokens from an async stream.

    Usage::

        buf = StreamBuffer()
        async for token in source_stream:
            buf.push(token)
        buf.close()
        full_text = buf.text()
    """

    def __init__(self) -> None:
        self._tokens: List[StreamToken] = []
        self._closed = False

    def push(self, token: StreamToken) -> None:
        self._tokens.append(token)

    def close(self) -> None:
        self._closed = True

    def text(self) -> str:
        return "".join(t.text for t in self._tokens)

    def tokens(self) -> List[StreamToken]:
        return list(self._tokens)

    @property
    def is_closed(self) -> bool:
        return self._closed

    def __len__(self) -> int:
        return len(self._tokens)


# ── StreamingAgent ─────────────────────────────────────────────────────────────

class StreamingAgent:
    """
    Wraps an async-generator function into a named streaming agent.

    The *stream_fn* must be an ``async def`` that ``yield``s either:
    - ``str``          — text fragment
    - ``StreamToken``  — fully formed token
    - any object       — converted via ``str()``

    Usage::

        async def llm_stream(ctx):
            for word in "hello world foo bar".split():
                await asyncio.sleep(0.01)
                yield word + " "

        agent = StreamingAgent("llm", stream_fn=llm_stream)
        async for token in agent.stream({"prompt": "hi"}):
            print(token.text, end="")
        full = await agent.run(ctx)   # blocks, returns full text
    """

    def __init__(
        self,
        name: str,
        stream_fn: Callable,
        on_token: Optional[Callable[[StreamToken], Any]] = None,
    ) -> None:
        self.name = name
        self._stream_fn = stream_fn
        self._on_token = on_token

    async def stream(
        self,
        ctx: Dict[str, Any],
    ) -> AsyncIterator[StreamToken]:
        """Yield ``StreamToken`` objects as they arrive."""
        gen = self._stream_fn(ctx)
        # Support both regular async generators and plain coroutines that
        # return iterables
        if not hasattr(gen, "__aiter__"):
            # Coroutine — await it to get a list/string, then stream that
            result = await gen
            items = result if isinstance(result, (list, tuple)) else [result]
            for i, item in enumerate(items):
                is_final = i == len(items) - 1
                text = item if isinstance(item, str) else str(item)
                token = StreamToken(text=text, source=self.name, is_final=is_final)
                if self._on_token:
                    cb = self._on_token(token)
                    if asyncio.iscoroutine(cb):
                        await cb
                yield token
            return

        last_token: Optional[StreamToken] = None
        async for chunk in gen:
            if last_token is not None:
                if self._on_token:
                    cb = self._on_token(last_token)
                    if asyncio.iscoroutine(cb):
                        await cb
                yield last_token
            if isinstance(chunk, StreamToken):
                last_token = StreamToken(
                    text=chunk.text,
                    source=chunk.source or self.name,
                    is_final=chunk.is_final,
                    metadata=chunk.metadata,
                )
            else:
                last_token = StreamToken(text=str(chunk), source=self.name)

        # Emit final token
        if last_token is not None:
            last_token.is_final = True
            if self._on_token:
                cb = self._on_token(last_token)
                if asyncio.iscoroutine(cb):
                    await cb
            yield last_token

    async def run(self, ctx: Dict[str, Any]) -> str:
        """Consume the entire stream and return the full text."""
        buf = StreamBuffer()
        async for token in self.stream(ctx):
            buf.push(token)
        buf.close()
        return buf.text()

    async def __call__(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        text = await self.run(ctx)
        return {**ctx, self.name: {"response": text}}


# ── ParallelStreamer ───────────────────────────────────────────────────────────

class ParallelStreamer:
    """
    Fans out to N ``StreamingAgent`` instances and interleaves their token
    streams into a single ordered stream (tokens arrive as soon as any branch
    produces them).

    Usage::

        ps = ParallelStreamer([agent_finance, agent_tech, agent_policy])
        async for token in ps.stream({"topic": "AI regulation"}):
            print(f"[{token.source}] {token.text}", end="")
    """

    def __init__(self, agents: List[StreamingAgent]) -> None:
        self._agents = agents

    async def stream(
        self,
        ctx: Dict[str, Any],
    ) -> AsyncIterator[StreamToken]:
        """Interleaved multi-branch token stream."""
        queue: asyncio.Queue = asyncio.Queue()
        active = len(self._agents)

        async def _drain(agent: StreamingAgent) -> None:
            nonlocal active
            try:
                async for token in agent.stream(ctx):
                    await queue.put(token)
            finally:
                active -= 1
                await queue.put(None)  # sentinel

        tasks = [asyncio.create_task(_drain(a)) for a in self._agents]
        done_count = 0
        try:
            while done_count < len(self._agents):
                item = await queue.get()
                if item is None:
                    done_count += 1
                else:
                    yield item
        finally:
            for t in tasks:
                t.cancel()

    async def run(self, ctx: Dict[str, Any]) -> Dict[str, str]:
        """Run all agents and return ``{agent_name: full_text}``."""
        results: Dict[str, List[str]] = {a.name: [] for a in self._agents}
        async for token in self.stream(ctx):
            results[token.source].append(token.text)
        return {name: "".join(parts) for name, parts in results.items()}


# ── StreamAggregator ──────────────────────────────────────────────────────────

class StreamAggregator:
    """
    Collects N parallel async-generator streams into one merged stream.

    Unlike ``ParallelStreamer`` (which works with ``StreamingAgent``), this
    accepts raw async generators directly, making it useful as a lower-level
    primitive.

    Usage::

        agg = StreamAggregator()
        agg.add("branch_a", stream_a(ctx))
        agg.add("branch_b", stream_b(ctx))
        async for token in agg.merge():
            print(token)
    """

    def __init__(self) -> None:
        self._streams: Dict[str, AsyncGenerator] = {}

    def add(self, name: str, stream: AsyncGenerator) -> "StreamAggregator":
        self._streams[name] = stream
        return self

    async def merge(self) -> AsyncIterator[StreamToken]:
        """Yield tokens from all streams as they arrive."""
        queue: asyncio.Queue = asyncio.Queue()
        n = len(self._streams)

        async def _drain(name: str, gen: AsyncGenerator) -> None:
            try:
                async for chunk in gen:
                    if isinstance(chunk, StreamToken):
                        token = StreamToken(
                            text=chunk.text,
                            source=name,
                            is_final=chunk.is_final,
                            metadata=chunk.metadata,
                        )
                    else:
                        token = StreamToken(text=str(chunk), source=name)
                    await queue.put(token)
            finally:
                await queue.put(None)

        tasks = [
            asyncio.create_task(_drain(name, gen))
            for name, gen in self._streams.items()
        ]
        done = 0
        try:
            while done < n:
                item = await queue.get()
                if item is None:
                    done += 1
                else:
                    yield item
        finally:
            for t in tasks:
                t.cancel()


# ── PartialResult / PartialResultBus ──────────────────────────────────────────

@dataclass
class PartialResult:
    """An incremental result event emitted during workflow execution."""
    run_id: str
    step: str
    content: Any
    progress: float = 0.0    # 0.0–1.0 estimate
    is_final: bool = False
    timestamp: float = field(default_factory=time.monotonic)


class PartialResultBus:
    """
    Pub/sub bus for partial result events during workflow execution.

    Subscribers register with a *run_id* pattern (exact match or ``"*"`` for
    all runs).  When an event is emitted, all matching subscribers are called.

    Usage::

        bus = PartialResultBus()
        bus.subscribe("run-42", lambda pr: update_ui(pr.content))
        bus.subscribe("*",      lambda pr: logger.info(pr))

        bus.emit("run-42", PartialResult(run_id="run-42", step="step1", content="hello"))
    """

    def __init__(self) -> None:
        self._subscribers: Dict[str, List[Callable]] = {}

    def subscribe(self, run_id: str, fn: Callable) -> None:
        """Register *fn* to receive events for *run_id* (use ``\"*\"`` for all)."""
        self._subscribers.setdefault(run_id, []).append(fn)

    def unsubscribe(self, run_id: str, fn: Optional[Callable] = None) -> None:
        """Remove subscriber(s) for *run_id*."""
        if fn is None:
            self._subscribers.pop(run_id, None)
        else:
            subs = self._subscribers.get(run_id, [])
            self._subscribers[run_id] = [s for s in subs if s is not fn]

    def emit(self, run_id: str, event: PartialResult) -> None:
        """Synchronously call all matching subscribers."""
        for pattern, fns in self._subscribers.items():
            if pattern == "*" or pattern == run_id:
                for fn in fns:
                    try:
                        fn(event)
                    except Exception as exc:
                        logger.warning("PartialResultBus: subscriber error: %s", exc)

    async def emit_async(self, run_id: str, event: PartialResult) -> None:
        """Asynchronously call all matching subscribers (awaits coroutines)."""
        for pattern, fns in self._subscribers.items():
            if pattern == "*" or pattern == run_id:
                for fn in fns:
                    try:
                        result = fn(event)
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:
                        logger.warning("PartialResultBus: async subscriber error: %s", exc)

    def run_ids(self) -> List[str]:
        return list(self._subscribers.keys())


__all__ = [
    "StreamToken",
    "StreamBuffer",
    "StreamingAgent",
    "ParallelStreamer",
    "StreamAggregator",
    "PartialResult",
    "PartialResultBus",
]
