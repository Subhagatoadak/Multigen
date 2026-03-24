"""Hierarchical agent and workflow type structures."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Union


# ── Enumerations ──────────────────────────────────────────────────────────────

class AgentRole(Enum):
    """Semantic role of an agent within a group."""

    COORDINATOR = auto()  # orchestrates sub-agents
    WORKER = auto()       # executes tasks
    CRITIC = auto()       # evaluates outputs
    PLANNER = auto()      # decomposes tasks into sub-tasks
    MONITOR = auto()      # observes and reports health
    GATEWAY = auto()      # input/output routing


class StepKind(Enum):
    """Semantic kind of a typed pipeline step."""

    TRANSFORM = auto()   # pure data transformation
    FILTER = auto()      # predicate gate (passes or drops)
    AGGREGATE = auto()   # reduce / combine multiple inputs
    BRANCH = auto()      # conditional routing
    LOOP = auto()        # iterative / recursive execution
    SPAWN = auto()       # dynamic agent creation
    VALIDATE = auto()    # schema / constraint checking
    EMIT = auto()        # event / message emission


# ── Agent Spec ────────────────────────────────────────────────────────────────

@dataclass
class AgentSpec:
    """Typed specification for an agent within a hierarchy."""

    name: str
    role: AgentRole
    capabilities: List[str] = field(default_factory=list)
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    max_concurrency: int = 1
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role.name,
            "capabilities": self.capabilities,
            "max_concurrency": self.max_concurrency,
            "metadata": self.metadata,
        }


# ── Agent Group ───────────────────────────────────────────────────────────────

@dataclass
class AgentGroup:
    """
    A named team of agents with an optional designated coordinator.

    Usage::

        group = AgentGroup("research_team")
        group.add_agent(AgentSpec("Planner", AgentRole.PLANNER))
        group.add_agent(AgentSpec("Researcher", AgentRole.WORKER))
        group.set_coordinator(AgentSpec("Lead", AgentRole.COORDINATOR))
    """

    name: str
    description: str = ""
    agents: List[AgentSpec] = field(default_factory=list)
    coordinator: Optional[AgentSpec] = None

    def add_agent(self, spec: AgentSpec) -> "AgentGroup":
        self.agents.append(spec)
        return self

    def set_coordinator(self, spec: AgentSpec) -> "AgentGroup":
        self.coordinator = spec
        if spec not in self.agents:
            self.agents.append(spec)
        return self

    @property
    def workers(self) -> List[AgentSpec]:
        return [a for a in self.agents if a.role == AgentRole.WORKER]

    @property
    def critics(self) -> List[AgentSpec]:
        return [a for a in self.agents if a.role == AgentRole.CRITIC]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "coordinator": self.coordinator.to_dict() if self.coordinator else None,
            "agents": [a.to_dict() for a in self.agents],
        }


# ── Agent Hierarchy ───────────────────────────────────────────────────────────

class AgentHierarchy:
    """
    Multi-level tree of AgentGroups — supports recursive team-of-teams patterns.

    Usage::

        hierarchy = AgentHierarchy(root=AgentGroup("enterprise"))
        hierarchy.add_subgroup("enterprise", AgentGroup("research"))
        hierarchy.add_subgroup("enterprise", AgentGroup("analysis"))
        hierarchy.add_subgroup("research", AgentGroup("data_collection"))

        print(hierarchy.visualize())
        all_agents = hierarchy.all_agents()
    """

    _ROLE_ICONS: Dict[str, str] = {
        "COORDINATOR": "🎯",
        "WORKER": "⚙️",
        "CRITIC": "🔍",
        "PLANNER": "📋",
        "MONITOR": "👁️",
        "GATEWAY": "🚪",
    }

    def __init__(self, root: AgentGroup) -> None:
        self.root = root
        self._children: Dict[str, List[AgentGroup]] = {}

    def add_subgroup(self, parent_name: str, group: AgentGroup) -> "AgentHierarchy":
        self._children.setdefault(parent_name, []).append(group)
        return self

    def get_subgroups(self, group_name: str) -> List[AgentGroup]:
        return self._children.get(group_name, [])

    def all_groups(self) -> List[AgentGroup]:
        result: List[AgentGroup] = [self.root]

        def collect(name: str) -> None:
            for g in self._children.get(name, []):
                result.append(g)
                collect(g.name)

        collect(self.root.name)
        return result

    def all_agents(self) -> List[AgentSpec]:
        agents: List[AgentSpec] = []
        for group in self.all_groups():
            agents.extend(group.agents)
        return agents

    def find_agent(self, name: str) -> Optional[AgentSpec]:
        return next((a for a in self.all_agents() if a.name == name), None)

    def find_group(self, name: str) -> Optional[AgentGroup]:
        return next((g for g in self.all_groups() if g.name == name), None)

    def visualize(self, indent: str = "  ") -> str:
        lines: List[str] = []

        def render(group: AgentGroup, depth: int) -> None:
            prefix = indent * depth
            coord_tag = f" [{group.coordinator.name}]" if group.coordinator else ""
            lines.append(f"{prefix}📁 {group.name}{coord_tag}")
            for agent in group.agents:
                icon = self._ROLE_ICONS.get(agent.role.name, "🤖")
                lines.append(f"{prefix}{indent}{icon} {agent.name} ({agent.role.name})")
            for child in self._children.get(group.name, []):
                render(child, depth + 1)

        render(self.root, 0)
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        def build(group: AgentGroup) -> Dict[str, Any]:
            d = group.to_dict()
            d["subgroups"] = [build(g) for g in self._children.get(group.name, [])]
            return d

        return build(self.root)


# ── Typed Step ────────────────────────────────────────────────────────────────

@dataclass
class TypedStep:
    """
    A pipeline step with explicit semantic kind and optional schema validation.

    Usage::

        step = TypedStep(
            name="normalise",
            kind=StepKind.TRANSFORM,
            fn=lambda ctx: {"value": ctx["raw"] / 100},
            input_schema={"raw": {"required": True}},
        )
        await step.run({"raw": 500})
    """

    name: str
    kind: StepKind
    fn: Callable
    input_schema: Dict[str, Any] = field(default_factory=dict)
    output_schema: Dict[str, Any] = field(default_factory=dict)
    description: str = ""

    def validate_input(self, ctx: Dict[str, Any]) -> List[str]:
        """Return a list of missing required fields."""
        return [
            k for k, spec in self.input_schema.items()
            if isinstance(spec, dict) and spec.get("required") and k not in ctx
        ]

    async def run(self, ctx: Dict[str, Any]) -> Any:
        missing = self.validate_input(ctx)
        if missing:
            raise ValueError(f"Step {self.name!r} missing required inputs: {missing}")
        result = self.fn(ctx)
        if asyncio.iscoroutine(result):
            return await result
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
        }


# ── Hierarchical Pipeline ─────────────────────────────────────────────────────

class HierarchicalPipeline:
    """
    Nestable pipeline that can contain TypedSteps and sub-pipelines.

    Supports recursive flattening and a text tree visualisation.

    Usage::

        root = HierarchicalPipeline("etl")
        root.add_step(TypedStep("ingest", StepKind.TRANSFORM, ingest_fn))
        root.add_step(TypedStep("validate", StepKind.VALIDATE, validate_fn))

        sub = HierarchicalPipeline("enrichment")
        sub.add_step(TypedStep("lookup", StepKind.TRANSFORM, lookup_fn))
        root.embed(sub)

        result = await root.run({"source": "s3://bucket/file.csv"})
        print(root.describe())
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._steps: List[Union[TypedStep, "HierarchicalPipeline"]] = []

    def add_step(self, step: Union[TypedStep, "HierarchicalPipeline"]) -> "HierarchicalPipeline":
        self._steps.append(step)
        return self

    def embed(self, pipeline: "HierarchicalPipeline") -> "HierarchicalPipeline":
        """Embed another pipeline as a nested sub-step."""
        self._steps.append(pipeline)
        return self

    def flatten(self) -> List[TypedStep]:
        """Recursively flatten to a linear list of TypedSteps."""
        flat: List[TypedStep] = []
        for step in self._steps:
            if isinstance(step, HierarchicalPipeline):
                flat.extend(step.flatten())
            else:
                flat.append(step)
        return flat

    async def run(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        result = dict(ctx)
        for step in self._steps:
            if isinstance(step, HierarchicalPipeline):
                sub = await step.run(result)
                result.update(sub)
            else:
                output = await step.run(result)
                if isinstance(output, dict):
                    result.update(output)
                else:
                    result[step.name] = output
        return result

    def describe(self, indent: str = "  ", depth: int = 0) -> str:
        prefix = indent * depth
        lines = [f"{prefix}Pipeline: {self.name}"]
        for step in self._steps:
            if isinstance(step, HierarchicalPipeline):
                lines.append(step.describe(indent, depth + 1))
            else:
                lines.append(f"{prefix}{indent}[{step.kind.name}] {step.name}")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "steps": [s.to_dict() for s in self._steps],
        }


__all__ = [
    "AgentGroup",
    "AgentHierarchy",
    "AgentRole",
    "AgentSpec",
    "HierarchicalPipeline",
    "StepKind",
    "TypedStep",
]
