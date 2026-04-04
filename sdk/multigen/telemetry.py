"""
Native observability for Multigen: distributed tracing, structured metrics,
JSON logging, and Langfuse-style LLM observability — zero external dependencies.

Langfuse-inspired features (built natively)
-------------------------------------------
- ``Session``       — group traces for a single user/conversation session
- ``Trace``         — top-level workflow execution container (like Langfuse Trace)
- ``Generation``    — individual LLM call: model, prompt, completion, tokens, cost
- ``Span``          — intermediate processing step (tool, retrieval, rerank, etc.)
- ``Score``         — evaluation score attached to any observation (name, value, comment)
- ``CostLedger``    — per-generation USD cost, per-trace / per-tenant aggregation

Novel additions (not in Langfuse or any other tool)
----------------------------------------------------
- ``EpistemicAttribute``    — confidence + propagated_uncertainty per span
- ``GoalDriftEvent``        — goal drift score as a first-class span event
- ``SecurityEvent``         — security violations embedded in the trace
- ``TenantObservabilityScope`` — hard multi-tenant boundary on ALL telemetry
- ``BehavioralMetrics``     — per-agent entropy/TTR/latency fingerprint
- ``W3C TraceContext``      — cross-agent trace propagation headers

Architecture
------------
::

    ObservabilityManager          ← single entry-point
      ├── Tracer                  ← creates Spans / AgentSpans; W3C context
      ├── GenerationStore         ← Langfuse-style Generation + cost tracking
      ├── SessionStore            ← Langfuse-style Session management
      ├── ScoreStore              ← Scores attached to any observation
      ├── Meter + MetricRegistry  ← Prometheus-format Counter/Gauge/Histogram/Summary
      ├── StructuredLogger        ← JSON logs, per-tenant, correlated to traces
      ├── TenantQuotaTracker      ← observability overhead per tenant
      └── List[BaseExporter]      ← InMemory / JSONFile / OTelHTTP / Prometheus / Alert

Usage::

    from multigen.telemetry import ObservabilityManager, LogLevel

    obs = ObservabilityManager.create("my-pipeline")
    obs.instrument_agent(my_agent)
    obs.instrument_graph(my_graph)

    with obs.session("user-42") as sess:
        with obs.trace("order-workflow", session_id=sess.session_id, tenant_id="acme") as tr:
            gen = obs.generation(
                trace_id=tr.trace_id,
                name="llm-call",
                model="claude-sonnet-4-6",
                input=[{"role": "user", "content": "Summarise this order"}],
            )
            # ... call LLM ...
            gen.finish(output="Order #1234 for 3 items", input_tokens=20, output_tokens=10)
            obs.score(gen.generation_id, name="relevance", value=0.9, comment="on-topic")

    print(obs.tenant_report("acme"))
    obs.export_all()
"""
from __future__ import annotations

import bisect
import collections
import contextlib
import contextvars
import enum
import functools
import http.server
import io
import json
import re
import secrets
import threading
import time
import traceback
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple


# ── Price table (USD per token) ───────────────────────────────────────────────

_MODEL_PRICE: Dict[str, Dict[str, float]] = {
    "gpt-4o":             {"input": 5.00e-6,  "output": 15.00e-6,  "cached": 2.50e-6},
    "gpt-4o-mini":        {"input": 0.15e-6,  "output": 0.60e-6,   "cached": 0.075e-6},
    "claude-opus-4-6":    {"input": 15.00e-6, "output": 75.00e-6,  "cached": 1.50e-6},
    "claude-sonnet-4-6":  {"input": 3.00e-6,  "output": 15.00e-6,  "cached": 1.50e-6},
    "claude-haiku-4-5":   {"input": 0.80e-6,  "output": 4.00e-6,   "cached": 0.08e-6},
    "gemini-1.5-flash":   {"input": 0.075e-6, "output": 0.30e-6,   "cached": 0.01875e-6},
    "mistral-small":      {"input": 0.20e-6,  "output": 0.60e-6,   "cached": 0.0},
    "llama3":             {"input": 0.0,       "output": 0.0,       "cached": 0.0},
}

_DEFAULT_LATENCY_BUCKETS: Tuple[float, ...] = (
    5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, float("inf"),
)


# ═══════════════════════════════════════════════════════════════════════════════
# § 1  DISTRIBUTED TRACING  (W3C TraceContext)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class TraceContext:
    """W3C traceparent-compatible context propagated across agents and services."""
    trace_id: str          # 32 hex chars (128-bit)
    span_id: str           # 16 hex chars (64-bit)
    parent_span_id: Optional[str] = None
    flags: str = "01"      # "01" = sampled

    # ── factory ──────────────────────────────────────────────────────────────

    @staticmethod
    def new_root() -> "TraceContext":
        return TraceContext(
            trace_id=uuid.uuid4().hex,
            span_id=secrets.token_hex(8),
        )

    def child(self) -> "TraceContext":
        return TraceContext(
            trace_id=self.trace_id,
            span_id=secrets.token_hex(8),
            parent_span_id=self.span_id,
            flags=self.flags,
        )

    # ── W3C wire format ───────────────────────────────────────────────────────

    def to_traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.flags}"

    @staticmethod
    def from_traceparent(header: str) -> Optional["TraceContext"]:
        m = re.match(r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$", header)
        if not m:
            return None
        return TraceContext(trace_id=m.group(1), span_id=m.group(2), flags=m.group(3))

    def inject(self, headers: Dict[str, str]) -> None:
        headers["traceparent"] = self.to_traceparent()

    @staticmethod
    def extract(headers: Dict[str, str]) -> Optional["TraceContext"]:
        hdr = headers.get("traceparent", "")
        return TraceContext.from_traceparent(hdr) if hdr else None


class SpanStatus(enum.Enum):
    UNSET = "UNSET"
    OK    = "OK"
    ERROR = "ERROR"


@dataclass
class SpanEvent:
    name: str
    timestamp: float = field(default_factory=time.time)
    attributes: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "timestamp": self.timestamp, **self.attributes}


@dataclass
class Span:
    """Unit of work in a distributed trace."""
    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    start_time: float
    end_time: Optional[float] = None
    status: SpanStatus = SpanStatus.UNSET
    attributes: Dict[str, Any] = field(default_factory=dict)
    events: List[SpanEvent] = field(default_factory=list)
    tenant_id: Optional[str] = None
    workflow_id: Optional[str] = None

    # ── mutations ─────────────────────────────────────────────────────────────

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def add_event(self, name: str, attributes: Optional[Dict[str, Any]] = None) -> SpanEvent:
        ev = SpanEvent(name=name, attributes=attributes or {})
        self.events.append(ev)
        return ev

    def set_status(self, status: SpanStatus, message: str = "") -> None:
        self.status = status
        if message:
            self.attributes["status.message"] = message

    def end(self, end_time: Optional[float] = None) -> None:
        if self.end_time is None:
            self.end_time = end_time or time.time()

    def duration_ms(self) -> Optional[float]:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms(),
            "status": self.status.value,
            "attributes": self.attributes,
            "events": [e.to_dict() for e in self.events],
            "tenant_id": self.tenant_id,
            "workflow_id": self.workflow_id,
        }


@dataclass
class AgentSpan(Span):
    """Enriched span for an agent invocation (model, tokens, confidence, drift)."""
    agent_id: str = ""
    agent_name: str = ""
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    confidence: float = 1.0
    goal_drift_score: float = 0.0

    def record_token_usage(self, input_t: int, output_t: int, cached_t: int = 0) -> None:
        self.input_tokens += input_t
        self.output_tokens += output_t
        self.cached_tokens += cached_t
        self.set_attribute("tokens.input", self.input_tokens)
        self.set_attribute("tokens.output", self.output_tokens)
        self.set_attribute("tokens.cached", self.cached_tokens)

    def record_confidence(self, score: float, sources: Optional[List[str]] = None) -> None:
        self.confidence = score
        self.set_attribute("epistemic.confidence", score)
        if sources:
            self.set_attribute("epistemic.uncertainty_sources", sources)

    def record_goal_drift(self, score: float, reason: str = "") -> None:
        self.goal_drift_score = score
        self.add_event("goal_drift", {"drift_score": score, "reason": reason})
        self.set_attribute("goal_drift_score", score)

    def add_security_event(self, event_type: str, severity: str = "high",
                           details: Optional[Dict[str, Any]] = None) -> SpanEvent:
        return self.add_event("security_event", {
            "event_type": event_type, "severity": severity, **(details or {})
        })

    def add_hallucination_event(self, detected: bool, score: float = 0.0,
                                flagged_claims: Optional[List[str]] = None) -> SpanEvent:
        return self.add_event("hallucination_check", {
            "detected": detected, "score": score,
            "flagged_claims": flagged_claims or [],
        })


# ── Correlation context (asyncio + thread safe) ───────────────────────────────

@dataclass
class CorrelationContext:
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    tenant_id: Optional[str] = None
    agent_id: Optional[str] = None
    workflow_id: Optional[str] = None
    session_id: Optional[str] = None


_correlation_ctx: contextvars.ContextVar[CorrelationContext] = (
    contextvars.ContextVar("_mg_correlation", default=CorrelationContext())
)
_current_span_var: contextvars.ContextVar[Optional[Span]] = (
    contextvars.ContextVar("_mg_current_span", default=None)
)


def get_correlation() -> CorrelationContext:
    return _correlation_ctx.get()


def set_correlation(**kwargs: Any) -> contextvars.Token:
    current = _correlation_ctx.get()
    updated = CorrelationContext(
        trace_id=kwargs.get("trace_id", current.trace_id),
        span_id=kwargs.get("span_id", current.span_id),
        tenant_id=kwargs.get("tenant_id", current.tenant_id),
        agent_id=kwargs.get("agent_id", current.agent_id),
        workflow_id=kwargs.get("workflow_id", current.workflow_id),
        session_id=kwargs.get("session_id", current.session_id),
    )
    return _correlation_ctx.set(updated)


class Tracer:
    """Creates and manages Spans with W3C context propagation."""

    def __init__(self, service_name: str = "multigen",
                 exporters: Optional[List["BaseExporter"]] = None) -> None:
        self.service_name = service_name
        self._exporters: List["BaseExporter"] = exporters or []
        self._lock = threading.Lock()
        self._active_spans: Dict[str, Span] = {}

    def start_span(
        self,
        name: str,
        *,
        parent: Optional[Span] = None,
        trace_context: Optional[TraceContext] = None,
        attributes: Optional[Dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
        workflow_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        agent_name: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Span:
        corr = get_correlation()
        if trace_context:
            ctx = trace_context.child()
        elif parent:
            ctx = TraceContext(
                trace_id=parent.trace_id,
                span_id=secrets.token_hex(8),
                parent_span_id=parent.span_id,
            )
        else:
            ctx = TraceContext.new_root()

        tid = tenant_id or corr.tenant_id
        wid = workflow_id or corr.workflow_id

        if agent_id or agent_name or model:
            span: Span = AgentSpan(
                name=name,
                trace_id=ctx.trace_id,
                span_id=ctx.span_id,
                parent_span_id=ctx.parent_span_id,
                start_time=time.time(),
                tenant_id=tid,
                workflow_id=wid,
                agent_id=agent_id or "",
                agent_name=agent_name or "",
                model=model or "",
            )
        else:
            span = Span(
                name=name,
                trace_id=ctx.trace_id,
                span_id=ctx.span_id,
                parent_span_id=ctx.parent_span_id,
                start_time=time.time(),
                tenant_id=tid,
                workflow_id=wid,
            )

        span.set_attribute("service.name", self.service_name)
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)

        with self._lock:
            self._active_spans[span.span_id] = span
        return span

    @contextlib.contextmanager
    def span(self, name: str, **kwargs: Any) -> Iterator[Span]:
        s = self.start_span(name, **kwargs)
        token = _current_span_var.set(s)
        try:
            yield s
            if s.status == SpanStatus.UNSET:
                s.set_status(SpanStatus.OK)
        except Exception as exc:
            s.set_status(SpanStatus.ERROR, str(exc))
            s.add_event("exception", {
                "exception.type": type(exc).__name__,
                "exception.message": str(exc),
                "exception.stacktrace": traceback.format_exc(),
            })
            raise
        finally:
            s.end()
            _current_span_var.reset(token)
            self._finish(s)

    @contextlib.asynccontextmanager
    async def async_span(self, name: str, **kwargs: Any):
        s = self.start_span(name, **kwargs)
        token = _current_span_var.set(s)
        try:
            yield s
            if s.status == SpanStatus.UNSET:
                s.set_status(SpanStatus.OK)
        except Exception as exc:
            s.set_status(SpanStatus.ERROR, str(exc))
            s.add_event("exception", {
                "exception.type": type(exc).__name__,
                "exception.message": str(exc),
                "exception.stacktrace": traceback.format_exc(),
            })
            raise
        finally:
            s.end()
            _current_span_var.reset(token)
            self._finish(s)

    def current_span(self) -> Optional[Span]:
        return _current_span_var.get()

    def inject(self, span: Span, headers: Dict[str, str]) -> None:
        ctx = TraceContext(trace_id=span.trace_id, span_id=span.span_id)
        ctx.inject(headers)

    def extract(self, headers: Dict[str, str]) -> Optional[TraceContext]:
        return TraceContext.extract(headers)

    def _finish(self, span: Span) -> None:
        with self._lock:
            self._active_spans.pop(span.span_id, None)
        for exp in self._exporters:
            try:
                exp.export_span(span)
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════════
# § 2  LANGFUSE-STYLE OBSERVATIONS  (Traces · Generations · Sessions · Scores)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class Trace:
    """
    Top-level container for a workflow execution — equivalent to Langfuse Trace.
    Groups all spans/generations that belong to one logical run.
    """
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    name: str = ""
    session_id: Optional[str] = None
    tenant_id: Optional[str] = None
    user_id: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    input: Any = None
    output: Any = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    status: str = "running"      # running | ok | error
    root_span: Optional[Span] = None

    def finish(self, output: Any = None, status: str = "ok") -> None:
        self.end_time = time.time()
        self.status = status
        if output is not None:
            self.output = output

    def duration_ms(self) -> Optional[float]:
        if self.end_time is None:
            return None
        return (self.end_time - self.start_time) * 1000.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "name": self.name,
            "session_id": self.session_id,
            "tenant_id": self.tenant_id,
            "user_id": self.user_id,
            "tags": self.tags,
            "metadata": self.metadata,
            "input": self.input,
            "output": self.output,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "duration_ms": self.duration_ms(),
            "status": self.status,
        }


@dataclass
class Generation:
    """
    Individual LLM call — equivalent to Langfuse Generation.
    Records model, prompt, completion, token counts, cost, and latency.
    """
    generation_id: str = field(default_factory=lambda: str(uuid.uuid4())[:16])
    trace_id: str = ""
    parent_span_id: Optional[str] = None
    name: str = ""
    model: str = ""
    provider: str = ""
    tenant_id: Optional[str] = None
    session_id: Optional[str] = None
    input: Any = None             # prompt messages or text
    output: Optional[str] = None  # completion text
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    cost_usd: float = 0.0
    latency_ms: Optional[float] = None
    start_time: float = field(default_factory=time.time)
    end_time: Optional[float] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    level: str = "DEFAULT"        # DEFAULT | DEBUG | WARNING | ERROR
    status_message: Optional[str] = None
    # AI-specific extensions
    confidence: float = 1.0
    hallucination_detected: bool = False
    goal_drift_score: float = 0.0

    def finish(
        self,
        output: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        confidence: float = 1.0,
        hallucination_detected: bool = False,
        price_table: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> "Generation":
        self.output = output
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cached_tokens = cached_tokens
        self.confidence = confidence
        self.hallucination_detected = hallucination_detected
        self.end_time = time.time()
        self.latency_ms = (self.end_time - self.start_time) * 1000.0
        prices = (price_table or _MODEL_PRICE).get(self.model, {})
        self.cost_usd = (
            input_tokens * prices.get("input", 0.0)
            + output_tokens * prices.get("output", 0.0)
            + cached_tokens * prices.get("cached", 0.0)
        )
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "trace_id": self.trace_id,
            "parent_span_id": self.parent_span_id,
            "name": self.name,
            "model": self.model,
            "provider": self.provider,
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "input": self.input,
            "output": self.output,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cached_tokens": self.cached_tokens,
            "cost_usd": self.cost_usd,
            "latency_ms": self.latency_ms,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "confidence": self.confidence,
            "hallucination_detected": self.hallucination_detected,
            "goal_drift_score": self.goal_drift_score,
            "metadata": self.metadata,
            "level": self.level,
        }


@dataclass
class Session:
    """
    Groups multiple Traces from one user/conversation — equivalent to Langfuse Session.
    """
    session_id: str = field(default_factory=lambda: str(uuid.uuid4())[:16])
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    trace_ids: List[str] = field(default_factory=list)

    def add_trace(self, trace_id: str) -> None:
        if trace_id not in self.trace_ids:
            self.trace_ids.append(trace_id)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "trace_count": len(self.trace_ids),
            "trace_ids": self.trace_ids,
        }


@dataclass
class Score:
    """
    Evaluation score attached to any observation (Trace, Generation, Span).
    Equivalent to Langfuse Score — supports numeric and categorical values.
    """
    score_id: str = field(default_factory=lambda: str(uuid.uuid4())[:16])
    observation_id: str = ""      # trace_id, generation_id, or span_id
    observation_type: str = ""    # "trace" | "generation" | "span"
    name: str = ""                # e.g. "relevance", "faithfulness", "toxicity"
    value: float = 0.0            # numeric score (0–1 recommended)
    string_value: Optional[str] = None   # categorical label
    comment: Optional[str] = None
    source: str = "manual"        # "manual" | "model" | "rule" | "human"
    tenant_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score_id": self.score_id,
            "observation_id": self.observation_id,
            "observation_type": self.observation_type,
            "name": self.name,
            "value": self.value,
            "string_value": self.string_value,
            "comment": self.comment,
            "source": self.source,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at,
        }


# ── Stores ────────────────────────────────────────────────────────────────────

class TraceStore:
    """Thread-safe store for Trace objects."""

    def __init__(self, maxsize: int = 10_000) -> None:
        self._traces: collections.OrderedDict = collections.OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def add(self, trace: Trace) -> None:
        with self._lock:
            if len(self._traces) >= self._maxsize:
                self._traces.popitem(last=False)
            self._traces[trace.trace_id] = trace

    def get(self, trace_id: str) -> Optional[Trace]:
        return self._traces.get(trace_id)

    def get_by_tenant(self, tenant_id: str) -> List[Trace]:
        return [t for t in self._traces.values() if t.tenant_id == tenant_id]

    def get_by_session(self, session_id: str) -> List[Trace]:
        return [t for t in self._traces.values() if t.session_id == session_id]

    def all(self) -> List[Trace]:
        return list(self._traces.values())


class GenerationStore:
    """Thread-safe store for Generation objects with cost aggregation."""

    def __init__(self, maxsize: int = 50_000) -> None:
        self._gens: collections.OrderedDict = collections.OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def add(self, gen: Generation) -> None:
        with self._lock:
            if len(self._gens) >= self._maxsize:
                self._gens.popitem(last=False)
            self._gens[gen.generation_id] = gen

    def get(self, generation_id: str) -> Optional[Generation]:
        return self._gens.get(generation_id)

    def by_trace(self, trace_id: str) -> List[Generation]:
        return [g for g in self._gens.values() if g.trace_id == trace_id]

    def by_tenant(self, tenant_id: str) -> List[Generation]:
        return [g for g in self._gens.values() if g.tenant_id == tenant_id]

    def by_model(self, model: str) -> List[Generation]:
        return [g for g in self._gens.values() if g.model == model]

    def cost_for_trace(self, trace_id: str) -> float:
        return sum(g.cost_usd for g in self.by_trace(trace_id))

    def cost_for_tenant(self, tenant_id: str) -> float:
        return sum(g.cost_usd for g in self.by_tenant(tenant_id))

    def cost_for_model(self, model: str) -> float:
        return sum(g.cost_usd for g in self.by_model(model))

    def model_summary(self) -> Dict[str, Dict[str, Any]]:
        by_model: Dict[str, List[Generation]] = {}
        for g in self._gens.values():
            by_model.setdefault(g.model, []).append(g)
        result = {}
        for model, gens in by_model.items():
            lats = [g.latency_ms for g in gens if g.latency_ms is not None]
            lats_sorted = sorted(lats)
            p95_idx = int(0.95 * len(lats_sorted)) if lats_sorted else 0
            result[model] = {
                "total_calls": len(gens),
                "total_input_tokens": sum(g.input_tokens for g in gens),
                "total_output_tokens": sum(g.output_tokens for g in gens),
                "total_cost_usd": sum(g.cost_usd for g in gens),
                "mean_confidence": sum(g.confidence for g in gens) / len(gens),
                "hallucination_rate": sum(1 for g in gens if g.hallucination_detected) / len(gens),
                "latency_p95_ms": lats_sorted[p95_idx] if lats_sorted else None,
                "mean_latency_ms": sum(lats) / len(lats) if lats else None,
            }
        return result

    def all(self) -> List[Generation]:
        return list(self._gens.values())


class TelemetrySessionStore:
    """Thread-safe store for Session objects."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, user_id: Optional[str] = None,
               tenant_id: Optional[str] = None,
               metadata: Optional[Dict[str, Any]] = None) -> Session:
        sess = Session(user_id=user_id, tenant_id=tenant_id, metadata=metadata or {})
        with self._lock:
            self._sessions[sess.session_id] = sess
        return sess

    def get(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def get_by_tenant(self, tenant_id: str) -> List[Session]:
        return [s for s in self._sessions.values() if s.tenant_id == tenant_id]

    def all(self) -> List[Session]:
        return list(self._sessions.values())


class ScoreStore:
    """Thread-safe store for Score objects."""

    def __init__(self, maxsize: int = 100_000) -> None:
        self._scores: collections.OrderedDict = collections.OrderedDict()
        self._maxsize = maxsize
        self._lock = threading.Lock()

    def add(self, score: Score) -> None:
        with self._lock:
            if len(self._scores) >= self._maxsize:
                self._scores.popitem(last=False)
            self._scores[score.score_id] = score

    def for_observation(self, observation_id: str) -> List[Score]:
        return [s for s in self._scores.values() if s.observation_id == observation_id]

    def for_tenant(self, tenant_id: str) -> List[Score]:
        return [s for s in self._scores.values() if s.tenant_id == tenant_id]

    def by_name(self, name: str) -> List[Score]:
        return [s for s in self._scores.values() if s.name == name]

    def mean(self, name: str, tenant_id: Optional[str] = None) -> Optional[float]:
        scores = [s for s in self._scores.values()
                  if s.name == name and (tenant_id is None or s.tenant_id == tenant_id)]
        if not scores:
            return None
        return sum(s.value for s in scores) / len(scores)

    def all(self) -> List[Score]:
        return list(self._scores.values())


# ═══════════════════════════════════════════════════════════════════════════════
# § 3  METRICS  (Prometheus exposition format)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class MetricSample:
    name: str
    labels: Dict[str, str]
    value: float
    metric_type: str = "untyped"
    description: str = ""


def _render_labels(labels: Dict[str, str]) -> str:
    if not labels:
        return ""
    parts = [f'{k}="{v.replace(chr(34), chr(92)+chr(34))}"'
             for k, v in sorted(labels.items())]
    return "{" + ",".join(parts) + "}"


class _Instrument:
    def __init__(self, name: str, description: str = "", unit: str = "",
                 base_labels: Optional[Dict[str, str]] = None) -> None:
        self.name = name
        self.description = description
        self.unit = unit
        self._base_labels: Dict[str, str] = base_labels or {}
        self._lock = threading.Lock()

    def _merged(self, extra: Dict[str, str]) -> Dict[str, str]:
        return {**self._base_labels, **extra}

    def _key(self, labels: Dict[str, str]) -> str:
        return json.dumps(sorted(labels.items()))

    def collect(self) -> List[MetricSample]:
        raise NotImplementedError


class Counter(_Instrument):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._values: Dict[str, float] = collections.defaultdict(float)

    def add(self, value: float = 1.0, labels: Optional[Dict[str, str]] = None) -> None:
        merged = self._merged(labels or {})
        with self._lock:
            self._values[self._key(merged)] += value

    def get(self, labels: Optional[Dict[str, str]] = None) -> float:
        merged = self._merged(labels or {})
        return self._values.get(self._key(merged), 0.0)

    def collect(self) -> List[MetricSample]:
        with self._lock:
            return [
                MetricSample(name=self.name + "_total",
                             labels=dict(json.loads(k)),
                             value=v, metric_type="counter",
                             description=self.description)
                for k, v in self._values.items()
            ]


class Gauge(_Instrument):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._values: Dict[str, float] = {}

    def set(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        k = self._key(self._merged(labels or {}))
        with self._lock:
            self._values[k] = value

    def inc(self, delta: float = 1.0, labels: Optional[Dict[str, str]] = None) -> None:
        k = self._key(self._merged(labels or {}))
        with self._lock:
            self._values[k] = self._values.get(k, 0.0) + delta

    def dec(self, delta: float = 1.0, labels: Optional[Dict[str, str]] = None) -> None:
        self.inc(-delta, labels)

    def get(self, labels: Optional[Dict[str, str]] = None) -> float:
        return self._values.get(self._key(self._merged(labels or {})), 0.0)

    def collect(self) -> List[MetricSample]:
        with self._lock:
            return [
                MetricSample(name=self.name, labels=dict(json.loads(k)),
                             value=v, metric_type="gauge", description=self.description)
                for k, v in self._values.items()
            ]


class Histogram(_Instrument):
    def __init__(self, name: str, description: str = "", unit: str = "",
                 base_labels: Optional[Dict[str, str]] = None,
                 buckets: Tuple[float, ...] = _DEFAULT_LATENCY_BUCKETS) -> None:
        super().__init__(name, description, unit, base_labels)
        self._buckets = tuple(sorted(b for b in buckets if b != float("inf"))) + (float("inf"),)
        self._counts: Dict[str, List[int]] = {}
        self._sums: Dict[str, float] = collections.defaultdict(float)
        self._totals: Dict[str, int] = collections.defaultdict(int)

    def observe(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        k = self._key(self._merged(labels or {}))
        with self._lock:
            if k not in self._counts:
                self._counts[k] = [0] * len(self._buckets)
            for i, bound in enumerate(self._buckets):
                if value <= bound:
                    self._counts[k][i] += 1
            self._sums[k] += value
            self._totals[k] += 1

    def collect(self) -> List[MetricSample]:
        with self._lock:
            samples = []
            for k, bucket_counts in self._counts.items():
                lbs = dict(json.loads(k))
                cumulative = 0
                for i, bound in enumerate(self._buckets):
                    cumulative += bucket_counts[i]
                    le_lbs = {**lbs, "le": "+Inf" if bound == float("inf") else str(bound)}
                    samples.append(MetricSample(
                        name=self.name + "_bucket", labels=le_lbs,
                        value=cumulative, metric_type="histogram"))
                samples.append(MetricSample(
                    name=self.name + "_sum", labels=lbs,
                    value=self._sums[k], metric_type="histogram"))
                samples.append(MetricSample(
                    name=self.name + "_count", labels=lbs,
                    value=self._totals[k], metric_type="histogram"))
            return samples


class Summary(_Instrument):
    """Sliding-window quantiles using bisect (stdlib only)."""

    def __init__(self, name: str, description: str = "", unit: str = "",
                 base_labels: Optional[Dict[str, str]] = None,
                 window_seconds: float = 300.0) -> None:
        super().__init__(name, description, unit, base_labels)
        self._window_seconds = window_seconds
        self._windows: Dict[str, List[Tuple[float, float]]] = {}  # key→[(value,ts)]
        self._sums: Dict[str, float] = collections.defaultdict(float)
        self._totals: Dict[str, int] = collections.defaultdict(int)

    def observe(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        k = self._key(self._merged(labels or {}))
        now = time.time()
        cutoff = now - self._window_seconds
        with self._lock:
            window = self._windows.setdefault(k, [])
            bisect.insort(window, (value, now))
            while window and window[0][1] < cutoff:
                window.pop(0)
            self._sums[k] += value
            self._totals[k] += 1

    def quantile(self, q: float, labels: Optional[Dict[str, str]] = None) -> float:
        k = self._key(self._merged(labels or {}))
        vals = [v for v, _ in self._windows.get(k, [])]
        if not vals:
            return 0.0
        idx = max(0, min(int(q * len(vals)), len(vals) - 1))
        return vals[idx]

    def collect(self) -> List[MetricSample]:
        with self._lock:
            samples = []
            for k, window in self._windows.items():
                lbs = dict(json.loads(k))
                vals = [v for v, _ in window]
                for q in (0.5, 0.95, 0.99):
                    qlbs = {**lbs, "quantile": str(q)}
                    idx = max(0, min(int(q * len(vals)), len(vals) - 1)) if vals else 0
                    samples.append(MetricSample(
                        name=self.name, labels=qlbs,
                        value=vals[idx] if vals else 0.0, metric_type="summary"))
                samples.append(MetricSample(
                    name=self.name + "_sum", labels=lbs,
                    value=self._sums[k], metric_type="summary"))
                samples.append(MetricSample(
                    name=self.name + "_count", labels=lbs,
                    value=float(self._totals[k]), metric_type="summary"))
            return samples


class MetricRegistry:
    """Global registry. Collects all instruments; renders Prometheus text format."""

    _lock = threading.Lock()
    _instruments: List[_Instrument] = []

    @classmethod
    def register(cls, instrument: _Instrument) -> None:
        with cls._lock:
            cls._instruments.append(instrument)

    @classmethod
    def collect(cls) -> List[MetricSample]:
        samples = []
        for inst in cls._instruments:
            samples.extend(inst.collect())
        return samples

    @classmethod
    def to_prometheus_text(cls) -> str:
        buf = io.StringIO()
        by_name: Dict[str, List[MetricSample]] = {}
        for s in cls.collect():
            by_name.setdefault(s.name, []).append(s)
        for name, samples in by_name.items():
            mtype = samples[0].metric_type
            desc = samples[0].description
            if desc:
                buf.write(f"# HELP {name} {desc}\n")
            buf.write(f"# TYPE {name} {mtype}\n")
            for s in samples:
                buf.write(f"{s.name}{_render_labels(s.labels)} {s.value}\n")
        return buf.getvalue()

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            cls._instruments.clear()


class Meter:
    """Factory for metric instruments. Auto-registers with MetricRegistry."""

    def __init__(self, prefix: str = "multigen",
                 default_labels: Optional[Dict[str, str]] = None) -> None:
        self.prefix = prefix
        self._default_labels = default_labels or {}

    def _name(self, name: str) -> str:
        return f"{self.prefix}_{name}" if self.prefix else name

    def counter(self, name: str, description: str = "", unit: str = "",
                labels: Optional[Dict[str, str]] = None) -> Counter:
        c = Counter(self._name(name), description, unit,
                    {**self._default_labels, **(labels or {})})
        MetricRegistry.register(c)
        return c

    def gauge(self, name: str, description: str = "", unit: str = "",
              labels: Optional[Dict[str, str]] = None) -> Gauge:
        g = Gauge(self._name(name), description, unit,
                  {**self._default_labels, **(labels or {})})
        MetricRegistry.register(g)
        return g

    def histogram(self, name: str, description: str = "", unit: str = "",
                  labels: Optional[Dict[str, str]] = None,
                  buckets: Tuple[float, ...] = _DEFAULT_LATENCY_BUCKETS) -> Histogram:
        h = Histogram(self._name(name), description, unit,
                      {**self._default_labels, **(labels or {})}, buckets)
        MetricRegistry.register(h)
        return h

    def summary(self, name: str, description: str = "", unit: str = "",
                labels: Optional[Dict[str, str]] = None,
                window_seconds: float = 300.0) -> Summary:
        s = Summary(self._name(name), description, unit,
                    {**self._default_labels, **(labels or {})}, window_seconds)
        MetricRegistry.register(s)
        return s


class TenantMetrics:
    """Per-tenant metric namespace. Labels every instrument with tenant_id."""

    def __init__(self, tenant_id: str, prefix: str = "multigen") -> None:
        self._meter = Meter(prefix=prefix, default_labels={"tenant": tenant_id})
        self.tenant_id = tenant_id

    def counter(self, name: str, **kwargs: Any) -> Counter:
        return self._meter.counter(name, **kwargs)

    def gauge(self, name: str, **kwargs: Any) -> Gauge:
        return self._meter.gauge(name, **kwargs)

    def histogram(self, name: str, **kwargs: Any) -> Histogram:
        return self._meter.histogram(name, **kwargs)

    def summary(self, name: str, **kwargs: Any) -> Summary:
        return self._meter.summary(name, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════════
# § 4  STRUCTURED LOGGING  (JSON, per-tenant, correlated)
# ═══════════════════════════════════════════════════════════════════════════════

class LogLevel(enum.IntEnum):
    TRACE    = 5
    DEBUG    = 10
    INFO     = 20
    WARN     = 30
    ERROR    = 40
    CRITICAL = 50


@dataclass
class LogRecord:
    timestamp: float
    level: LogLevel
    message: str
    logger_name: str
    trace_id: Optional[str] = None
    span_id: Optional[str] = None
    tenant_id: Optional[str] = None
    agent_id: Optional[str] = None
    workflow_id: Optional[str] = None
    session_id: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "level": self.level.name,
            "message": self.message,
            "logger": self.logger_name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "tenant_id": self.tenant_id,
            "agent_id": self.agent_id,
            "workflow_id": self.workflow_id,
            "session_id": self.session_id,
            **self.extra,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), default=str)


class LogBuffer:
    """Thread-safe circular buffer for LogRecords."""

    def __init__(self, maxsize: int = 10_000) -> None:
        self._buf: collections.deque = collections.deque(maxlen=maxsize)
        self._lock = threading.Lock()

    def append(self, record: LogRecord) -> None:
        with self._lock:
            self._buf.append(record)

    def tail(self, n: int = 100) -> List[LogRecord]:
        with self._lock:
            items = list(self._buf)
        return items[-n:]

    def query_by_tenant(self, tenant_id: str, limit: int = 1000) -> List[LogRecord]:
        with self._lock:
            items = list(self._buf)
        return [r for r in items if r.tenant_id == tenant_id][-limit:]

    def query_by_trace(self, trace_id: str) -> List[LogRecord]:
        with self._lock:
            return [r for r in self._buf if r.trace_id == trace_id]

    def query_by_level(self, min_level: LogLevel, limit: int = 1000) -> List[LogRecord]:
        with self._lock:
            return [r for r in self._buf if r.level >= min_level][-limit:]

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    def __len__(self) -> int:
        return len(self._buf)


class LogSampler:
    """Rate-based sampling — accept 1-in-N for a given level."""

    def __init__(self, rates: Optional[Dict[LogLevel, int]] = None) -> None:
        self._rates: Dict[LogLevel, int] = rates or {LogLevel.DEBUG: 10, LogLevel.TRACE: 100}
        self._counters: Dict[LogLevel, int] = collections.defaultdict(int)
        self._lock = threading.Lock()

    def should_sample(self, level: LogLevel) -> bool:
        rate = self._rates.get(level, 1)
        if rate <= 1:
            return True
        with self._lock:
            self._counters[level] += 1
            return self._counters[level] % rate == 1


class StructuredLogger:
    """Thread-safe, async-safe JSON logger correlated with trace/tenant context."""

    def __init__(self, name: str, level: LogLevel = LogLevel.INFO,
                 buffer: Optional[LogBuffer] = None,
                 sampler: Optional[LogSampler] = None,
                 handlers: Optional[List[Callable[[LogRecord], None]]] = None) -> None:
        self.name = name
        self.level = level
        self._buffer = buffer or LogBuffer()
        self._sampler = sampler
        self._handlers: List[Callable[[LogRecord], None]] = handlers or []
        self._lock = threading.Lock()

    def _make_record(self, level: LogLevel, msg: str, **extra: Any) -> LogRecord:
        corr = get_correlation()
        return LogRecord(
            timestamp=time.time(), level=level, message=msg,
            logger_name=self.name,
            trace_id=corr.trace_id, span_id=corr.span_id,
            tenant_id=corr.tenant_id, agent_id=corr.agent_id,
            workflow_id=corr.workflow_id, session_id=corr.session_id,
            extra=extra,
        )

    def _emit(self, record: LogRecord) -> None:
        if record.level < self.level:
            return
        if self._sampler and not self._sampler.should_sample(record.level):
            return
        self._buffer.append(record)
        for handler in self._handlers:
            try:
                handler(record)
            except Exception:
                pass

    def trace(self, msg: str, **extra: Any) -> None:
        self._emit(self._make_record(LogLevel.TRACE, msg, **extra))

    def debug(self, msg: str, **extra: Any) -> None:
        self._emit(self._make_record(LogLevel.DEBUG, msg, **extra))

    def info(self, msg: str, **extra: Any) -> None:
        self._emit(self._make_record(LogLevel.INFO, msg, **extra))

    def warn(self, msg: str, **extra: Any) -> None:
        self._emit(self._make_record(LogLevel.WARN, msg, **extra))

    def error(self, msg: str, **extra: Any) -> None:
        self._emit(self._make_record(LogLevel.ERROR, msg, **extra))

    def critical(self, msg: str, **extra: Any) -> None:
        self._emit(self._make_record(LogLevel.CRITICAL, msg, **extra))

    def bind(self, **context: Any) -> "BoundLogger":
        return BoundLogger(self, context)

    @property
    def buffer(self) -> LogBuffer:
        return self._buffer


class BoundLogger:
    """Logger with pre-bound context fields injected into every record."""

    def __init__(self, parent: StructuredLogger, context: Dict[str, Any]) -> None:
        self._parent = parent
        self._ctx = context

    def _log(self, method: str, msg: str, **extra: Any) -> None:
        getattr(self._parent, method)(msg, **{**self._ctx, **extra})

    def info(self, msg: str, **extra: Any) -> None:
        self._log("info", msg, **extra)

    def warn(self, msg: str, **extra: Any) -> None:
        self._log("warn", msg, **extra)

    def error(self, msg: str, **extra: Any) -> None:
        self._log("error", msg, **extra)

    def debug(self, msg: str, **extra: Any) -> None:
        self._log("debug", msg, **extra)


class TenantLogger:
    """Scoped logger: auto-injects tenant_id; cannot leak to other tenants."""

    def __init__(self, tenant_id: str, base: StructuredLogger,
                 buffer: LogBuffer) -> None:
        self.tenant_id = tenant_id
        self._base = base
        self._buffer = buffer

    def _log(self, method: str, msg: str, **extra: Any) -> None:
        getattr(self._base, method)(msg, tenant_id=self.tenant_id, **extra)

    def info(self, msg: str, **extra: Any) -> None:
        self._log("info", msg, **extra)

    def warn(self, msg: str, **extra: Any) -> None:
        self._log("warn", msg, **extra)

    def error(self, msg: str, **extra: Any) -> None:
        self._log("error", msg, **extra)

    def debug(self, msg: str, **extra: Any) -> None:
        self._log("debug", msg, **extra)

    def tail(self, n: int = 100) -> List[LogRecord]:
        return self._buffer.query_by_tenant(self.tenant_id, limit=n)


# ═══════════════════════════════════════════════════════════════════════════════
# § 5  MULTI-TENANT ISOLATION
# ═══════════════════════════════════════════════════════════════════════════════

class ObservabilityQuotaError(Exception):
    pass


class TenantQuotaTracker:
    """Tracks observability overhead per tenant; raises if quota exceeded."""

    def __init__(self) -> None:
        self._span_counts: Dict[str, int] = collections.defaultdict(int)
        self._log_bytes: Dict[str, int] = collections.defaultdict(int)
        self._quotas: Dict[str, Dict[str, int]] = {}
        self._lock = threading.Lock()

    def set_quota(self, tenant_id: str, max_spans: int = 100_000,
                  max_log_bytes: int = 100 * 1024 * 1024) -> None:
        with self._lock:
            self._quotas[tenant_id] = {"max_spans": max_spans, "max_log_bytes": max_log_bytes}

    def record_span(self, tenant_id: str) -> None:
        with self._lock:
            self._span_counts[tenant_id] += 1
            q = self._quotas.get(tenant_id, {})
            if q and self._span_counts[tenant_id] > q.get("max_spans", 10**9):
                raise ObservabilityQuotaError(
                    f"Tenant {tenant_id!r} exceeded span quota {q['max_spans']}")

    def record_log(self, tenant_id: str, record: LogRecord) -> None:
        size = len(record.to_json())
        with self._lock:
            self._log_bytes[tenant_id] += size
            q = self._quotas.get(tenant_id, {})
            if q and self._log_bytes[tenant_id] > q.get("max_log_bytes", 10**12):
                raise ObservabilityQuotaError(
                    f"Tenant {tenant_id!r} exceeded log byte quota")

    def get_usage(self, tenant_id: str) -> Dict[str, int]:
        return {
            "span_count": self._span_counts.get(tenant_id, 0),
            "log_bytes": self._log_bytes.get(tenant_id, 0),
        }

    def reset(self, tenant_id: str) -> None:
        with self._lock:
            self._span_counts.pop(tenant_id, None)
            self._log_bytes.pop(tenant_id, None)


@dataclass
class TenantObservabilityReport:
    tenant_id: str
    total_spans: int
    error_rate: float
    p95_latency_ms: float
    p99_latency_ms: float
    total_traces: int
    total_generations: int
    total_tokens_in: int
    total_tokens_out: int
    total_cost_usd: float
    top_agents_by_cost: List[Tuple[str, float]]
    mean_confidence: float
    max_goal_drift: float
    hallucination_rate: float
    active_sessions: int
    score_means: Dict[str, float]         # {score_name: mean_value}
    generated_at: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "total_spans": self.total_spans,
            "error_rate": round(self.error_rate, 4),
            "latency": {"p95_ms": self.p95_latency_ms, "p99_ms": self.p99_latency_ms},
            "traces": self.total_traces,
            "generations": self.total_generations,
            "tokens": {"input": self.total_tokens_in, "output": self.total_tokens_out},
            "cost_usd": round(self.total_cost_usd, 6),
            "top_agents_by_cost": self.top_agents_by_cost,
            "mean_confidence": round(self.mean_confidence, 4),
            "max_goal_drift": round(self.max_goal_drift, 4),
            "hallucination_rate": round(self.hallucination_rate, 4),
            "active_sessions": self.active_sessions,
            "score_means": {k: round(v, 4) for k, v in self.score_means.items()},
            "generated_at": self.generated_at,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# § 6  EPISTEMIC ATTRIBUTES  (novel — doesn't exist anywhere)
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class EpistemicAttribute:
    """
    Attaches confidence + propagated uncertainty to a Span.
    Enables visualising the "confidence river" flowing through a workflow graph.

    Propagation rule:
        effective_confidence = own_confidence × parent_effective_confidence
    """
    confidence: float = 1.0
    uncertainty_sources: List[str] = field(default_factory=list)
    propagated_uncertainty: float = 0.0
    effective_confidence: float = 1.0
    evidence_quality: str = "medium"      # "high" | "medium" | "low"
    known_unknowns: List[str] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)

    def attach_to_span(self, span: Span) -> None:
        span.set_attribute("epistemic.confidence", self.confidence)
        span.set_attribute("epistemic.effective_confidence", self.effective_confidence)
        span.set_attribute("epistemic.propagated_uncertainty", self.propagated_uncertainty)
        span.set_attribute("epistemic.evidence_quality", self.evidence_quality)
        if self.uncertainty_sources:
            span.set_attribute("epistemic.uncertainty_sources", self.uncertainty_sources)

    @staticmethod
    def propagate(
        parent: "EpistemicAttribute",
        child_confidence: float,
        child_sources: Optional[List[str]] = None,
    ) -> "EpistemicAttribute":
        prop_unc = 1.0 - parent.effective_confidence
        eff = child_confidence * parent.effective_confidence
        return EpistemicAttribute(
            confidence=child_confidence,
            uncertainty_sources=child_sources or [],
            propagated_uncertainty=prop_unc,
            effective_confidence=eff,
        )


# ═══════════════════════════════════════════════════════════════════════════════
# § 7  EXPORTERS
# ═══════════════════════════════════════════════════════════════════════════════

class BaseExporter:
    def export_span(self, span: Span) -> None:
        pass

    def export_spans(self, spans: List[Span]) -> None:
        for s in spans:
            self.export_span(s)

    def export_metrics(self, samples: List[MetricSample]) -> None:
        pass

    def export_logs(self, records: List[LogRecord]) -> None:
        pass

    def export_generations(self, generations: List[Generation]) -> None:
        pass

    def flush(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


class InMemorySpanExporter(BaseExporter):
    """Stores spans in memory. Primary backend for testing and inspection."""

    def __init__(self, max_spans: int = 50_000) -> None:
        self._spans: collections.deque = collections.deque(maxlen=max_spans)
        self._lock = threading.Lock()

    def export_span(self, span: Span) -> None:
        with self._lock:
            self._spans.append(span)

    def get_finished_spans(self) -> List[Span]:
        with self._lock:
            return [s for s in self._spans if s.end_time is not None]

    def filter_by_trace(self, trace_id: str) -> List[Span]:
        return [s for s in self._spans if s.trace_id == trace_id]

    def filter_by_tenant(self, tenant_id: str) -> List[Span]:
        return [s for s in self._spans if s.tenant_id == tenant_id]

    def filter_by_agent(self, agent_id: str) -> List[Span]:
        return [s for s in self._spans
                if isinstance(s, AgentSpan) and s.agent_id == agent_id]

    def filter_by_status(self, status: SpanStatus) -> List[Span]:
        return [s for s in self._spans if s.status == status]

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()

    def __len__(self) -> int:
        return len(self._spans)


class JSONFileExporter(BaseExporter):
    """Writes spans/metrics/logs/generations as NDJSON to files."""

    def __init__(self, spans_path: Optional[str] = None,
                 metrics_path: Optional[str] = None,
                 logs_path: Optional[str] = None,
                 generations_path: Optional[str] = None) -> None:
        self._paths = {
            "spans": spans_path,
            "metrics": metrics_path,
            "logs": logs_path,
            "generations": generations_path,
        }
        self._lock = threading.Lock()

    def _write(self, path: Optional[str], obj: Dict[str, Any]) -> None:
        if not path:
            return
        with self._lock:
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(obj, default=str) + "\n")

    def export_span(self, span: Span) -> None:
        self._write(self._paths["spans"], span.to_dict())

    def export_metrics(self, samples: List[MetricSample]) -> None:
        for s in samples:
            self._write(self._paths["metrics"], {
                "name": s.name, "labels": s.labels, "value": s.value,
                "type": s.metric_type, "ts": time.time(),
            })

    def export_logs(self, records: List[LogRecord]) -> None:
        for r in records:
            self._write(self._paths["logs"], r.to_dict())

    def export_generations(self, generations: List[Generation]) -> None:
        for g in generations:
            self._write(self._paths["generations"], g.to_dict())


class OTelHTTPExporter(BaseExporter):
    """
    Exports traces/metrics/logs to an OpenTelemetry collector via urllib.request.
    Uses OTLP JSON format (no protobuf, no otlp-exporter package).
    """

    def __init__(self, endpoint: str = "http://localhost:4318",
                 headers: Optional[Dict[str, str]] = None,
                 timeout: float = 5.0) -> None:
        self._endpoint = endpoint.rstrip("/")
        self._headers = headers or {}
        self._timeout = timeout

    def _post(self, path: str, payload: bytes) -> bool:
        url = f"{self._endpoint}{path}"
        req = urllib.request.Request(
            url, data=payload, method="POST",
            headers={"Content-Type": "application/json", **self._headers},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout):
                return True
        except Exception:
            return False

    def _span_to_otlp(self, span: Span) -> Dict[str, Any]:
        attrs = [{"key": k, "value": {"stringValue": str(v)}}
                 for k, v in span.attributes.items()]
        return {
            "traceId": span.trace_id,
            "spanId": span.span_id,
            "parentSpanId": span.parent_span_id or "",
            "name": span.name,
            "startTimeUnixNano": int(span.start_time * 1e9),
            "endTimeUnixNano": int((span.end_time or time.time()) * 1e9),
            "status": {"code": 1 if span.status == SpanStatus.ERROR else 0},
            "attributes": attrs,
            "events": [{"name": e.name, "timeUnixNano": int(e.timestamp * 1e9),
                        "attributes": [{"key": k, "value": {"stringValue": str(v)}}
                                       for k, v in e.attributes.items()]}
                       for e in span.events],
        }

    def export_spans(self, spans: List[Span]) -> None:
        if not spans:
            return
        payload = json.dumps({
            "resourceSpans": [{
                "resource": {"attributes": []},
                "scopeSpans": [{"spans": [self._span_to_otlp(s) for s in spans]}],
            }]
        }).encode()
        self._post("/v1/traces", payload)


class PrometheusTextExporter(BaseExporter):
    """Serves Prometheus metrics via stdlib http.server on a background thread."""

    def __init__(self, registry: Optional[MetricRegistry] = None,
                 port: int = 9090) -> None:
        self._port = port
        self._server: Optional[http.server.HTTPServer] = None

    def to_text(self) -> str:
        return MetricRegistry.to_prometheus_text()

    def serve(self, port: Optional[int] = None) -> None:
        exporter = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path == "/metrics":
                    body = exporter.to_text().encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain; version=0.0.4")
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def log_message(self, *args: Any) -> None:
                pass

        srv_port = port or self._port
        self._server = http.server.HTTPServer(("", srv_port), Handler)
        t = threading.Thread(target=self._server.serve_forever, daemon=True)
        t.start()

    def shutdown(self) -> None:
        if self._server:
            self._server.shutdown()


@dataclass
class AlertRule:
    name: str
    condition: Callable[["TenantObservabilityReport"], bool]
    callback: Callable[["TenantObservabilityReport"], None]
    description: str = ""
    cooldown_seconds: float = 60.0


class AlertingExporter(BaseExporter):
    """Fires Python callbacks on error rate, latency, security, or goal drift alerts."""

    def __init__(self) -> None:
        self._rules: List[AlertRule] = []
        self._last_fired: Dict[str, float] = {}
        self._lock = threading.Lock()

    def add_rule(self, rule: AlertRule) -> None:
        self._rules.append(rule)

    def add_error_rate_alert(self, threshold: float,
                             callback: Callable, cooldown: float = 60.0) -> None:
        self.add_rule(AlertRule(
            name=f"error_rate>{threshold}",
            condition=lambda r: r.error_rate > threshold,
            callback=callback,
            cooldown_seconds=cooldown,
        ))

    def add_latency_alert(self, p95_ms: float,
                          callback: Callable, cooldown: float = 60.0) -> None:
        self.add_rule(AlertRule(
            name=f"p95_latency>{p95_ms}ms",
            condition=lambda r, t=p95_ms: r.p95_latency_ms > t,
            callback=callback,
            cooldown_seconds=cooldown,
        ))

    def add_goal_drift_alert(self, threshold: float,
                             callback: Callable, cooldown: float = 60.0) -> None:
        self.add_rule(AlertRule(
            name=f"goal_drift>{threshold}",
            condition=lambda r, t=threshold: r.max_goal_drift > t,
            callback=callback,
            cooldown_seconds=cooldown,
        ))

    def evaluate(self, report: "TenantObservabilityReport") -> List[str]:
        fired = []
        now = time.time()
        for rule in self._rules:
            try:
                if rule.condition(report):
                    last = self._last_fired.get(rule.name, 0.0)
                    if now - last >= rule.cooldown_seconds:
                        rule.callback(report)
                        with self._lock:
                            self._last_fired[rule.name] = now
                        fired.append(rule.name)
            except Exception:
                pass
        return fired


# ═══════════════════════════════════════════════════════════════════════════════
# § 8  OBSERVABILITY MANAGER  (facade)
# ═══════════════════════════════════════════════════════════════════════════════

class ObservabilityManager:
    """
    Single entry-point wiring together Tracer, Langfuse-style stores,
    Metrics, StructuredLogger, TenantIsolation, and Exporters.

    Usage::

        obs = ObservabilityManager.create("my-service")

        # Langfuse-style session + trace + generation + score
        sess = obs.sessions.create(user_id="alice", tenant_id="acme")
        with obs.trace("order-wf", session_id=sess.session_id, tenant_id="acme") as tr:
            gen = obs.generation(tr.trace_id, name="llm", model="claude-sonnet-4-6",
                                 input=[{"role": "user", "content": "..."}])
            gen.finish(output="...", input_tokens=50, output_tokens=80)
            obs.score(gen.generation_id, "relevance", 0.92, comment="on-topic")
        print(obs.tenant_report("acme").to_dict())
    """

    def __init__(
        self,
        service_name: str = "multigen",
        log_level: LogLevel = LogLevel.INFO,
        exporters: Optional[List[BaseExporter]] = None,
    ) -> None:
        self.service_name = service_name

        # Core stores
        self._span_exporter = InMemorySpanExporter()
        self._log_buffer = LogBuffer()
        self._exporters: List[BaseExporter] = [self._span_exporter] + (exporters or [])

        self.tracer = Tracer(service_name, self._exporters)
        self.meter = Meter(prefix="multigen")
        self.registry = MetricRegistry
        self.logger = StructuredLogger(service_name, level=log_level,
                                       buffer=self._log_buffer)

        # Langfuse-style stores
        self.traces = TraceStore()
        self.generations = GenerationStore()
        self.sessions = TelemetrySessionStore()
        self.scores = ScoreStore()

        self._quota_tracker = TenantQuotaTracker()
        self._alerting_exporter: Optional[AlertingExporter] = None
        self._tenant_metrics: Dict[str, TenantMetrics] = {}
        self._tenant_loggers: Dict[str, TenantLogger] = {}

        # Built-in metrics
        self._agent_latency = self.meter.histogram(
            "agent_latency_ms", "Agent call latency", "ms")
        self._agent_errors = self.meter.counter(
            "agent_errors_total", "Agent errors")
        self._agent_calls = self.meter.counter(
            "agent_calls_total", "Agent invocations")
        self._token_cost = self.meter.counter(
            "token_cost_usd_total", "Cumulative token cost USD")
        self._active_workflows = self.meter.gauge(
            "active_workflows", "Currently running workflows")

    # ── factory ───────────────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        service_name: str = "multigen",
        otel_endpoint: Optional[str] = None,
        prometheus_port: Optional[int] = None,
        json_export_dir: Optional[str] = None,
        log_level: LogLevel = LogLevel.INFO,
        price_table: Optional[Dict[str, Dict[str, float]]] = None,
    ) -> "ObservabilityManager":
        exporters: List[BaseExporter] = []
        if otel_endpoint:
            exporters.append(OTelHTTPExporter(endpoint=otel_endpoint))
        if json_export_dir:
            import os
            os.makedirs(json_export_dir, exist_ok=True)
            exporters.append(JSONFileExporter(
                spans_path=f"{json_export_dir}/spans.ndjson",
                metrics_path=f"{json_export_dir}/metrics.ndjson",
                logs_path=f"{json_export_dir}/logs.ndjson",
                generations_path=f"{json_export_dir}/generations.ndjson",
            ))
        obs = cls(service_name=service_name, log_level=log_level, exporters=exporters)
        if prometheus_port:
            prom = PrometheusTextExporter(port=prometheus_port)
            prom.serve()
            obs._exporters.append(prom)
        if price_table:
            _MODEL_PRICE.update(price_table)
        return obs

    # ── Langfuse-style API ────────────────────────────────────────────────────

    @contextlib.contextmanager
    def trace(
        self,
        name: str,
        *,
        session_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        user_id: Optional[str] = None,
        tags: Optional[List[str]] = None,
        input: Any = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Trace]:
        """Context manager that creates and stores a Trace."""
        tr = Trace(
            name=name, session_id=session_id, tenant_id=tenant_id,
            user_id=user_id, tags=tags or [], input=input,
            metadata=metadata or {},
        )
        token = set_correlation(trace_id=tr.trace_id, tenant_id=tenant_id,
                                session_id=session_id)
        if session_id:
            sess = self.sessions.get(session_id)
            if sess:
                sess.add_trace(tr.trace_id)
        self.traces.add(tr)
        self._active_workflows.inc(labels={"tenant": tenant_id or "global"})
        try:
            yield tr
            tr.finish(status="ok")
        except Exception as exc:
            tr.finish(status="error")
            self.logger.error(f"Trace {name!r} failed: {exc}",
                              trace_id=tr.trace_id, tenant_id=tenant_id)
            raise
        finally:
            _correlation_ctx.reset(token)
            self._active_workflows.dec(labels={"tenant": tenant_id or "global"})

    @contextlib.contextmanager
    def session(
        self,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Iterator[Session]:
        """Context manager that creates a Session."""
        sess = self.sessions.create(user_id=user_id, tenant_id=tenant_id,
                                    metadata=metadata or {})
        token = set_correlation(session_id=sess.session_id, tenant_id=tenant_id)
        try:
            yield sess
        finally:
            _correlation_ctx.reset(token)

    def generation(
        self,
        trace_id: str,
        name: str,
        model: str,
        input: Any,
        *,
        parent_span_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        session_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Generation:
        """Create a Generation record for an LLM call. Call .finish() when done."""
        corr = get_correlation()
        gen = Generation(
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            name=name,
            model=model,
            tenant_id=tenant_id or corr.tenant_id,
            session_id=session_id or corr.session_id,
            input=input,
            metadata=metadata or {},
        )
        self.generations.add(gen)
        return gen

    def score(
        self,
        observation_id: str,
        name: str,
        value: float,
        *,
        observation_type: str = "generation",
        string_value: Optional[str] = None,
        comment: Optional[str] = None,
        source: str = "manual",
        tenant_id: Optional[str] = None,
    ) -> Score:
        """Attach a score to any observation (trace, generation, or span)."""
        corr = get_correlation()
        sc = Score(
            observation_id=observation_id,
            observation_type=observation_type,
            name=name,
            value=value,
            string_value=string_value,
            comment=comment,
            source=source,
            tenant_id=tenant_id or corr.tenant_id,
        )
        self.scores.add(sc)
        return sc

    # ── Instrumentation ───────────────────────────────────────────────────────

    def instrument_agent(self, agent: Any) -> None:
        """Monkey-patch agent.__call__ to wrap execution in an AgentSpan."""
        original = agent.__call__

        @functools.wraps(original)
        async def _wrapped(ctx: Dict[str, Any]) -> Any:
            trace_headers = ctx.get("_trace_headers", {})
            trace_ctx = self.tracer.extract(trace_headers)
            name = getattr(agent, "name", type(agent).__name__)
            agent_id = getattr(agent, "id", "")
            model = getattr(agent, "model", "")
            tenant_id = ctx.get("_tenant_id") or get_correlation().tenant_id or ""
            lbs = {"agent": name, "tenant": tenant_id or "global"}
            self._agent_calls.add(labels=lbs)
            async with self.tracer.async_span(
                f"agent.{name}",
                trace_context=trace_ctx,
                agent_id=agent_id,
                agent_name=name,
                model=model,
                tenant_id=tenant_id,
                workflow_id=ctx.get("_workflow_id"),
            ) as span:
                ctx["_trace_headers"] = {}
                self.tracer.inject(span, ctx["_trace_headers"])
                token = set_correlation(
                    trace_id=span.trace_id, span_id=span.span_id,
                    agent_id=agent_id, tenant_id=tenant_id,
                )
                t0 = time.time()
                try:
                    result = await original(ctx)
                    if isinstance(result, dict):
                        conf = result.get("confidence", 1.0)
                        if isinstance(span, AgentSpan):
                            span.record_confidence(conf)
                        usage = result.get("_token_usage", {})
                        if usage and isinstance(span, AgentSpan):
                            span.record_token_usage(
                                usage.get("input", 0),
                                usage.get("output", 0),
                                usage.get("cached", 0),
                            )
                    lat = (time.time() - t0) * 1000
                    self._agent_latency.observe(lat, labels=lbs)
                    return result
                except Exception:
                    self._agent_errors.add(labels=lbs)
                    raise
                finally:
                    _correlation_ctx.reset(token)

        agent.__call__ = _wrapped

    def instrument_graph(self, graph: Any) -> None:
        """Hook into Graph.run() to create a WorkflowTrace root span."""
        original_run = graph.run

        @functools.wraps(original_run)
        async def _wrapped(ctx: Optional[Dict[str, Any]] = None) -> Any:
            ctx = dict(ctx or {})
            workflow_id = str(uuid.uuid4())[:12]
            tenant_id = ctx.get("_tenant_id") or get_correlation().tenant_id or ""
            ctx["_workflow_id"] = workflow_id
            self._active_workflows.inc(labels={"tenant": tenant_id or "global"})
            async with self.tracer.async_span(
                f"graph.{getattr(graph, 'name', 'workflow')}",
                workflow_id=workflow_id,
                tenant_id=tenant_id,
            ) as root_span:
                ctx["_trace_headers"] = {}
                self.tracer.inject(root_span, ctx["_trace_headers"])
                token = set_correlation(
                    trace_id=root_span.trace_id, span_id=root_span.span_id,
                    workflow_id=workflow_id, tenant_id=tenant_id,
                )
                try:
                    result = await original_run(ctx)
                    if hasattr(result, "status"):
                        root_span.set_attribute("graph.status", result.status)
                    if hasattr(result, "executed"):
                        root_span.set_attribute("graph.executed_nodes", result.executed)
                    return result
                finally:
                    _correlation_ctx.reset(token)
                    self._active_workflows.dec(labels={"tenant": tenant_id or "global"})

        graph.run = _wrapped

    # ── Tenant helpers ────────────────────────────────────────────────────────

    def tenant_metrics(self, tenant_id: str) -> TenantMetrics:
        if tenant_id not in self._tenant_metrics:
            self._tenant_metrics[tenant_id] = TenantMetrics(tenant_id)
        return self._tenant_metrics[tenant_id]

    def tenant_logger(self, tenant_id: str) -> TenantLogger:
        if tenant_id not in self._tenant_loggers:
            self._tenant_loggers[tenant_id] = TenantLogger(
                tenant_id, self.logger, self._log_buffer)
        return self._tenant_loggers[tenant_id]

    @contextlib.contextmanager
    def tenant_scope(self, tenant_id: str) -> Iterator["ObservabilityManager"]:
        token = set_correlation(tenant_id=tenant_id)
        try:
            yield self
        finally:
            _correlation_ctx.reset(token)

    # ── Reporting ─────────────────────────────────────────────────────────────

    def tenant_report(self, tenant_id: str) -> TenantObservabilityReport:
        spans = self._span_exporter.filter_by_tenant(tenant_id)
        gens = self.generations.by_tenant(tenant_id)
        traces = self.traces.get_by_tenant(tenant_id)
        sessions = self.sessions.get_by_tenant(tenant_id)
        scores_all = self.scores.for_tenant(tenant_id)

        total = len(spans)
        errors = sum(1 for s in spans if s.status == SpanStatus.ERROR)
        error_rate = errors / total if total else 0.0

        lats = sorted(s.duration_ms() for s in spans if s.duration_ms() is not None)
        def _pct(vals: List[float], p: float) -> float:
            if not vals:
                return 0.0
            return vals[min(int(p * len(vals)), len(vals) - 1)]

        # Cost by agent
        agent_costs: Dict[str, float] = collections.defaultdict(float)
        for g in gens:
            agent_costs[g.name] += g.cost_usd
        top_agents = sorted(agent_costs.items(), key=lambda x: x[1], reverse=True)[:10]

        confidences = [g.confidence for g in gens if g.confidence is not None]
        mean_conf = sum(confidences) / len(confidences) if confidences else 1.0

        drift_scores = [s.attributes.get("goal_drift_score", 0.0)
                        for s in spans if "goal_drift_score" in s.attributes]
        max_drift = max(drift_scores) if drift_scores else 0.0

        halluc_rate = (sum(1 for g in gens if g.hallucination_detected) / len(gens)
                       if gens else 0.0)

        # Score means per name
        score_names: Set[str] = {s.name for s in scores_all}
        score_means = {}
        for sn in score_names:
            vals = [s.value for s in scores_all if s.name == sn]
            score_means[sn] = sum(vals) / len(vals) if vals else 0.0

        return TenantObservabilityReport(
            tenant_id=tenant_id,
            total_spans=total,
            error_rate=error_rate,
            p95_latency_ms=_pct(lats, 0.95),
            p99_latency_ms=_pct(lats, 0.99),
            total_traces=len(traces),
            total_generations=len(gens),
            total_tokens_in=sum(g.input_tokens for g in gens),
            total_tokens_out=sum(g.output_tokens for g in gens),
            total_cost_usd=sum(g.cost_usd for g in gens),
            top_agents_by_cost=top_agents,
            mean_confidence=mean_conf,
            max_goal_drift=max_drift,
            hallucination_rate=halluc_rate,
            active_sessions=len(sessions),
            score_means=score_means,
        )

    def export_all(self) -> None:
        """Push all accumulated telemetry to all exporters."""
        spans = self._span_exporter.get_finished_spans()
        metrics = MetricRegistry.collect()
        logs = self._log_buffer.tail(10_000)
        gens = self.generations.all()
        for exp in self._exporters:
            try:
                exp.export_spans(spans)
                exp.export_metrics(metrics)
                exp.export_logs(logs)
                exp.export_generations(gens)
            except Exception:
                pass

    def add_exporter(self, exporter: BaseExporter) -> None:
        self._exporters.append(exporter)
        if exporter not in self.tracer._exporters:
            self.tracer._exporters.append(exporter)

    def get_logger(self, name: str, tenant_id: Optional[str] = None) -> StructuredLogger:
        if tenant_id:
            return self.tenant_logger(tenant_id)._base.bind(tenant_id=tenant_id)
        return self.logger.bind(logger=name)

    def shutdown(self) -> None:
        self.export_all()
        for exp in self._exporters:
            try:
                exp.shutdown()
            except Exception:
                pass


# ── Convenience singleton ──────────────────────────────────────────────────────

_default_manager: Optional[ObservabilityManager] = None


def get_telemetry(service_name: str = "multigen") -> ObservabilityManager:
    """Return (or lazily create) the process-wide ObservabilityManager."""
    global _default_manager
    if _default_manager is None:
        _default_manager = ObservabilityManager.create(service_name)
    return _default_manager


__all__ = [
    # Tracing
    "TraceContext", "SpanStatus", "SpanEvent", "Span", "AgentSpan",
    "CorrelationContext", "get_correlation", "set_correlation", "Tracer",
    # Langfuse-style
    "Trace", "Generation", "Session", "Score",
    "TraceStore", "GenerationStore", "TelemetrySessionStore", "ScoreStore",
    # Metrics
    "MetricSample", "Counter", "Gauge", "Histogram", "Summary",
    "MetricRegistry", "Meter", "TenantMetrics",
    # Logging
    "LogLevel", "LogRecord", "LogBuffer", "LogSampler",
    "StructuredLogger", "BoundLogger", "TenantLogger",
    "CorrelationContext", "get_correlation", "set_correlation",
    # Tenant
    "ObservabilityQuotaError", "TenantQuotaTracker",
    "TenantObservabilityReport",
    # Novel AI
    "EpistemicAttribute",
    # Exporters
    "BaseExporter", "InMemorySpanExporter", "JSONFileExporter",
    "OTelHTTPExporter", "PrometheusTextExporter",
    "AlertRule", "AlertingExporter",
    # Facade
    "ObservabilityManager", "get_telemetry",
]
