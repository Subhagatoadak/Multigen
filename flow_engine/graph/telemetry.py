"""
Graph engine telemetry — OpenTelemetry spans + optional Prometheus metrics.

Design
------
Every graph node execution is wrapped in an OTel span with attributes:
  graph.workflow_id, graph.node_id, graph.agent, graph.iteration,
  graph.circuit_state, graph.confidence, graph.reflected

Prometheus metrics (optional — falls back gracefully if not installed):
  multigen_graph_nodes_total          counter  (workflow_id, node_id, status)
  multigen_graph_node_duration_seconds histogram (node_id)
  multigen_graph_reflections_total    counter  (node_id)
  multigen_graph_circuit_open_total   counter  (node_id)
  multigen_graph_fan_out_total        counter  (workflow_id)
  multigen_graph_errors_total         counter  (workflow_id, node_id)

Usage inside engine.py:
    from flow_engine.graph.telemetry import GraphTelemetry
    tel = GraphTelemetry(workflow_id)
    with tel.node_span(node_id, agent, iteration, circuit_state) as span:
        ...
        tel.record_node_complete(node_id, duration_s, confidence, status="success")
"""
from __future__ import annotations

import contextlib
from typing import Any, Generator

from opentelemetry import trace

_tracer = trace.get_tracer("multigen.graph")

# ── Prometheus (optional) ──────────────────────────────────────────────────────
try:
    from prometheus_client import Counter, Histogram

    _NODES_TOTAL = Counter(
        "multigen_graph_nodes_total",
        "Total graph node executions",
        ["workflow_id", "node_id", "status"],
    )
    _NODE_DURATION = Histogram(
        "multigen_graph_node_duration_seconds",
        "Graph node execution duration",
        ["node_id"],
        buckets=[0.1, 0.5, 1, 2, 5, 10, 30, 60],
    )
    _REFLECTIONS = Counter(
        "multigen_graph_reflections_total",
        "Total reflection loops triggered",
        ["node_id"],
    )
    _CB_OPEN = Counter(
        "multigen_graph_circuit_open_total",
        "Times a node circuit breaker tripped open",
        ["node_id"],
    )
    _FAN_OUT = Counter(
        "multigen_graph_fan_out_total",
        "Total fan-out groups executed",
        ["workflow_id"],
    )
    _ERRORS = Counter(
        "multigen_graph_errors_total",
        "Total node errors (activity errors + CB trips)",
        ["workflow_id", "node_id"],
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False


class GraphTelemetry:
    """Per-workflow telemetry handle — cheap to construct, call from engine."""

    def __init__(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id

    # ── OTel span context manager ──────────────────────────────────────────────

    @contextlib.contextmanager
    def node_span(
        self,
        node_id: str,
        agent: str,
        iteration: int,
        circuit_state: str = "closed",
    ) -> Generator[trace.Span, None, None]:
        """
        Context manager that wraps a single node execution in an OTel span.
        Records the exception and re-raises if one occurs.
        """
        with _tracer.start_as_current_span(f"graph.node.{node_id}") as span:
            span.set_attribute("graph.workflow_id", self.workflow_id)
            span.set_attribute("graph.node_id", node_id)
            span.set_attribute("graph.agent", agent)
            span.set_attribute("graph.iteration", iteration)
            span.set_attribute("graph.circuit_state", circuit_state)
            try:
                yield span
            except Exception as exc:
                span.record_exception(exc)
                raise

    # ── Metric recording helpers ───────────────────────────────────────────────

    def record_node_complete(
        self,
        node_id: str,
        duration_s: float,
        confidence: float,
        status: str = "success",
    ) -> None:
        if not _PROMETHEUS_AVAILABLE:
            return
        _NODES_TOTAL.labels(
            workflow_id=self.workflow_id,
            node_id=node_id,
            status=status,
        ).inc()
        _NODE_DURATION.labels(node_id=node_id).observe(duration_s)

    def record_reflection(self, node_id: str) -> None:
        if not _PROMETHEUS_AVAILABLE:
            return
        _REFLECTIONS.labels(node_id=node_id).inc()

    def record_circuit_open(self, node_id: str) -> None:
        if not _PROMETHEUS_AVAILABLE:
            return
        _CB_OPEN.labels(node_id=node_id).inc()
        _ERRORS.labels(workflow_id=self.workflow_id, node_id=node_id).inc()

    def record_fan_out(self) -> None:
        if not _PROMETHEUS_AVAILABLE:
            return
        _FAN_OUT.labels(workflow_id=self.workflow_id).inc()

    def record_error(self, node_id: str) -> None:
        if not _PROMETHEUS_AVAILABLE:
            return
        _ERRORS.labels(workflow_id=self.workflow_id, node_id=node_id).inc()

    def annotate_span(self, key: str, value: Any) -> None:
        """Set an attribute on the current active OTel span if one exists."""
        span = trace.get_current_span()
        if span and span.is_recording():
            span.set_attribute(key, str(value))
