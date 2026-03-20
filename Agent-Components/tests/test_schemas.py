from __future__ import annotations

import pytest

from agentic_codex.core.schemas import Message
from agentic_codex.manifests.validators import ValidationError


def test_message_requires_string_role():
    with pytest.raises((TypeError, ValidationError, ValueError)):
        # Depending on the installed pydantic version a distinct error type may
        # bubble up.  All indicate validation has occurred.
        Message(role=123, content="invalid")  # type: ignore[arg-type]


def test_message_defaults_are_isolated():
    first = Message(role="user", content="hi")
    second = Message(role="user", content="hi")
    assert first.meta is not second.meta
