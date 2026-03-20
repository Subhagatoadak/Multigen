"""
Graph workflow control API — Enterprise Edition.

Runtime control endpoints (all use Temporal signals or queries):

  POST /workflows/{id}/interrupt          pause before next node
  POST /workflows/{id}/resume             unpause
  POST /workflows/{id}/inject             append a new node at runtime
  POST /workflows/{id}/jump               push a node to the FRONT of the queue
  POST /workflows/{id}/skip               mark a node to be silently dropped
  POST /workflows/{id}/reroute            add a dynamic edge at runtime
  POST /workflows/{id}/fan-out            run N nodes in parallel + consensus
  POST /workflows/{id}/prune              cancel a branch and its descendants

State / observability (MongoDB CQRS read model):
  GET  /workflows/{id}/state              all node outputs
  GET  /workflows/{id}/state/{node_id}    single node output

Live introspection (Temporal queries — no DB needed):
  GET  /workflows/{id}/health             circuit breakers, errors, pending count
  GET  /workflows/{id}/metrics            execution counters and stats
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import orchestrator.services.config as config

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Request models ─────────────────────────────────────────────────────────────

class InjectNodeRequest(BaseModel):
    """Payload for injecting a new node into a running graph workflow."""
    id: str
    agent: Optional[str] = None
    blueprint: Optional[Dict[str, Any]] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    tools: list = Field(default_factory=list)
    retry: int = 3
    timeout: int = 30
    edges_to: List[str] = Field(default_factory=list)
    reflection_threshold: float = 0.0
    max_reflections: int = 2
    fallback_agent: Optional[str] = None


class JumpRequest(BaseModel):
    """Push a node to the front of the execution queue."""
    node_id: str


class SkipRequest(BaseModel):
    """Mark a node to be silently dropped whenever it is encountered."""
    node_id: str


class RerouteRequest(BaseModel):
    """Add a dynamic edge between two existing nodes."""
    source: str
    target: str
    condition: str = ""


class FanOutNodeDef(BaseModel):
    id: str
    agent: Optional[str] = None
    blueprint: Optional[Dict[str, Any]] = None
    params: Dict[str, Any] = Field(default_factory=dict)
    tools: list = Field(default_factory=list)
    retry: int = 3
    timeout: int = 30


class FanOutRequest(BaseModel):
    """
    Execute N nodes in parallel then merge results via a consensus strategy.

    consensus:
        highest_confidence  — pick the result with the best confidence score
        aggregate           — return all results as a list
        majority_vote       — pick the most frequent output value
        first_success       — return the first non-error result
    """
    group_id: str
    nodes: List[FanOutNodeDef]
    consensus: str = "highest_confidence"


class PruneRequest(BaseModel):
    """Cancel a node and all reachable downstream descendants."""
    node_id: str


# ── Temporal client helpers ────────────────────────────────────────────────────

async def _get_temporal_client():
    from temporalio.client import Client
    return await Client.connect(config.TEMPORAL_SERVER_URL)


async def _get_graph_state_col():
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(config.MONGODB_URI)
    return client[config.GRAPH_STATE_DB_NAME][config.GRAPH_STATE_COLLECTION]


async def _handle(workflow_id: str):
    client = await _get_temporal_client()
    return client.get_workflow_handle(workflow_id)


# ── Interrupt / Resume ─────────────────────────────────────────────────────────

@router.post("/{workflow_id}/interrupt", tags=["graph"])
async def interrupt_workflow(workflow_id: str):
    """
    Pause a running GraphWorkflow before its next node starts.
    All in-flight activities complete normally; send /resume to continue.
    """
    try:
        handle = await _handle(workflow_id)
        await handle.signal("interrupt")
        return {"status": "interrupted", "workflow_id": workflow_id}
    except Exception as exc:
        logger.exception("interrupt failed: %s", workflow_id)
        raise HTTPException(status_code=404, detail=str(exc))


@router.post("/{workflow_id}/resume", tags=["graph"])
async def resume_workflow(workflow_id: str):
    """Resume a paused GraphWorkflow from the exact point it stopped."""
    try:
        handle = await _handle(workflow_id)
        await handle.signal("resume")
        return {"status": "resumed", "workflow_id": workflow_id}
    except Exception as exc:
        logger.exception("resume failed: %s", workflow_id)
        raise HTTPException(status_code=404, detail=str(exc))


# ── Inject ─────────────────────────────────────────────────────────────────────

@router.post("/{workflow_id}/inject", tags=["graph"])
async def inject_node(workflow_id: str, req: InjectNodeRequest):
    """
    Dynamically append a new node to a running GraphWorkflow.

    Supports blueprint-based dynamic agent creation:
        {"id": "custom_analyst", "blueprint": {"system_prompt": "...", "instruction": "..."}}

    Or standard agent routing:
        {"id": "extra_check", "agent": "CritiqueAgent", "edges_to": ["report"]}
    """
    try:
        handle = await _handle(workflow_id)
        await handle.signal("inject_node", json.dumps(req.model_dump()))
        return {"status": "injected", "workflow_id": workflow_id, "node_id": req.id}
    except Exception as exc:
        logger.exception("inject_node failed: %s", workflow_id)
        raise HTTPException(status_code=404, detail=str(exc))


# ── Jump (priority execution) ──────────────────────────────────────────────────

@router.post("/{workflow_id}/jump", tags=["graph"])
async def jump_to_node(workflow_id: str, req: JumpRequest):
    """
    Push a node_id to the FRONT of the execution queue.

    Use for urgent reasoning steps that must run before anything else
    currently pending — e.g. injecting a safety check mid-workflow.
    """
    try:
        handle = await _handle(workflow_id)
        await handle.signal("jump_to", req.node_id)
        return {"status": "jump_queued", "workflow_id": workflow_id, "node_id": req.node_id}
    except Exception as exc:
        logger.exception("jump_to failed: %s", workflow_id)
        raise HTTPException(status_code=404, detail=str(exc))


# ── Skip (branch no-go) ────────────────────────────────────────────────────────

@router.post("/{workflow_id}/skip", tags=["graph"])
async def skip_node(workflow_id: str, req: SkipRequest):
    """
    Mark a node to be silently dropped whenever it appears in the queue.

    Use to abort a specific reasoning step without touching other branches —
    the node is skipped but its successors may still be enqueued by other paths.
    """
    try:
        handle = await _handle(workflow_id)
        await handle.signal("skip_node", req.node_id)
        return {"status": "node_skipped", "workflow_id": workflow_id, "node_id": req.node_id}
    except Exception as exc:
        logger.exception("skip_node failed: %s", workflow_id)
        raise HTTPException(status_code=404, detail=str(exc))


# ── Reroute (dynamic edge) ─────────────────────────────────────────────────────

@router.post("/{workflow_id}/reroute", tags=["graph"])
async def reroute_workflow(workflow_id: str, req: RerouteRequest):
    """
    Add a dynamic edge between two nodes in a running workflow.

    The edge takes effect immediately — if source has already completed,
    the target will be enqueued the next time source would normally trigger it.
    Use condition="" for an unconditional edge.

    Example: {"source": "analyse", "target": "escalate", "condition": "risk_score > 0.8"}
    """
    try:
        handle = await _handle(workflow_id)
        await handle.signal("reroute", json.dumps(req.model_dump()))
        return {
            "status": "edge_added",
            "workflow_id": workflow_id,
            "edge": req.model_dump(),
        }
    except Exception as exc:
        logger.exception("reroute failed: %s", workflow_id)
        raise HTTPException(status_code=404, detail=str(exc))


# ── Fan-out (parallel reasoning) ──────────────────────────────────────────────

@router.post("/{workflow_id}/fan-out", tags=["graph"])
async def fan_out_workflow(workflow_id: str, req: FanOutRequest):
    """
    Execute N nodes in parallel and merge results via consensus.

    Enables reasoning fan-out: run multiple hypothesis-generating agents
    simultaneously, then select or aggregate their outputs.

    The merged result is stored in context under group_id so downstream
    nodes can reference it: {{steps.<group_id>.output.<key>}}

    Example:
        {
          "group_id": "multi_critique",
          "consensus": "highest_confidence",
          "nodes": [
            {"id": "critic_a", "agent": "StrictCritiqueAgent"},
            {"id": "critic_b", "agent": "CreativeCritiqueAgent"},
            {"id": "critic_c", "agent": "RiskCritiqueAgent"}
          ]
        }
    """
    try:
        handle = await _handle(workflow_id)
        payload = {
            "group_id": req.group_id,
            "consensus": req.consensus,
            "nodes": [n.model_dump() for n in req.nodes],
        }
        await handle.signal("fan_out", json.dumps(payload))
        return {
            "status": "fan_out_queued",
            "workflow_id": workflow_id,
            "group_id": req.group_id,
            "node_count": len(req.nodes),
            "consensus": req.consensus,
        }
    except Exception as exc:
        logger.exception("fan_out failed: %s", workflow_id)
        raise HTTPException(status_code=404, detail=str(exc))


# ── Prune (branch cancel) ──────────────────────────────────────────────────────

@router.post("/{workflow_id}/prune", tags=["graph"])
async def prune_branch(workflow_id: str, req: PruneRequest):
    """
    Cancel a node and all reachable downstream descendants.

    Used as a circuit breaker at the reasoning-graph level: if a branch
    is detected as off-track (e.g. by a supervisor), prune it entirely
    to free resources and prevent cascading bad outputs.
    """
    try:
        handle = await _handle(workflow_id)
        await handle.signal("prune_branch", req.node_id)
        return {
            "status": "branch_pruned",
            "workflow_id": workflow_id,
            "pruned_from": req.node_id,
        }
    except Exception as exc:
        logger.exception("prune_branch failed: %s", workflow_id)
        raise HTTPException(status_code=404, detail=str(exc))


# ── State (CQRS MongoDB read model) ───────────────────────────────────────────

@router.get("/{workflow_id}/state", tags=["graph"])
async def get_workflow_state(workflow_id: str):
    """Read all node outputs from the distributed MongoDB state backend."""
    try:
        col = await _get_graph_state_col()
        cursor = col.find(
            {"workflow_id": workflow_id},
            {"_id": 0, "node_id": 1, "output": 1, "updated_at": 1},
        )
        nodes = await cursor.to_list(length=500)
        return {"workflow_id": workflow_id, "nodes": nodes, "count": len(nodes)}
    except Exception as exc:
        logger.exception("get_workflow_state failed: %s", workflow_id)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/{workflow_id}/state/{node_id}", tags=["graph"])
async def get_node_state(workflow_id: str, node_id: str):
    """Read the output of a single graph node from MongoDB."""
    try:
        col = await _get_graph_state_col()
        doc = await col.find_one(
            {"workflow_id": workflow_id, "node_id": node_id},
            {"_id": 0},
        )
        if doc is None:
            raise HTTPException(
                status_code=404,
                detail=f"No state for node '{node_id}' in workflow '{workflow_id}'",
            )
        return doc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("get_node_state failed: %s / %s", workflow_id, node_id)
        raise HTTPException(status_code=500, detail=str(exc))


# ── Live introspection (Temporal queries) ──────────────────────────────────────

@router.get("/{workflow_id}/health", tags=["graph"])
async def get_workflow_health(workflow_id: str):
    """
    Query live circuit breaker status, error log, and interrupt state
    directly from the running Temporal workflow (no DB required).

    Returns:
        interrupted        bool   — whether the workflow is paused
        pending_count      int    — nodes currently in the queue
        skip_nodes         list   — nodes marked for skipping
        cb_trips_total     int    — total circuit breaker trips
        errors             list   — last 20 error messages
        dead_letters       list   — node IDs that failed with no recovery
    """
    try:
        handle = await _handle(workflow_id)
        result = await handle.query("get_health")
        return {"workflow_id": workflow_id, **result}
    except Exception as exc:
        logger.exception("get_health query failed: %s", workflow_id)
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{workflow_id}/metrics", tags=["graph"])
async def get_workflow_metrics(workflow_id: str):
    """
    Query live execution statistics from the running Temporal workflow.

    Returns:
        nodes_executed          int
        nodes_skipped           int
        reflections_triggered   int — auto-reflection loops fired
        fan_outs_executed       int — parallel reasoning groups completed
        circuit_breaker_trips   int
        error_count             int
        dead_letter_count       int
    """
    try:
        handle = await _handle(workflow_id)
        result = await handle.query("get_metrics")
        return {"workflow_id": workflow_id, **result}
    except Exception as exc:
        logger.exception("get_metrics query failed: %s", workflow_id)
        raise HTTPException(status_code=404, detail=str(exc))


@router.get("/{workflow_id}/pending", tags=["graph"])
async def get_pending_count(workflow_id: str):
    """Return the number of nodes currently waiting in the execution queue."""
    try:
        handle = await _handle(workflow_id)
        count = await handle.query("get_pending_count")
        return {"workflow_id": workflow_id, "pending_count": count}
    except Exception as exc:
        logger.exception("get_pending_count query failed: %s", workflow_id)
        raise HTTPException(status_code=404, detail=str(exc))
