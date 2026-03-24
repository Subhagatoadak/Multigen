"""Snapshot store for time-travel debugging."""
from __future__ import annotations

import json
import sqlite3
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Snapshot:
    """Complete state capture at a single workflow step."""

    workflow_id: str
    step_index: int
    node_id: str
    agent: str
    input_params: Dict[str, Any]
    output: Dict[str, Any]
    context: Dict[str, Any]
    timestamp: str
    duration_ms: float
    status: str  # "success" | "failed" | "skipped"
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    snapshot_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    def diff(self, other: "Snapshot") -> Dict[str, Any]:
        """Return fields that differ between this snapshot and *other*."""
        result: Dict[str, Any] = {}
        for attr in ("input_params", "output", "context", "status", "error"):
            a, b = getattr(self, attr), getattr(other, attr)
            if a != b:
                result[attr] = {"before": a, "after": b}
        return result

    def to_dict(self) -> Dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "workflow_id": self.workflow_id,
            "step_index": self.step_index,
            "node_id": self.node_id,
            "agent": self.agent,
            "input_params": self.input_params,
            "output": self.output,
            "context": self.context,
            "timestamp": self.timestamp,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error": self.error,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Snapshot":
        return cls(**d)


class SnapshotStore(ABC):
    """Abstract base for snapshot persistence backends."""

    @abstractmethod
    async def save(self, snapshot: Snapshot) -> str: ...

    @abstractmethod
    async def load(self, snapshot_id: str) -> Optional[Snapshot]: ...

    @abstractmethod
    async def list_for_workflow(self, workflow_id: str) -> List[Snapshot]: ...

    @abstractmethod
    async def load_at_step(self, workflow_id: str, step_index: int) -> Optional[Snapshot]: ...


class InMemorySnapshotStore(SnapshotStore):
    """In-memory store — zero dependencies, ideal for tests and notebooks."""

    def __init__(self) -> None:
        self._store: Dict[str, Snapshot] = {}

    async def save(self, snapshot: Snapshot) -> str:
        self._store[snapshot.snapshot_id] = snapshot
        return snapshot.snapshot_id

    async def load(self, snapshot_id: str) -> Optional[Snapshot]:
        return self._store.get(snapshot_id)

    async def list_for_workflow(self, workflow_id: str) -> List[Snapshot]:
        return sorted(
            (s for s in self._store.values() if s.workflow_id == workflow_id),
            key=lambda s: s.step_index,
        )

    async def load_at_step(self, workflow_id: str, step_index: int) -> Optional[Snapshot]:
        for s in self._store.values():
            if s.workflow_id == workflow_id and s.step_index == step_index:
                return s
        return None

    def clear(self) -> None:
        self._store.clear()

    def __len__(self) -> int:
        return len(self._store)


class SQLiteSnapshotStore(SnapshotStore):
    """SQLite-backed store — persistent local development storage."""

    _CREATE = """
        CREATE TABLE IF NOT EXISTS snapshots (
            snapshot_id  TEXT PRIMARY KEY,
            workflow_id  TEXT NOT NULL,
            step_index   INTEGER NOT NULL,
            node_id      TEXT NOT NULL,
            agent        TEXT NOT NULL,
            input_params TEXT NOT NULL,
            output       TEXT NOT NULL,
            context      TEXT NOT NULL,
            timestamp    TEXT NOT NULL,
            duration_ms  REAL NOT NULL,
            status       TEXT NOT NULL,
            error        TEXT,
            metadata     TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_wf_step
            ON snapshots(workflow_id, step_index);
    """

    def __init__(self, db_path: str = "multigen_snapshots.db") -> None:
        self._db_path = db_path
        with sqlite3.connect(self._db_path) as conn:
            conn.executescript(self._CREATE)

    async def save(self, snapshot: Snapshot) -> str:
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    snapshot.snapshot_id,
                    snapshot.workflow_id,
                    snapshot.step_index,
                    snapshot.node_id,
                    snapshot.agent,
                    json.dumps(snapshot.input_params),
                    json.dumps(snapshot.output),
                    json.dumps(snapshot.context),
                    snapshot.timestamp,
                    snapshot.duration_ms,
                    snapshot.status,
                    snapshot.error,
                    json.dumps(snapshot.metadata),
                ),
            )
        return snapshot.snapshot_id

    async def load(self, snapshot_id: str) -> Optional[Snapshot]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone()
        return _row_to_snapshot(row) if row else None

    async def list_for_workflow(self, workflow_id: str) -> List[Snapshot]:
        with sqlite3.connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM snapshots WHERE workflow_id=? ORDER BY step_index",
                (workflow_id,),
            ).fetchall()
        return [_row_to_snapshot(r) for r in rows]

    async def load_at_step(self, workflow_id: str, step_index: int) -> Optional[Snapshot]:
        with sqlite3.connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM snapshots WHERE workflow_id=? AND step_index=?",
                (workflow_id, step_index),
            ).fetchone()
        return _row_to_snapshot(row) if row else None


def _row_to_snapshot(row: tuple) -> Snapshot:
    return Snapshot(
        snapshot_id=row[0],
        workflow_id=row[1],
        step_index=row[2],
        node_id=row[3],
        agent=row[4],
        input_params=json.loads(row[5]),
        output=json.loads(row[6]),
        context=json.loads(row[7]),
        timestamp=row[8],
        duration_ms=row[9],
        status=row[10],
        error=row[11],
        metadata=json.loads(row[12]),
    )


__all__ = [
    "InMemorySnapshotStore",
    "Snapshot",
    "SnapshotStore",
    "SQLiteSnapshotStore",
]
