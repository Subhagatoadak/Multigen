"""
Type-safe DSL definitions for Multigen graph and workflow structures.

These TypedDicts describe the exact dict shapes accepted by:
  - GraphWorkflow.run(graph_def, payload, workflow_id)
  - POST /workflows/graph/run  { graph_def, payload, workflow_id }
  - POST /workflows/run        { dsl, payload, workflow_id }

They are purely structural — no runtime validation — but provide full
IDE auto-complete and mypy/pyright checking when you build definitions
programmatically instead of using the fluent GraphBuilder / WorkflowBuilder.

Usage
-----
    from sdk.multigen.types import GraphDef, NodeDef, EdgeDef

    graph: GraphDef = {
        "nodes": [
            {"id": "research", "agent": "ResearchAgent", "timeout": 60},
            {"id": "report",   "agent": "ReportAgent",   "depends_on": ["research"]},
        ],
        "edges":  [{"source": "research", "target": "report"}],
        "entry":  "research",
    }
"""
from __future__ import annotations

from typing import Any, Dict, List

# Python 3.8+ ships TypedDict in typing; 3.11+ adds NotRequired.
# We use typing_extensions for broad compatibility.
try:
    from typing import Literal, NotRequired, TypedDict
except ImportError:  # pragma: no cover
    from typing_extensions import Literal, NotRequired, TypedDict  # type: ignore[assignment]


# ── Tool specs ────────────────────────────────────────────────────────────────

class ToolSpec(TypedDict, total=False):
    """Inline tool definition attached to a graph node."""
    name: str
    description: str
    parameters: Dict[str, Any]
    endpoint: str
    method: str


# ── Fan-out spec ──────────────────────────────────────────────────────────────

class FanOutSpec(TypedDict, total=False):
    """
    Dynamic fan-out instruction inside a NodeDef.

    When present the engine generates child nodes at runtime from a list
    produced by the fan-out agent.
    """
    items_key: str          # key in agent output whose value is the list to fan over
    agent: str              # agent to run for each item
    params_template: Dict[str, Any]   # Jinja2 template dict; ``{{item}}`` → list element
    task_queues: List[str]  # round-robin partition across Temporal task queues
    max_concurrent: int     # cap concurrent child nodes (0 = unlimited)


# ── Circuit-breaker config ────────────────────────────────────────────────────

class CircuitBreakerConfig(TypedDict, total=False):
    trip_threshold: int      # consecutive failures before tripping (default 3)
    recovery_executions: int # successful executions to recover (default 5)


# ── Node definition ───────────────────────────────────────────────────────────

class NodeDef(TypedDict, total=False):
    """
    Definition of a single node in a graph workflow.

    Only ``id`` is strictly required; all other fields are optional.
    """
    # Required
    id: str

    # Agent selection
    agent: str                  # registered agent name
    agent_version: str          # semver string; "" = latest
    fallback_agent: str         # agent to run if circuit-breaker trips

    # Execution parameters
    params: Dict[str, Any]
    tools: List[ToolSpec]
    timeout: int                # seconds; 0 = no limit
    retry: int                  # max retry attempts on transient failure

    # Task queue routing (for partition-aware fan-out)
    task_queue: str

    # Dependency tracking (auto-computed from edges, or explicit)
    depends_on: List[str]       # node IDs that must complete before this node

    # Reflection / confidence loop
    reflection_threshold: float   # confidence < this → trigger reflection
    max_reflections: int          # max reflection rounds (default 2)
    critic_agent: str             # agent that evaluates output (default CritiqueAgent)

    # Fan-out
    fan_out: FanOutSpec

    # Human-in-the-loop gate
    requires_approval: bool       # pause & wait for approve_agent signal
    approval_timeout: int         # seconds before auto-reject

    # Dynamic agent creation
    blueprint: Dict[str, Any]     # spec for create_agent_activity


# ── Edge definition ───────────────────────────────────────────────────────────

class EdgeDef(TypedDict, total=False):
    """Directed edge between two graph nodes."""
    source: str         # source node id
    target: str         # target node id
    condition: str      # Python expression; edge only followed when True
                        # Available variables: confidence, output, steps


# ── Graph definition ──────────────────────────────────────────────────────────

class GraphDef(TypedDict, total=False):
    """
    Top-level graph workflow definition.

    Pass as the ``graph_def`` argument to GraphWorkflow or POST /workflows/graph/run.
    """
    nodes: List[NodeDef]
    edges: List[EdgeDef]
    entry: str                          # id of the entry node
    max_cycles: int                     # global cycle cap (default 20)
    circuit_breaker: CircuitBreakerConfig


# ── Sequential workflow DSL ───────────────────────────────────────────────────

class StepDef(TypedDict, total=False):
    """A single step in a sequential workflow."""
    name: str
    agent: str
    params: Dict[str, Any]
    agent_version: str
    retry: int
    timeout: int
    # Nested constructs
    parallel: List["StepDef"]          # parallel branches
    conditional: List[Dict[str, Any]]  # conditional branches (when/otherwise)
    loop: Dict[str, Any]               # loop spec {until, max_iterations, steps}
    graph: GraphDef                    # embedded graph sub-workflow


class WorkflowDSL(TypedDict, total=False):
    """Sequential workflow DSL passed to POST /workflows/run."""
    steps: List[StepDef]


# ── API request bodies ────────────────────────────────────────────────────────

class RunGraphRequest(TypedDict, total=False):
    graph_def: GraphDef
    payload: Dict[str, Any]
    workflow_id: str


class RunWorkflowRequest(TypedDict, total=False):
    dsl: WorkflowDSL
    payload: Dict[str, Any]
    workflow_id: str


# ── Epistemic envelope ────────────────────────────────────────────────────────

class EpistemicEnvelope(TypedDict, total=False):
    """
    Epistemic metadata that every agent is expected to attach to its output.

    Agents signal how confident they are, what they don't know, and what
    assumptions were made so the orchestrator can route, reflect, and
    flag nodes for human review appropriately.
    """
    confidence: float               # 0.0–1.0
    reasoning: str                  # free-text justification
    uncertainty_sources: List[str]  # named sources of uncertainty
    assumptions: List[str]
    known_limitations: List[str]
    known_unknowns: List[str]
    evidence_quality: Literal["high", "medium", "low", "none"]
    data_completeness: float        # 0.0–1.0
    flags: List[str]                # e.g. ["needs_human_review", "stale_data"]


class AgentOutput(TypedDict, total=False):
    """
    Canonical shape of the dict returned by BaseAgent.run().

    The ``output`` key contains agent-specific results plus the
    ``epistemic`` sub-dict.
    """
    output: Dict[str, Any]          # must contain "epistemic" key
    agent: str                      # agent name, injected by activity wrapper
    node_id: str                    # node id, injected by activity wrapper
    confidence: float               # top-level copy of epistemic.confidence
    error: str                      # present only on failure


# ── Convenience re-exports ────────────────────────────────────────────────────

__all__ = [
    "AgentOutput",
    "CircuitBreakerConfig",
    "EdgeDef",
    "EpistemicEnvelope",
    "FanOutSpec",
    "GraphDef",
    "NodeDef",
    "RunGraphRequest",
    "RunWorkflowRequest",
    "StepDef",
    "ToolSpec",
    "WorkflowDSL",
]
