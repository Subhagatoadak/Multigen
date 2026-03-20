"""
Fluent DSL builder for Multigen workflows and graph definitions.

Usage — Sequential / parallel / conditional workflow:
    dsl = (
        WorkflowBuilder()
        .step("screen", agent="ScreeningAgent", params={"threshold": 0.8})
        .parallel("enrich",
            branch("geo",    agent="GeoAgent"),
            branch("credit", agent="CreditAgent"),
        )
        .conditional("route",
            when("credit.score >= 700", agent="ApproveAgent"),
            otherwise(agent="ReviewAgent"),
        )
        .build()
    )

Usage — Graph with cycles, reflection, fan-out:
    graph = (
        GraphBuilder()
        .node("plan",   agent="PlannerAgent",  reflection_threshold=0.75)
        .node("act",    agent="ActorAgent")
        .node("check",  agent="CritiqueAgent", fallback_agent="FallbackCritique")
        .edge("plan",   "act")
        .edge("act",    "check")
        .edge("check",  "plan", condition="check.confidence < 0.9")
        .entry("plan")
        .max_cycles(5)
        .build()
    )
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


# ── Helpers ────────────────────────────────────────────────────────────────────

def branch(name: str, agent: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Create a parallel branch definition."""
    return {"name": name, "agent": agent, "params": params or {}}


def when(
    condition: str,
    agent: Optional[str] = None,
    name: Optional[str] = None,
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a conditional branch (if condition → agent)."""
    return {
        "condition": condition,
        "then": {"name": name or f"when_{condition[:20]}", "agent": agent, "params": params or {}},
    }


def otherwise(
    agent: Optional[str] = None,
    name: str = "else_branch",
    params: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create the else branch of a conditional step."""
    return {"_else": True, "agent": agent, "name": name, "params": params or {}}


# ── Workflow builder ───────────────────────────────────────────────────────────

class WorkflowBuilder:
    """
    Fluent builder for sequential / parallel / conditional / loop / graph workflows.
    Produces the DSL dict accepted by POST /workflows/run.
    """

    def __init__(self) -> None:
        self._steps: List[Dict[str, Any]] = []

    def step(
        self,
        name: str,
        agent: str,
        params: Optional[Dict[str, Any]] = None,
        agent_version: Optional[str] = None,
    ) -> "WorkflowBuilder":
        """Add a sequential step."""
        s: Dict[str, Any] = {"name": name, "agent": agent, "params": params or {}}
        if agent_version:
            s["agent_version"] = agent_version
        self._steps.append(s)
        return self

    def parallel(self, name: str, *branches: Dict[str, Any]) -> "WorkflowBuilder":
        """Add a step that executes multiple branches concurrently."""
        self._steps.append({"name": name, "parallel": list(branches)})
        return self

    def conditional(
        self,
        name: str,
        *branches: Dict[str, Any],
    ) -> "WorkflowBuilder":
        """
        Add a conditional step.
        Pass when(...) helpers for if-branches and otherwise(...) for the else branch.
        """
        cond_list = []
        else_spec = None
        for b in branches:
            if b.get("_else"):
                else_spec = {"name": b["name"], "agent": b.get("agent"), "params": b.get("params", {})}
            else:
                cond_list.append(b)
        step: Dict[str, Any] = {"name": name, "conditional": cond_list}
        if else_spec:
            step["else"] = else_spec
        self._steps.append(step)
        return self

    def loop(
        self,
        name: str,
        steps: List[Dict[str, Any]],
        until: str = "",
        max_iterations: int = 10,
    ) -> "WorkflowBuilder":
        """Add a loop step that repeats until condition is met or max_iterations reached."""
        self._steps.append({
            "name": name,
            "loop": {
                "until": until,
                "max_iterations": max_iterations,
                "steps": steps,
            },
        })
        return self

    def graph(self, name: str, graph_def: Dict[str, Any]) -> "WorkflowBuilder":
        """Embed a full GraphDefinition as a child workflow step."""
        self._steps.append({"name": name, "graph": graph_def})
        return self

    def build(self, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Return the DSL dict ready to send to POST /workflows/run."""
        return {
            "dsl": {"steps": self._steps},
            "payload": payload or {},
        }


# ── Graph builder ──────────────────────────────────────────────────────────────

class GraphNodeBuilder:
    """Internal node accumulator for GraphBuilder."""

    def __init__(self, node_id: str) -> None:
        self._def: Dict[str, Any] = {"id": node_id}

    def agent(self, name: str) -> "GraphNodeBuilder":
        self._def["agent"] = name
        return self

    def blueprint(self, bp: Dict[str, Any]) -> "GraphNodeBuilder":
        self._def["blueprint"] = bp
        return self

    def params(self, **kw: Any) -> "GraphNodeBuilder":
        self._def.setdefault("params", {}).update(kw)
        return self

    def tools(self, *specs: Dict[str, Any]) -> "GraphNodeBuilder":
        self._def["tools"] = list(specs)
        return self

    def retry(self, n: int) -> "GraphNodeBuilder":
        self._def["retry"] = n
        return self

    def timeout(self, seconds: int) -> "GraphNodeBuilder":
        self._def["timeout"] = seconds
        return self

    def reflect(self, threshold: float, max_rounds: int = 2, critic: str = "CritiqueAgent") -> "GraphNodeBuilder":
        """Enable confidence-based auto-reflection for this node."""
        self._def["reflection_threshold"] = threshold
        self._def["max_reflections"] = max_rounds
        self._def["critic_agent"] = critic
        return self

    def fallback(self, agent_name: str) -> "GraphNodeBuilder":
        """Set a fallback agent for circuit-breaker trips."""
        self._def["fallback_agent"] = agent_name
        return self

    def build(self) -> Dict[str, Any]:
        return self._def


class GraphBuilder:
    """
    Fluent builder for GraphDefinition (arbitrary directed graph with cycles).
    Produces the dict accepted by the 'graph' field of a workflow step
    or as the graph_def argument of GraphWorkflow.run.

    Example:
        graph_def = (
            GraphBuilder()
            .node("think").agent("PlannerAgent").reflect(0.75).done()
            .node("act").agent("ActorAgent").timeout(60).done()
            .node("critique").agent("CritiqueAgent").fallback("FallbackAgent").done()
            .edge("think", "act")
            .edge("act",   "critique")
            .edge("critique", "think", condition="confidence < 0.9")
            .entry("think")
            .max_cycles(5)
            .build()
        )
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, Dict[str, Any]] = {}
        self._edges: List[Dict[str, Any]] = []
        self._entry: str = ""
        self._max_cycles: int = 20
        self._cb_trip: int = 3
        self._cb_recovery: int = 5
        self._current: Optional[GraphNodeBuilder] = None

    def node(self, node_id: str) -> GraphNodeBuilder:
        """
        Start building a node.  Call .done() on the returned builder
        to return to the GraphBuilder.
        """
        nb = _BoundNodeBuilder(node_id, self)
        self._current = nb
        return nb

    def _add_node(self, node_def: Dict[str, Any]) -> "GraphBuilder":
        self._nodes[node_def["id"]] = node_def
        return self

    def edge(
        self,
        source: str,
        target: str,
        condition: str = "",
    ) -> "GraphBuilder":
        self._edges.append({"source": source, "target": target, "condition": condition})
        return self

    def entry(self, node_id: str) -> "GraphBuilder":
        self._entry = node_id
        return self

    def max_cycles(self, n: int) -> "GraphBuilder":
        self._max_cycles = n
        return self

    def circuit_breaker(self, trip_threshold: int = 3, recovery_executions: int = 5) -> "GraphBuilder":
        self._cb_trip = trip_threshold
        self._cb_recovery = recovery_executions
        return self

    def build(self) -> Dict[str, Any]:
        if not self._entry:
            raise ValueError("GraphBuilder: call .entry(node_id) before .build()")
        return {
            "nodes": list(self._nodes.values()),
            "edges": self._edges,
            "entry": self._entry,
            "max_cycles": self._max_cycles,
            "circuit_breaker": {
                "trip_threshold": self._cb_trip,
                "recovery_executions": self._cb_recovery,
            },
        }


class _BoundNodeBuilder(GraphNodeBuilder):
    """GraphNodeBuilder subclass that returns to its parent GraphBuilder on .done()."""

    def __init__(self, node_id: str, parent: GraphBuilder) -> None:
        super().__init__(node_id)
        self._parent = parent

    def done(self) -> GraphBuilder:
        self._parent._add_node(self.build())
        return self._parent
