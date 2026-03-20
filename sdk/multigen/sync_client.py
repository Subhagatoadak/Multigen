"""
Synchronous wrapper around MultigenClient.

Runs the async client in a dedicated background thread — works correctly
from plain scripts, Jupyter notebooks, and non-async frameworks even when
an event loop is already running in the calling thread.

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
import concurrent.futures
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
    """
    Synchronous façade over the async MultigenClient.

    Uses a dedicated daemon thread with its own event loop so that
    ``_run()`` never conflicts with an already-running loop (e.g. Jupyter).
    """

    def __init__(
        self,
        base_url: str = "http://localhost:8000",
        api_key: Optional[str] = None,
        timeout: float = 30.0,
        retries: int = 3,
    ) -> None:
        # Dedicated event loop living in a background thread.
        self._loop = asyncio.new_event_loop()
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="multigen-sync"
        )
        # Start the loop inside the background thread.
        self._executor.submit(self._loop.run_forever)

        self._async = MultigenClient(
            base_url=base_url,
            api_key=api_key,
            timeout=timeout,
            retries=retries,
        )

    def _run(self, coro: Any) -> Any:
        """Submit a coroutine to the background loop and block until done."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def __enter__(self) -> "SyncMultigenClient":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def close(self) -> None:
        self._run(self._async.close())
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._executor.shutdown(wait=True)

    # ── Workflow lifecycle ─────────────────────────────────────────────────────

    def run_workflow(
        self,
        dsl: Optional[Dict[str, Any]] = None,
        text: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> RunResponse:
        return self._run(self._async.run_workflow(dsl=dsl, text=text, payload=payload))

    def run_graph(
        self,
        graph_def: Dict[str, Any],
        payload: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[str] = None,
    ) -> RunResponse:
        return self._run(self._async.run_graph(graph_def=graph_def, payload=payload, workflow_id=workflow_id))

    # ── Graph control signals ──────────────────────────────────────────────────

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

    # ── State / observability ──────────────────────────────────────────────────

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

    # ── Epistemic transparency ─────────────────────────────────────────────────

    def get_epistemic_report(self, workflow_id: str) -> Dict[str, Any]:
        return self._run(self._async.get_epistemic_report(workflow_id))

    # ── Human approval gates ───────────────────────────────────────────────────

    def get_pending_approvals(self, workflow_id: str) -> List[Dict[str, Any]]:
        return self._run(self._async.get_pending_approvals(workflow_id))

    def approve_agent(self, workflow_id: str, spec: Dict[str, Any]) -> Dict[str, Any]:
        return self._run(self._async.approve_agent(workflow_id, spec))

    def reject_agent(
        self,
        workflow_id: str,
        agent_name: str,
        reason: str = "rejected by human reviewer",
    ) -> Dict[str, Any]:
        return self._run(self._async.reject_agent(workflow_id, agent_name, reason))

    def get_dynamic_agents(self, workflow_id: str) -> Dict[str, Any]:
        return self._run(self._async.get_dynamic_agents(workflow_id))

    # ── Capability directory ───────────────────────────────────────────────────

    def list_capabilities(self) -> List[Capability]:
        return self._run(self._async.list_capabilities())

    def get_capability(self, name: str) -> Capability:
        return self._run(self._async.get_capability(name))

    def register_capability(self, capability: Capability) -> Capability:
        return self._run(self._async.register_capability(capability))

    # ── Registered agents ──────────────────────────────────────────────────────

    def list_agents(self) -> List[Dict[str, str]]:
        return self._run(self._async.list_agents())

    # ── Health ─────────────────────────────────────────────────────────────────

    def ping(self) -> bool:
        return self._run(self._async.ping())
