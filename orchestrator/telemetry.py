"""
OpenTelemetry setup for Multigen services.

Reads configuration from environment variables:
  OTEL_SERVICE_NAME              — service name (default: "multigen")
  OTEL_EXPORTER_OTLP_ENDPOINT   — OTLP collector endpoint, e.g. http://jaeger:4317
                                   If unset, a console exporter is used (dev mode).

Usage:
    from orchestrator.telemetry import setup_tracing, get_tracer
    setup_tracing()
    tracer = get_tracer(__name__)

    with tracer.start_as_current_span("my-operation") as span:
        span.set_attribute("key", "value")
        ...
"""
import logging
import os

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

logger = logging.getLogger(__name__)

_initialized = False


def setup_tracing() -> None:
    """
    Configure and register the global TracerProvider.
    Safe to call multiple times — only initializes once.
    """
    global _initialized
    if _initialized:
        return

    service_name = os.getenv("OTEL_SERVICE_NAME", "multigen")
    otlp_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")

    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)

    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
            logger.info("OpenTelemetry: exporting traces to %s", otlp_endpoint)
        except ImportError:
            logger.warning(
                "opentelemetry-exporter-otlp-proto-grpc not installed; "
                "falling back to console exporter."
            )
            exporter = ConsoleSpanExporter()
    else:
        exporter = ConsoleSpanExporter()
        logger.info("OpenTelemetry: OTEL_EXPORTER_OTLP_ENDPOINT not set, using console exporter.")

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _initialized = True


def get_tracer(name: str) -> trace.Tracer:
    """Return a tracer for the given module/component name."""
    return trace.get_tracer(name)
