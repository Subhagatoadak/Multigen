"""Graph-based planning utilities."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional, Sequence


@dataclass
class PlanNode:
    """A node in the task graph."""

    id: str
    description: str
    parents: Sequence[str] = field(default_factory=tuple)
    metadata: Mapping[str, str] = field(default_factory=dict)


@dataclass
class TaskPlan:
    """Directed acyclic graph of sub-tasks."""

    goal: str
    nodes: Dict[str, PlanNode]

    def roots(self) -> List[PlanNode]:
        return [node for node in self.nodes.values() if not node.parents]

    def children(self, node_id: str) -> List[PlanNode]:
        return [node for node in self.nodes.values() if node_id in node.parents]


def simple_split(goal: str, *, delimiter: str = ".", prefix: str = "task") -> TaskPlan:
    """Split a goal statement into a linear plan."""

    raw_steps = [chunk.strip() for chunk in goal.split(delimiter) if chunk.strip()]
    nodes = {
        f"{prefix}-{idx+1}": PlanNode(
            id=f"{prefix}-{idx+1}",
            description=step,
            parents=(f"{prefix}-{idx}" ,) if idx > 0 else tuple(),
        )
        for idx, step in enumerate(raw_steps)
    }
    return TaskPlan(goal=goal, nodes=nodes)


def build_plan(goal: str, hints: Optional[Iterable[str]] = None) -> TaskPlan:
    """Construct a simple plan using hints for branching."""

    plan = simple_split(goal)
    if hints:
        parents = tuple(plan.nodes.keys())
        for idx, hint in enumerate(hints, start=1):
            node_id = f"{plan.goal[:3].lower()}-hint-{idx}"
            plan.nodes[node_id] = PlanNode(
                id=node_id,
                description=hint,
                parents=parents,
                metadata={"type": "hint"},
            )
    return plan


__all__ = ["PlanNode", "TaskPlan", "simple_split", "build_plan"]
