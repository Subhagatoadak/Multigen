"""
multigen.bus
============
In-memory event bus with full communication protocol support.
No Kafka/Redis required — pure asyncio.

Protocols implemented:
- Publish / Subscribe  (fan-out to all subscribers of a topic)
- Request / Reply      (correlated async request-response)
- Broadcast            (send to ALL known agents)
- Push / Pull          (work-queue, round-robin consumers)
- Pipeline             (ordered message fan-through)
- Dead-letter queue    (capture unhandled / failed messages)
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Deque, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# ─── Message ─────────────────────────────────────────────────────────────────

@dataclass
class Message:
    """Universal message envelope for all Multigen communication."""
    id:          str                  = field(default_factory=lambda: str(uuid.uuid4())[:12])
    topic:       str                  = ""
    sender:      str                  = "system"
    recipient:   Optional[str]        = None   # None = broadcast
    content:     Any                  = None
    reply_to:    Optional[str]        = None   # topic for reply routing
    correlation_id: Optional[str]     = None   # request/reply correlation
    timestamp:   float                = field(default_factory=time.time)
    headers:     Dict[str, str]       = field(default_factory=dict)
    ttl:         Optional[float]      = None   # seconds, None = no expiry
    priority:    int                  = 0      # higher = more urgent

    def is_expired(self) -> bool:
        return self.ttl is not None and (time.time() - self.timestamp) > self.ttl

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id":           self.id,
            "topic":        self.topic,
            "sender":       self.sender,
            "recipient":    self.recipient,
            "content":      self.content,
            "reply_to":     self.reply_to,
            "correlation_id": self.correlation_id,
            "timestamp":    self.timestamp,
            "headers":      self.headers,
            "priority":     self.priority,
        }


Handler = Callable[[Message], Awaitable[None]]


# ─── InMemoryBus ─────────────────────────────────────────────────────────────

class InMemoryBus:
    """Async, in-memory message bus supporting multiple communication patterns.

    Usage::

        bus = InMemoryBus()

        # Pub/Sub
        @bus.subscribe("results")
        async def on_result(msg: Message) -> None:
            print(msg.content)

        await bus.publish(Message(topic="results", content={"score": 0.9}))

        # Request/Reply
        @bus.on_request("classify")
        async def classify(msg: Message) -> dict:
            return {"label": "positive"}

        reply = await bus.request("classify", content={"text": "great!"})

        # Broadcast
        await bus.broadcast(Message(content="shutdown"))
    """

    def __init__(self, *, dead_letter_limit: int = 1000) -> None:
        # pub/sub: topic → list of handlers
        self._subscribers: Dict[str, List[Handler]] = defaultdict(list)
        # direct: agent_id → queue
        self._queues: Dict[str, asyncio.Queue[Message]] = {}
        # request handlers: topic → coroutine
        self._request_handlers: Dict[str, Callable[[Message], Awaitable[Any]]] = {}
        # pending reply futures: correlation_id → Future
        self._pending: Dict[str, asyncio.Future[Message]] = {}
        # push/pull worker pools: topic → deque of consumers
        self._workers: Dict[str, Deque[Handler]] = defaultdict(deque)
        # DLQ
        self._dlq: Deque[Message] = deque(maxlen=dead_letter_limit)
        # all registered agent ids
        self._agents: Set[str] = set()
        # stats
        self._published = 0
        self._delivered = 0
        self._dropped = 0
        self._lock = asyncio.Lock()

    # ── Agent registration ──────────────────────────────────────────────────

    def register_agent(self, agent_id: str) -> asyncio.Queue[Message]:
        """Register an agent and return its dedicated inbox queue."""
        self._agents.add(agent_id)
        if agent_id not in self._queues:
            self._queues[agent_id] = asyncio.Queue()
        return self._queues[agent_id]

    # ── Publish / Subscribe ─────────────────────────────────────────────────

    def subscribe(self, topic: str) -> Callable[[Handler], Handler]:
        """Decorator: subscribe a handler to a topic."""
        def decorator(fn: Handler) -> Handler:
            self._subscribers[topic].append(fn)
            return fn
        return decorator

    async def publish(self, msg: Message) -> int:
        """Publish to all subscribers of msg.topic. Returns delivery count."""
        if msg.is_expired():
            self._dlq.append(msg)
            self._dropped += 1
            return 0

        handlers = self._subscribers.get(msg.topic, [])
        self._published += 1
        if not handlers:
            self._dlq.append(msg)
            logger.debug("No subscribers for topic=%s — sent to DLQ", msg.topic)
            return 0

        tasks = [asyncio.create_task(h(msg)) for h in handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        delivered = sum(1 for r in results if not isinstance(r, BaseException))
        self._delivered += delivered
        return delivered

    # ── Request / Reply ─────────────────────────────────────────────────────

    def on_request(self, topic: str) -> Callable:
        """Register a request handler that returns a reply payload."""
        def decorator(fn: Callable[[Message], Awaitable[Any]]):
            self._request_handlers[topic] = fn
            return fn
        return decorator

    async def request(
        self,
        topic: str,
        content: Any = None,
        *,
        sender: str = "client",
        timeout: float = 30.0,
        headers: Optional[Dict[str, str]] = None,
    ) -> Message:
        """Send a request and await a correlated reply."""
        corr_id = str(uuid.uuid4())
        reply_topic = f"__reply__.{corr_id}"

        loop = asyncio.get_event_loop()
        fut: asyncio.Future[Message] = loop.create_future()
        self._pending[corr_id] = fut

        # Register ephemeral reply subscriber
        async def _reply_handler(msg: Message) -> None:
            if not fut.done():
                fut.set_result(msg)
        self._subscribers[reply_topic].append(_reply_handler)

        req = Message(
            topic=topic,
            sender=sender,
            content=content,
            reply_to=reply_topic,
            correlation_id=corr_id,
            headers=headers or {},
        )

        handler = self._request_handlers.get(topic)
        if handler:
            asyncio.create_task(self._dispatch_request(handler, req))
        else:
            await self.publish(req)

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise TimeoutError(f"No reply received for topic={topic!r} within {timeout}s")
        finally:
            self._subscribers[reply_topic].clear()
            self._pending.pop(corr_id, None)

    async def reply(self, original: Message, content: Any) -> None:
        """Send a reply to an incoming request message."""
        if not original.reply_to:
            return
        msg = Message(
            topic=original.reply_to,
            sender="server",
            content=content,
            correlation_id=original.correlation_id,
        )
        await self.publish(msg)

    async def _dispatch_request(self, handler: Callable, req: Message) -> None:
        try:
            result = await handler(req)
            await self.reply(req, result)
        except Exception as exc:
            await self.reply(req, {"error": str(exc)})

    # ── Broadcast ───────────────────────────────────────────────────────────

    async def broadcast(self, msg: Message) -> int:
        """Deliver to ALL registered agent queues."""
        count = 0
        for aid, q in self._queues.items():
            await q.put(Message(**{**msg.__dict__, "recipient": aid, "id": str(uuid.uuid4())[:12]}))
            count += 1
        self._delivered += count
        return count

    # ── Push / Pull (work queue) ─────────────────────────────────────────────

    def add_worker(self, topic: str, worker: Handler) -> None:
        """Register a pull consumer for round-robin work distribution."""
        self._workers[topic].append(worker)

    async def push(self, topic: str, content: Any, sender: str = "system") -> None:
        """Push a task to the next available round-robin worker."""
        pool = self._workers.get(topic)
        if not pool:
            self._dlq.append(Message(topic=topic, sender=sender, content=content))
            return
        worker = pool[0]
        pool.rotate(-1)  # round-robin
        msg = Message(topic=topic, sender=sender, content=content)
        asyncio.create_task(worker(msg))

    # ── Direct send ─────────────────────────────────────────────────────────

    async def send(self, agent_id: str, content: Any, sender: str = "system", **kwargs: Any) -> None:
        """Put a message directly into a registered agent's queue."""
        q = self._queues.get(agent_id)
        if q is None:
            q = self._queues[agent_id] = asyncio.Queue()
            self._agents.add(agent_id)
        msg = Message(recipient=agent_id, sender=sender, content=content, **kwargs)
        await q.put(msg)
        self._published += 1

    async def receive(self, agent_id: str, timeout: Optional[float] = None) -> Optional[Message]:
        """Block until a message arrives for agent_id (or timeout)."""
        q = self._queues.get(agent_id)
        if q is None:
            return None
        try:
            return await asyncio.wait_for(q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ── Dead-letter queue ────────────────────────────────────────────────────

    def dlq_snapshot(self) -> List[Dict[str, Any]]:
        return [m.to_dict() for m in self._dlq]

    def dlq_flush(self) -> List[Message]:
        msgs = list(self._dlq)
        self._dlq.clear()
        return msgs

    # ── Stats ────────────────────────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        return {
            "agents":       len(self._agents),
            "topics":       len(self._subscribers),
            "published":    self._published,
            "delivered":    self._delivered,
            "dropped":      self._dropped,
            "dlq_size":     len(self._dlq),
            "pending_replies": len(self._pending),
        }

    def __repr__(self) -> str:
        return f"InMemoryBus(agents={len(self._agents)}, topics={len(self._subscribers)})"


# ─── Shared default bus instance ─────────────────────────────────────────────

_default_bus: Optional[InMemoryBus] = None


def get_default_bus() -> InMemoryBus:
    global _default_bus
    if _default_bus is None:
        _default_bus = InMemoryBus()
    return _default_bus
