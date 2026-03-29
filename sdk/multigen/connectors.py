"""
External world integration for Multigen — webhook triggers, outbound connectors,
event-driven branching, and document ingestion.

Problems solved
---------------
- No webhook inbound triggers (can't start workflow from CRM/ERP event)
- No outbound connector framework (no structured way to push results out)
- No event-driven graph branching (can't react to external event mid-workflow)
- No native document ingestion pipeline (document → chunks → agent context)

Classes
-------
- ``WebhookEvent``          — inbound event from an external system
- ``WebhookRouter``         — routes inbound webhook events to handler functions
- ``OutboundConnector``     — base class for pushing results to external systems
- ``HttpConnector``         — POST results to an HTTP endpoint
- ``ConnectorRegistry``     — named registry of outbound connectors
- ``EventBranch``           — evaluates a condition and fires an event mid-workflow
- ``EventDrivenBranchManager`` — manages multiple event branches
- ``DocumentChunk``         — a chunk of an ingested document
- ``DocumentIngester``      — splits documents into chunks for agent context
- ``IngestionPipeline``     — end-to-end document → context pipeline

Usage::

    from multigen.connectors import WebhookRouter, ConnectorRegistry, IngestionPipeline

    # Inbound webhook
    router = WebhookRouter()

    @router.on("crm.deal.closed")
    async def on_deal_closed(event):
        await run_pipeline({"deal": event.payload})

    event = WebhookEvent(source="crm", event_type="crm.deal.closed",
                          payload={"deal_id": "D-001", "value": 50000})
    await router.dispatch(event)

    # Document ingestion
    pipeline = IngestionPipeline(chunk_size=500, overlap=50)
    chunks = pipeline.ingest("Long document text goes here...", source="report.pdf")
"""
from __future__ import annotations

import asyncio
import re
import time
import urllib.request
import urllib.parse
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ── WebhookEvent ──────────────────────────────────────────────────────────────

@dataclass
class WebhookEvent:
    """An inbound event from an external system."""
    source: str
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    headers: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: str(time.time_ns()))

    def matches(self, pattern: str) -> bool:
        """Match event_type against a glob-style pattern (``*`` wildcard)."""
        regex = re.escape(pattern).replace(r"\*", ".*")
        return bool(re.fullmatch(regex, self.event_type))


# ── WebhookRouter ─────────────────────────────────────────────────────────────

class WebhookRouter:
    """
    Routes inbound webhook events to registered async handler functions.

    Handlers are registered with an event type pattern (``*`` wildcards
    supported).  Multiple handlers can match the same event.

    Usage::

        router = WebhookRouter()

        @router.on("crm.*")
        async def handle_crm(event):
            print("CRM event:", event.event_type)

        await router.dispatch(WebhookEvent(source="crm", event_type="crm.deal.closed",
                                           payload={"id": "D-001"}))
    """

    def __init__(self) -> None:
        self._handlers: List[tuple] = []  # (pattern, fn)
        self._history: List[WebhookEvent] = []

    def on(self, event_pattern: str) -> Callable:
        """Decorator to register a handler for *event_pattern*."""
        def decorator(fn: Callable) -> Callable:
            self._handlers.append((event_pattern, fn))
            return fn
        return decorator

    def register(self, event_pattern: str, handler: Callable) -> None:
        self._handlers.append((event_pattern, handler))

    async def dispatch(self, event: WebhookEvent) -> List[Any]:
        """Dispatch *event* to all matching handlers, return their results."""
        self._history.append(event)
        matched = [fn for pat, fn in self._handlers if event.matches(pat)]
        results = await asyncio.gather(*[
            fn(event) if asyncio.iscoroutinefunction(fn) else asyncio.coroutine(fn)(event)
            for fn in matched
        ], return_exceptions=True)
        return list(results)

    def history(self) -> List[WebhookEvent]:
        return list(self._history)


# ── Outbound Connectors ───────────────────────────────────────────────────────

@dataclass
class ConnectorResult:
    connector_name: str
    success: bool
    response: Any = None
    error: Optional[str] = None
    latency_ms: float = 0.0


class OutboundConnector:
    """
    Base class for outbound connectors.  Subclass and implement ``_send()``.
    """
    name: str = "base"

    async def send(self, payload: Any, **kwargs: Any) -> ConnectorResult:
        start = time.monotonic()
        try:
            response = await self._send(payload, **kwargs)
            return ConnectorResult(
                connector_name=self.name,
                success=True,
                response=response,
                latency_ms=(time.monotonic() - start) * 1000,
            )
        except Exception as exc:
            return ConnectorResult(
                connector_name=self.name,
                success=False,
                error=str(exc),
                latency_ms=(time.monotonic() - start) * 1000,
            )

    async def _send(self, payload: Any, **kwargs: Any) -> Any:
        raise NotImplementedError


class HttpConnector(OutboundConnector):
    """
    Sends JSON payload to an HTTP endpoint via POST.

    Uses the stdlib ``urllib`` — no external deps.

    Usage::

        connector = HttpConnector("slack-webhook", url="https://hooks.slack.com/...")
        result = await connector.send({"text": "Workflow completed!"})
    """

    def __init__(self, name: str, url: str, headers: Optional[Dict[str, str]] = None, timeout_s: float = 10.0) -> None:
        self.name = name
        self._url = url
        self._headers = headers or {"Content-Type": "application/json"}
        self._timeout = timeout_s

    async def _send(self, payload: Any, **kwargs: Any) -> Any:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            self._url,
            data=data,
            headers=self._headers,
            method="POST",
        )
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: urllib.request.urlopen(req, timeout=self._timeout).read().decode(),
        )


class LoggingConnector(OutboundConnector):
    """
    Captures outbound payloads in memory for testing / debugging.

    Usage::

        conn = LoggingConnector("test-log")
        await conn.send({"result": "done"})
        print(conn.payloads())
    """

    def __init__(self, name: str = "log") -> None:
        self.name = name
        self._payloads: List[Any] = []

    async def _send(self, payload: Any, **kwargs: Any) -> Any:
        self._payloads.append({"payload": payload, "ts": time.time()})
        return {"logged": True}

    def payloads(self) -> List[Any]:
        return list(self._payloads)


class ConnectorRegistry:
    """
    Named registry of outbound connectors.

    Usage::

        reg = ConnectorRegistry()
        reg.register(HttpConnector("slack", url="..."))
        reg.register(LoggingConnector("debug"))

        result = await reg.send("slack", payload={"text": "Done"})
        await reg.broadcast(payload, exclude=["debug"])
    """

    def __init__(self) -> None:
        self._connectors: Dict[str, OutboundConnector] = {}

    def register(self, connector: OutboundConnector) -> None:
        self._connectors[connector.name] = connector

    async def send(self, name: str, payload: Any, **kwargs: Any) -> ConnectorResult:
        conn = self._connectors.get(name)
        if not conn:
            raise KeyError(f"Connector {name!r} not registered")
        return await conn.send(payload, **kwargs)

    async def broadcast(
        self,
        payload: Any,
        exclude: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> List[ConnectorResult]:
        exclude = exclude or []
        results = await asyncio.gather(*[
            conn.send(payload, **kwargs)
            for name, conn in self._connectors.items()
            if name not in exclude
        ], return_exceptions=True)
        return [r for r in results if isinstance(r, ConnectorResult)]

    def list(self) -> List[str]:
        return list(self._connectors.keys())


# ── Event-Driven Branching ────────────────────────────────────────────────────

@dataclass
class BranchCondition:
    name: str
    condition_fn: Callable          # (ctx) → bool
    handler: Callable               # async (ctx) → dict
    fire_once: bool = True
    fired: bool = False


class EventDrivenBranchManager:
    """
    Manages conditional branches that fire mid-workflow when an external
    condition becomes true (e.g. an external API returns a threshold value).

    Usage::

        mgr = EventDrivenBranchManager()
        mgr.register("high_risk",
            condition_fn=lambda ctx: ctx.get("risk_score", 0) > 0.8,
            handler=escalation_agent)

        updated_ctx = await mgr.evaluate(ctx)
    """

    def __init__(self) -> None:
        self._branches: List[BranchCondition] = []

    def register(
        self,
        name: str,
        condition_fn: Callable,
        handler: Callable,
        fire_once: bool = True,
    ) -> BranchCondition:
        bc = BranchCondition(name=name, condition_fn=condition_fn,
                             handler=handler, fire_once=fire_once)
        self._branches.append(bc)
        return bc

    async def evaluate(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Evaluate all branches against *ctx*, firing those that match."""
        for branch in self._branches:
            if branch.fire_once and branch.fired:
                continue
            try:
                should_fire = branch.condition_fn(ctx)
                if asyncio.iscoroutine(should_fire):
                    should_fire = await should_fire
            except Exception:
                continue

            if should_fire:
                branch.fired = True
                try:
                    result = branch.handler(ctx)
                    if asyncio.iscoroutine(result):
                        result = await result
                    if isinstance(result, dict):
                        ctx = {**ctx, **result, f"_branch_{branch.name}": True}
                except Exception:
                    pass

        return ctx


# ── Document Ingestion ────────────────────────────────────────────────────────

@dataclass
class DocumentChunk:
    """A single text chunk from an ingested document."""
    text: str
    source: str
    chunk_index: int
    start_char: int
    end_char: int
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.text)


class DocumentIngester:
    """
    Splits raw document text into overlapping chunks ready for agent context.

    Parameters
    ----------
    chunk_size   Target chunk size in characters
    overlap      Characters of overlap between consecutive chunks
    strip_extra  Strip extra whitespace / newlines from chunks

    Usage::

        ingester = DocumentIngester(chunk_size=500, overlap=50)
        chunks = ingester.ingest("Long document...", source="annual_report.pdf")
    """

    def __init__(
        self,
        chunk_size: int = 500,
        overlap: int = 50,
        strip_extra: bool = True,
    ) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.strip_extra = strip_extra

    def ingest(self, text: str, source: str = "unknown") -> List[DocumentChunk]:
        if self.strip_extra:
            text = re.sub(r"\s+", " ", text).strip()

        chunks: List[DocumentChunk] = []
        step = max(1, self.chunk_size - self.overlap)
        idx = 0
        i = 0
        while i < len(text):
            end = min(i + self.chunk_size, len(text))
            chunk_text = text[i:end]
            chunks.append(DocumentChunk(
                text=chunk_text,
                source=source,
                chunk_index=idx,
                start_char=i,
                end_char=end,
            ))
            idx += 1
            i += step

        return chunks

    def to_context(self, chunks: List[DocumentChunk], max_chunks: int = 5) -> str:
        """Format top *max_chunks* as a single context string."""
        return "\n\n".join(
            f"[Source: {c.source}, chunk {c.chunk_index}]\n{c.text}"
            for c in chunks[:max_chunks]
        )


class IngestionPipeline:
    """
    End-to-end document ingestion pipeline.

    Ingests raw text, optionally applies a filter/ranker function, and
    formats the top chunks into an agent-ready context string.

    Usage::

        pipeline = IngestionPipeline(chunk_size=400, overlap=40)
        chunks = pipeline.ingest("Very long report...", source="q3_report.pdf")
        context = pipeline.top_context(chunks, query="revenue growth", n=5)
    """

    def __init__(
        self,
        chunk_size: int = 500,
        overlap: int = 50,
        ranker_fn: Optional[Callable] = None,
    ) -> None:
        self._ingester = DocumentIngester(chunk_size=chunk_size, overlap=overlap)
        self._ranker = ranker_fn  # (query, chunks) → ranked chunks

    def ingest(self, text: str, source: str = "unknown") -> List[DocumentChunk]:
        return self._ingester.ingest(text, source)

    def top_context(
        self,
        chunks: List[DocumentChunk],
        query: str = "",
        n: int = 5,
    ) -> str:
        if self._ranker and query:
            ranked = self._ranker(query, chunks)
        else:
            # Simple keyword overlap scoring
            qwords = set(query.lower().split())
            def score(c: DocumentChunk) -> float:
                cwords = set(c.text.lower().split())
                return len(qwords & cwords) / (len(qwords) + 1)
            ranked = sorted(chunks, key=score, reverse=True) if qwords else chunks

        return self._ingester.to_context(ranked, max_chunks=n)


__all__ = [
    "WebhookEvent",
    "WebhookRouter",
    "ConnectorResult",
    "OutboundConnector",
    "HttpConnector",
    "LoggingConnector",
    "ConnectorRegistry",
    "BranchCondition",
    "EventDrivenBranchManager",
    "DocumentChunk",
    "DocumentIngester",
    "IngestionPipeline",
]
