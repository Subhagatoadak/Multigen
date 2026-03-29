"""
Distributed memory coherence for Multigen.

Problems solved
---------------
- Memory sync protocol across workers (Worker A ≠ Worker B)
- Conflict resolution on concurrent memory writes
- Memory versioning / MVCC (rollback bad writes)
- Distributed working memory (shared across agents in a workflow)

Classes
-------
- ``VersionedEntry``         — memory entry with monotonic version + vector clock
- ``ConflictResolutionMode`` — last-write-wins | first-write-wins | merge | error
- ``MVCCMemoryStore``        — multi-version store with read-your-writes, rollback
- ``MemorySyncProtocol``     — bidirectional sync between two stores
- ``DistributedWorkingMemory`` — shared sliding-window working memory for a run
- ``MemoryCoordinator``      — high-level facade for multi-worker coherence

Usage::

    from multigen.memory_sync import MVCCMemoryStore, DistributedWorkingMemory

    store = MVCCMemoryStore(conflict_mode="last_write_wins")
    store.write("answer", "Paris", author="agent-a")
    store.write("answer", "Lyon",  author="agent-b")   # conflict → LWW wins

    wm = DistributedWorkingMemory(run_id="run-42", window=5)
    wm.push("agent-a", "I found 3 results")
    wm.push("agent-b", "Summarising now…")
    print(wm.context_text())
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterator, List, Optional, Tuple


# ── Vector clock helpers ──────────────────────────────────────────────────────

VectorClock = Dict[str, int]  # {worker_id: logical_time}


def _merge_clocks(a: VectorClock, b: VectorClock) -> VectorClock:
    keys = set(a) | set(b)
    return {k: max(a.get(k, 0), b.get(k, 0)) for k in keys}


def _clock_dominates(a: VectorClock, b: VectorClock) -> bool:
    """Return True if *a* is strictly newer than *b* on all dimensions."""
    if not b:
        return True
    return all(a.get(k, 0) >= v for k, v in b.items()) and any(
        a.get(k, 0) > v for k, v in b.items()
    )


# ── VersionedEntry ────────────────────────────────────────────────────────────

@dataclass
class VersionedEntry:
    """A single versioned memory slot."""
    key: str
    value: Any
    version: int                          # monotonic counter
    author: str                           # which worker wrote this
    clock: VectorClock = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    deleted: bool = False

    def is_newer_than(self, other: "VersionedEntry") -> bool:
        return self.version > other.version


# ── Conflict resolution ───────────────────────────────────────────────────────

class ConflictResolutionMode(Enum):
    LAST_WRITE_WINS = "last_write_wins"   # highest version wins
    FIRST_WRITE_WINS = "first_write_wins" # first writer keeps value
    MERGE = "merge"                       # concatenate strings / merge dicts
    ERROR = "error"                       # raise ConflictError


class ConflictError(Exception):
    """Raised when two writers conflict and mode=ERROR."""
    pass


# ── MVCCMemoryStore ───────────────────────────────────────────────────────────

class MVCCMemoryStore:
    """
    Multi-Version Concurrency Control memory store.

    Maintains a full version history per key.  Reads always return the
    current live version.  Rollback reinstates an earlier snapshot.

    Parameters
    ----------
    conflict_mode  How to resolve concurrent writes to the same key
    max_history    Maximum versions retained per key (oldest pruned)

    Usage::

        store = MVCCMemoryStore()
        store.write("answer", "Paris", author="agent-a")
        v1 = store.version("answer")
        store.write("answer", "Lyon", author="agent-b")
        store.rollback("answer", v1)
        print(store.read("answer"))  # → "Paris"
    """

    def __init__(
        self,
        conflict_mode: str = "last_write_wins",
        max_history: int = 20,
    ) -> None:
        self._mode = ConflictResolutionMode(conflict_mode)
        self._max_history = max_history
        # key → list of VersionedEntry (oldest first)
        self._history: Dict[str, List[VersionedEntry]] = {}
        self._lock = asyncio.Lock()
        self._global_version = 0

    # ── writes ──

    def write(
        self,
        key: str,
        value: Any,
        author: str = "unknown",
        clock: Optional[VectorClock] = None,
    ) -> VersionedEntry:
        self._global_version += 1
        existing = self._current(key)

        if existing and not existing.deleted:
            value = self._resolve(key, existing, value, author)

        entry = VersionedEntry(
            key=key,
            value=value,
            version=self._global_version,
            author=author,
            clock=dict(clock or {}),
        )
        self._history.setdefault(key, []).append(entry)
        self._prune(key)
        return entry

    def delete(self, key: str, author: str = "unknown") -> None:
        self._global_version += 1
        entry = VersionedEntry(
            key=key,
            value=None,
            version=self._global_version,
            author=author,
            deleted=True,
        )
        self._history.setdefault(key, []).append(entry)

    # ── reads ──

    def read(self, key: str, default: Any = None) -> Any:
        entry = self._current(key)
        if entry is None or entry.deleted:
            return default
        return entry.value

    def version(self, key: str) -> Optional[int]:
        entry = self._current(key)
        return entry.version if entry else None

    def history(self, key: str) -> List[VersionedEntry]:
        return list(self._history.get(key, []))

    def all_keys(self) -> List[str]:
        return [k for k, h in self._history.items() if h and not h[-1].deleted]

    def snapshot(self) -> Dict[str, Any]:
        """Return current live values as a plain dict."""
        return {k: self.read(k) for k in self.all_keys()}

    # ── rollback ──

    def rollback(self, key: str, to_version: int) -> Optional[Any]:
        """Reinstate the value at *to_version*. Returns the reinstated value."""
        entries = self._history.get(key, [])
        target = next((e for e in entries if e.version == to_version), None)
        if target is None:
            raise ValueError(f"Version {to_version} not found for key {key!r}")
        return self.write(key, target.value, author="rollback")

    # ── internal ──

    def _current(self, key: str) -> Optional[VersionedEntry]:
        entries = self._history.get(key, [])
        return entries[-1] if entries else None

    def _resolve(
        self, key: str, existing: VersionedEntry, new_value: Any, author: str
    ) -> Any:
        if self._mode == ConflictResolutionMode.LAST_WRITE_WINS:
            return new_value
        if self._mode == ConflictResolutionMode.FIRST_WRITE_WINS:
            return existing.value
        if self._mode == ConflictResolutionMode.ERROR:
            raise ConflictError(
                f"Conflict on key {key!r}: authors {existing.author!r} vs {author!r}"
            )
        # MERGE
        if isinstance(existing.value, dict) and isinstance(new_value, dict):
            return {**existing.value, **new_value}
        if isinstance(existing.value, str) and isinstance(new_value, str):
            return existing.value + "\n" + new_value
        if isinstance(existing.value, list):
            return existing.value + (new_value if isinstance(new_value, list) else [new_value])
        return new_value  # fallback to LWW

    def _prune(self, key: str) -> None:
        history = self._history.get(key, [])
        if len(history) > self._max_history:
            self._history[key] = history[-self._max_history:]


# ── MemorySyncProtocol ────────────────────────────────────────────────────────

@dataclass
class SyncReport:
    """Result of a bidirectional sync operation."""
    keys_synced: int
    conflicts_resolved: int
    local_newer: int
    remote_newer: int


class MemorySyncProtocol:
    """
    Bidirectional sync between two ``MVCCMemoryStore`` instances.

    The merge strategy follows vector-clock dominance — if one side's
    entry clearly dominates the other's, the dominant value wins.
    When clocks are concurrent, the configured *conflict_mode* applies.

    Usage::

        worker_a = MVCCMemoryStore()
        worker_b = MVCCMemoryStore()
        # … both write independently …
        sync = MemorySyncProtocol()
        report = sync.sync(worker_a, worker_b)
    """

    def sync(
        self,
        local: MVCCMemoryStore,
        remote: MVCCMemoryStore,
    ) -> SyncReport:
        all_keys = set(local.all_keys()) | set(remote.all_keys())
        synced = conflicts = local_newer = remote_newer = 0

        for key in all_keys:
            le = local._current(key)
            re = remote._current(key)

            if le is None and re is not None:
                local.write(key, re.value, author=re.author, clock=re.clock)
                remote_newer += 1
            elif re is None and le is not None:
                remote.write(key, le.value, author=le.author, clock=le.clock)
                local_newer += 1
            elif le and re:
                if le.version == re.version:
                    pass  # identical
                elif _clock_dominates(le.clock, re.clock):
                    remote.write(key, le.value, author=le.author, clock=le.clock)
                    local_newer += 1
                elif _clock_dominates(re.clock, le.clock):
                    local.write(key, re.value, author=re.author, clock=re.clock)
                    remote_newer += 1
                else:
                    # Concurrent writes — use version as tiebreaker
                    if le.version >= re.version:
                        remote.write(key, le.value, author=le.author)
                        conflicts += 1
                    else:
                        local.write(key, re.value, author=re.author)
                        conflicts += 1
            synced += 1

        return SyncReport(
            keys_synced=synced,
            conflicts_resolved=conflicts,
            local_newer=local_newer,
            remote_newer=remote_newer,
        )


# ── DistributedWorkingMemory ──────────────────────────────────────────────────

@dataclass
class WorkingMemoryEntry:
    """A single entry in the distributed working memory window."""
    run_id: str
    agent: str
    content: str
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return f"[{self.agent}] {self.content}"


class DistributedWorkingMemory:
    """
    Shared sliding-window working memory for a single workflow run.

    Unlike the per-agent ``WorkingMemory``, this instance is shared
    across all agents in the run.  Each agent ``push()``es observations
    and any agent can read the shared ``context_text()`` window.

    Parameters
    ----------
    run_id  Unique identifier for the workflow run
    window  Maximum number of entries kept (oldest evicted)

    Usage::

        wm = DistributedWorkingMemory(run_id="run-42", window=8)
        wm.push("agent-a", "Found 3 search results")
        wm.push("agent-b", "Analysing top result")
        print(wm.context_text())
    """

    def __init__(self, run_id: str, window: int = 10) -> None:
        self.run_id = run_id
        self.window = window
        self._entries: List[WorkingMemoryEntry] = []

    def push(self, agent: str, content: str) -> None:
        self._entries.append(
            WorkingMemoryEntry(run_id=self.run_id, agent=agent, content=content)
        )
        if len(self._entries) > self.window:
            self._entries.pop(0)

    def entries(self) -> List[WorkingMemoryEntry]:
        return list(self._entries)

    def context_text(self, separator: str = "\n") -> str:
        return separator.join(str(e) for e in self._entries)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


# ── MemoryCoordinator ─────────────────────────────────────────────────────────

class MemoryCoordinator:
    """
    Coordinates shared and per-worker memory stores across a multi-worker setup.

    Maintains:
    - A set of named ``MVCCMemoryStore`` instances (one per worker / agent)
    - A ``MemorySyncProtocol`` for pairwise sync
    - A registry of ``DistributedWorkingMemory`` instances per run_id

    Usage::

        coord = MemoryCoordinator()
        coord.register_worker("worker-a")
        coord.register_worker("worker-b")

        coord.write("worker-a", "result", "42")
        coord.write("worker-b", "result", "43")
        report = coord.sync("worker-a", "worker-b")

        wm = coord.working_memory("run-1")
        wm.push("agent-a", "Step 1 done")
    """

    def __init__(self, conflict_mode: str = "last_write_wins") -> None:
        self._conflict_mode = conflict_mode
        self._stores: Dict[str, MVCCMemoryStore] = {}
        self._working: Dict[str, DistributedWorkingMemory] = {}
        self._sync = MemorySyncProtocol()

    def register_worker(self, worker_id: str) -> MVCCMemoryStore:
        store = MVCCMemoryStore(conflict_mode=self._conflict_mode)
        self._stores[worker_id] = store
        return store

    def store(self, worker_id: str) -> MVCCMemoryStore:
        if worker_id not in self._stores:
            return self.register_worker(worker_id)
        return self._stores[worker_id]

    def write(self, worker_id: str, key: str, value: Any, **kwargs: Any) -> None:
        self.store(worker_id).write(key, value, author=worker_id, **kwargs)

    def read(self, worker_id: str, key: str, default: Any = None) -> Any:
        return self.store(worker_id).read(key, default)

    def sync(self, worker_a: str, worker_b: str) -> SyncReport:
        return self._sync.sync(self.store(worker_a), self.store(worker_b))

    def sync_all(self) -> Dict[Tuple[str, str], SyncReport]:
        """Full-mesh sync across all registered workers."""
        workers = list(self._stores.keys())
        reports: Dict[Tuple[str, str], SyncReport] = {}
        for i in range(len(workers)):
            for j in range(i + 1, len(workers)):
                pair = (workers[i], workers[j])
                reports[pair] = self.sync(*pair)
        return reports

    def working_memory(
        self, run_id: str, window: int = 10
    ) -> DistributedWorkingMemory:
        if run_id not in self._working:
            self._working[run_id] = DistributedWorkingMemory(run_id, window)
        return self._working[run_id]


__all__ = [
    "VectorClock",
    "VersionedEntry",
    "ConflictResolutionMode",
    "ConflictError",
    "MVCCMemoryStore",
    "SyncReport",
    "MemorySyncProtocol",
    "WorkingMemoryEntry",
    "DistributedWorkingMemory",
    "MemoryCoordinator",
]
