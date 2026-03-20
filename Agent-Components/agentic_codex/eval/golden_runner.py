"""Replay runner for golden traces."""
from __future__ import annotations

from typing import Iterable

from ..core.schemas import Message, RunResult


def replay_golden(messages: Iterable[Message]) -> RunResult:
    return RunResult(messages=list(messages))


__all__ = ["replay_golden"]
