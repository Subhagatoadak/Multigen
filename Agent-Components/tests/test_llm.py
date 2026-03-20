from __future__ import annotations

from agentic_codex.core.llm import FunctionAdapter
from agentic_codex.core.schemas import Message


def test_function_adapter_chat():
    adapter = FunctionAdapter(lambda prompt: prompt.upper())
    response = adapter.chat([Message(role="user", content="hello")])
    assert response.content.endswith("HELLO")
