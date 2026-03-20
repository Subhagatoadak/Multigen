"""Utility for replaying stored runs."""
from __future__ import annotations

from typing import Iterable

from ..schemas import Message


def replay(messages: Iterable[Message]) -> list[str]:
    return [message.content for message in messages]
