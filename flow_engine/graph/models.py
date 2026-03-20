"""Data models for the Multigen graph execution engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ToolSpec:
    """A tool available to a graph node during execution."""
    name: str                               # "http" | "rag" | "math" | "code" | "db"
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GraphEdge:
    """
    Directed edge between two graph nodes.

    If condition is empty the edge is always traversed.
    Conditions use the same safe AST syntax as conditional steps:
        "score >= 90"
        "status == \"approved\""
    """
    source: str
    target: str
    condition: str = ""


@dataclass
class GraphNode:
    """
    A single node in the execution graph.

    Attributes:
        id:      Unique identifier within the graph.
        agent:   Name of the registered Multigen agent to run.
        params:  Static parameters passed to the agent.
                 {{steps.<node_id>.output.<key>}} refs are resolved at runtime.
        tools:   Tool specs injected into the agent's params before execution.
        retry:   Maximum Temporal retry attempts.
        timeout: Activity timeout in seconds.
    """
    id: str
    agent: str
    params: Dict[str, Any] = field(default_factory=dict)
    tools: List[ToolSpec] = field(default_factory=list)
    retry: int = 3
    timeout: int = 30


@dataclass
class GraphDefinition:
    """
    Complete graph workflow definition.

    Attributes:
        nodes:          All nodes in the graph.
        edges:          All directed edges (may form cycles).
        entry:          ID of the node to start execution from.
        state_backend:  "mongodb" (distributed) or "memory" (in-process, dev only).
        max_cycles:     Maximum times any single node may execute (cycle guard).
    """
    nodes: List[GraphNode]
    edges: List[GraphEdge]
    entry: str
    state_backend: str = "mongodb"
    max_cycles: int = 20

    def node_map(self) -> Dict[str, GraphNode]:
        return {n.id: n for n in self.nodes}

    def outgoing_edges(self, node_id: str) -> List[GraphEdge]:
        return [e for e in self.edges if e.source == node_id]
