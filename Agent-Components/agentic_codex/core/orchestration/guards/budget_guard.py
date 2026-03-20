"""Budget guard enforces call limits."""
from __future__ import annotations

from typing import Mapping

from ...interfaces import GuardResult


class BudgetGuard:
    name = "budget_guard"

    def __init__(self, max_cost: float) -> None:
        self.max_cost = max_cost

    def check(self, state: Mapping[str, object]) -> GuardResult:
        spent = float(state.get("cost", 0))
        if spent > self.max_cost:
            return GuardResult(ok=False, reason="budget exceeded", spent=spent)
        return GuardResult(ok=True, spent=spent)
