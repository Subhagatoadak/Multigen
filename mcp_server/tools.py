"""
MCP tool definitions for the Multigen orchestrator.

Each entry in TOOL_REGISTRY is a dict with:
  name        — MCP tool name (called by the host / Claude)
  description — Shown to the model to guide tool selection
  schema      — JSON Schema for the input arguments
  handler     — Async callable(args) → dict

dispatch_tool(name, args) routes to the correct handler.
"""
from __future__ import annotations

import os
from typing import Any, Dict

from sdk.multigen.client import MultigenClient
from sdk.multigen.models import FanOutNodeDef, FanOutRequest, InjectNodeRequest


def _client() -> MultigenClient:
    return MultigenClient(
        base_url=os.getenv("MULTIGEN_BASE_URL", "http://localhost:8000"),
        api_key=os.getenv("MULTIGEN_API_KEY") or None,
        timeout=float(os.getenv("MULTIGEN_TIMEOUT", "30")),
    )


# ── Handlers ───────────────────────────────────────────────────────────────────

async def _run_workflow(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        resp = await c.run_workflow(
            dsl=args.get("dsl"),
            text=args.get("text"),
            payload=args.get("payload", {}),
        )
    return {"instance_id": resp.instance_id, "status": "started"}


async def _run_graph_workflow(args: Dict[str, Any]) -> Dict:
    """
    Start a workflow whose single step is a full GraphDefinition.
    This is the primary entry point for complex reasoning graphs.
    """
    graph_def = args["graph_def"]
    payload = args.get("payload", {})
    step_name = args.get("step_name", "reasoning_graph")
    dsl = {
        "steps": [{"name": step_name, "graph": graph_def}]
    }
    async with _client() as c:
        resp = await c.run_workflow(dsl=dsl, payload=payload)
    return {"instance_id": resp.instance_id, "status": "started", "graph_step": step_name}


async def _get_state(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        state = await c.get_state(args["workflow_id"])
    return {
        "workflow_id": state.workflow_id,
        "count": state.count,
        "nodes": [
            {"node_id": n.node_id, "output": n.output, "updated_at": n.updated_at}
            for n in state.nodes
        ],
    }


async def _get_node_state(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        node = await c.get_node_state(args["workflow_id"], args["node_id"])
    return {"node_id": node.node_id, "output": node.output, "updated_at": node.updated_at}


async def _interrupt(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        return await c.interrupt(args["workflow_id"])


async def _resume(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        return await c.resume(args["workflow_id"])


async def _jump_to(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        return await c.jump_to(args["workflow_id"], args["node_id"])


async def _skip_node(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        return await c.skip_node(args["workflow_id"], args["node_id"])


async def _reroute(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        return await c.reroute(
            args["workflow_id"],
            args["source"],
            args["target"],
            args.get("condition", ""),
        )


async def _prune_branch(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        return await c.prune_branch(args["workflow_id"], args["node_id"])


async def _inject_node(args: Dict[str, Any]) -> Dict:
    node = InjectNodeRequest(
        id=args["id"],
        agent=args.get("agent"),
        blueprint=args.get("blueprint"),
        params=args.get("params", {}),
        edges_to=args.get("edges_to", []),
        retry=args.get("retry", 3),
        timeout=args.get("timeout", 30),
        reflection_threshold=args.get("reflection_threshold", 0.0),
        fallback_agent=args.get("fallback_agent"),
    )
    async with _client() as c:
        return await c.inject_node(args["workflow_id"], node)


async def _fan_out(args: Dict[str, Any]) -> Dict:
    nodes = [
        FanOutNodeDef(
            id=n["id"],
            agent=n.get("agent"),
            blueprint=n.get("blueprint"),
            params=n.get("params", {}),
            retry=n.get("retry", 3),
            timeout=n.get("timeout", 30),
        )
        for n in args["nodes"]
    ]
    req = FanOutRequest(
        group_id=args["group_id"],
        nodes=nodes,
        consensus=args.get("consensus", "highest_confidence"),
    )
    async with _client() as c:
        return await c.fan_out(args["workflow_id"], req)


async def _get_health(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        h = await c.get_health(args["workflow_id"])
    return {
        "workflow_id": args["workflow_id"],
        "interrupted": h.interrupted,
        "pending_count": h.pending_count,
        "circuit_breaker_trips": h.cb_trips_total,
        "skip_nodes": h.skip_nodes,
        "recent_errors": h.errors,
        "dead_letters": h.dead_letters,
    }


async def _get_metrics(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        m = await c.get_metrics(args["workflow_id"])
    return {
        "workflow_id": args["workflow_id"],
        "nodes_executed": m.nodes_executed,
        "nodes_skipped": m.nodes_skipped,
        "reflections_triggered": m.reflections_triggered,
        "fan_outs_executed": m.fan_outs_executed,
        "circuit_breaker_trips": m.circuit_breaker_trips,
        "error_count": m.error_count,
        "dead_letter_count": m.dead_letter_count,
    }


async def _list_agents(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        agents = await c.list_agents()
    return {"agents": agents}


async def _list_capabilities(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        caps = await c.list_capabilities()
    return {"capabilities": [c.model_dump() for c in caps]}


async def _register_capability(args: Dict[str, Any]) -> Dict:
    from sdk.multigen.models import Capability
    cap = Capability(**{k: v for k, v in args.items()})
    async with _client() as c:
        result = await c.register_capability(cap)
    return result.model_dump()


async def _ping(args: Dict[str, Any]) -> Dict:
    async with _client() as c:
        ok = await c.ping()
    return {"healthy": ok, "base_url": os.getenv("MULTIGEN_BASE_URL", "http://localhost:8000")}


# ── Tool registry ──────────────────────────────────────────────────────────────

TOOL_REGISTRY = [
    {
        "name": "multigen_ping",
        "description": "Check if the Multigen orchestrator is reachable and healthy.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "handler": _ping,
    },
    {
        "name": "multigen_run_workflow",
        "description": (
            "Start a multi-agent workflow. Provide either 'dsl' (a structured dict with a 'steps' list) "
            "or 'text' (natural language — requires OPENAI_API_KEY on server to auto-generate DSL). "
            "Returns instance_id to track the workflow."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "dsl": {
                    "type": "object",
                    "description": "Workflow DSL dict with a 'steps' key.",
                },
                "text": {
                    "type": "string",
                    "description": "Natural language description of the task (LLM converts to DSL).",
                },
                "payload": {
                    "type": "object",
                    "description": "Input data passed to steps that have no explicit params.",
                    "default": {},
                },
            },
        },
        "handler": _run_workflow,
    },
    {
        "name": "multigen_run_graph",
        "description": (
            "Start a complex reasoning graph workflow with support for cycles, reflection, "
            "circuit breakers, fan-out, and dynamic node injection. "
            "graph_def must have: nodes (list), edges (list), entry (string). "
            "Each node: id, agent (or blueprint), params, tools, retry, timeout, "
            "reflection_threshold, fallback_agent. "
            "Each edge: source, target, condition (optional expression). "
            "Returns instance_id for all subsequent control calls."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "graph_def": {
                    "type": "object",
                    "description": "Full graph definition dict.",
                },
                "payload": {
                    "type": "object",
                    "description": "Input data for the graph.",
                    "default": {},
                },
                "step_name": {
                    "type": "string",
                    "description": "Name for the graph step in the parent workflow.",
                    "default": "reasoning_graph",
                },
            },
            "required": ["graph_def"],
        },
        "handler": _run_graph_workflow,
    },
    {
        "name": "multigen_get_state",
        "description": "Read all completed node outputs for a workflow from the distributed state backend (MongoDB).",
        "schema": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string", "description": "Workflow instance ID."},
            },
            "required": ["workflow_id"],
        },
        "handler": _get_state,
    },
    {
        "name": "multigen_get_node_state",
        "description": "Read the output of a single graph node.",
        "schema": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": ["workflow_id", "node_id"],
        },
        "handler": _get_node_state,
    },
    {
        "name": "multigen_interrupt",
        "description": "Pause a running workflow before its next node starts. Send multigen_resume to continue.",
        "schema": {
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
            "required": ["workflow_id"],
        },
        "handler": _interrupt,
    },
    {
        "name": "multigen_resume",
        "description": "Resume a paused workflow from the exact point it stopped.",
        "schema": {
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
            "required": ["workflow_id"],
        },
        "handler": _resume,
    },
    {
        "name": "multigen_jump_to",
        "description": (
            "Push a specific node to the FRONT of the execution queue (priority lane). "
            "Use this to force-execute a safety or escalation node immediately, "
            "before anything else currently pending."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node_id": {"type": "string", "description": "Node to prioritize."},
            },
            "required": ["workflow_id", "node_id"],
        },
        "handler": _jump_to,
    },
    {
        "name": "multigen_skip_node",
        "description": (
            "Mark a node to be silently dropped (branch no-go). "
            "The node is skipped but its successors may still run via other edges."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node_id": {"type": "string"},
            },
            "required": ["workflow_id", "node_id"],
        },
        "handler": _skip_node,
    },
    {
        "name": "multigen_reroute",
        "description": (
            "Add a dynamic edge between two nodes at runtime. "
            "Use condition='' for unconditional routing. "
            "Example condition: 'risk_score > 0.8'"
        ),
        "schema": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "source": {"type": "string", "description": "Source node ID."},
                "target": {"type": "string", "description": "Target node ID."},
                "condition": {
                    "type": "string",
                    "description": "Boolean expression. Empty = always route.",
                    "default": "",
                },
            },
            "required": ["workflow_id", "source", "target"],
        },
        "handler": _reroute,
    },
    {
        "name": "multigen_inject_node",
        "description": (
            "Dynamically add a new node to a running workflow at runtime. "
            "Supports blueprint-based dynamic agent creation: provide 'blueprint' dict instead of 'agent'. "
            "Use edges_to to wire this node to existing downstream nodes."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "id": {"type": "string", "description": "Unique node ID."},
                "agent": {"type": "string", "description": "Registered agent name."},
                "blueprint": {"type": "object", "description": "Dynamic agent blueprint (alternative to agent)."},
                "params": {"type": "object", "default": {}},
                "edges_to": {"type": "array", "items": {"type": "string"}, "default": []},
                "retry": {"type": "integer", "default": 3},
                "timeout": {"type": "integer", "default": 30},
                "reflection_threshold": {"type": "number", "default": 0.0},
                "fallback_agent": {"type": "string"},
            },
            "required": ["workflow_id", "id"],
        },
        "handler": _inject_node,
    },
    {
        "name": "multigen_fan_out",
        "description": (
            "Execute N nodes in parallel then merge results via a consensus strategy. "
            "Perfect for parallel hypothesis testing, multi-model evaluation, or diverse expert opinions. "
            "consensus options: highest_confidence, aggregate, majority_vote, first_success."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "group_id": {"type": "string", "description": "Name for the merged result in context."},
                "nodes": {
                    "type": "array",
                    "description": "List of node definitions to run in parallel.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "agent": {"type": "string"},
                            "blueprint": {"type": "object"},
                            "params": {"type": "object"},
                        },
                        "required": ["id"],
                    },
                },
                "consensus": {
                    "type": "string",
                    "enum": ["highest_confidence", "aggregate", "majority_vote", "first_success"],
                    "default": "highest_confidence",
                },
            },
            "required": ["workflow_id", "group_id", "nodes"],
        },
        "handler": _fan_out,
    },
    {
        "name": "multigen_prune_branch",
        "description": (
            "Cancel a node and ALL reachable downstream descendants. "
            "Use to abandon a bad reasoning path entirely."
        ),
        "schema": {
            "type": "object",
            "properties": {
                "workflow_id": {"type": "string"},
                "node_id": {"type": "string", "description": "Root of the branch to prune."},
            },
            "required": ["workflow_id", "node_id"],
        },
        "handler": _prune_branch,
    },
    {
        "name": "multigen_get_health",
        "description": (
            "Get live circuit breaker status, error log, interrupt state, "
            "and dead letters from the running workflow."
        ),
        "schema": {
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
            "required": ["workflow_id"],
        },
        "handler": _get_health,
    },
    {
        "name": "multigen_get_metrics",
        "description": "Get live execution counters: nodes run, reflections triggered, fan-outs, CB trips, errors.",
        "schema": {
            "type": "object",
            "properties": {"workflow_id": {"type": "string"}},
            "required": ["workflow_id"],
        },
        "handler": _get_metrics,
    },
    {
        "name": "multigen_list_agents",
        "description": "List all agent names and class paths currently loaded in the worker registry.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "handler": _list_agents,
    },
    {
        "name": "multigen_list_capabilities",
        "description": "List all agent capabilities registered in the Capability Directory.",
        "schema": {"type": "object", "properties": {}, "required": []},
        "handler": _list_capabilities,
    },
    {
        "name": "multigen_register_capability",
        "description": "Register a new agent capability in the Capability Directory.",
        "schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "version": {"type": "string"},
                "description": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "metadata": {"type": "object"},
            },
            "required": ["name"],
        },
        "handler": _register_capability,
    },
]

_HANDLER_MAP = {t["name"]: t["handler"] for t in TOOL_REGISTRY}


async def dispatch_tool(name: str, args: Dict[str, Any]) -> Dict[str, Any]:
    handler = _HANDLER_MAP.get(name)
    if handler is None:
        raise ValueError(f"Unknown tool: {name!r}. Available: {sorted(_HANDLER_MAP)}")
    return await handler(args)
