"""Guard limiting coordinator hops."""
from __future__ import annotations

from typing import Mapping

from ...interfaces import GuardResult


class HopsGuard:
    name = "hops_guard"

    def __init__(self, max_hops: int) -> None:
        self.max_hops = max_hops

    def check(self, state: Mapping[str, object]) -> GuardResult:
        hops = int(state.get("hops", 0))
        if hops > self.max_hops:
            return GuardResult(ok=False, reason="max hops exceeded", hops=hops)
        return GuardResult(ok=True, hops=hops)
