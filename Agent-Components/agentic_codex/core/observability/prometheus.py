"""Minimal Prometheus text exposition."""
from __future__ import annotations

from typing import Mapping


def render_metrics(metrics: Mapping[str, Mapping]) -> str:
    """Render metrics stored in context.scratch['_metrics'] as Prometheus text."""

    lines = []
    counters = metrics.get("counters", {})
    for (name, labels), value in counters.items():
        label_str = ",".join(f'{k}="{v}"' for k, v in labels)
        lines.append(f"{name}{{{label_str}}} {value}")
    hist = metrics.get("latency", {})
    for (name, labels), agg in hist.items():
        label_str = ",".join(f'{k}="{v}"' for k, v in labels)
        avg = agg["total"] / agg["count"] if agg["count"] else 0.0
        lines.append(f"{name}_count{{{label_str}}} {agg['count']}")
        lines.append(f"{name}_sum{{{label_str}}} {agg['total']}")
        lines.append(f"{name}_avg{{{label_str}}} {avg}")
        lines.append(f"{name}_min{{{label_str}}} {agg['min']}")
        lines.append(f"{name}_max{{{label_str}}} {agg['max']}")
    return "\n".join(lines) + "\n"


__all__ = ["render_metrics"]
