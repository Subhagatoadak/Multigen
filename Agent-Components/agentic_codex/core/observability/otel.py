"""Optional OpenTelemetry exporter for spans and metrics."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Mapping


@dataclass
class OtelExporter:
    """Send spans and metrics to OTLP if OpenTelemetry is installed.

    This stays no-op if the optional dependency is missing.
    """

    service_name: str = "agentic-codex"
    endpoint: str | None = None
    enabled: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        try:  # pragma: no cover - optional dependency path
            from opentelemetry import metrics, trace
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.metrics import MeterProvider
            from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except Exception:
            self.enabled = False
            return

        resource = Resource.create({"service.name": self.service_name})

        span_exporter = OTLPSpanExporter(endpoint=self.endpoint) if self.endpoint else OTLPSpanExporter()
        tracer_provider = TracerProvider(resource=resource)
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        trace.set_tracer_provider(tracer_provider)
        self._tracer = trace.get_tracer(self.service_name)

        metric_exporter = OTLPMetricExporter(endpoint=self.endpoint) if self.endpoint else OTLPMetricExporter()
        reader = PeriodicExportingMetricReader(metric_exporter)
        meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
        metrics.set_meter_provider(meter_provider)
        self._meter = metrics.get_meter(self.service_name)
        self._counters: Dict[str, Any] = {}
        self._histograms: Dict[str, Any] = {}
        self.enabled = True

    def record_span(
        self,
        name: str,
        *,
        status: str,
        attributes: Mapping[str, Any] | None = None,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        if not self.enabled:
            return
        span = self._tracer.start_span(name, attributes=dict(attributes or {}), start_time=start_time)
        span.set_attribute("status", status)
        span.end(end_time=end_time)

    def record_metric(self, name: str, value: float, **attributes: Any) -> None:
        if not self.enabled:
            return
        counter = self._counters.get(name)
        if counter is None:
            counter = self._meter.create_counter(name)
            self._counters[name] = counter
        counter.add(value, attributes=attributes)

    def record_latency(self, name: str, value: float, **attributes: Any) -> None:
        if not self.enabled:
            return
        hist = self._histograms.get(name)
        if hist is None:
            hist = self._meter.create_histogram(name)
            self._histograms[name] = hist
        hist.record(value, attributes=attributes)


__all__ = ["OtelExporter"]
