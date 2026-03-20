"""Simple rate limiter utilities (token bucket)."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Dict, Tuple


@dataclass
class RateLimiter:
    """Token bucket rate limiter."""

    rate_per_sec: float
    capacity: int
    _tokens: float = 0.0
    _last: float = 0.0

    def allow(self) -> bool:
        now = time.time()
        if self._last == 0.0:
            self._last = now
            self._tokens = self.capacity
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_sec)
        if self._tokens >= 1.0:
            self._tokens -= 1.0
            return True
        return False


class MultiLimiter:
    """Manage multiple rate limiters keyed by namespace/agent/tool."""

    def __init__(self) -> None:
        self._limits: Dict[Tuple[str, str], RateLimiter] = {}

    def set_limit(self, kind: str, name: str, limiter: RateLimiter) -> None:
        self._limits[(kind, name)] = limiter

    def allow(self, kind: str, name: str) -> bool:
        limiter = self._limits.get((kind, name))
        return limiter.allow() if limiter else True

    def configure(self, *, namespaces=None, agents=None, tools=None) -> None:
        for name, cfg in (namespaces or {}).items():
            self.set_limit("namespace", name, RateLimiter(rate_per_sec=cfg["rate"], capacity=cfg["capacity"]))
        for name, cfg in (agents or {}).items():
            self.set_limit("agent", name, RateLimiter(rate_per_sec=cfg["rate"], capacity=cfg["capacity"]))
        for name, cfg in (tools or {}).items():
            self.set_limit("tool", name, RateLimiter(rate_per_sec=cfg["rate"], capacity=cfg["capacity"]))


__all__ = ["RateLimiter", "MultiLimiter"]
