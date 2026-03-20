"""
Multigen async Python client.

Wraps all orchestrator REST endpoints with typed methods, automatic
error mapping, and configurable retry/timeout behaviour.

Usage:
    async with MultigenClient("http://localhost:8000") as client:
        # Start a workflow from DSL
        resp = await client.run_workflow(dsl={"steps": [...]})

        # Start from natural language
        resp = await client.run_workflow(text="Screen and rank these candidates")

        # Graph control
        await client.interrupt(resp.instance_id)
        await client.jump_to(resp.instance_id, "safety_check")
        await client.resume(resp.instance_id)

        # Fan-out (parallel reasoning)
        await client.fan_out(resp.instance_id, FanOutRequest(
            group_id="multi_critique",
            consensus="highest_confidence",
            nodes=[
                FanOutNodeDef(id="critic_a", agent="StrictCritiqueAgent"),
                FanOutNodeDef(id="critic_b", agent="CreativeCritiqueAgent"),
            ],
        ))

        # State
        state = await client.get_state(resp.instance_id)
        metrics = await client.get_metrics(resp.instance_id)
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import httpx

from .exceptions import (
    AgentNotFoundError,
    DSLValidationError,
    GraphSignalError,
    MultigenError,
    MultigenHTTPError,
    StateReadError,
    WorkflowNotFoundError,
    WorkflowStartError,
)
from .models import (
    Capability,
    FanOutRequest,
    InjectNodeRequest,
    NodeState,
    RunResponse,
    WorkflowHealth,
    WorkflowMetrics,
    WorkflowState,
)

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 30.0
_DEFAULT_RETRIES = 3


def _raise_for(resp: httpx.Response, context: str = "") -> None:
    """Map HTTP error status codes to typed SDK exceptions."""
    if resp.status_code == 404:
        detail = _safe_detail(resp)
        if "agent" in detail.lower():
            raise AgentNotFoundError(detail)
        raise WorkflowNotFoundError(detail or context)
    if resp.status_code == 400:
        raise DSLValidationError(_safe_detail(resp))
    if resp.status_code >= 500:
        raise MultigenHTTPError(resp.status_code, _safe_detail(resp))
    if resp.status_code >= 400:
        raise MultigenHTTPError(resp.status_code, _safe_detail(resp))


def _safe_detail(resp: httpx.Response) -> str:
    try:
        return resp.json().get("detail", resp.text)
    except Exception:
        return resp.text


class MultigenClient:
    """
    Async HTTP client for the Multigen Orchestrator API.

    Parameters
    ----------
    base_url : str
        Root URL of the orchestrator, e.g. "http://localhost:8000".
    api_key : str, optional
        Bearer token if API key auth is enabled on the server.
    timeout : float
        Default request timeout in seconds.
    retries : int
        Number of times to retry on transient network errors.
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
        retries: int = _DEFAULT_RETRIES,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        transport = httpx.AsyncHTTPTransport(retries=retries)
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout,
            transport=transport,
        )

    async def __aenter__(self) -> "MultigenClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    # ── Workflow lifecycle ─────────────────────────────────────────────────────

    async def run_workflow(
        self,
        dsl: Optional[Dict[str, Any]] = None,
        text: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> RunResponse:
        """
        Start a new workflow execution.

        Provide either dsl (dict with 'steps' key) or text (natural language,
        requires OPENAI_API_KEY on the server). payload is the fallback dict
        passed to steps that do not declare explicit params.
        """
        if not dsl and not text:
            raise MultigenError("Provide either 'dsl' or 'text'")
        body: Dict[str, Any] = {"payload": payload or {}}
        if dsl:
            body["dsl"] = dsl
        if text:
            body["text"] = text

        resp = await self._http.post("/workflows/run", json=body)
        _raise_for(resp, "start workflow")
        return RunResponse.model_validate(resp.json())

    # ── Graph control signals ──────────────────────────────────────────────────

    async def interrupt(self, workflow_id: str) -> Dict[str, Any]:
        """Pause a running workflow at the next node boundary."""
        resp = await self._http.post(f"/workflows/{workflow_id}/interrupt")
        if resp.status_code not in (200, 404):
            _raise_for(resp, "interrupt")
        if resp.status_code == 404:
            raise WorkflowNotFoundError(workflow_id)
        return resp.json()

    async def resume(self, workflow_id: str) -> Dict[str, Any]:
        """Resume a paused workflow."""
        resp = await self._http.post(f"/workflows/{workflow_id}/resume")
        _raise_for(resp, "resume")
        return resp.json()

    async def inject_node(self, workflow_id: str, node: InjectNodeRequest) -> Dict[str, Any]:
        """Append a new node to a running workflow at runtime."""
        resp = await self._http.post(
            f"/workflows/{workflow_id}/inject",
            json=node.model_dump(exclude_none=True),
        )
        _raise_for(resp, "inject_node")
        return resp.json()

    async def jump_to(self, workflow_id: str, node_id: str) -> Dict[str, Any]:
        """Push node_id to the front of the execution queue (priority lane)."""
        resp = await self._http.post(
            f"/workflows/{workflow_id}/jump",
            json={"node_id": node_id},
        )
        _raise_for(resp, "jump_to")
        return resp.json()

    async def skip_node(self, workflow_id: str, node_id: str) -> Dict[str, Any]:
        """Mark a node to be silently dropped (branch no-go)."""
        resp = await self._http.post(
            f"/workflows/{workflow_id}/skip",
            json={"node_id": node_id},
        )
        _raise_for(resp, "skip_node")
        return resp.json()

    async def reroute(
        self,
        workflow_id: str,
        source: str,
        target: str,
        condition: str = "",
    ) -> Dict[str, Any]:
        """Add a dynamic edge between two nodes at runtime."""
        resp = await self._http.post(
            f"/workflows/{workflow_id}/reroute",
            json={"source": source, "target": target, "condition": condition},
        )
        _raise_for(resp, "reroute")
        return resp.json()

    async def fan_out(self, workflow_id: str, req: FanOutRequest) -> Dict[str, Any]:
        """Execute N nodes in parallel then merge via consensus strategy."""
        resp = await self._http.post(
            f"/workflows/{workflow_id}/fan-out",
            json=req.model_dump(exclude_none=True),
        )
        _raise_for(resp, "fan_out")
        return resp.json()

    async def prune_branch(self, workflow_id: str, node_id: str) -> Dict[str, Any]:
        """Cancel a branch node and all its reachable descendants."""
        resp = await self._http.post(
            f"/workflows/{workflow_id}/prune",
            json={"node_id": node_id},
        )
        _raise_for(resp, "prune_branch")
        return resp.json()

    # ── State / observability ──────────────────────────────────────────────────

    async def get_state(self, workflow_id: str) -> WorkflowState:
        """Read all node outputs from the MongoDB CQRS read model."""
        resp = await self._http.get(f"/workflows/{workflow_id}/state")
        _raise_for(resp, "get_state")
        data = resp.json()
        return WorkflowState(
            workflow_id=data["workflow_id"],
            nodes=[NodeState(**n) for n in data.get("nodes", [])],
            count=data.get("count", 0),
        )

    async def get_node_state(self, workflow_id: str, node_id: str) -> NodeState:
        """Read a single node's output from MongoDB."""
        resp = await self._http.get(f"/workflows/{workflow_id}/state/{node_id}")
        _raise_for(resp, f"get_node_state({node_id})")
        return NodeState.model_validate(resp.json())

    async def get_health(self, workflow_id: str) -> WorkflowHealth:
        """
        Query live circuit breaker status, error log, and interrupt state
        from the running Temporal workflow (no DB round-trip).
        """
        resp = await self._http.get(f"/workflows/{workflow_id}/health")
        _raise_for(resp, "get_health")
        return WorkflowHealth.model_validate(resp.json())

    async def get_metrics(self, workflow_id: str) -> WorkflowMetrics:
        """Query live execution counters from the running Temporal workflow."""
        resp = await self._http.get(f"/workflows/{workflow_id}/metrics")
        _raise_for(resp, "get_metrics")
        return WorkflowMetrics.model_validate(resp.json())

    async def get_pending_count(self, workflow_id: str) -> int:
        """Return the number of nodes currently waiting in the execution queue."""
        resp = await self._http.get(f"/workflows/{workflow_id}/pending")
        _raise_for(resp, "get_pending_count")
        return resp.json().get("pending_count", 0)

    # ── Capability directory ───────────────────────────────────────────────────

    async def list_capabilities(self) -> List[Capability]:
        """List all registered agent capabilities."""
        resp = await self._http.get("/capabilities")
        _raise_for(resp, "list_capabilities")
        return [Capability.model_validate(c) for c in resp.json()]

    async def get_capability(self, name: str) -> Capability:
        """Fetch a specific agent capability by name."""
        resp = await self._http.get(f"/capabilities/{name}")
        _raise_for(resp, f"get_capability({name})")
        return Capability.model_validate(resp.json())

    async def register_capability(self, capability: Capability) -> Capability:
        """Register a new agent capability."""
        resp = await self._http.post(
            "/capabilities",
            json=capability.model_dump(exclude_none=True),
        )
        _raise_for(resp, "register_capability")
        return Capability.model_validate(resp.json())

    # ── Registered agents ──────────────────────────────────────────────────────

    async def list_agents(self) -> List[Dict[str, str]]:
        """List all agents currently loaded in the worker registry."""
        resp = await self._http.get("/agents")
        _raise_for(resp, "list_agents")
        return resp.json().get("agents", [])

    # ── Health ─────────────────────────────────────────────────────────────────

    async def ping(self) -> bool:
        """Return True if the orchestrator is reachable and healthy."""
        try:
            resp = await self._http.get("/health")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False
