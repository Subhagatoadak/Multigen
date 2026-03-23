"""Branching graph runner with conditional, batch, and parallel execution."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set

from ..agent import Agent, Context
from ..schemas import Message
from .coordinator.base import CoordinatorBase


@dataclass
class GraphNodeSpec:
    """Describe how a node in the graph should execute."""

    agent: Agent
    after: Sequence[str] = field(default_factory=tuple)
    condition: Optional[Callable[[Context], bool]] = None
    batch_key: Optional[str] = None
    name: Optional[str] = None
    parallel_group: Optional[str] = None


class GraphRunner(CoordinatorBase):
    """Execute a DAG of agent nodes with branching, batching, and monitoring."""

    def __init__(
        self,
        graph: Mapping[str, GraphNodeSpec],
        *,
        allow_parallel: bool = True,
        max_parallel: Optional[int] = None,
        batch_window: Optional[int] = None,
        isolate_parallel_state: bool = False,
        guards: Sequence = (),  # type: ignore[type-arg]
        run_id: str | None = None,
        run_store: Any | None = None,
        branch_budget: Optional[int] = None,
    ) -> None:
        super().__init__(guards=guards, run_id=run_id, run_store=run_store)
        if not graph:
            raise ValueError("GraphRunner requires at least one node.")
        self.graph = dict(graph)
        self.allow_parallel = allow_parallel
        self.max_parallel = max_parallel
        self.batch_window = batch_window
        self.isolate_parallel_state = isolate_parallel_state
        self.branch_budget = branch_budget

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        completed: Set[str] = set()
        token = context.components.get("cancel_token") if isinstance(context.components, Mapping) else None
        branch_additions = 0

        while len(completed) < len(self.graph):
            if getattr(token, "cancelled", False):
                break
            ready = self._ready_nodes(completed)
            if not ready:
                raise RuntimeError("No executable graph nodes found; possible cycle or unsatisfied condition.")

            if self.batch_window:
                ready = ready[: self.batch_window]
            self.tracer.metric("graph.ready", float(len(ready)))

            grouped: Dict[str, List[str]] = {}
            for node_id in ready:
                group = self.graph[node_id].parallel_group or node_id
                grouped.setdefault(group, []).append(node_id)

            for nodes in grouped.values():
                if self.allow_parallel and len(nodes) > 1:
                    with ThreadPoolExecutor(max_workers=self.max_parallel or len(nodes)) as pool:
                        futures = {
                            pool.submit(self._execute_node, node_id, context): node_id for node_id in nodes
                        }
                        for future, node_id in futures.items():
                            history.extend(future.result())
                            completed.add(node_id)
                else:
                    for node_id in nodes:
                        history.extend(self._execute_node(node_id, context))
                        completed.add(node_id)

                if context.scratch.get("graph_stop"):
                    break

            if context.scratch.get("graph_stop"):
                break

            # Handle dynamic branching: nodes appended to context.scratch["_graph_additions"]
            # as list of (id, GraphNodeSpec)
            additions = context.scratch.pop("_graph_additions", [])
            for node_id, spec in additions:
                if self.branch_budget is not None and branch_additions >= self.branch_budget:
                    continue
                if node_id in self.graph:
                    continue
                self.graph[node_id] = spec
                branch_additions += 1

        events.extend(self.tracer.events)
        return history

    def _ready_nodes(self, completed: Set[str]) -> List[str]:
        ready: List[str] = []
        for node_id, spec in self.graph.items():
            if node_id in completed:
                continue
            if all(parent in completed for parent in spec.after):
                ready.append(node_id)
        return ready

    def _execute_node(self, node_id: str, context: Context) -> List[Message]:
        spec = self.graph[node_id]
        label = spec.name or spec.agent.name or node_id

        if spec.condition and not spec.condition(context):
            context.scratch.setdefault("graph_skipped", []).append(node_id)
            self.tracer.metric("graph.skipped", 1.0, node=node_id, name=label)
            return []

        messages: List[Message] = []

        def _run_once(batch_index: int | None = None) -> None:
            self._check_guards(context.scratch)
            target_context = context
            if self.isolate_parallel_state:
                target_context = Context(
                    goal=context.goal,
                    scratch=dict(context.scratch),
                    registry=context.registry,
                    inbox=list(context.inbox),
                    memory=dict(context.memory),
                    stores=dict(context.stores),
                    policies=dict(context.policies),
                    components=context.components,
                    llm=context.llm,
                    tools=context.tools,
                )
            with self.tracer.span(label, role="graph_node", node=node_id, batch_index=batch_index):
                try:
                    result = spec.agent.run(target_context)
                except Exception:
                    self.tracer.metric("graph.error", 1.0, node=node_id, name=label)
                    raise
                messages.extend(result.out_messages)
                if result.out_messages:
                    context.push_message(result.out_messages[-1])
                context.scratch.update(result.state_updates if target_context is context else target_context.scratch)
                self.tracer.metric(
                    "graph.node.latency",
                    self.tracer.events[-1].payload.get("duration", 0.0) if self.tracer.events else 0.0,
                    node=node_id,
                )
                # Dynamic graph mutations can be emitted via state_updates["_graph_additions"]
                additions = result.state_updates.get("_graph_additions", [])
                if additions:
                    context.scratch.setdefault("_graph_additions", []).extend(additions)
                if result.stop:
                    context.scratch["graph_stop"] = node_id

        if spec.batch_key:
            items = list(context.scratch.get(spec.batch_key, []))
            context.scratch.setdefault("graph_batches", {})[node_id] = len(items)
            for batch_idx, item in enumerate(items):
                context.scratch["batch_item"] = item
                _run_once(batch_idx)
        else:
            _run_once()

        return messages


__all__ = ["GraphRunner", "GraphNodeSpec"]
