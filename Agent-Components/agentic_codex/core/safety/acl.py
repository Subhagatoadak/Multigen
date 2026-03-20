"""Access control lists for skills."""
from __future__ import annotations

from typing import Iterable, Mapping


class ACL:
    def __init__(self, rules: Mapping[str, Iterable[str]] | None = None) -> None:
        self._rules = {agent: set(skills) for agent, skills in (rules or {}).items()}

    def is_allowed(self, agent: str, skill: str) -> bool:
        allowed = self._rules.get(agent)
        return allowed is None or skill in allowed
