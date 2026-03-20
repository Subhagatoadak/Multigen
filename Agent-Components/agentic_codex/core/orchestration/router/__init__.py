"""Routing policies for agent dispatch."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence


@dataclass
class RouteDecision:
    target: str
    reason: str
    meta: Mapping[str, Any]


class PolicyRouter:
    """Route payloads according to ordered rules or ML scorers."""

    def __init__(
        self,
        *,
        rules: Sequence[tuple[str, Callable[[Mapping[str, Any]], bool], str]],
        default: str,
    ) -> None:
        self.rules = list(rules)
        self.default = default

    def route(self, payload: Mapping[str, Any]) -> RouteDecision:
        for name, predicate, destination in self.rules:
            try:
                if predicate(payload):
                    return RouteDecision(target=destination, reason=name, meta=dict(payload))
            except Exception as exc:  # pragma: no cover - defensive
                return RouteDecision(target=self.default, reason=f"{name}:error", meta={"error": str(exc)})
        return RouteDecision(target=self.default, reason="default", meta=dict(payload))


def rule_based(route_table: Mapping[str, str], payload: Mapping[str, Any]) -> str:
    """Compatibility helper using a simple tag-based routing table."""

    for rule, target in route_table.items():
        if rule == "default":
            continue
        if rule in payload.get("tags", []):
            return target
    return route_table.get("default", "default")


__all__ = ["PolicyRouter", "RouteDecision", "rule_based"]
