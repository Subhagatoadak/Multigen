"""
Flow engine workflow: runs a dynamic sequence of agent-driven steps via Temporal,
with support for parallel execution, conditional branching, dynamic subtrees,
and error resilience.
"""
import ast
import operator
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


def evaluate_condition(condition: str, context: Dict[str, Any]) -> bool:
    """
    Safely evaluate a condition string against a context dict of prior step outputs.
    Only comparisons, boolean ops, attribute/subscript access, and constants are allowed.
    Returns False on any parse or evaluation error.
    """
    try:
        tree = ast.parse(condition, mode='eval')
        result = _eval_node(tree.body, context)
        return bool(result)
    except Exception:
        return False


# ─── Activities ───────────────────────────────────────────────────────────────

@activity.defn
async def agent_activity(
    agent_name: str,
    params: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Activity that locates and executes the named agent with given parameters.
    May raise on failure; Temporal retry_policy applies automatically.
    """
    with _tracer.start_as_current_span(f"agent.{agent_name}") as span:
        span.set_attribute("agent.name", agent_name)
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
            params = step.get("params") or payload
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
