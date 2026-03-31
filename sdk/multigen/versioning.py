"""
Workflow versioning for Multigen — safe iteration in production.

Features
--------
- ``WorkflowVersion``       — immutable snapshot of a workflow definition
- ``VersionedWorkflow``     — workflow with full version history
- ``WorkflowDiff``          — structured diff between two versions
- ``VersionStore``          — abstract storage backend
- ``InMemoryVersionStore``  — in-process version store
- ``SQLiteVersionStore``    — durable SQLite-backed version store
- ``ChangeLog``             — human-readable change history
- ``semver``                — lightweight semantic version helpers

Usage::

    from multigen.versioning import (
        VersionedWorkflow, SQLiteVersionStore, WorkflowDiff,
    )

    store = SQLiteVersionStore("workflows.db")
    wf = VersionedWorkflow(name="report-pipeline", store=store)

    # Save initial definition
    v1 = await wf.commit(
        definition={"steps": ["load", "analyse", "write"]},
        message="Initial pipeline",
        author="alice",
    )
    print(v1.version)   # "1.0.0"

    # Iterate
    v2 = await wf.commit(
        definition={"steps": ["load", "validate", "analyse", "write"]},
        message="Add validation step",
        author="bob",
    )

    # Inspect diff
    diff = wf.diff(v1.version, v2.version)
    print(diff.summary())

    # Rollback
    await wf.rollback(v1.version)
    current = await wf.current()
    print(current.version)  # "1.0.0"
"""
from __future__ import annotations

import copy
import json
import sqlite3
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


# ── Semantic version helpers ───────────────────────────────────────────────────

def _parse_version(v: str) -> Tuple[int, int, int]:
    parts = v.lstrip("v").split(".")
    try:
        major, minor, patch = int(parts[0]), int(parts[1]), int(parts[2])
    except (IndexError, ValueError):
        major, minor, patch = 1, 0, 0
    return major, minor, patch


def _bump(v: str, bump: str = "patch") -> str:
    major, minor, patch = _parse_version(v)
    if bump == "major":
        return f"{major+1}.0.0"
    if bump == "minor":
        return f"{major}.{minor+1}.0"
    return f"{major}.{minor}.{patch+1}"


# ── WorkflowVersion ────────────────────────────────────────────────────────────

@dataclass
class WorkflowVersion:
    """
    Immutable snapshot of a workflow definition at a point in time.

    Attributes
    ----------
    workflow_name   Name of the workflow this version belongs to.
    version         Semantic version string (e.g. ``"1.2.3"``).
    definition      The workflow definition (any JSON-serialisable dict).
    message         Commit message.
    author          Who made the change.
    timestamp       Unix timestamp of the commit.
    version_id      Unique identifier for this specific snapshot.
    tags            Optional labels (e.g. ``["stable", "canary"]``).
    parent_version  Version string of the preceding snapshot (None for first).
    """

    workflow_name: str
    version: str
    definition: Dict[str, Any]
    message: str = ""
    author: str = "system"
    timestamp: float = field(default_factory=time.time)
    version_id: str = field(default_factory=lambda: str(uuid.uuid4())[:12])
    tags: List[str] = field(default_factory=list)
    parent_version: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_name": self.workflow_name,
            "version": self.version,
            "definition": self.definition,
            "message": self.message,
            "author": self.author,
            "timestamp": self.timestamp,
            "version_id": self.version_id,
            "tags": self.tags,
            "parent_version": self.parent_version,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "WorkflowVersion":
        return cls(
            workflow_name=d["workflow_name"],
            version=d["version"],
            definition=d["definition"],
            message=d.get("message", ""),
            author=d.get("author", "system"),
            timestamp=d.get("timestamp", time.time()),
            version_id=d.get("version_id", str(uuid.uuid4())[:12]),
            tags=d.get("tags", []),
            parent_version=d.get("parent_version"),
        )

    def __repr__(self) -> str:
        return f"WorkflowVersion({self.workflow_name!r} v{self.version} by {self.author!r})"


# ── WorkflowDiff ───────────────────────────────────────────────────────────────

@dataclass
class WorkflowDiff:
    """Structured diff between two workflow versions."""

    from_version: str
    to_version: str
    workflow_name: str
    added_keys: List[str] = field(default_factory=list)
    removed_keys: List[str] = field(default_factory=list)
    changed_keys: List[str] = field(default_factory=list)
    unchanged_keys: List[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added_keys or self.removed_keys or self.changed_keys)

    def summary(self) -> str:
        lines = [
            f"Diff {self.workflow_name}: v{self.from_version} → v{self.to_version}",
        ]
        if not self.has_changes:
            lines.append("  (no changes)")
            return "\n".join(lines)
        if self.added_keys:
            lines.append(f"  + Added:   {self.added_keys}")
        if self.removed_keys:
            lines.append(f"  - Removed: {self.removed_keys}")
        if self.changed_keys:
            lines.append(f"  ~ Changed: {self.changed_keys}")
        return "\n".join(lines)


def _diff_definitions(
    a: Dict[str, Any], b: Dict[str, Any],
    workflow_name: str, from_v: str, to_v: str,
) -> WorkflowDiff:
    a_keys = set(a.keys())
    b_keys = set(b.keys())
    added = sorted(b_keys - a_keys)
    removed = sorted(a_keys - b_keys)
    common = a_keys & b_keys
    changed = sorted(k for k in common if a[k] != b[k])
    unchanged = sorted(k for k in common if a[k] == b[k])
    return WorkflowDiff(
        from_version=from_v,
        to_version=to_v,
        workflow_name=workflow_name,
        added_keys=added,
        removed_keys=removed,
        changed_keys=changed,
        unchanged_keys=unchanged,
    )


# ── ChangeLog ─────────────────────────────────────────────────────────────────

class ChangeLog:
    """Human-readable change history for a workflow."""

    def __init__(self, versions: List[WorkflowVersion]) -> None:
        self._versions = sorted(versions, key=lambda v: v.timestamp, reverse=True)

    def render(self, max_entries: int = 20) -> str:
        lines = []
        for wv in self._versions[:max_entries]:
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(wv.timestamp))
            tags = f"  [{', '.join(wv.tags)}]" if wv.tags else ""
            lines.append(f"v{wv.version}  {ts}  {wv.author}: {wv.message}{tags}")
        return "\n".join(lines) if lines else "(empty)"

    def __repr__(self) -> str:
        return f"ChangeLog({len(self._versions)} entries)"


# ── VersionStore (abstract) ────────────────────────────────────────────────────

class VersionStore(ABC):
    @abstractmethod
    async def save(self, version: WorkflowVersion) -> None: ...

    @abstractmethod
    async def get(self, workflow_name: str, version: str) -> Optional[WorkflowVersion]: ...

    @abstractmethod
    async def list_versions(self, workflow_name: str) -> List[WorkflowVersion]: ...

    @abstractmethod
    async def latest(self, workflow_name: str) -> Optional[WorkflowVersion]: ...

    @abstractmethod
    async def delete(self, workflow_name: str, version: str) -> bool: ...

    @abstractmethod
    async def list_workflows(self) -> List[str]: ...


# ── InMemoryVersionStore ───────────────────────────────────────────────────────

class InMemoryVersionStore(VersionStore):
    """
    Non-persistent, in-process version store.

    Versions are lost on process exit.  Use ``SQLiteVersionStore`` for durability.
    """

    def __init__(self) -> None:
        # workflow_name → {version_str: WorkflowVersion}
        self._store: Dict[str, Dict[str, WorkflowVersion]] = {}

    async def save(self, version: WorkflowVersion) -> None:
        self._store.setdefault(version.workflow_name, {})[version.version] = version

    async def get(self, workflow_name: str, version: str) -> Optional[WorkflowVersion]:
        return self._store.get(workflow_name, {}).get(version)

    async def list_versions(self, workflow_name: str) -> List[WorkflowVersion]:
        versions = list(self._store.get(workflow_name, {}).values())
        return sorted(versions, key=lambda v: v.timestamp)

    async def latest(self, workflow_name: str) -> Optional[WorkflowVersion]:
        versions = await self.list_versions(workflow_name)
        return versions[-1] if versions else None

    async def delete(self, workflow_name: str, version: str) -> bool:
        wf = self._store.get(workflow_name, {})
        if version in wf:
            del wf[version]
            return True
        return False

    async def list_workflows(self) -> List[str]:
        return list(self._store.keys())


# ── SQLiteVersionStore ─────────────────────────────────────────────────────────

class SQLiteVersionStore(VersionStore):
    """
    Durable SQLite-backed version store.

    Usage::

        store = SQLiteVersionStore("workflows.db")
        wf = VersionedWorkflow("my-pipeline", store=store)
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS workflow_versions (
            workflow_name TEXT NOT NULL,
            version       TEXT NOT NULL,
            definition    TEXT NOT NULL,
            message       TEXT NOT NULL DEFAULT '',
            author        TEXT NOT NULL DEFAULT 'system',
            timestamp     REAL NOT NULL,
            version_id    TEXT NOT NULL,
            tags          TEXT NOT NULL DEFAULT '[]',
            parent_version TEXT,
            PRIMARY KEY (workflow_name, version)
        );
        CREATE INDEX IF NOT EXISTS idx_wv_name ON workflow_versions (workflow_name, timestamp);
    """

    def __init__(self, db_path: str = "workflows.db") -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        for stmt in self._DDL.strip().split(";"):
            s = stmt.strip()
            if s:
                self._conn.execute(s)
        self._conn.commit()

    def _row_to_version(self, row: sqlite3.Row) -> WorkflowVersion:
        return WorkflowVersion(
            workflow_name=row["workflow_name"],
            version=row["version"],
            definition=json.loads(row["definition"]),
            message=row["message"],
            author=row["author"],
            timestamp=row["timestamp"],
            version_id=row["version_id"],
            tags=json.loads(row["tags"]),
            parent_version=row["parent_version"],
        )

    async def save(self, version: WorkflowVersion) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO workflow_versions
               (workflow_name, version, definition, message, author,
                timestamp, version_id, tags, parent_version)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                version.workflow_name,
                version.version,
                json.dumps(version.definition),
                version.message,
                version.author,
                version.timestamp,
                version.version_id,
                json.dumps(version.tags),
                version.parent_version,
            ),
        )
        self._conn.commit()

    async def get(self, workflow_name: str, version: str) -> Optional[WorkflowVersion]:
        cur = self._conn.execute(
            "SELECT * FROM workflow_versions WHERE workflow_name=? AND version=?",
            (workflow_name, version),
        )
        row = cur.fetchone()
        return self._row_to_version(row) if row else None

    async def list_versions(self, workflow_name: str) -> List[WorkflowVersion]:
        cur = self._conn.execute(
            "SELECT * FROM workflow_versions WHERE workflow_name=? ORDER BY timestamp ASC",
            (workflow_name,),
        )
        return [self._row_to_version(r) for r in cur.fetchall()]

    async def latest(self, workflow_name: str) -> Optional[WorkflowVersion]:
        cur = self._conn.execute(
            "SELECT * FROM workflow_versions WHERE workflow_name=? ORDER BY timestamp DESC LIMIT 1",
            (workflow_name,),
        )
        row = cur.fetchone()
        return self._row_to_version(row) if row else None

    async def delete(self, workflow_name: str, version: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM workflow_versions WHERE workflow_name=? AND version=?",
            (workflow_name, version),
        )
        self._conn.commit()
        return cur.rowcount > 0

    async def list_workflows(self) -> List[str]:
        cur = self._conn.execute(
            "SELECT DISTINCT workflow_name FROM workflow_versions ORDER BY workflow_name"
        )
        return [r["workflow_name"] for r in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()


# ── VersionedWorkflow ─────────────────────────────────────────────────────────

class VersionedWorkflow:
    """
    High-level facade for versioned workflow management.

    Usage::

        wf = VersionedWorkflow("report-pipeline", store=SQLiteVersionStore("wf.db"))
        v1 = await wf.commit({"steps": ["a", "b"]}, message="init")
        v2 = await wf.commit({"steps": ["a", "validate", "b"]}, message="add validation")
        print(wf.diff(v1.version, v2.version).summary())
        await wf.rollback(v1.version)
    """

    def __init__(
        self,
        name: str,
        store: Optional[VersionStore] = None,
        default_bump: str = "minor",
    ) -> None:
        self.name = name
        self._store = store or InMemoryVersionStore()
        self._default_bump = default_bump
        self._local_versions: Dict[str, WorkflowVersion] = {}

    async def commit(
        self,
        definition: Dict[str, Any],
        message: str = "",
        author: str = "system",
        bump: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> WorkflowVersion:
        """
        Create a new version from *definition*.

        Parameters
        ----------
        definition  The new workflow definition dict.
        message     Commit message.
        author      Who made the change.
        bump        ``"major"``, ``"minor"``, or ``"patch"`` (default: ``"minor"``).
        tags        Optional labels for this version.
        """
        latest = await self._store.latest(self.name)
        if latest is None:
            new_version = "1.0.0"
            parent_version = None
        else:
            new_version = _bump(latest.version, bump or self._default_bump)
            parent_version = latest.version

        wv = WorkflowVersion(
            workflow_name=self.name,
            version=new_version,
            definition=copy.deepcopy(definition),
            message=message,
            author=author,
            parent_version=parent_version,
            tags=list(tags) if tags else [],
        )
        await self._store.save(wv)
        self._local_versions[new_version] = wv
        return wv

    async def current(self) -> Optional[WorkflowVersion]:
        """Return the most recent version."""
        return await self._store.latest(self.name)

    async def get(self, version: str) -> Optional[WorkflowVersion]:
        return await self._store.get(self.name, version)

    async def history(self) -> List[WorkflowVersion]:
        return await self._store.list_versions(self.name)

    async def changelog(self) -> ChangeLog:
        return ChangeLog(await self.history())

    def diff(self, from_version: str, to_version: str) -> WorkflowDiff:
        """
        Compute a diff between two versions.

        Versions must already be committed and accessible in the store.
        For async stores use ``await wf.get(v)`` first.
        """
        fv = self._local_versions.get(from_version)
        tv = self._local_versions.get(to_version)
        if fv is None or tv is None:
            raise KeyError(
                "Version(s) not found locally. "
                "Use await wf.get() first for persisted versions."
            )
        return _diff_definitions(
            fv.definition, tv.definition, self.name, from_version, to_version
        )

    async def rollback(self, target_version: str) -> WorkflowVersion:
        """
        Create a new version that re-applies the definition from *target_version*.

        Does NOT delete intermediate versions — the history is always preserved.
        """
        target = await self._store.get(self.name, target_version)
        if target is None:
            raise ValueError(f"Version {target_version!r} not found for {self.name!r}")
        latest = await self._store.latest(self.name)
        new_version_str = _bump(latest.version if latest else "1.0.0", "patch")
        rollback_wv = WorkflowVersion(
            workflow_name=self.name,
            version=new_version_str,
            definition=copy.deepcopy(target.definition),
            message=f"Rollback to v{target_version}",
            author="system",
            parent_version=latest.version if latest else None,
            tags=["rollback"],
        )
        await self._store.save(rollback_wv)
        self._local_versions[new_version_str] = rollback_wv
        return rollback_wv

    async def tag(self, version: str, *tags: str) -> bool:
        """Add tags to an existing version."""
        wv = await self._store.get(self.name, version)
        if wv is None:
            return False
        wv.tags = list(set(wv.tags) | set(tags))
        await self._store.save(wv)
        return True

    def __repr__(self) -> str:
        return f"VersionedWorkflow({self.name!r})"


__all__ = [
    "ChangeLog",
    "InMemoryVersionStore",
    "SQLiteVersionStore",
    "VersionStore",
    "VersionedWorkflow",
    "WorkflowDiff",
    "WorkflowVersion",
]
