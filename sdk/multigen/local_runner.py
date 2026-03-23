"""
Local (in-process) workflow runner for rapid prototyping.

Executes Multigen graph or sequential DSL workflows directly in the current
Python process — no Temporal server, no Kafka, no MongoDB required.

Limitations vs. the full Temporal-backed engine
------------------------------------------------
- No durable execution or crash recovery
- No circuit breakers, reflection loops, or fan-out
- No human-in-the-loop approval gates
- No MongoDB state persistence
- No SSE streaming (results returned when workflow completes)
- Signals (interrupt/resume/skip/jump) are not supported
- Temporal-specific features (task queues, versioning) are ignored

These limitations are by design — local mode is for local dev and testing.

Usage
-----
    from sdk.multigen.local_runner import LocalWorkflowRunner

    runner = LocalWorkflowRunner()
    results = await runner.run(graph_def, payload={"topic": "AI"}, workflow_id="local-1")
    print(results)
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from typing import Any, Deque, Dict, List, Set

logger = logging.getLogger(__name__)


class LocalWorkflowRunner:
    """
    In-process workflow executor for local development mode.

    Supports both graph DSL (``nodes`` + ``edges`` + ``entry``) and
    sequential DSL (``steps`` list).
    """

    # ── Public API ─────────────────────────────────────────────────────────────

    async def run(
        self,
        dsl: Dict[str, Any],
        payload: Dict[str, Any] | None = None,
        workflow_id: str = "local",
    ) -> Dict[str, Any]:
        """
        Execute a DSL definition and return the full results dict.

        Parameters
        ----------
        dsl:
            Either a graph DSL (``{"nodes": [...], "edges": [...], "entry": "..."}``),
            a sequential DSL (``{"steps": [...]}``) or the outer request body
            (``{"graph_def": ..., "payload": ...}``).
        payload:
            Input payload.  Merged with ``dsl["payload"]`` if both present.
        workflow_id:
            Identifier for log output.
        """
        payload = payload or {}

        # Unwrap outer request body format if passed directly
        if "graph_def" in dsl:
            payload = {**dsl.get("payload", {}), **payload}
            dsl = dsl["graph_def"]
        elif "dsl" in dsl:
            payload = {**dsl.get("payload", {}), **payload}
            dsl = dsl["dsl"]

        if "nodes" in dsl:
            return await self._run_graph(dsl, payload, workflow_id)
        elif "steps" in dsl:
            return await self._run_sequential(dsl, payload, workflow_id)
        else:
            raise ValueError(
                "DSL must contain either 'nodes' (graph) or 'steps' (sequential). "
                f"Got keys: {list(dsl.keys())}"
            )

    # ── Graph execution ────────────────────────────────────────────────────────

    async def _run_graph(
        self,
        graph_def: Dict[str, Any],
        payload: Dict[str, Any],
        workflow_id: str,
    ) -> Dict[str, Any]:
        nodes_list: List[Dict[str, Any]] = graph_def.get("nodes", [])
        edges: List[Dict[str, Any]] = graph_def.get("edges", [])
        entry: str = graph_def.get("entry", "")
        max_cycles: int = graph_def.get("max_cycles", 20)

        nodes: Dict[str, Dict[str, Any]] = {n["id"]: n for n in nodes_list}
        context: Dict[str, Any] = {"payload": payload}
        completed: Set[str] = set()
        cycle = 0
        results: Dict[str, Any] = {}

        if not entry:
            raise ValueError("graph_def must specify 'entry' node id")
        if entry not in nodes:
            raise ValueError(f"Entry node '{entry}' not found in nodes list")

        # BFS queue; pending_set prevents double-enqueue on diamond graphs
        pending: Deque[str] = deque([entry])
        pending_set: Set[str] = {entry}

        logger.info("[%s] local-runner: starting graph with entry=%s", workflow_id, entry)

        while pending and cycle < max_cycles:
            # Collect all nodes whose dependencies are satisfied this wave
            wave: List[str] = []
            deferred: List[str] = []

            while pending:
                nid = pending.popleft()
                pending_set.discard(nid)
                if nid in completed:
                    continue
                if self._deps_satisfied(nid, nodes, edges, completed):
                    wave.append(nid)
                else:
                    deferred.append(nid)

            # Re-queue deferred nodes (dependencies not yet met)
            for nid in deferred:
                if nid not in pending_set:
                    pending.append(nid)
                    pending_set.add(nid)

            if not wave:
                if deferred:
                    logger.warning(
                        "[%s] local-runner: deadlock — nodes waiting on unsatisfied deps: %s",
                        workflow_id, deferred,
                    )
                break

            # Execute the wave in parallel
            wave_results = await asyncio.gather(
                *[self._execute_node(nid, nodes[nid], context, workflow_id) for nid in wave],
                return_exceptions=True,
            )

            for nid, result in zip(wave, wave_results):
                if isinstance(result, BaseException):
                    logger.error("[%s] node '%s' failed: %s", workflow_id, nid, result)
                    context[nid] = {"error": str(result)}
                    results[nid] = {"error": str(result)}
                else:
                    context[nid] = result
                    results[nid] = result
                completed.add(nid)

            # Enqueue successors whose deps are now satisfied
            for edge in edges:
                if edge.get("source") not in completed:
                    continue
                target = edge.get("target", "")
                if not target or target in completed or target in pending_set:
                    continue
                cond = edge.get("condition", "")
                if cond and not self._eval_condition(cond, context):
                    continue
                pending.append(target)
                pending_set.add(target)

            # Also check explicit depends_on for nodes not reachable via edges
            for nid, node in nodes.items():
                if nid in completed or nid in pending_set:
                    continue
                if self._deps_satisfied(nid, nodes, edges, completed):
                    pending.append(nid)
                    pending_set.add(nid)

            cycle += 1

        logger.info(
            "[%s] local-runner: graph complete — %d nodes executed in %d cycles",
            workflow_id, len(completed), cycle,
        )

        return {
            "workflow_id": workflow_id,
            "status": "completed",
            "nodes_executed": list(completed),
            "results": results,
            "payload": payload,
        }

    def _deps_satisfied(
        self,
        nid: str,
        nodes: Dict[str, Dict[str, Any]],
        edges: List[Dict[str, Any]],
        completed: Set[str],
    ) -> bool:
        """Return True only if all upstream deps for *nid* are completed."""
        edge_deps = {e["source"] for e in edges if e.get("target") == nid}
        explicit_deps: List[str] = nodes.get(nid, {}).get("depends_on") or []
        all_deps = edge_deps | set(explicit_deps)
        return all(dep in completed for dep in all_deps)

    def _eval_condition(self, condition: str, context: Dict[str, Any]) -> bool:
        """Safely evaluate an edge condition expression."""
        try:
            return bool(eval(condition, {"__builtins__": {}}, context))  # noqa: S307
        except Exception as exc:
            logger.warning("local-runner: condition eval failed ('%s'): %s", condition, exc)
            return True  # fail-open: follow the edge

    async def _execute_node(
        self,
        node_id: str,
        node: Dict[str, Any],
        context: Dict[str, Any],
        workflow_id: str,
    ) -> Dict[str, Any]:
        """Run a single graph node and return its output."""
        from orchestrator.services.agent_registry import get_agent

        agent_name: str = node.get("agent", "")
        if not agent_name:
            logger.warning("[%s] node '%s' has no agent — skipping", workflow_id, node_id)
            return {"skipped": True, "node_id": node_id}

        # Resolve params (simple {{key}} template substitution)
        params = self._resolve_params(node.get("params", {}), context)

        logger.info("[%s] executing node '%s' via agent '%s'", workflow_id, node_id, agent_name)

        agent = get_agent(agent_name)
        try:
            result = await agent.run(params)
        except Exception as exc:
            logger.exception("[%s] node '%s' agent error", workflow_id, node_id)
            raise RuntimeError(f"Agent '{agent_name}' failed on node '{node_id}': {exc}") from exc

        # Flatten confidence to top-level for convenience
        output = result.get("output", result)
        ep = output.get("epistemic", {}) if isinstance(output, dict) else {}
        confidence = ep.get("confidence", 0.5) if isinstance(ep, dict) else 0.5

        return {
            "node_id": node_id,
            "agent": agent_name,
            "confidence": confidence,
            "output": output,
        }

    # ── Sequential execution ───────────────────────────────────────────────────

    async def _run_sequential(
        self,
        dsl: Dict[str, Any],
        payload: Dict[str, Any],
        workflow_id: str,
    ) -> Dict[str, Any]:
        steps: List[Dict[str, Any]] = dsl.get("steps", [])
        context: Dict[str, Any] = {"payload": payload}
        results: List[Dict[str, Any]] = []

        logger.info("[%s] local-runner: starting sequential workflow with %d steps", workflow_id, len(steps))

        for step in steps:
            step_name = step.get("name", "unknown")

            if "parallel" in step:
                # Run branches concurrently
                branches = step["parallel"]
                branch_results = await asyncio.gather(
                    *[self._run_step(b, context, workflow_id) for b in branches],
                    return_exceptions=True,
                )
                merged: Dict[str, Any] = {}
                for b, r in zip(branches, branch_results):
                    bname = b.get("name", "branch")
                    if isinstance(r, BaseException):
                        logger.error("[%s] parallel branch '%s' failed: %s", workflow_id, bname, r)
                        merged[bname] = {"error": str(r)}
                    else:
                        merged[bname] = r
                context[step_name] = merged
                results.append({"name": step_name, "type": "parallel", "output": merged})

            elif "conditional" in step:
                # Evaluate conditions and run matching branch
                chosen = await self._run_conditional(step, context, workflow_id)
                context[step_name] = chosen
                results.append({"name": step_name, "type": "conditional", "output": chosen})

            elif "loop" in step:
                loop_result = await self._run_loop(step, context, workflow_id)
                context[step_name] = loop_result
                results.append({"name": step_name, "type": "loop", "output": loop_result})

            elif "graph" in step:
                graph_result = await self._run_graph(step["graph"], context, workflow_id)
                context[step_name] = graph_result
                results.append({"name": step_name, "type": "graph", "output": graph_result})

            else:
                step_result = await self._run_step(step, context, workflow_id)
                context[step_name] = step_result
                results.append({"name": step_name, "output": step_result})

        logger.info("[%s] local-runner: sequential workflow complete", workflow_id)

        return {
            "workflow_id": workflow_id,
            "status": "completed",
            "steps": results,
            "payload": payload,
        }

    async def _run_step(
        self,
        step: Dict[str, Any],
        context: Dict[str, Any],
        workflow_id: str,
    ) -> Dict[str, Any]:
        from orchestrator.services.agent_registry import get_agent

        agent_name = step.get("agent", "")
        step_name = step.get("name", "unknown")
        if not agent_name:
            return {"skipped": True, "step": step_name}

        params = self._resolve_params(step.get("params", {}), context)
        logger.info("[%s] step '%s' → agent '%s'", workflow_id, step_name, agent_name)

        agent = get_agent(agent_name)
        result = await agent.run(params)
        output = result.get("output", result)
        ep = output.get("epistemic", {}) if isinstance(output, dict) else {}
        confidence = ep.get("confidence", 0.5) if isinstance(ep, dict) else 0.5

        return {"step": step_name, "agent": agent_name, "confidence": confidence, "output": output}

    async def _run_conditional(
        self,
        step: Dict[str, Any],
        context: Dict[str, Any],
        workflow_id: str,
    ) -> Dict[str, Any]:
        branches = step.get("conditional", [])
        else_branch = step.get("else")

        for branch in branches:
            cond = branch.get("condition", "")
            if self._eval_condition(cond, context):
                return await self._run_step(branch.get("then", branch), context, workflow_id)

        if else_branch:
            return await self._run_step(else_branch, context, workflow_id)

        return {"skipped": True, "reason": "no condition matched"}

    async def _run_loop(
        self,
        step: Dict[str, Any],
        context: Dict[str, Any],
        workflow_id: str,
    ) -> Dict[str, Any]:
        loop_spec = step.get("loop", {})
        until = loop_spec.get("until", "")
        max_iter = loop_spec.get("max_iterations", 10)
        sub_steps = loop_spec.get("steps", [])

        iteration = 0
        last_result: Dict[str, Any] = {}

        while iteration < max_iter:
            for sub in sub_steps:
                last_result = await self._run_step(sub, context, workflow_id)
                context[sub.get("name", f"loop_step_{iteration}")] = last_result

            if until and self._eval_condition(until, context):
                break
            iteration += 1

        return {"iterations": iteration + 1, "last_output": last_result}

    # ── Parameter resolution ───────────────────────────────────────────────────

    def _resolve_params(
        self,
        params: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Perform simple ``{{key}}`` and ``{{steps.node.output.field}}`` substitution
        on string parameter values.  Non-string values are passed through unchanged.
        """
        import re

        def _resolve_value(v: Any) -> Any:
            if not isinstance(v, str):
                return v
            # Replace {{...}} expressions
            def replacer(m: re.Match) -> str:  # type: ignore[type-arg]
                expr = m.group(1).strip()
                # Walk dot-separated keys into context
                parts = expr.split(".")
                obj: Any = context
                for part in parts:
                    if isinstance(obj, dict):
                        obj = obj.get(part, m.group(0))
                    else:
                        return m.group(0)
                return str(obj) if not isinstance(obj, str) else obj
            return re.sub(r"\{\{([^}]+)\}\}", replacer, v)

        return {k: _resolve_value(v) for k, v in params.items()}
