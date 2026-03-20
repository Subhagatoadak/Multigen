"""
Synchronous wrapper around MultigenClient.

Runs the async client on a managed event loop — suitable for scripts,
Jupyter notebooks, and non-async frameworks.

Usage:
    client = SyncMultigenClient("http://localhost:8000")
    resp = client.run_workflow(dsl={"steps": [...]})
    client.interrupt(resp.instance_id)
    state = client.get_state(resp.instance_id)
    client.close()

    # Context manager
    with SyncMultigenClient() as client:
        resp = client.run_workflow(text="Summarise this document")
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

from .client import MultigenClient
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


class SyncMultigenClient:
    """Synchronous façade over the async MultigenClient."""

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        retries: int = 3,
    ) -> None:
        self._loop = asyncio.new_event_loop()
        self._async = MultigenClient(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            retries=retries,
        )

    def _run(self, coro: Any) -> Any:
        return self._loop.run_until_complete(coro)

    def __enter__(self) -> "SyncMultigenClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._run(self._async.close())
        self._loop.close()

    # ── Delegate all methods ───────────────────────────────────────────────────

    def run_workflow(
        self,
        dsl: Optional[Dict[str, Any]] = None,
        text: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> RunResponse:
        return self._run(self._async.run_workflow(dsl=dsl, text=text, payload=payload))

    def interrupt(self, workflow_id: str) -> Dict[str, Any]:
        return self._run(self._async.interrupt(workflow_id))

    def resume(self, workflow_id: str) -> Dict[str, Any]:
        return self._run(self._async.resume(workflow_id))

    def inject_node(self, workflow_id: str, node: InjectNodeRequest) -> Dict[str, Any]:
        return self._run(self._async.inject_node(workflow_id, node))

    def jump_to(self, workflow_id: str, node_id: str) -> Dict[str, Any]:
        return self._run(self._async.jump_to(workflow_id, node_id))

    def skip_node(self, workflow_id: str, node_id: str) -> Dict[str, Any]:
        return self._run(self._async.skip_node(workflow_id, node_id))

    def reroute(
        self,
        workflow_id: str,
        source: str,
        target: str,
        condition: str = "",
    ) -> Dict[str, Any]:
        return self._run(self._async.reroute(workflow_id, source, target, condition))

    def fan_out(self, workflow_id: str, req: FanOutRequest) -> Dict[str, Any]:
        return self._run(self._async.fan_out(workflow_id, req))

    def prune_branch(self, workflow_id: str, node_id: str) -> Dict[str, Any]:
        return self._run(self._async.prune_branch(workflow_id, node_id))

    def get_state(self, workflow_id: str) -> WorkflowState:
        return self._run(self._async.get_state(workflow_id))

    def get_node_state(self, workflow_id: str, node_id: str) -> NodeState:
        return self._run(self._async.get_node_state(workflow_id, node_id))

    def get_health(self, workflow_id: str) -> WorkflowHealth:
        return self._run(self._async.get_health(workflow_id))

    def get_metrics(self, workflow_id: str) -> WorkflowMetrics:
        return self._run(self._async.get_metrics(workflow_id))

    def get_pending_count(self, workflow_id: str) -> int:
        return self._run(self._async.get_pending_count(workflow_id))

    def list_capabilities(self) -> List[Capability]:
        return self._run(self._async.list_capabilities())

    def get_capability(self, name: str) -> Capability:
        return self._run(self._async.get_capability(name))

    def register_capability(self, capability: Capability) -> Capability:
        return self._run(self._async.register_capability(capability))

    def list_agents(self) -> List[Dict[str, str]]:
        return self._run(self._async.list_agents())

    def ping(self) -> bool:
        return self._run(self._async.ping())
