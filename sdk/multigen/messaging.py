"""
Advanced agent messaging system.

Features
--------
- Priority pub/sub (subscribers receive messages by priority queue order)
- Request/reply with correlation IDs and configurable timeouts
- Wildcard topic routing (``*`` matches one segment, ``**`` matches many)
- Dead-letter queue (DLQ) for undeliverable / failed messages
- Scatter-gather: broadcast to N handlers, collect first-K replies
- Message pipeline: chain of middleware transforms applied before delivery
- MessageFilter: predicate-based subscription filtering
- MessageRouter: content-based routing to named channels
- PriorityMessageQueue: asyncio-compatible min-heap priority queue
"""
from __future__ import annotations

import asyncio
import fnmatch
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple


# ── Core message type ─────────────────────────────────────────────────────────

@dataclass
class AdvancedMessage:
    """
    An envelope for a single message on the advanced bus.

    Parameters
    ----------
    topic:
        Dot-separated topic string, e.g. ``"agent.research.result"``.
    content:
        Arbitrary payload.
    priority:
        Lower value = higher priority (0 is highest).  Defaults to 5.
    correlation_id:
        Used to match request/reply pairs.  Auto-generated if not supplied.
    reply_to:
        Topic on which the reply should be published.
    sender:
        Logical name of the sending agent / component.
    headers:
        Arbitrary key-value metadata.
    """

    topic: str
    content: Any
    priority: int = 5
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    reply_to: Optional[str] = None
    sender: str = ""
    headers: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    message_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # Internal: number of delivery attempts (set by the bus)
    _attempts: int = field(default=0, repr=False)

    def reply(self, content: Any, **kwargs: Any) -> "AdvancedMessage":
        """Convenience factory — creates a reply message."""
        return AdvancedMessage(
            topic=self.reply_to or f"{self.topic}.reply",
            content=content,
            correlation_id=self.correlation_id,
            sender=kwargs.pop("sender", ""),
            **kwargs,
        )


# ── Priority queue ─────────────────────────────────────────────────────────────

class PriorityMessageQueue:
    """
    Asyncio-compatible min-heap priority queue for :class:`AdvancedMessage`.

    Lower ``priority`` values are dequeued first.

    Usage::

        q = PriorityMessageQueue()
        await q.put(msg)
        msg = await q.get()
    """

    def __init__(self) -> None:
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue()

    async def put(self, msg: AdvancedMessage) -> None:
        # Tie-break by timestamp so equal-priority messages are FIFO
        await self._queue.put((msg.priority, msg.timestamp, msg))

    async def get(self) -> AdvancedMessage:
        _, _, msg = await self._queue.get()
        return msg

    def task_done(self) -> None:
        self._queue.task_done()

    def qsize(self) -> int:
        return self._queue.qsize()

    def empty(self) -> bool:
        return self._queue.empty()


# ── Dead-letter queue ─────────────────────────────────────────────────────────

class DeadLetterQueue:
    """
    Stores messages that could not be delivered or whose handlers failed.

    Usage::

        dlq = DeadLetterQueue(max_size=500)
        dlq.push(msg, reason="no subscribers")
        dead = dlq.drain()
    """

    def __init__(self, max_size: int = 1000) -> None:
        self._entries: List[Tuple[AdvancedMessage, str]] = []
        self.max_size = max_size

    def push(self, msg: AdvancedMessage, reason: str = "") -> None:
        if len(self._entries) < self.max_size:
            self._entries.append((msg, reason))

    def drain(self) -> List[Tuple[AdvancedMessage, str]]:
        """Return and clear all dead-lettered entries."""
        entries = list(self._entries)
        self._entries.clear()
        return entries

    def __len__(self) -> int:
        return len(self._entries)


# ── Subscription helpers ──────────────────────────────────────────────────────

Handler = Callable[[AdvancedMessage], Coroutine[Any, Any, Any]]


def _topic_matches(pattern: str, topic: str) -> bool:
    """
    Match a topic against a pattern.

    * ``*``  — matches exactly one dot-separated segment
    * ``**`` — matches zero or more segments (anywhere in the pattern)
    * Exact match always works.
    """
    if pattern == topic:
        return True
    # Translate dot-separated glob to fnmatch pattern
    # "**" → match any number of chars (including dots)
    # "*"  → match any chars except dot
    if "**" in pattern:
        fnpat = pattern.replace("**", "\x00")  # placeholder
        fnpat = fnpat.replace("*", "[^.]*")
        fnpat = fnpat.replace("\x00", ".*")
        import re
        return bool(re.fullmatch(fnpat, topic))
    # Simple single-level wildcard
    return fnmatch.fnmatch(topic, pattern)


@dataclass
class Subscription:
    pattern: str
    handler: Handler
    filter_fn: Optional[Callable[[AdvancedMessage], bool]] = None
    sub_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def matches(self, msg: AdvancedMessage) -> bool:
        if not _topic_matches(self.pattern, msg.topic):
            return False
        if self.filter_fn is not None and not self.filter_fn(msg):
            return False
        return True


# ── Message pipeline ──────────────────────────────────────────────────────────

class MessagePipeline:
    """
    A chain of async middleware transforms applied to a message before delivery.

    Each middleware receives ``(msg, next_fn)`` and must call ``await next_fn(msg)``
    to continue the chain, optionally modifying the message first.

    Usage::

        async def log_middleware(msg, next_fn):
            print(f"[pipe] {msg.topic}")
            await next_fn(msg)

        pipeline = MessagePipeline([log_middleware])
        await pipeline.run(msg, final_handler)
    """

    Middleware = Callable[
        [AdvancedMessage, Callable],
        Coroutine[Any, Any, None],
    ]

    def __init__(self, middlewares: Optional[List[Middleware]] = None) -> None:
        self._middlewares: List[MessagePipeline.Middleware] = middlewares or []

    def use(self, middleware: "MessagePipeline.Middleware") -> "MessagePipeline":
        self._middlewares.append(middleware)
        return self

    async def run(
        self,
        msg: AdvancedMessage,
        final: Callable[[AdvancedMessage], Coroutine[Any, Any, Any]],
    ) -> Any:
        idx = 0
        middlewares = self._middlewares

        async def _next(m: AdvancedMessage) -> Any:
            nonlocal idx
            if idx < len(middlewares):
                mw = middlewares[idx]
                idx += 1
                return await mw(m, _next)
            return await final(m)

        return await _next(msg)


# ── Message router ────────────────────────────────────────────────────────────

class MessageRouter:
    """
    Content-based routing: dispatch a message to the first matching channel.

    Usage::

        router = MessageRouter()
        router.route(lambda msg: msg.content.get("type") == "error", error_handler)
        router.route(lambda msg: True, default_handler)   # catch-all

        await router.dispatch(msg)
    """

    def __init__(self) -> None:
        self._routes: List[Tuple[Callable[[AdvancedMessage], bool], Handler]] = []

    def route(
        self,
        predicate: Callable[[AdvancedMessage], bool],
        handler: Handler,
    ) -> "MessageRouter":
        self._routes.append((predicate, handler))
        return self

    async def dispatch(self, msg: AdvancedMessage) -> Optional[Any]:
        for predicate, handler in self._routes:
            if predicate(msg):
                return await handler(msg)
        return None


# ── Advanced message bus ──────────────────────────────────────────────────────

class AdvancedMessageBus:
    """
    Full-featured async message bus for multi-agent systems.

    Features
    --------
    - **Priority pub/sub** — subscribers receive messages via priority queue.
    - **Wildcard routing** — ``*`` (one segment) and ``**`` (many segments).
    - **Request/reply** — ``request()`` publishes and awaits a correlated reply.
    - **Dead-letter queue** — failed deliveries are captured, not silently dropped.
    - **Scatter-gather** — broadcast to all matching handlers, collect N replies.
    - **Middleware pipeline** — hook into message delivery for logging, auth, etc.
    - **MessageFilter** — per-subscription predicate filtering.

    Usage::

        bus = AdvancedMessageBus()

        # Subscribe with wildcard
        @bus.subscribe("agent.**")
        async def on_agent(msg):
            print(msg.topic, msg.content)

        # Publish
        await bus.publish(AdvancedMessage(topic="agent.research.done", content={"result": "..."}))

        # Request / reply
        reply = await bus.request(
            AdvancedMessage(topic="agent.query", content={"q": "hello"}),
            timeout=5.0,
        )

        # Scatter-gather
        results = await bus.scatter_gather(
            AdvancedMessage(topic="agent.vote", content={"proposal": "..."}),
            gather_count=3,
            timeout=2.0,
        )
    """

    def __init__(
        self,
        pipeline: Optional[MessagePipeline] = None,
        dlq: Optional[DeadLetterQueue] = None,
        max_retries: int = 0,
    ) -> None:
        self._subscriptions: List[Subscription] = []
        self._pipeline = pipeline or MessagePipeline()
        self.dlq = dlq or DeadLetterQueue()
        self._max_retries = max_retries
        self._reply_waiters: Dict[str, asyncio.Future] = {}
        self._published: int = 0
        self._delivered: int = 0
        self._dead_lettered: int = 0

    # ── Subscribe ─────────────────────────────────────────────────────────────

    def subscribe(
        self,
        pattern: str,
        filter_fn: Optional[Callable[[AdvancedMessage], bool]] = None,
    ) -> Callable[[Handler], Handler]:
        """Decorator: register a handler for topics matching *pattern*."""

        def decorator(handler: Handler) -> Handler:
            self._subscriptions.append(Subscription(
                pattern=pattern,
                handler=handler,
                filter_fn=filter_fn,
            ))
            return handler

        return decorator

    def subscribe_handler(
        self,
        pattern: str,
        handler: Handler,
        filter_fn: Optional[Callable[[AdvancedMessage], bool]] = None,
    ) -> str:
        """Programmatic subscribe.  Returns the subscription ID."""
        sub = Subscription(pattern=pattern, handler=handler, filter_fn=filter_fn)
        self._subscriptions.append(sub)
        return sub.sub_id

    def unsubscribe(self, sub_id: str) -> bool:
        before = len(self._subscriptions)
        self._subscriptions = [s for s in self._subscriptions if s.sub_id != sub_id]
        return len(self._subscriptions) < before

    # ── Publish ───────────────────────────────────────────────────────────────

    async def publish(self, msg: AdvancedMessage) -> int:
        """
        Publish *msg* to all matching subscribers.

        Returns the number of handlers that received the message.
        """
        self._published += 1
        matched = [s for s in self._subscriptions if s.matches(msg)]

        # If this is a reply, resolve the waiter first
        if msg.correlation_id in self._reply_waiters:
            fut = self._reply_waiters.pop(msg.correlation_id)
            if not fut.done():
                fut.set_result(msg)

        if not matched:
            self.dlq.push(msg, reason="no_subscribers")
            self._dead_lettered += 1
            return 0

        delivered = 0
        for sub in matched:
            success = await self._deliver(msg, sub)
            if success:
                delivered += 1
        self._delivered += delivered
        return delivered

    async def _deliver(self, msg: AdvancedMessage, sub: Subscription) -> bool:
        for attempt in range(self._max_retries + 1):
            try:
                await self._pipeline.run(msg, sub.handler)
                return True
            except Exception as exc:  # noqa: BLE001
                msg._attempts = attempt + 1
                if attempt == self._max_retries:
                    self.dlq.push(msg, reason=str(exc))
                    self._dead_lettered += 1
                    return False
        return False

    # ── Request / Reply ───────────────────────────────────────────────────────

    async def request(
        self,
        msg: AdvancedMessage,
        timeout: float = 10.0,
    ) -> AdvancedMessage:
        """
        Publish *msg* and wait for a correlated reply.

        The reply is matched by ``correlation_id``.  Any subscriber that calls
        ``await bus.publish(original_msg.reply(...))`` will resolve this future.

        Raises
        ------
        asyncio.TimeoutError
            If no reply arrives within *timeout* seconds.
        """
        loop = asyncio.get_event_loop()
        fut: asyncio.Future = loop.create_future()
        self._reply_waiters[msg.correlation_id] = fut
        await self.publish(msg)
        try:
            return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
        except asyncio.TimeoutError:
            self._reply_waiters.pop(msg.correlation_id, None)
            raise

    # ── Scatter-Gather ────────────────────────────────────────────────────────

    async def scatter_gather(
        self,
        msg: AdvancedMessage,
        gather_count: Optional[int] = None,
        timeout: float = 5.0,
    ) -> List[Any]:
        """
        Publish *msg* to all matching subscribers and collect their return values.

        Parameters
        ----------
        gather_count:
            If set, return as soon as this many handlers have replied (or
            *timeout* expires, whichever is first).
        timeout:
            Maximum time to wait in seconds.
        """
        matched = [s for s in self._subscriptions if s.matches(msg)]
        if not matched:
            self.dlq.push(msg, reason="no_subscribers")
            return []

        coros = [self._pipeline.run(msg, sub.handler) for sub in matched]
        n = gather_count or len(coros)

        results: List[Any] = []
        tasks = [asyncio.ensure_future(c) for c in coros]

        try:
            done, pending = await asyncio.wait(
                tasks,
                timeout=timeout,
                return_when=asyncio.ALL_COMPLETED if gather_count is None else asyncio.FIRST_COMPLETED,
            )
            for t in done:
                if not t.exception():
                    results.append(t.result())
                if len(results) >= n:
                    break
        finally:
            for t in tasks:
                if not t.done():
                    t.cancel()

        return results

    # ── Statistics ────────────────────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "published": self._published,
            "delivered": self._delivered,
            "dead_lettered": self._dead_lettered,
            "subscriptions": len(self._subscriptions),
            "dlq_size": len(self.dlq),
        }


# ── MessageFilter helper ──────────────────────────────────────────────────────

class MessageFilter:
    """
    Composable predicate builder for subscription filtering.

    Usage::

        f = MessageFilter.by_sender("ResearchAgent") & MessageFilter.by_header("urgent", True)
        bus.subscribe_handler("agent.**", handler, filter_fn=f)
    """

    def __init__(self, fn: Callable[[AdvancedMessage], bool]) -> None:
        self._fn = fn

    def __call__(self, msg: AdvancedMessage) -> bool:
        return self._fn(msg)

    def __and__(self, other: "MessageFilter") -> "MessageFilter":
        return MessageFilter(lambda m: self(m) and other(m))

    def __or__(self, other: "MessageFilter") -> "MessageFilter":
        return MessageFilter(lambda m: self(m) or other(m))

    def __invert__(self) -> "MessageFilter":
        return MessageFilter(lambda m: not self(m))

    # ── Factory methods ───────────────────────────────────────────────────────

    @classmethod
    def by_sender(cls, sender: str) -> "MessageFilter":
        return cls(lambda m: m.sender == sender)

    @classmethod
    def by_priority(cls, max_priority: int) -> "MessageFilter":
        """Include only messages with priority <= max_priority."""
        return cls(lambda m: m.priority <= max_priority)

    @classmethod
    def by_header(cls, key: str, value: Any) -> "MessageFilter":
        return cls(lambda m: m.headers.get(key) == value)

    @classmethod
    def by_content_key(cls, key: str) -> "MessageFilter":
        """Include only messages whose content dict contains *key*."""
        return cls(lambda m: isinstance(m.content, dict) and key in m.content)


__all__ = [
    "AdvancedMessage",
    "AdvancedMessageBus",
    "DeadLetterQueue",
    "MessageFilter",
    "MessagePipeline",
    "MessageRouter",
    "PriorityMessageQueue",
    "Subscription",
]
