"""
Multigen Graph Workflow Engine — Enterprise Edition.

Capabilities over v1
────────────────────
SIGNAL / RUNTIME CONTROL
  interrupt               pause at next node boundary; resume to continue
  resume                  unpause
  inject_node <json>      append a new node + optional edges_to wiring
  jump_to <node_id>       push node to FRONT of queue (priority lane)
  skip_node <node_id>     mark a node to be silently dropped when reached
  reroute <json>          add a dynamic edge {source, target, condition}
  fan_out <json>          run N nodes in parallel + consensus selection
  prune_branch <node_id>  cancel node and all reachable descendants

REASONING QUALITY
  confidence scoring      every node output scored 0-1
  reflection loops        low-confidence outputs auto-trigger critic nodes
  fan-out / join          parallel hypothesis exploration + consensus merge
  circuit breakers        per-node CLOSED/OPEN/HALF_OPEN with fallback routing
  self-healing            fallback_agent field; dead-letter capture on total failure

DYNAMIC AGENT CREATION
  blueprint nodes         nodes with 'blueprint' field instead of 'agent'
                          spawn a new agent class at runtime via activity

OBSERVABILITY
  OTel span per node      with node_id, agent, iteration, circuit_state, confidence
  Prometheus counters     nodes_total, errors_total, reflections_total, circuit_open
  Temporal query API      get_health, get_metrics, get_pending_count
  MongoDB CQRS            each node output persisted after execution

DSL shape (graph field of a step):
    {
      "nodes": [
        {"id": "think",  "agent": "PlannerAgent",  "params": {},
         "tools": [...], "retry": 3, "timeout": 30,
         "reflection_threshold": 0.7, "max_reflections": 2,
         "critic_agent": "CritiqueAgent",
         "fallback_agent": "FallbackPlannerAgent"},
        {"id": "custom", "blueprint": {"system_prompt": "...", "instruction": "..."}},
      ],
      "edges": [
        {"source": "think", "target": "act"},
        {"source": "act",   "target": "check", "condition": "quality_score < 0.9"}
      ],
      "entry":      "think",
      "max_cycles": 5,
      "circuit_breaker": {"trip_threshold": 3, "recovery_executions": 5}
    }
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from datetime import timedelta
from typing import Any, Deque, Dict, List, Optional

from temporalio import activity, workflow
from temporalio.common import RetryPolicy

import orchestrator.services.config as config
from flow_engine.graph.circuit_breaker import CircuitBreakerRegistry
from flow_engine.graph.reasoning import (
    build_reflection_node,
    compute_descendants,
    extract_confidence,
    prune_pending,
    select_consensus,
    should_reflect,
)
from flow_engine.graph.telemetry import GraphTelemetry
from flow_engine.workflows.sequence import (
    _resolve_refs,
    agent_activity,
    evaluate_condition,
)

logger = logging.getLogger(__name__)


# ─── Activities ────────────────────────────────────────────────────────────────

@activity.defn
async def tool_activity(
    tool_name: str,
    tool_config: Dict[str, Any],
    context_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Execute one tool and return its result.

    Bridges to agentic_codex adapters: HTTPToolAdapter, RAGToolAdapter,
    MathToolAdapter, SanitizedCodeExecutionToolAdapter, DBToolAdapter.
    Results are injected into the next agent's params under _tools.<name>.
    """
    try:
        from agentic_codex.core.tools import (
            DBToolAdapter,
            HTTPToolAdapter,
            MathToolAdapter,
            RAGToolAdapter,
            SanitizedCodeExecutionToolAdapter,
        )

        adapters: Dict[str, Any] = {
            "http": HTTPToolAdapter,
            "rag":  RAGToolAdapter,
            "math": MathToolAdapter,
            "code": SanitizedCodeExecutionToolAdapter,
            "db":   DBToolAdapter,
        }
        adapter_cls = adapters.get(tool_name)
        if adapter_cls is None:
            return {"tool": tool_name, "error": f"Unknown tool type: {tool_name}"}

        init_kwargs = {k: v for k, v in tool_config.items() if k != "invoke_kwargs"}
        invoke_kwargs = tool_config.get("invoke_kwargs", {})
        invoke_kwargs.setdefault("context", context_snapshot)

        adapter = adapter_cls(**init_kwargs)
        result = adapter.invoke(**invoke_kwargs)
        return {"tool": tool_name, "result": result}

    except Exception as exc:
        logger.exception("tool_activity failed: %s", tool_name)
        return {"tool": tool_name, "error": str(exc)}


@activity.defn
async def persist_node_state_activity(
    workflow_id: str,
    node_id: str,
    output: Dict[str, Any],
) -> None:
    """
    CQRS write side: persist node output to MongoDB graph_state collection.
    Non-fatal — Temporal event history remains the authoritative source of truth.
    """
    try:
        from datetime import datetime, timezone

        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(config.MONGODB_URI)
        col = client[config.GRAPH_STATE_DB_NAME][config.GRAPH_STATE_COLLECTION]
        await col.update_one(
            {"workflow_id": workflow_id, "node_id": node_id},
            {
                "$set": {
                    "output": output,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            },
            upsert=True,
        )
    except Exception as exc:
        logger.warning("persist_node_state_activity failed: %s", exc)


@activity.defn
async def create_agent_activity(agent_name: str, blueprint: Dict[str, Any]) -> str:
    """
    Create and register a dynamic agent from a blueprint dict.
    Runs as a Temporal activity so registry mutation happens outside
    the sandboxed workflow context.
    Returns agent_name for chaining.
    """
    from flow_engine.graph.agent_factory import create_agent_from_blueprint
    create_agent_from_blueprint(agent_name, blueprint)
    return agent_name


# ─── Graph Workflow ────────────────────────────────────────────────────────────

@workflow.defn
class GraphWorkflow:
    """
    Enterprise-grade Temporal workflow for directed graph agent execution.

    Execution model
    ───────────────
    1. Start with the entry node in the pending BFS queue.
    2. Absorb any pending jump / inject / fan-out specs at each iteration.
    3. Check interrupt signal → wait at node boundary if paused.
    4. Check circuit breaker → skip or route to fallback_agent if OPEN.
    5. Check skip_nodes set → silently drop if matched.
    6. Run tool activities → inject results into agent params.
    7. Run agent activity (or create from blueprint first).
    8. Score confidence → inject reflection node if below threshold.
    9. Persist output to MongoDB (non-fatal).
    10. Evaluate outgoing edges + dynamic edges → enqueue successors.
    11. Repeat until pending queue and all injection queues are empty.
    """

    def __init__(self) -> None:
        # ── Signal buffers (consumed each iteration) ──────────────────────
        self._interrupted: bool = False
        self._injected: List[Dict[str, Any]] = []       # inject_node specs
        self._jump_queue: Deque[str] = deque()          # priority node IDs
        self._skip_nodes: set = set()                    # nodes to silently drop
        self._dynamic_edges: List[Dict[str, Any]] = []  # reroute additions
        self._fan_out_specs: List[Dict[str, Any]] = []  # fan_out groups
        self._prune_requests: List[str] = []             # prune_branch targets
        # ── Shared mutable graph state (updated from run()) ───────────────
        self._raw_edges: List[Dict[str, Any]] = []
        # ── Live metrics (exposed via query handlers) ─────────────────────
        self._nodes_executed: int = 0
        self._nodes_skipped: int = 0
        self._reflections_triggered: int = 0
        self._fan_outs_executed: int = 0
        self._cb_trips: int = 0
        self._pending_count: int = 0
        self._errors: List[str] = []
        self._dead_letters: List[Dict[str, Any]] = []   # unrecoverable failures

    # ── Signal handlers ───────────────────────────────────────────────────────

    @workflow.signal
    def interrupt(self) -> None:
        """Pause before the next node starts."""
        self._interrupted = True
        logger.info("GraphWorkflow: INTERRUPT signal received")

    @workflow.signal
    def resume(self) -> None:
        """Resume a paused workflow."""
        self._interrupted = False
        logger.info("GraphWorkflow: RESUME signal received")

    @workflow.signal
    def inject_node(self, node_json: str) -> None:
        """
        Append a new node to the end of the pending queue.

        node_json keys: id, agent, params, tools, retry, timeout, edges_to,
                        blueprint (for dynamic agents), reflection_threshold,
                        fallback_agent
        """
        try:
            self._injected.append(json.loads(node_json))
            logger.info("GraphWorkflow: inject_node received")
        except json.JSONDecodeError as exc:
            logger.error("inject_node: invalid JSON: %s", exc)

    @workflow.signal
    def jump_to(self, node_id: str) -> None:
        """Push node_id to the FRONT of the execution queue (priority execution)."""
        self._jump_queue.appendleft(node_id)
        logger.info("GraphWorkflow: jump_to '%s' received", node_id)

    @workflow.signal
    def skip_node(self, node_id: str) -> None:
        """Silently drop node_id whenever it appears in the pending queue."""
        self._skip_nodes.add(node_id)
        logger.info("GraphWorkflow: skip_node '%s' received", node_id)

    @workflow.signal
    def reroute(self, edge_json: str) -> None:
        """
        Add a dynamic edge at runtime.
        edge_json: {"source": "nodeA", "target": "nodeB", "condition": ""}
        """
        try:
            edge = json.loads(edge_json)
            self._dynamic_edges.append(edge)
            self._raw_edges.append(edge)  # keep consolidated list current
            logger.info(
                "GraphWorkflow: reroute %s → %s",
                edge.get("source"), edge.get("target"),
            )
        except json.JSONDecodeError as exc:
            logger.error("reroute: invalid JSON: %s", exc)

    @workflow.signal
    def fan_out(self, fan_json: str) -> None:
        """
        Inject N nodes to execute in parallel, then aggregate via consensus.

        fan_json:
          {
            "group_id":  "my_fanout",
            "consensus": "highest_confidence|aggregate|majority_vote|first_success",
            "nodes":     [<node_def>, ...]
          }
        """
        try:
            self._fan_out_specs.append(json.loads(fan_json))
            logger.info("GraphWorkflow: fan_out received")
        except json.JSONDecodeError as exc:
            logger.error("fan_out: invalid JSON: %s", exc)

    @workflow.signal
    def prune_branch(self, node_id: str) -> None:
        """Cancel node_id and all reachable descendants from it."""
        self._prune_requests.append(node_id)
        logger.info("GraphWorkflow: prune_branch '%s' received", node_id)

    # ── Query handlers (live introspection without touching Temporal history) ──

    @workflow.query
    def get_health(self) -> Dict[str, Any]:
        """Circuit breaker status + interrupt/skip state."""
        return {
            "interrupted": self._interrupted,
            "pending_count": self._pending_count,
            "skip_nodes": list(self._skip_nodes),
            "cb_trips_total": self._cb_trips,
            "errors": self._errors[-20:],  # last 20
            "dead_letters": [d.get("node_id") for d in self._dead_letters],
        }

    @workflow.query
    def get_metrics(self) -> Dict[str, Any]:
        """Execution statistics for monitoring dashboards."""
        return {
            "nodes_executed": self._nodes_executed,
            "nodes_skipped": self._nodes_skipped,
            "reflections_triggered": self._reflections_triggered,
            "fan_outs_executed": self._fan_outs_executed,
            "circuit_breaker_trips": self._cb_trips,
            "error_count": len(self._errors),
            "dead_letter_count": len(self._dead_letters),
        }

    @workflow.query
    def get_pending_count(self) -> int:
        return self._pending_count

    # ── Run ───────────────────────────────────────────────────────────────────

    @workflow.run
    async def run(
        self,
        graph_def: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Execute the graph and return:
            nodes_executed, context, results, skipped_cycles, dead_letters
        """
        # ── Parse definition ──────────────────────────────────────────────
        raw_nodes: List[Dict] = graph_def.get("nodes", [])
        self._raw_edges = list(graph_def.get("edges", []))
        entry: str = graph_def.get("entry", "")
        max_cycles: int = int(graph_def.get("max_cycles", 20))
        workflow_id: str = workflow.info().workflow_id

        cb_cfg = graph_def.get("circuit_breaker", {})
        cb = CircuitBreakerRegistry(
            trip_threshold=int(cb_cfg.get("trip_threshold", 3)),
            recovery_executions=int(cb_cfg.get("recovery_executions", 5)),
        )
        tel = GraphTelemetry(workflow_id)

        nodes: Dict[str, Dict] = {n["id"]: n for n in raw_nodes}

        def _all_edges() -> List[Dict]:
            return self._raw_edges  # kept current by reroute signal

        def _outgoing(node_id: str) -> List[Dict]:
            return [e for e in _all_edges() if e.get("source") == node_id]

        # ── Execution state ───────────────────────────────────────────────
        context: Dict[str, Any] = {}
        pending: Deque[str] = deque([entry])
        execution_count: Dict[str, int] = {}
        results: List[Dict[str, Any]] = []
        skipped_cycles: List[str] = []

        # ── Inner helpers ─────────────────────────────────────────────────

        async def _run_tools(node: Dict) -> Dict[str, Any]:
            tool_results: Dict[str, Any] = {}
            for tool_spec in node.get("tools", []):
                t_name = tool_spec.get("name", "")
                t_cfg = tool_spec.get("config", {})
                tool_out = await workflow.execute_activity(
                    tool_activity,
                    args=[t_name, t_cfg, context],
                    start_to_close_timeout=timedelta(seconds=15),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                tool_results[t_name] = tool_out.get("result") or tool_out.get("error")
            return tool_results

        async def _run_agent(node: Dict, enriched_params: Dict) -> Dict[str, Any]:
            """
            Resolve or create the agent for this node, then execute it.
            If the node has a 'blueprint' key instead of 'agent', a dynamic
            agent is created via activity first.
            """
            agent_name = node.get("agent") or node.get("id")
            blueprint = node.get("blueprint")

            if blueprint and not node.get("agent"):
                dyn_name = f"blueprint_{node['id']}"
                await workflow.execute_activity(
                    create_agent_activity,
                    args=[dyn_name, blueprint],
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )
                agent_name = dyn_name

            retry_count = node.get("retry", 3)
            timeout_sec = node.get("timeout", 30)
            return await workflow.execute_activity(
                agent_activity,
                args=[agent_name, enriched_params],
                start_to_close_timeout=timedelta(seconds=timeout_sec),
                retry_policy=RetryPolicy(
                    initial_interval=timedelta(seconds=1),
                    maximum_attempts=retry_count,
                ),
            )

        async def _execute_node_full(node: Dict) -> Optional[Dict[str, Any]]:
            """
            Full node execution pipeline:
            tools → agent → persist → return result dict or None on hard failure.
            """
            node_id = node["id"]
            agent_name = node.get("agent") or (
                f"blueprint_{node_id}" if node.get("blueprint") else node_id
            )
            iteration = execution_count.get(node_id, 0)
            cb_state = "open" if node_id in cb._breakers and not cb.can_execute(node_id, self._nodes_executed) else "closed"

            t0 = time.monotonic()
            with tel.node_span(node_id, agent_name, iteration, cb_state):
                try:
                    tool_results = await _run_tools(node)
                    base_params = node.get("params") or payload
                    enriched = {**base_params, "_tools": tool_results}
                    enriched = _resolve_refs(enriched, context)

                    agent_result = await _run_agent(node, enriched)
                    duration = time.monotonic() - t0

                    output = agent_result.get("output", {})
                    confidence = extract_confidence(output)
                    tel.annotate_span("graph.confidence", confidence)
                    tel.record_node_complete(node_id, duration, confidence, "success")
                    cb.record_success(node_id)
                    return {**agent_result, "node_id": node_id, "iteration": iteration + 1, "confidence": confidence}

                except Exception as exc:
                    duration = time.monotonic() - t0
                    cb.record_failure(node_id, self._nodes_executed)
                    tel.record_error(node_id)
                    tel.record_node_complete(node_id, duration, 0.0, "error")
                    err_msg = f"{node_id}: {exc}"
                    self._errors.append(err_msg)
                    logger.error("Node '%s' failed: %s", node_id, exc)
                    return {"node_id": node_id, "agent": agent_name, "error": str(exc)}

        # ── Main loop ─────────────────────────────────────────────────────
        while pending or self._injected or self._jump_queue or self._fan_out_specs:

            # 1. Interrupt — pause at node boundary
            if self._interrupted:
                logger.info("GraphWorkflow paused")
                await workflow.wait_condition(lambda: not self._interrupted)
                logger.info("GraphWorkflow resumed")

            # 2. Handle prune requests
            while self._prune_requests:
                root = self._prune_requests.pop(0)
                prune_pending(pending, self._skip_nodes, root, _all_edges())
                logger.info("GraphWorkflow: pruned branch from '%s'", root)

            # 3. Priority jumps — push to front of pending
            while self._jump_queue:
                jump_id = self._jump_queue.popleft()
                pending.appendleft(jump_id)
                logger.info("GraphWorkflow: jump_to '%s' queued at front", jump_id)

            # 4. Fan-out groups — parallel execution + consensus
            while self._fan_out_specs:
                spec = self._fan_out_specs.pop(0)
                group_id = spec.get("group_id", f"fanout_{self._nodes_executed}")
                consensus_strategy = spec.get("consensus", "highest_confidence")
                fan_nodes_defs = spec.get("nodes", [])

                for fn in fan_nodes_defs:
                    nodes[fn["id"]] = fn

                self._fan_outs_executed += 1
                tel.record_fan_out()

                fan_results_raw = await asyncio.gather(
                    *[_execute_node_full(fn) for fn in fan_nodes_defs],
                    return_exceptions=False,
                )
                fan_results = [r for r in fan_results_raw if r is not None]
                self._nodes_executed += len(fan_results)

                best = select_consensus(fan_results, consensus_strategy)
                context[group_id] = best.get("output", {})
                results.extend(r for r in fan_results if "error" not in r)

                # Persist consensus output
                await workflow.execute_activity(
                    persist_node_state_activity,
                    args=[workflow_id, group_id, context[group_id]],
                    start_to_close_timeout=timedelta(seconds=10),
                    retry_policy=RetryPolicy(maximum_attempts=2),
                )

            # 5. Absorb injected nodes → append to pending
            while self._injected:
                injected = self._injected.pop(0)
                node_id = injected.get("id", f"injected_{self._nodes_executed}")
                nodes[node_id] = injected
                pending.append(node_id)
                for target in injected.get("edges_to", []):
                    new_edge = {"source": node_id, "target": target, "condition": ""}
                    self._raw_edges.append(new_edge)
                logger.info("GraphWorkflow: absorbed injected node '%s'", node_id)

            if not pending:
                break

            self._pending_count = len(pending)
            node_id = pending.popleft()
            self._pending_count = len(pending)

            # 6. Skip check
            if node_id in self._skip_nodes:
                self._nodes_skipped += 1
                logger.info("GraphWorkflow: skipping node '%s' (in skip set)", node_id)
                continue

            node = nodes.get(node_id)
            if node is None:
                logger.warning("GraphWorkflow: unknown node '%s' — skipping", node_id)
                continue

            # 7. Cycle guard
            count = execution_count.get(node_id, 0)
            if count >= max_cycles:
                logger.warning("GraphWorkflow: node '%s' hit max_cycles=%d", node_id, max_cycles)
                skipped_cycles.append(node_id)
                continue

            # 8. Circuit breaker
            if not cb.can_execute(node_id, self._nodes_executed):
                self._cb_trips += 1
                tel.record_circuit_open(node_id)
                fallback = node.get("fallback_agent")
                if fallback:
                    logger.warning("CB OPEN '%s' → fallback '%s'", node_id, fallback)
                    fallback_node = {**node, "agent": fallback, "id": f"{node_id}__fb"}
                    nodes[fallback_node["id"]] = fallback_node
                    pending.appendleft(fallback_node["id"])
                else:
                    logger.warning("CB OPEN '%s' — no fallback, skipping", node_id)
                    self._nodes_skipped += 1
                    self._dead_letters.append({"node_id": node_id, "reason": "circuit_open"})
                continue

            execution_count[node_id] = count + 1
            self._nodes_executed += 1

            # 9. Execute node
            result = await _execute_node_full(node)
            if result is None:
                continue

            if "error" in result:
                # Hard failure after all retries — check for fallback
                fallback = node.get("fallback_agent")
                if fallback and f"{node_id}__fb" not in nodes:
                    fb_node = {**node, "agent": fallback, "id": f"{node_id}__fb"}
                    nodes[fb_node["id"]] = fb_node
                    pending.appendleft(fb_node["id"])
                    logger.info("Self-heal: routing '%s' to fallback '%s'", node_id, fallback)
                else:
                    self._dead_letters.append(result)
                continue

            # 10. Store output
            output = result.get("output", {})
            context[node_id] = output
            results.append(result)

            # 11. Persist to MongoDB (non-fatal)
            await workflow.execute_activity(
                persist_node_state_activity,
                args=[workflow_id, node_id, output],
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )

            # 12. Reflection — auto-inject critic if confidence too low
            reflection_threshold = float(node.get("reflection_threshold", 0.0))
            max_reflections = int(node.get("max_reflections", 2))
            reflect_key = f"__reflect_count_{node_id}"
            reflection_count = context.get(reflect_key, 0)

            if should_reflect(output, reflection_threshold, reflection_count, max_reflections):
                critic_agent = node.get("critic_agent", "CritiqueAgent")
                ref_node = build_reflection_node(
                    node_id, reflection_count + 1, critic_agent, output
                )
                context[reflect_key] = reflection_count + 1
                nodes[ref_node["id"]] = ref_node
                pending.appendleft(ref_node["id"])  # priority: reflect immediately
                self._reflections_triggered += 1
                tel.record_reflection(node_id)
                logger.info(
                    "Reflection triggered for '%s' (confidence=%.2f, round=%d)",
                    node_id, extract_confidence(output), reflection_count + 1,
                )

            # 13. Evaluate outgoing edges → enqueue successors
            for edge in _outgoing(node_id):
                condition = edge.get("condition", "")
                if not condition or evaluate_condition(condition, context):
                    target = edge["target"]
                    if target not in self._skip_nodes:
                        pending.append(target)

        return {
            "nodes_executed": self._nodes_executed,
            "context": context,
            "results": results,
            "skipped_cycles": skipped_cycles,
            "dead_letters": self._dead_letters,
            "reflections": self._reflections_triggered,
            "fan_outs": self._fan_outs_executed,
            "circuit_breaker_trips": self._cb_trips,
        }
