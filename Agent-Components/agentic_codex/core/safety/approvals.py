"""Simple approval workflow hooks."""
from __future__ import annotations

from typing import Callable, Mapping


class ApprovalGate:
    def __init__(self, approver: Callable[[Mapping[str, object]], bool]) -> None:
        self.approver = approver

    def check(self, payload: Mapping[str, object]) -> bool:
        return bool(self.approver(payload))
