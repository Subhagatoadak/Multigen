"""Manifest validation using :mod:`pydantic`."""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional

from pydantic import BaseModel, Field, ValidationError

try:  # pragma: no cover - optional import path for pydantic v2
    from pydantic import ConfigDict  # type: ignore
except ImportError:  # pragma: no cover - pydantic v1 fallback
    ConfigDict = None


if ConfigDict is not None:  # pragma: no branch

    class ManifestModel(BaseModel):
        model_config = ConfigDict(extra="forbid")


else:  # pragma: no cover - executed when running with pydantic v1

    class ManifestModel(BaseModel):
        class Config:
            extra = "forbid"


class WorkflowStep(ManifestModel):
    """Single workflow step as defined in YAML manifests."""

    name: str
    description: Optional[str] = None
    agent: Optional[str] = None
    tool: Optional[str] = None
    config: Dict[str, Any] = Field(default_factory=dict)


class ToolConfig(ManifestModel):
    """Declarative tool definition."""

    name: str
    type: str
    config: Dict[str, Any] = Field(default_factory=dict)


class AgentConfig(ManifestModel):
    """Declarative agent description referencing capabilities and tools."""

    name: str
    role: str
    model: Optional[str] = None
    tools: List[str] = Field(default_factory=list)
    policies: List[str] = Field(default_factory=list)
    config: Dict[str, Any] = Field(default_factory=dict)


class PolicyConfig(ManifestModel):
    """Policy or guard configuration entry."""

    name: str
    type: str
    rules: Dict[str, Any] = Field(default_factory=dict)
    targets: List[str] = Field(default_factory=list)
    namespaces: List[str] = Field(default_factory=list)


class PatternConfig(ManifestModel):
    """Pattern instantiation defined in configuration."""

    name: str
    type: str
    stages: List[str] = Field(default_factory=list)
    config: Dict[str, Any] = Field(default_factory=dict)


class WorkflowManifest(ManifestModel):
    """Validated representation of a workflow manifest."""

    name: str
    steps: List[WorkflowStep]
    description: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    models: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    agents: Dict[str, AgentConfig] = Field(default_factory=dict)
    tools: Dict[str, ToolConfig] = Field(default_factory=dict)
    mcp_servers: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    mcp_tools: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    policies: Dict[str, PolicyConfig] = Field(default_factory=dict)
    patterns: Dict[str, PatternConfig] = Field(default_factory=dict)
    run_store: Optional[Dict[str, Any]] = None
    rate_limits: Dict[str, Dict[str, Any]] = Field(default_factory=dict)
    prompts: Dict[str, Dict[str, Any]] = Field(default_factory=dict)

    def summary(self) -> str:
        """Return a compact human readable summary string."""

        return f"Workflow(name={self.name!r}, steps={len(self.steps)}, agents={len(self.agents)})"


def _parse_manifest(data: Mapping[str, Any]) -> WorkflowManifest:
    """Parse mapping data into a :class:`WorkflowManifest` instance."""

    try:
        parse = getattr(WorkflowManifest, "model_validate")
    except AttributeError:  # pragma: no cover - pydantic v1
        parse = WorkflowManifest.parse_obj  # type: ignore[attr-defined]
    return parse(data)


def validate_workflow(manifest: Mapping[str, Any] | WorkflowManifest) -> WorkflowManifest:
    """Validate manifest data and return a structured model.

    Parameters
    ----------
    manifest:
        Mapping parsed from YAML or an existing :class:`WorkflowManifest`
        instance.
    """

    if isinstance(manifest, WorkflowManifest):
        return manifest
    return _parse_manifest(manifest)


__all__ = [
    "WorkflowManifest",
    "WorkflowStep",
    "ToolConfig",
    "AgentConfig",
    "PolicyConfig",
    "PatternConfig",
    "validate_workflow",
    "ValidationError",
]
