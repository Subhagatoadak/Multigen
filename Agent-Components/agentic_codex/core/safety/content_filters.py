"""Heuristic content filters."""
from __future__ import annotations

BLOCKLIST = {"hack", "exploit"}


def is_allowed(text: str) -> bool:
    lowered = text.lower()
    return not any(term in lowered for term in BLOCKLIST)
