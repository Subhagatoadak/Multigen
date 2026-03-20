"""Pydantic models mirroring the Multigen orchestrator API contracts."""
from __future__ import annotations

from typing import Any, Dict, List, Literal, Optional
from pydantic import BaseModel, Field


# ── Workflow ───────────────────────────────────────────────────────────────────

class WorkflowStep(BaseModel):
    name: str
    agent: Optional[str] = None
    agent_version: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)


class RunRequest(BaseModel):
    """Either dsl or text must be provided."""
    dsl: Optional[Dict[str, Any]] = None
    text: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)


class RunResponse(BaseModel):
    instance_id: str


# ── Graph node / edge ──────────────────────────────────────────────────────────

class ToolSpec(BaseModel):
    name: str
    config: Dict[str, Any] = Field(default_factory=dict)


class GraphNodeDef(BaseModel):
    id: str
    agent: Optional[str] = None
    blueprint: Optional[Dict[str, Any]] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    tools: List[ToolSpec] = Field(default_factory=list)
    retry: int = 3
    timeout: int = 30
    reflection_threshold: float = 0.0
    max_reflections: int = 2
    critic_agent: str = "CritiqueAgent"
    fallback_agent: Optional[str] = None


class GraphEdgeDef(BaseModel):
    source: str
    target: str
    condition: str = ""


class CircuitBreakerConfig(BaseModel):
    trip_threshold: int = 3
    recovery_executions: int = 5


class GraphDefinition(BaseModel):
    nodes: List[GraphNodeDef]
    edges: List[GraphEdgeDef] = Field(default_factory=list)
    entry: str
    max_cycles: int = 20
    circuit_breaker: CircuitBreakerConfig = Field(default_factory=CircuitBreakerConfig)


# ── Signal request models ──────────────────────────────────────────────────────

class InjectNodeRequest(BaseModel):
    id: str
    agent: Optional[str] = None
    blueprint: Optional[Dict[str, Any]] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    tools: list = Field(default_factory=list)
    retry: int = 3
    timeout: int = 30
    edges_to: List[str] = Field(default_factory=list)
    reflection_threshold: float = 0.0
    fallback_agent: Optional[str] = None


class FanOutNodeDef(BaseModel):
    id: str
    agent: Optional[str] = None
    blueprint: Optional[Dict[str, Any]] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    retry: int = 3
    timeout: int = 30


class FanOutRequest(BaseModel):
    group_id: str
    nodes: List[FanOutNodeDef]
    consensus: Literal[
        "highest_confidence", "aggregate", "majority_vote", "first_success"
    ] = "highest_confidence"


# ── State / introspection responses ───────────────────────────────────────────

class NodeState(BaseModel):
    node_id: str
    output: Dict[str, Any]
    updated_at: Optional[str] = None


class WorkflowState(BaseModel):
    workflow_id: str
    nodes: List[NodeState]
    count: int


class WorkflowHealth(BaseModel):
    workflow_id: str
    interrupted: bool
    pending_count: int
    skip_nodes: List[str]
    cb_trips_total: int
    errors: List[str]
    dead_letters: List[str]


class WorkflowMetrics(BaseModel):
    workflow_id: str
    nodes_executed: int
    nodes_skipped: int
    reflections_triggered: int
    fan_outs_executed: int
    circuit_breaker_trips: int
    error_count: int
    dead_letter_count: int


# ── Capability ─────────────────────────────────────────────────────────────────

class Capability(BaseModel):
    name: str
    version: Optional[str] = None
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
