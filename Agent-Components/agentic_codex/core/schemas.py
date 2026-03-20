"""Core schema definitions for :mod:`agentic_codex`.

The public data structures are implemented with :mod:`pydantic` models so
that every interaction surface benefits from runtime validation and
developer friendly error messages.  The models intentionally mirror the
previous dataclass-based API while providing strict typing and defensive
defaults.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional

from pydantic import BaseModel, Field

try:  # pragma: no cover - optional import path for pydantic v2
    from pydantic import ConfigDict  # type: ignore
except ImportError:  # pragma: no cover - pydantic v1
    ConfigDict = None


if ConfigDict is not None:  # pragma: no branch - resolved at import time

    class FrozenModel(BaseModel):
        """Base model with immutable, strict configuration (pydantic v2)."""

        model_config = ConfigDict(extra="forbid", frozen=True)


else:  # pragma: no cover - exercised when pydantic v1 is installed

    class FrozenModel(BaseModel):
        """Base model with immutable, strict configuration (pydantic v1)."""

        class Config:
            extra = "forbid"
            allow_mutation = False


class ToolCall(FrozenModel):
    """Describe a tool call emitted by an agent."""

    name: str
    args: Mapping[str, Any]


class Message(FrozenModel):
    """Atomic unit of communication between participants."""

    role: str
    content: str
    meta: Mapping[str, Any] = Field(default_factory=dict)
    tool_calls: Optional[Iterable[ToolCall]] = None


class AgentStep(FrozenModel):
    """Return value of :pyfunc:`agentic_codex.core.agent.Agent.step`."""

    out_messages: List[Message]
    state_updates: Dict[str, Any] = Field(default_factory=dict)
    stop: bool = False


class RunEvent(FrozenModel):
    """Structured trace entry emitted during orchestration."""

    ts: datetime
    kind: str
    payload: Mapping[str, Any]


class Artifact(FrozenModel):
    """Pointer to artefacts generated during a run (files, URLs, etc.)."""

    uri: str
    mime: str
    meta: Mapping[str, Any] = Field(default_factory=dict)


class RunResult(FrozenModel):
    """The final outcome of a coordinator run."""

    messages: List[Message]
    meta: Dict[str, Any] = Field(default_factory=dict)
    events: List[RunEvent] = Field(default_factory=list)
    artifacts: List[Artifact] = Field(default_factory=list)
    run_id: Optional[str] = None


__all__ = [
    "ToolCall",
    "Message",
    "AgentStep",
    "RunEvent",
    "RunResult",
    "Artifact",
]
