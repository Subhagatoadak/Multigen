"""
Flow engine workflow: runs a dynamic sequence of agent-driven steps via Temporal,
with support for parallel execution, conditional branching, dynamic subtrees,
and error resilience.
"""
import ast
import logging
import operator
import re
from datetime import timedelta
from typing import Any, Dict, List, Optional
import asyncio

from temporalio import activity, workflow
from temporalio.client import Client
from temporalio.common import RetryPolicy
from opentelemetry import trace

import orchestrator.services.config as config
from orchestrator.services.agent_registry import get_agent

_tracer = trace.get_tracer(__name__)

# ─── Output reference resolver ─────────────────────────────────────────────────

_REF_RE = re.compile(r"\{\{steps\.([^.}\s]+)\.output\.([^}\s]+)\}\}")


def _resolve_refs(value: Any, context: Dict[str, Any]) -> Any:
    """
    Recursively replace ``{{steps.<step>.output.<key.path>}}`` placeholders
    with the actual value from the accumulated step-output context.

    Supports nested key paths: ``{{steps.parse.output.profile.skills}}``.
    If the reference cannot be resolved the placeholder is left intact.
    """
    if isinstance(value, str):
        def _sub(match: re.Match) -> str:
            step_name = match.group(1)
            key_path = match.group(2).split(".")
            obj: Any = context.get(step_name, {})
            for key in key_path:
                if isinstance(obj, dict):
                    obj = obj.get(key)
                else:
                    return match.group(0)  # unresolvable — leave as-is
            return str(obj) if obj is not None else match.group(0)
        return _REF_RE.sub(_sub, value)
    if isinstance(value, dict):
        return {k: _resolve_refs(v, context) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_refs(item, context) for item in value]
    return value


if not hasattr(workflow, "WorkflowError"):
    class WorkflowError(Exception):
        """Aggregated workflow-level error for failed steps."""
    setattr(workflow, "WorkflowError", WorkflowError)


# ─── Safe condition evaluator ─────────────────────────────────────────────────

_SAFE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


def _eval_node(node: ast.AST, ctx: Dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return ctx.get(node.id)
    if isinstance(node, ast.Attribute):
        obj = _eval_node(node.value, ctx)
        return getattr(obj, node.attr, None) if obj is not None else None
    if isinstance(node, ast.Subscript):
        obj = _eval_node(node.value, ctx)
        key = _eval_node(node.slice, ctx)
        try:
            return obj[key]
        except (KeyError, IndexError, TypeError):
            return None
    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, ctx)
        for op_node, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, ctx)
            op_fn = _SAFE_OPS.get(type(op_node))
            if op_fn is None:
                raise ValueError(f"Unsupported operator: {type(op_node).__name__}")
            if not op_fn(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.BoolOp):
        values = [_eval_node(v, ctx) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval_node(node.operand, ctx)
    raise ValueError(f"Unsupported expression node: {type(node).__name__}")


_condition_logger = logging.getLogger(__name__)


def evaluate_condition(condition: str, context: Dict[str, Any]) -> bool:
    """
    Safely evaluate a condition string against a context dict of prior step outputs.
    Only comparisons, boolean ops, attribute/subscript access, and constants are allowed.
    Returns False on any parse or evaluation error (logged at DEBUG level).
    """
    try:
        tree = ast.parse(condition, mode='eval')
        result = _eval_node(tree.body, context)
        return bool(result)
    except SyntaxError as exc:
        _condition_logger.debug("Condition syntax error %r: %s", condition, exc)
        return False
    except Exception as exc:
        _condition_logger.debug("Condition evaluation failed %r: %s", condition, exc)
        return False


# ─── Activities ───────────────────────────────────────────────────────────────

@activity.defn
async def agent_activity(
    agent_name: str,
    params: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Activity that locates and executes the named agent with given parameters.

    Lazy hydration: if the agent is not in the local registry (e.g. it was
    created as a dynamic agent on a different worker replica), this activity
    fetches the blueprint spec from the distributed agent store (MongoDB) and
    reconstructs the agent locally before executing.

    May raise on failure; Temporal retry_policy applies automatically.
    """
    with _tracer.start_as_current_span(f"agent.{agent_name}") as span:
        span.set_attribute("agent.name", agent_name)
        # Try local registry first (fast path — almost all agents)
        from orchestrator.services.agent_registry import _registry
        if agent_name not in _registry:
            # Lazy hydration: reconstruct dynamic agent from distributed store
            from flow_engine.graph.agent_store import hydrate_agent_if_missing
            hydrated = await hydrate_agent_if_missing(agent_name)
            if not hydrated:
                raise RuntimeError(
                    f"Agent '{agent_name}' not found in local registry or distributed "
                    f"agent store. Available locally: {sorted(_registry.keys())}"
                )
        agent = get_agent(agent_name)
        result = await agent.run(params)
        return {"agent": agent_name, "output": result}


# ─── Workflow ─────────────────────────────────────────────────────────────────

@workflow.defn
class ComplexSequenceWorkflow:
    """
    A Temporal workflow that executes a list of steps, each of which may be:
      - sequential: run one agent
      - parallel:   run multiple agents concurrently
      - conditional: evaluate expressions against prior outputs and branch
      - dynamic:    run a spawner agent and execute its returned steps

    Each step dict carries a 'type' key plus type-specific fields.
    """

    @workflow.run
    async def run(
        self,
        steps: List[Dict[str, Any]],
        payload: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        errors: List[str] = []
        # Accumulated outputs keyed by step name for condition evaluation
        context: Dict[str, Any] = {}

        async def _execute(step: Dict[str, Any]) -> Optional[Dict[str, Any]]:
            agent_name = step.get("agent") or step.get("name")
            raw_params = step.get("params") or payload
            params = _resolve_refs(raw_params, context)
            retry_count = step.get("retry", 3)
            policy = RetryPolicy(
                initial_interval=timedelta(seconds=1),
                maximum_attempts=retry_count,
            )
            try:
                return await workflow.execute_activity(
                    agent_activity,
                    args=[agent_name, params],
                    start_to_close_timeout=timedelta(seconds=30),
                    retry_policy=policy,
                )
            except Exception as exc:
                return {"agent": agent_name, "error": str(exc)}

        for step in steps:
            step_type = step.get("type", "sequential")
            step_name = step.get("name", "")

            if step_type == "parallel":
                group = step.get("parallel_with", [])
                batch = await asyncio.gather(*[_execute(s) for s in group])
                for res in batch:
                    if res is None:
                        continue
                    if "error" in res:
                        errors.append(f"{res.get('agent')}: {res['error']}")
                    else:
                        context[step_name] = res.get("output", {})
                        results.append(res)

            elif step_type == "conditional":
                chosen: Optional[Dict[str, Any]] = None
                else_branch: Optional[Dict[str, Any]] = None

                for branch in step.get("branches", []):
                    cond = branch.get("condition", "")
                    if cond == "else":
                        else_branch = branch
                    elif evaluate_condition(cond, context):
                        chosen = branch
                        break

                if chosen is None:
                    chosen = else_branch

                if chosen:
                    res = await _execute(chosen)
                    if res is None:
                        pass
                    elif "error" in res:
                        errors.append(f"{res.get('agent')}: {res['error']}")
                    else:
                        context[step_name] = res.get("output", {})
                        results.append(res)

            elif step_type == "loop":
                loop_cfg = step.get("loop", {})
                until_cond = loop_cfg.get("until", "")
                max_iter = int(loop_cfg.get("max_iterations", 10))
                loop_steps = loop_cfg.get("steps", [])

                for _iteration in range(max_iter):
                    for sub in loop_steps:
                        res = await _execute(sub)
                        if res is None:
                            continue
                        if "error" in res:
                            errors.append(f"{res.get('agent')}: {res['error']}")
                        else:
                            context[sub.get("name", "")] = res.get("output", {})
                            results.append(res)

                    # Check exit condition after each full iteration
                    if until_cond and evaluate_condition(until_cond, context):
                        break

            elif step_type == "dynamic":
                # Step 1: run the spawner agent to get sub-steps
                spawner_res = await _execute(step)
                if spawner_res and "error" not in spawner_res:
                    spawned_steps = spawner_res.get("output", {}).get("spawned_steps", [])
                    # Step 2: execute each spawned step sequentially
                    for sub in spawned_steps:
                        sub_step = {
                            "type": "sequential",
                            "name": sub.get("name"),
                            "agent": sub.get("agent"),
                            "params": sub.get("params", payload),
                        }
                        res = await _execute(sub_step)
                        if res is None:
                            continue
                        if "error" in res:
                            errors.append(f"{res.get('agent')}: {res['error']}")
                        else:
                            context[sub.get("name", "")] = res.get("output", {})
                            results.append(res)
                elif spawner_res:
                    errors.append(f"{spawner_res.get('agent')}: {spawner_res['error']}")

            elif step_type == "graph":
                # Delegate to GraphWorkflow as a child workflow.
                # The child gets its own Temporal execution history and can be
                # independently interrupted, resumed, and queried.
                from flow_engine.graph.engine import GraphWorkflow

                graph_def = step.get("graph", {})
                child_id = f"{workflow.info().workflow_id}_{step_name}"

                graph_result = await workflow.execute_child_workflow(
                    GraphWorkflow.run,
                    args=[graph_def, payload],
                    id=child_id,
                    task_queue=config.TEMPORAL_TASK_QUEUE,
                )
                context[step_name] = graph_result.get("context", {})
                results.append({
                    "agent": "graph",
                    "node_id": step_name,
                    "output": graph_result,
                })

            else:
                # Default: sequential
                # Backward-compatible: support old format with just 'name'/'params'
                res = await _execute(step)
                if res is None:
                    continue
                if "error" in res:
                    errors.append(f"{res.get('agent')}: {res['error']}")
                else:
                    context[step_name] = res.get("output", {})
                    results.append(res)

        if errors:
            raise workflow.WorkflowError(f"Errors executing steps: {errors}")

        return results


async def run_complex_workflow(
    steps: List[Dict[str, Any]],
    payload: Dict[str, Any],
    workflow_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Entry point to start the ComplexSequenceWorkflow via the Temporal client.

    Args:
        steps:       List of step dicts (type, name, agent, params, branches, etc.)
        payload:     Fallback payload for steps without explicit params.
        workflow_id: Optional ID; autogenerated if None.

    Returns:
        Results from all successful agent_activity executions.
    """
    client = await Client.connect(config.TEMPORAL_SERVER_URL)
    handle = await client.start_workflow(
        ComplexSequenceWorkflow.run,
        args=[steps, payload],
        id=workflow_id,
        task_queue=config.TEMPORAL_TASK_QUEUE,
    )
    return await handle.result()
