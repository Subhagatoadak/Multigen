"""Tracing helpers."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

try:  # Python 3.11+
    from datetime import UTC  # type: ignore
except ImportError:  # Python 3.10 fallback
    UTC = timezone.utc

from ..schemas import RunEvent
from .otel import OtelExporter


@dataclass
class Span:
    name: str
    start: datetime
    metadata: Dict[str, Any]


class Tracer:
    def __init__(self, run_id: str | None = None, exporter: Optional[OtelExporter] = None) -> None:
        self._events: List[RunEvent] = []
        self._run_id = run_id
        self._exporter = exporter

    def set_run_id(self, run_id: str) -> None:
        """Attach a run identifier so all events can be correlated."""

        self._run_id = run_id

    def _with_run_id(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if self._run_id:
            payload = {**payload, "run_id": self._run_id}
        return payload

    @contextmanager
    def span(self, name: str, **metadata: Any) -> Iterator[None]:
        start = datetime.now(UTC)
        try:
            yield
            status = "ok"
        except Exception:
            status = "error"
            raise
        finally:
            end = datetime.now(UTC)
            payload = {"name": name, "status": status, **metadata, "duration": (end - start).total_seconds()}
            event = RunEvent(ts=end, kind="span", payload=self._with_run_id(payload))
            self._events.append(event)
            if self._exporter:
                self._exporter.record_span(
                    name=name,
                    status=status,
                    attributes=self._with_run_id(metadata),
                    start_time=start,
                    end_time=end,
                )

    @property
    def events(self) -> List[RunEvent]:
        return list(self._events)

    def metric(self, name: str, value: float, **metadata: Any) -> None:
        """Record a metric event."""

        payload = {"name": name, "value": value, **metadata}
        event = RunEvent(ts=datetime.now(UTC), kind="metric", payload=self._with_run_id(payload))
        self._events.append(event)
        if self._exporter:
            self._exporter.record_metric(name, value, **self._with_run_id(metadata))

    def log(self, kind: str, **payload: Any) -> None:
        """Capture an arbitrary breadcrumb for monitoring dashboards."""

        event = RunEvent(ts=datetime.now(UTC), kind=kind, payload=self._with_run_id(payload))
        self._events.append(event)
