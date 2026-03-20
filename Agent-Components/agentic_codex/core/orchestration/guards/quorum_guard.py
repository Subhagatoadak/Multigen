"""Guard that waits for quorum."""
from __future__ import annotations

from typing import Mapping

from ...interfaces import GuardResult


class QuorumGuard:
    name = "quorum_guard"

    def __init__(self, threshold: int) -> None:
        self.threshold = threshold

    def check(self, state: Mapping[str, object]) -> GuardResult:
        votes = int(state.get("votes", 0))
        if votes >= self.threshold:
            return GuardResult(ok=True, votes=votes)
        return GuardResult(ok=False, reason="insufficient quorum", votes=votes)
