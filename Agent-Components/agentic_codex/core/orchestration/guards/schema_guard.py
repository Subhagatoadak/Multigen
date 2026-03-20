"""Schema guard ensures messages follow expected keys."""
from __future__ import annotations

from typing import Mapping, Sequence

from ...interfaces import GuardResult


class SchemaGuard:
    name = "schema_guard"

    def __init__(self, required_keys: Sequence[str]) -> None:
        self.required_keys = tuple(required_keys)

    def check(self, state: Mapping[str, object]) -> GuardResult:
        messages = state.get("messages", [])
        for message in messages:
            for key in self.required_keys:
                if key not in getattr(message, "meta", {}):
                    return GuardResult(ok=False, reason=f"missing key {key}")
        return GuardResult(ok=True)
