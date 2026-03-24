"""Time-travel debugging helpers for the Flow Engine (Temporal + MongoDB)."""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def build_snapshot(
    *,
    workflow_id: str,
    step_index: int,
    node_id: str,
    agent: str,
    input_params: Dict[str, Any],
    output: Dict[str, Any],
    context: Dict[str, Any],
    duration_s: float,
    status: str = "success",
    error: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a canonical snapshot dict suitable for persistence."""
    return {
        "workflow_id": workflow_id,
        "step_index": step_index,
        "node_id": node_id,
        "agent": agent,
        "input_params": copy.deepcopy(input_params),
        "output": copy.deepcopy(output),
        "context": copy.deepcopy(context),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "duration_ms": duration_s * 1000,
        "status": status,
        "error": error,
        "metadata": metadata or {},
    }


class MongoSnapshotStore:
    """
    MongoDB-backed snapshot store for production GraphWorkflow time-travel.

    Uses a dedicated ``workflow_snapshots`` collection, separate from the
    ``graph_state`` CQRS read model.

    Parameters
    ----------
    db : Motor AsyncIOMotorDatabase
        Async Motor database handle (already used by the rest of the engine).

    Usage inside GraphWorkflow::

        from flow_engine.graph.time_travel import MongoSnapshotStore, build_snapshot
        from orchestrator.services.mongo import get_db

        db = await get_db()
        snap_store = MongoSnapshotStore(db)

        snap = build_snapshot(
            workflow_id=wf_id,
            step_index=node_execution_index,
            node_id=node_id,
            agent=agent_name,
            input_params=resolved_params,
            output=result,
            context=dict(context),
            duration_s=elapsed,
        )
        await snap_store.save(snap)
    """

    COLLECTION = "workflow_snapshots"

    def __init__(self, db: Any) -> None:
        self._db = db

    async def save(self, snapshot: Dict[str, Any]) -> str:
        result = await self._db[self.COLLECTION].insert_one(snapshot)
        return str(result.inserted_id)

    async def load_at_step(
        self, workflow_id: str, step_index: int
    ) -> Optional[Dict[str, Any]]:
        """Return the snapshot for a specific execution step."""
        return await self._db[self.COLLECTION].find_one(
            {"workflow_id": workflow_id, "step_index": step_index},
            {"_id": 0},
        )

    async def list_for_workflow(self, workflow_id: str) -> List[Dict[str, Any]]:
        """Return all snapshots for a workflow ordered by step index."""
        cursor = (
            self._db[self.COLLECTION]
            .find({"workflow_id": workflow_id}, {"_id": 0})
            .sort("step_index", 1)
        )
        return await cursor.to_list(length=None)

    async def get_context_at_step(
        self, workflow_id: str, step_index: int
    ) -> Dict[str, Any]:
        """
        Return the accumulated context dict as it existed after step N completed.

        Useful for reconstructing "what did every upstream node output?" at any
        point in a historical execution.
        """
        snap = await self.load_at_step(workflow_id, step_index)
        if snap is None:
            raise ValueError(
                f"No snapshot at step {step_index} for workflow {workflow_id!r}"
            )
        return snap.get("context", {})

    async def diff_steps(
        self, workflow_id: str, step_a: int, step_b: int
    ) -> Dict[str, Any]:
        """
        Diff the captured state at two step indices.

        Returns ``{field: {"before": ..., "after": ...}}`` for fields that differ.
        """
        snap_a = await self.load_at_step(workflow_id, step_a)
        snap_b = await self.load_at_step(workflow_id, step_b)
        if snap_a is None or snap_b is None:
            missing = step_a if snap_a is None else step_b
            raise ValueError(f"No snapshot at step {missing} for {workflow_id!r}")

        result: Dict[str, Any] = {}
        for key in ("input_params", "output", "context", "status", "error"):
            a, b = snap_a.get(key), snap_b.get(key)
            if a != b:
                result[key] = {"before": a, "after": b}
        return result


__all__ = ["build_snapshot", "MongoSnapshotStore"]
