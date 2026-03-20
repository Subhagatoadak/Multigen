"""Declarative policy routing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping


@dataclass
class PolicyDecision:
    action: str
    reason: str
    meta: Dict[str, Any]


class PolicyEngine:
    def __init__(self, policies: Mapping[str, Mapping[str, Any]] | None = None) -> None:
        self.policies = policies or {}

    def evaluate(self, context: Mapping[str, Any]) -> PolicyDecision:
        for name, policy in self.policies.items():
            if policy.get("condition") in context.get("tags", []):
                return PolicyDecision(action=policy.get("action", "allow"), reason=name, meta=dict(policy))
        return PolicyDecision(action="allow", reason="default", meta={})
