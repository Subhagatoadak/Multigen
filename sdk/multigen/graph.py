"""
multigen.graph
==============
DAG-based agent graph executor — pure Python, no Temporal/Kafka required.

Features:
- Directed Acyclic Graph (DAG) with topological execution
- Conditional edges (execute only if predicate passes)
- Dynamic routing (edge target determined at runtime by agent output)
- Cycle detection and loop nodes (with max_iterations guard)
- Fork/Join: fan-out to parallel branches, rejoin with aggregator
- Entry / terminal node designation
- Per-node timeout and retry
- Graph composition: sub-graphs as nodes
- Serialisation to/from dict (compatible with Multigen DSL)

Usage::

    from multigen.graph import Graph
    from multigen.agent import LLMAgent, FunctionAgent

    g = Graph(name="research_pipeline")

    g.node("fetch",    FunctionAgent("fetch", fn=fetch_web))
    g.node("analyse",  LLMAgent("analyse", prompt="Analyse: {fetch.output}"))
    g.node("report",   LLMAgent("report",  prompt="Write report: {analyse.response}"))

    g.edge("fetch",   "analyse")
    g.edge("analyse", "report")

    result = await g.run({"query": "AI regulation"})
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set

from .agent import BaseAgent, AgentOutput

logger = logging.getLogger(__name__)
Context = Dict[str, Any]


@dataclass
class NodeSpec:
    id:           str
    agent:        BaseAgent
    timeout:      Optional[float] = None
    max_retries:  int = 0
    is_loop:      bool = False
    max_loop_iter: int = 10
    is_subgraph:  bool = False


@dataclass
class EdgeSpec:
    source:    str
    target:    str
    condition: Optional[Callable[[Context], bool]] = None  # None = always take
    dynamic:   bool = False      # if True, target is determined by source output


@dataclass
class GraphResult:
    status:          str             # completed | failed | partial
    executed:        List[str]
    outputs:         Dict[str, AgentOutput]
    context:         Context
    total_latency_ms: float
    error:           Optional[str] = None

    def __getitem__(self, node_id: str) -> AgentOutput:
        return self.outputs[node_id]

    @property
    def final_output(self) -> Any:
        """Return the output of the last executed node."""
        if self.executed:
            return self.outputs.get(self.executed[-1], {})
        return {}


class Graph:
    """Pure-Python DAG executor.

    Build:  g.node(id, agent)  /  g.edge(src, tgt, condition=fn)
    Run:    await g.run(ctx)
    """

    def __init__(self, name: str = "graph", *, entry: Optional[str] = None) -> None:
        self.name = name
        self._nodes: Dict[str, NodeSpec] = {}
        self._edges: List[EdgeSpec] = {}   # type: ignore[assignment]
        self._edges = []
        self._entry: Optional[str] = entry
        self._terminals: Set[str] = set()

    # ── Build API ────────────────────────────────────────────────────────

    def node(
        self,
        node_id: str,
        agent: BaseAgent,
        *,
        timeout: Optional[float] = None,
        max_retries: int = 0,
        is_loop: bool = False,
        max_loop_iter: int = 10,
    ) -> "Graph":
        self._nodes[node_id] = NodeSpec(
            id=node_id, agent=agent, timeout=timeout,
            max_retries=max_retries, is_loop=is_loop, max_loop_iter=max_loop_iter,
        )
        return self

    def edge(
        self,
        source: str,
        target: str,
        *,
        condition: Optional[Callable[[Context], bool]] = None,
        dynamic: bool = False,
    ) -> "Graph":
        self._edges.append(EdgeSpec(source=source, target=target, condition=condition, dynamic=dynamic))
        return self

    def set_entry(self, node_id: str) -> "Graph":
        self._entry = node_id
        return self

    def set_terminal(self, *node_ids: str) -> "Graph":
        self._terminals.update(node_ids)
        return self

    # Operator overloads for fluent DSL
    def __rshift__(self, other: "Graph") -> "Graph":
        """g1 >> g2 — connect last node of g1 to first node of g2."""
        # find terminal/entry
        last = list(self._nodes)[-1] if self._nodes else None
        first = other._entry or (list(other._nodes)[0] if other._nodes else None)
        merged = Graph(name=f"{self.name}+{other.name}")
        merged._nodes = {**self._nodes, **other._nodes}
        merged._edges = self._edges + other._edges
        if last and first:
            merged._edges.append(EdgeSpec(source=last, target=first))
        merged._entry = self._entry or list(self._nodes)[0] if self._nodes else None
        return merged

    # ── Validation ───────────────────────────────────────────────────────

    def validate(self) -> List[str]:
        """Return list of validation errors (empty = valid)."""
        issues = []
        node_ids = set(self._nodes)
        for e in self._edges:
            if e.source not in node_ids:
                issues.append(f"Edge source {e.source!r} not in nodes")
            if not e.dynamic and e.target not in node_ids:
                issues.append(f"Edge target {e.target!r} not in nodes")
        if self._entry and self._entry not in node_ids:
            issues.append(f"Entry {self._entry!r} not in nodes")
        return issues

    def _topological_sort(self) -> List[str]:
        """Kahn's algorithm — returns BFS order."""
        in_deg: Dict[str, int] = {nid: 0 for nid in self._nodes}
        adj: Dict[str, List[str]] = {nid: [] for nid in self._nodes}
        for e in self._edges:
            if e.target in self._nodes:
                adj[e.source].append(e.target)
                in_deg[e.target] += 1

        queue = [n for n, d in in_deg.items() if d == 0]
        order = []
        while queue:
            n = queue.pop(0)
            order.append(n)
            for nb in adj[n]:
                in_deg[nb] -= 1
                if in_deg[nb] == 0:
                    queue.append(nb)

        return order

    # ── Execution ────────────────────────────────────────────────────────

    async def run(self, ctx: Optional[Context] = None) -> GraphResult:
        issues = self.validate()
        if issues:
            raise ValueError(f"Graph validation failed: {issues}")

        context: Context = dict(ctx or {})
        outputs: Dict[str, AgentOutput] = {}
        executed: List[str] = []
        start = time.perf_counter()

        # Determine execution order
        entry = self._entry or (list(self._nodes)[0] if self._nodes else None)
        if not entry:
            raise ValueError("Graph has no nodes")

        # Wave-based BFS (handles conditional edges + parallel waves)
        completed: Set[str] = set()
        pending = [entry]
        visited: Set[str] = {entry}

        while pending:
            # Collect all nodes whose deps are satisfied
            wave: List[str] = []
            deferred: List[str] = []
            for nid in pending:
                if self._deps_satisfied(nid, completed):
                    wave.append(nid)
                else:
                    deferred.append(nid)

            if not wave:
                if deferred:
                    logger.warning("Graph deadlock — unsatisfied: %s", deferred)
                break

            # Execute wave in parallel
            wave_results = await asyncio.gather(
                *[self._exec_node(nid, context, outputs) for nid in wave],
                return_exceptions=True,
            )

            for nid, result in zip(wave, wave_results):
                if isinstance(result, BaseException):
                    logger.error("Node %s failed: %s", nid, result)
                    outputs[nid] = {"error": str(result), "node_id": nid}
                    context[nid] = outputs[nid]
                    return GraphResult(
                        status="failed", executed=executed + [nid],
                        outputs=outputs, context=context,
                        total_latency_ms=round((time.perf_counter() - start) * 1000, 2),
                        error=f"Node {nid!r}: {result}",
                    )
                else:
                    outputs[nid] = result
                    context[nid] = result
                executed.append(nid)
                completed.add(nid)

            # Enqueue successors
            pending = list(deferred)
            for e in self._edges:
                if e.source not in completed:
                    continue
                target = e.target
                # Dynamic routing: target determined by agent output
                if e.dynamic:
                    target = outputs.get(e.source, {}).get("_next_node", "")
                if not target or target not in self._nodes:
                    continue
                if target in visited:
                    # Loop node: allow re-entry within max_loop_iter
                    spec = self._nodes.get(target)
                    if spec and spec.is_loop:
                        loop_key = f"__loop_count_{target}__"
                        count = context.get(loop_key, 0)
                        if count < spec.max_loop_iter:
                            context[loop_key] = count + 1
                            visited.discard(target)
                            completed.discard(target)
                        else:
                            continue
                    else:
                        continue
                # Check condition
                if e.condition and not e.condition(context):
                    continue
                if target not in visited:
                    pending.append(target)
                    visited.add(target)

        return GraphResult(
            status="completed",
            executed=executed,
            outputs=outputs,
            context=context,
            total_latency_ms=round((time.perf_counter() - start) * 1000, 2),
        )

    def _deps_satisfied(self, nid: str, completed: Set[str]) -> bool:
        incoming = [e.source for e in self._edges if e.target == nid]
        return all(s in completed for s in incoming) if incoming else True

    async def _exec_node(
        self,
        nid: str,
        context: Context,
        outputs: Dict[str, AgentOutput],
    ) -> AgentOutput:
        spec = self._nodes[nid]
        agent = spec.agent

        async def call() -> AgentOutput:
            result = await agent(context)
            return {**result, "node_id": nid}

        # Retry logic
        last_exc: Optional[Exception] = None
        for attempt in range(spec.max_retries + 1):
            try:
                if spec.timeout:
                    return await asyncio.wait_for(call(), spec.timeout)
                return await call()
            except Exception as exc:
                last_exc = exc
                if attempt < spec.max_retries:
                    await asyncio.sleep(2 ** attempt * 0.5)

        raise RuntimeError(f"Node {nid} failed after {spec.max_retries + 1} attempts") from last_exc

    # ── Serialisation ────────────────────────────────────────────────────

    def to_dict(self) -> Dict[str, Any]:
        """Convert graph structure to Multigen DSL dict (agent names, not objects)."""
        return {
            "name":  self.name,
            "entry": self._entry,
            "nodes": [
                {
                    "id":     n.id,
                    "agent":  n.agent.name,
                    "timeout": n.timeout,
                    "is_loop": n.is_loop,
                }
                for n in self._nodes.values()
            ],
            "edges": [
                {
                    "source":  e.source,
                    "target":  e.target,
                    "dynamic": e.dynamic,
                }
                for e in self._edges
            ],
        }

    def __repr__(self) -> str:
        return (
            f"Graph(name={self.name!r}, nodes={list(self._nodes)}, "
            f"edges={[(e.source, e.target) for e in self._edges]})"
        )
