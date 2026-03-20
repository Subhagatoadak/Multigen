"""Redaction utilities."""
from __future__ import annotations

import re


PII_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    re.compile(r"\b\d{16}\b"),
]


def redact(text: str, replacement: str = "[REDACTED]") -> str:
    for pattern in PII_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def contains_sensitive(text: str) -> bool:
    return any(pattern.search(text) for pattern in PII_PATTERNS)
