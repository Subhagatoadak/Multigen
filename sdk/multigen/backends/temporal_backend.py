"""
Optional Temporal backend for Multigen Runtime.

Install:  pip install temporalio

This thin adapter wraps the existing LocalWorkflowRunner logic in a
Temporal workflow activity, giving you:
  - Durable execution (crash recovery, retries)
  - Long-running workflow support (days/weeks)
  - Workflow versioning
  - Visibility UI (Temporal Web)
  - Distributed task scheduling
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class TemporalBackend:
    """Wraps Multigen DSL execution inside a Temporal workflow activity."""

    def __init__(self, host: str = "localhost:7233", namespace: str = "default") -> None:
        self.host = host
        self.namespace = namespace
        self._client = None

    async def _ensure_client(self) -> Any:
        if self._client:
            return self._client
        from temporalio.client import Client  # type: ignore
        self._client = await Client.connect(self.host, namespace=self.namespace)
        return self._client

    async def run(
        self,
        dsl: Dict[str, Any],
        *,
        payload: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[str] = None,
        task_queue: str = "multigen",
    ) -> Dict[str, Any]:
        """Submit DSL as a Temporal workflow and await result."""
        import uuid

        client = await self._ensure_client()
        wid = workflow_id or f"multigen-{uuid.uuid4().hex[:8]}"

        # Import the registered Temporal workflow
        try:
            from orchestrator.temporal_worker import MultigenWorkflow  # type: ignore
            handle = await client.start_workflow(
                MultigenWorkflow.run,
                {"dsl": dsl, "payload": payload or {}},
                id=wid,
                task_queue=task_queue,
            )
            result = await handle.result()
            return result
        except ImportError:
            # Fallback: run locally if orchestrator worker not installed
            logger.warning("Temporal worker not found, falling back to local runner")
            from ..local_runner import LocalWorkflowRunner
            runner = LocalWorkflowRunner()
            return await runner.run(dsl, payload=payload, workflow_id=wid)

    async def signal(self, workflow_id: str, signal: str, payload: Any = None) -> None:
        """Send a signal to a running Temporal workflow."""
        client = await self._ensure_client()
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(signal, payload)

    async def query(self, workflow_id: str, query: str) -> Any:
        """Query a running Temporal workflow."""
        client = await self._ensure_client()
        handle = client.get_workflow_handle(workflow_id)
        return await handle.query(query)

    async def cancel(self, workflow_id: str) -> None:
        client = await self._ensure_client()
        handle = client.get_workflow_handle(workflow_id)
        await handle.cancel()
