"""Cancellation primitives for orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class CancelToken:
    """Cooperative cancellation token."""

    reason: Optional[str] = None
    _cancelled: bool = False

    def cancel(self, reason: Optional[str] = None) -> None:
        self._cancelled = True
        if reason:
            self.reason = reason

    @property
    def cancelled(self) -> bool:
        return self._cancelled


__all__ = ["CancelToken"]
