"""Lightweight metrics helpers stored in context scratch."""
from __future__ import annotations

from typing import Any, Dict, MutableMapping, Tuple


def _bucket(metrics: MutableMapping[str, Any]) -> Dict[str, Any]:
    return metrics.setdefault("_metrics", {"counters": {}, "latency": {}})


def record_counter(metrics: MutableMapping[str, Any], name: str, value: float = 1.0, **labels: Any) -> None:
    """Increment a counter in scratch metrics."""

    bucket = _bucket(metrics)["counters"]
    key: Tuple[str, Tuple[Tuple[str, Any], ...]] = (name, tuple(sorted(labels.items())))
    bucket[key] = bucket.get(key, 0.0) + value


def record_latency(metrics: MutableMapping[str, Any], name: str, value: float, **labels: Any) -> None:
    """Record latency aggregates (sum/count/min/max)."""

    bucket = _bucket(metrics)["latency"]
    key: Tuple[str, Tuple[Tuple[str, Any], ...]] = (name, tuple(sorted(labels.items())))
    agg = bucket.get(key, {"count": 0, "total": 0.0, "min": value, "max": value})
    agg["count"] += 1
    agg["total"] += value
    agg["min"] = min(agg["min"], value)
    agg["max"] = max(agg["max"], value)
    bucket[key] = agg


__all__ = ["record_counter", "record_latency"]
