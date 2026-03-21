"""
A2A Server — exposes Multigen agents as A2A-compliant remote agents.

Endpoints
─────────
GET  /.well-known/agent.json              Global Agent Card (all registered agents as skills)
GET  /a2a/agents/{agent_name}/card        Per-agent Agent Card
POST /a2a                                 JSON-RPC 2.0 task endpoint (sync)
POST /a2a/stream                          JSON-RPC 2.0 task endpoint (SSE streaming)

Supported JSON-RPC methods
──────────────────────────
tasks/send              Execute a task synchronously
tasks/sendSubscribe     Execute a task with SSE streaming
tasks/get               Query task status (requires TaskStore)
tasks/cancel            Cancel an in-progress task

External agents (LangGraph, CrewAI, Vertex AI, etc.) can discover
Multigen's capabilities via the Agent Card and submit tasks via
the standard JSON-RPC 2.0 interface.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from orchestrator.services.agent_registry import get_agent, list_agents
from .models import (
    AgentCard,
    AgentCapabilities,
    AgentSkill,
    Artifact,
    DataPart,
    JSONRPCError,
    JSONRPCRequest,
    JSONRPCResponse,
    Message,
    Task,
    TaskCancelParams,
    TaskQueryParams,
    TaskSendParams,
    TaskState,
    TaskStatus,
    TextPart,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/a2a", tags=["a2a"])

# ── In-memory task store (replace with Redis/MongoDB for production) ───────────
_task_store: Dict[str, Task] = {}


# ── Agent Card helpers ─────────────────────────────────────────────────────────

def _make_agent_card(base_url: str, agent_filter: Optional[str] = None) -> AgentCard:
    """
    Build an AgentCard describing all (or one) registered agents as skills.
    """
    agents = list_agents()

    skills = []
    for name, cls in agents.items():
        if agent_filter and name != agent_filter:
            continue
        doc = (cls.__doc__ or "").strip().split("\n")[0][:200]
        skills.append(AgentSkill(
            id=name.lower().replace(" ", "_"),
            name=name,
            description=doc or f"Agent: {name}",
            tags=_infer_tags(name),
            examples=[f"Run {name} on input data"],
            input_modes=["text", "data"],
            output_modes=["text", "data"],
        ))

    return AgentCard(
        name="Multigen" if not agent_filter else agent_filter,
        description=(
            "Multigen enterprise multi-agent orchestration framework. "
            "Supports durable graph workflows, epistemic transparency, "
            "and human approval gates."
        ),
        url=base_url,
        version="0.2.0",
        documentation_url="https://github.com/Subhagatoadak/Multigen",
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            state_transition_history=True,
        ),
        authentication={"schemes": ["Bearer"]},
        skills=skills,
    )


def _infer_tags(agent_name: str) -> list:
    name_lower = agent_name.lower()
    tags = []
    mapping = {
        "echo": "demo", "screen": "hr", "langchain": "llm",
        "llama": "rag", "spawn": "orchestration", "pattern": "cognitive",
        "financial": "finance", "legal": "legal", "risk": "risk",
        "valuat": "finance", "research": "research", "synthesis": "analysis",
    }
    for key, tag in mapping.items():
        if key in name_lower:
            tags.append(tag)
    return tags or ["general"]


# ── Well-known Agent Card endpoint ─────────────────────────────────────────────

@router.get(
    "/well-known/agent.json",
    summary="Global Agent Card — all registered agents as A2A skills",
    tags=["a2a"],
)
async def global_agent_card(request: Request) -> Dict[str, Any]:
    base_url = str(request.base_url).rstrip("/")
    card = _make_agent_card(base_url=f"{base_url}/a2a")
    return card.model_dump(exclude_none=True)


@router.get(
    "/agents/{agent_name}/card",
    summary="Per-agent Agent Card",
    tags=["a2a"],
)
async def agent_card(agent_name: str, request: Request) -> Dict[str, Any]:
    agents = list_agents()
    if agent_name not in agents:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_name}' not registered")
    base_url = str(request.base_url).rstrip("/")
    card = _make_agent_card(base_url=f"{base_url}/a2a", agent_filter=agent_name)
    return card.model_dump(exclude_none=True)


# ── Task execution ─────────────────────────────────────────────────────────────

async def _execute_a2a_task(params: TaskSendParams) -> Task:
    """
    Route an A2A task to the appropriate Multigen agent.

    Skill resolution order:
      1. Exact agent name match (case-sensitive)
      2. Case-insensitive match
      3. Substring match within agent name
    """
    # Extract skill/agent from metadata or first text part
    skill_id = (params.metadata or {}).get("skill_id") or ""
    message_text = ""
    message_data: Dict[str, Any] = {}

    for part in params.message.parts:
        if isinstance(part, TextPart):
            message_text = part.text
        elif isinstance(part, DataPart):
            message_data = part.data

    # Resolve which agent to invoke
    agents = list_agents()
    agent_name = _resolve_skill(skill_id or message_text, agents)

    if not agent_name:
        task = Task(
            id=params.id,
            status=TaskStatus(
                state=TaskState.FAILED,
                message=Message(
                    role="agent",
                    parts=[TextPart(text=f"No agent found for skill '{skill_id}'. "
                                       f"Available: {list(agents.keys())}")],
                ),
            ),
        )
        _task_store[params.id] = task
        return task

    # Build agent params from message
    agent_params: Dict[str, Any] = {
        "a2a_task_id": params.id,
        "message": message_text,
        **message_data,
    }
    if params.session_id:
        agent_params["session_id"] = params.session_id

    # Execute agent
    try:
        agent = get_agent(agent_name)
        result = await agent.run(agent_params)
        output = result.get("output", result)
        confidence = result.get("confidence", 0.5)

        # Convert output to A2A artifact
        if isinstance(output, str):
            artifact_parts = [TextPart(text=output)]
        else:
            artifact_parts = [
                TextPart(text=json.dumps(output, default=str)),
                DataPart(data=output if isinstance(output, dict) else {"result": output}),
            ]

        task = Task(
            id=params.id,
            status=TaskStatus(
                state=TaskState.COMPLETED,
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            artifacts=[
                Artifact(
                    name="result",
                    description=f"Output from {agent_name}",
                    parts=artifact_parts,
                    metadata={"agent": agent_name, "confidence": confidence},
                )
            ],
        )
    except Exception as exc:
        logger.exception("A2A task '%s' failed on agent '%s'", params.id, agent_name)
        task = Task(
            id=params.id,
            status=TaskStatus(
                state=TaskState.FAILED,
                message=Message(
                    role="agent",
                    parts=[TextPart(text=str(exc))],
                ),
            ),
        )

    _task_store[params.id] = task
    return task


def _resolve_skill(skill_hint: str, agents: Dict) -> Optional[str]:
    """Fuzzy-match skill_hint to a registered agent name."""
    if not skill_hint:
        return None
    # 1. Exact match
    if skill_hint in agents:
        return skill_hint
    # 2. Case-insensitive
    for name in agents:
        if name.lower() == skill_hint.lower():
            return name
    # 3. skill_hint is a substring of an agent name (or vice versa)
    skill_lower = skill_hint.lower()
    for name in agents:
        if skill_lower in name.lower() or name.lower() in skill_lower:
            return name
    return None


# ── JSON-RPC 2.0 handler ───────────────────────────────────────────────────────

@router.post(
    "",
    summary="A2A JSON-RPC 2.0 task endpoint",
    response_model=JSONRPCResponse,
)
async def handle_a2a_task(request: Request) -> JSONRPCResponse:
    """
    Handles A2A JSON-RPC 2.0 methods:
      tasks/send        — synchronous task execution
      tasks/get         — query task status
      tasks/cancel      — cancel an in-progress task
    """
    try:
        body = await request.json()
        rpc = JSONRPCRequest(**body)
    except Exception as exc:
        return JSONRPCResponse(
            id=None,
            error=JSONRPCError(code=-32700, message=f"Parse error: {exc}"),
        )

    method = rpc.method
    params = rpc.params or {}

    try:
        if method == "tasks/send":
            task_params = TaskSendParams(**params)
            if not task_params.id:
                task_params.id = str(uuid.uuid4())
            task = await _execute_a2a_task(task_params)
            return JSONRPCResponse(id=rpc.id, result=task.model_dump(exclude_none=True))

        elif method == "tasks/get":
            qp = TaskQueryParams(**params)
            task = _task_store.get(qp.id)
            if not task:
                return JSONRPCResponse(
                    id=rpc.id,
                    error=JSONRPCError(code=-32001, message=f"Task '{qp.id}' not found"),
                )
            return JSONRPCResponse(id=rpc.id, result=task.model_dump(exclude_none=True))

        elif method == "tasks/cancel":
            cp = TaskCancelParams(**params)
            task = _task_store.get(cp.id)
            if task and task.status.state in (TaskState.SUBMITTED, TaskState.WORKING):
                task.status.state = TaskState.CANCELLED
                _task_store[cp.id] = task
            return JSONRPCResponse(id=rpc.id, result={"id": cp.id, "cancelled": True})

        elif method == "tasks/sendSubscribe":
            # Streaming is handled by /a2a/stream endpoint
            return JSONRPCResponse(
                id=rpc.id,
                error=JSONRPCError(
                    code=-32001,
                    message="Use POST /a2a/stream for streaming tasks",
                ),
            )

        else:
            return JSONRPCResponse(
                id=rpc.id,
                error=JSONRPCError(code=-32601, message=f"Method not found: {method}"),
            )

    except Exception as exc:
        logger.exception("A2A JSON-RPC error for method '%s'", method)
        return JSONRPCResponse(
            id=rpc.id,
            error=JSONRPCError(code=-32603, message=f"Internal error: {exc}"),
        )


# ── SSE Streaming endpoint ─────────────────────────────────────────────────────

@router.post(
    "/stream",
    summary="A2A streaming task endpoint (Server-Sent Events)",
)
async def handle_a2a_stream(request: Request) -> StreamingResponse:
    """
    Handles tasks/sendSubscribe via SSE.
    Emits working → completed (or failed) state transitions as SSE events.
    """
    try:
        body = await request.json()
        rpc = JSONRPCRequest(**body)
        params = rpc.params or {}
        task_params = TaskSendParams(**params)
        if not task_params.id:
            task_params.id = str(uuid.uuid4())
    except Exception as exc:
        error_resp = JSONRPCResponse(
            id=None,
            error=JSONRPCError(code=-32700, message=f"Parse error: {exc}"),
        )
        return StreamingResponse(
            _error_sse(error_resp),
            media_type="text/event-stream",
        )

    return StreamingResponse(
        _stream_task(rpc.id, task_params),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


async def _stream_task(rpc_id: Any, params: TaskSendParams) -> AsyncGenerator[str, None]:
    """SSE generator: emit working → completed/failed events."""
    task_id = params.id

    # Emit: submitted
    working_task = Task(
        id=task_id,
        status=TaskStatus(
            state=TaskState.WORKING,
            timestamp=datetime.now(timezone.utc).isoformat(),
        ),
    )
    yield _sse_event(JSONRPCResponse(
        id=rpc_id,
        result=working_task.model_dump(exclude_none=True),
    ))

    # Execute
    final_task = await _execute_a2a_task(params)
    yield _sse_event(JSONRPCResponse(
        id=rpc_id,
        result=final_task.model_dump(exclude_none=True),
    ))


async def _error_sse(resp: JSONRPCResponse) -> AsyncGenerator[str, None]:
    yield _sse_event(resp)


def _sse_event(resp: JSONRPCResponse) -> str:
    data = resp.model_dump_json(exclude_none=True)
    return f"data: {data}\n\n"


# ── Mount well-known at root level ─────────────────────────────────────────────
# This separate router allows mounting at /.well-known/agent.json on the root app.

well_known_router = APIRouter(tags=["a2a"])


@well_known_router.get("/.well-known/agent.json", include_in_schema=True)
async def root_agent_card(request: Request) -> Dict[str, Any]:
    """Standard A2A Agent Card discovery endpoint at /.well-known/agent.json"""
    base_url = str(request.base_url).rstrip("/")
    card = _make_agent_card(base_url=f"{base_url}/a2a")
    return card.model_dump(exclude_none=True)
