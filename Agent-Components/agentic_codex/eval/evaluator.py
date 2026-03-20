"""Simple evaluator stub."""
from __future__ import annotations

from typing import Dict

from ..core.schemas import RunResult


class BasicEvaluator:
    """Scores runs based on message count and final content length."""

    def score(self, run: RunResult) -> Dict[str, float]:
        final = run.messages[-1].content if run.messages else ""
        return {
            "message_count": float(len(run.messages)),
            "final_length": float(len(final)),
        }


__all__ = ["BasicEvaluator"]
