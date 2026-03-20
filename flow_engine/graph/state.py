"""
Distributed graph state backends.

The graph engine writes each node's output to a state backend after execution.
This makes state observable externally (API, dashboards) without reading
Temporal's internal event history.

Two backends:
    MemoryGraphState  — in-process dict; for testing and single-worker dev.
    MongoGraphState   — MongoDB collection; distributed, survives worker restarts.

The Temporal workflow itself is the SOURCE OF TRUTH for execution order and
correctness (Temporal event sourcing). MongoDB is the READ MODEL — a
materialised view for external state queries (CQRS pattern).
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class GraphStateBackend(ABC):
    """Abstract interface for graph state storage."""

    @abstractmethod
    async def set_node_output(
        self, workflow_id: str, node_id: str, output: Dict[str, Any]
    ) -> None: ...

    @abstractmethod
    async def get_node_output(
        self, workflow_id: str, node_id: str
    ) -> Optional[Dict[str, Any]]: ...

    @abstractmethod
    async def get_all_outputs(self, workflow_id: str) -> Dict[str, Dict[str, Any]]: ...

    @abstractmethod
    async def list_workflows(self) -> List[str]: ...


class MemoryGraphState(GraphStateBackend):
    """
    In-process state backend for development and testing.
    Not distributed — only accessible within the same process.
    """

    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, Any]] = {}  # {workflow_id: {node_id: output}}

    async def set_node_output(
        self, workflow_id: str, node_id: str, output: Dict[str, Any]
    ) -> None:
        wf = self._store.setdefault(workflow_id, {})
        wf[node_id] = {
            "output": output,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def get_node_output(
        self, workflow_id: str, node_id: str
    ) -> Optional[Dict[str, Any]]:
        return self._store.get(workflow_id, {}).get(node_id)

    async def get_all_outputs(self, workflow_id: str) -> Dict[str, Dict[str, Any]]:
        return dict(self._store.get(workflow_id, {}))

    async def list_workflows(self) -> List[str]:
        return list(self._store.keys())


class MongoGraphState(GraphStateBackend):
    """
    MongoDB-backed distributed graph state.

    Collection schema:
        {
          workflow_id: str,
          node_id:     str,
          output:      dict,
          updated_at:  ISO datetime string
        }

    Indexed on (workflow_id, node_id) for fast point reads.
    """

    def __init__(self, mongo_uri: str, db_name: str = "multigen") -> None:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
            self._client = AsyncIOMotorClient(mongo_uri)
            self._col = self._client[db_name]["graph_state"]
        except ImportError:
            raise RuntimeError(
                "motor is required for MongoGraphState: pip install motor"
            )

    async def set_node_output(
        self, workflow_id: str, node_id: str, output: Dict[str, Any]
    ) -> None:
        await self._col.update_one(
            {"workflow_id": workflow_id, "node_id": node_id},
            {
                "$set": {
                    "output": output,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            },
            upsert=True,
        )
        logger.debug("GraphState persisted: %s / %s", workflow_id, node_id)

    async def get_node_output(
        self, workflow_id: str, node_id: str
    ) -> Optional[Dict[str, Any]]:
        doc = await self._col.find_one(
            {"workflow_id": workflow_id, "node_id": node_id}
        )
        return doc.get("output") if doc else None

    async def get_all_outputs(self, workflow_id: str) -> Dict[str, Dict[str, Any]]:
        cursor = self._col.find({"workflow_id": workflow_id})
        return {
            doc["node_id"]: doc.get("output", {})
            async for doc in cursor
        }

    async def list_workflows(self) -> List[str]:
        return await self._col.distinct("workflow_id")
