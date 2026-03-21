"""
Agent2Agent (A2A) Protocol Models.

Implements the open Agent2Agent protocol spec for interoperability
between Multigen and any other A2A-compliant agent framework
(LangGraph, CrewAI, AutoGen, Vertex AI, etc.).

Spec reference: https://google.github.io/A2A/
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field


# ── Message parts ─────────────────────────────────────────────────────────────

class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str
    metadata: Optional[Dict[str, Any]] = None


class DataPart(BaseModel):
    type: Literal["data"] = "data"
    data: Dict[str, Any]
    metadata: Optional[Dict[str, Any]] = None


class FilePart(BaseModel):
    type: Literal["file"] = "file"
    file: Dict[str, Any]   # {name, mimeType, bytes (base64) or uri}
    metadata: Optional[Dict[str, Any]] = None


Part = Union[TextPart, DataPart, FilePart]


# ── Message ────────────────────────────────────────────────────────────────────

class Message(BaseModel):
    role: Literal["user", "agent"]
    parts: List[Part]
    metadata: Optional[Dict[str, Any]] = None


# ── Task status ────────────────────────────────────────────────────────────────

class TaskState(str, Enum):
    SUBMITTED   = "submitted"
    WORKING     = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED   = "completed"
    FAILED      = "failed"
    CANCELLED   = "cancelled"


class TaskStatus(BaseModel):
    state: TaskState
    message: Optional[Message] = None
    timestamp: Optional[str] = None


# ── Artifact ───────────────────────────────────────────────────────────────────

class Artifact(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    parts: List[Part]
    index: int = 0
    append: bool = False
    last_chunk: bool = True
    metadata: Optional[Dict[str, Any]] = None


# ── Task ───────────────────────────────────────────────────────────────────────

class Task(BaseModel):
    id: str
    session_id: Optional[str] = None
    status: TaskStatus
    artifacts: Optional[List[Artifact]] = None
    history: Optional[List[Message]] = None
    metadata: Optional[Dict[str, Any]] = None


# ── JSON-RPC 2.0 ───────────────────────────────────────────────────────────────

class JSONRPCRequest(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: Union[str, int, None] = None
    method: str
    params: Optional[Dict[str, Any]] = None


class JSONRPCError(BaseModel):
    code: int
    message: str
    data: Optional[Any] = None


class JSONRPCResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: Union[str, int, None] = None
    result: Optional[Any] = None
    error: Optional[JSONRPCError] = None


# ── tasks/send params ──────────────────────────────────────────────────────────

class TaskSendParams(BaseModel):
    id: str
    session_id: Optional[str] = None
    message: Message
    history_length: Optional[int] = None
    push_notification: Optional[Dict[str, Any]] = None
    metadata: Optional[Dict[str, Any]] = None


class TaskQueryParams(BaseModel):
    id: str
    history_length: Optional[int] = None


class TaskCancelParams(BaseModel):
    id: str


# ── Agent Card ─────────────────────────────────────────────────────────────────

class AgentSkill(BaseModel):
    id: str
    name: str
    description: str
    tags: List[str] = Field(default_factory=list)
    examples: List[str] = Field(default_factory=list)
    input_modes: List[str] = Field(default=["text", "data"])
    output_modes: List[str] = Field(default=["text", "data"])


class AgentCapabilities(BaseModel):
    streaming: bool = True
    push_notifications: bool = False
    state_transition_history: bool = True


class AgentCard(BaseModel):
    name: str
    description: str
    url: str
    version: str = "0.2.0"
    documentation_url: Optional[str] = None
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    authentication: Optional[Dict[str, Any]] = None
    default_input_modes: List[str] = Field(default=["text", "data"])
    default_output_modes: List[str] = Field(default=["text", "data"])
    skills: List[AgentSkill] = Field(default_factory=list)

    class Config:
        populate_by_name = True
