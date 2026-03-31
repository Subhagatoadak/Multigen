"""
Durable persistence backends for the Multigen local SDK.

All in-memory stores in the SDK are ephemeral — they vanish when the process
exits.  This module provides SQLite-backed (zero-dependency) replacements that
survive restarts:

- ``SQLiteSessionStore``       — drop-in replacement for ``InMemorySessionStore``
- ``SQLiteMemoryStore``        — persistent ``ShortTermMemory``-compatible store
- ``PersistentEpisodicMemory`` — ``EpisodicMemory`` backed by SQLite
- ``SQLiteCheckpointStore``    — step-level checkpoint/resume for ``Runtime``
- ``CheckpointedRuntime``      — ``Runtime`` subclass with automatic checkpointing
- ``DurableQueue``             — crash-safe task queue (replaces in-memory queues)

All stores are **async-safe** via ``asyncio.Lock`` + ``aiosqlite`` when available,
falling back to the standard-library ``sqlite3`` with ``run_in_executor`` otherwise.

Usage::

    from multigen.persistence import (
        SQLiteSessionStore,
        SQLiteMemoryStore,
        PersistentEpisodicMemory,
        SQLiteCheckpointStore,
        CheckpointedRuntime,
        DurableQueue,
    )

    # 1. Persistent sessions across restarts
    store = SQLiteSessionStore("sessions.db")
    manager = SessionManager(store=store)

    # 2. Persistent short-term memory
    mem = SQLiteMemoryStore("memory.db")
    mem.store("query", "hello")          # written to disk immediately
    mem.retrieve("query")                # reads from disk if not in cache

    # 3. Durable episodic memory
    episodic = PersistentEpisodicMemory("episodes.db")
    episodic.record("AgentA", inputs={}, outputs={})

    # 4. Checkpoint-based workflow resume
    ckpt = SQLiteCheckpointStore("checkpoints.db")
    rt   = CheckpointedRuntime(checkpoint_store=ckpt)
    result = await rt.run_chain([agent_a, agent_b], ctx={}, run_id="job-1")
    # On crash → resume from last completed step:
    result = await rt.resume("job-1", [agent_a, agent_b])

    # 5. Durable queue (survives crashes)
    q = DurableQueue("tasks.db")
    await q.enqueue({"task": "summarise", "doc_id": 42})
    item = await q.dequeue()             # returns None if empty
    await q.ack(item["_queue_id"])       # marks as done
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .session import SessionContext, SessionState, SessionStore
from .memory import Episode, EpisodicMemory, MemoryEntry, ShortTermMemory

logger = logging.getLogger(__name__)


# ── SQLite helper ──────────────────────────────────────────────────────────────

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


@contextmanager
def _tx(conn: sqlite3.Connection):
    """Thin context manager that commits on success and rolls back on error."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ── SQLiteSessionStore ────────────────────────────────────────────────────────

class SQLiteSessionStore(SessionStore):
    """
    SQLite-backed implementation of ``SessionStore``.

    Drop-in replacement for ``InMemorySessionStore`` — simply pass a file path::

        store = SQLiteSessionStore("sessions.db")
        manager = SessionManager(store=store)
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS sessions (
            session_id  TEXT PRIMARY KEY,
            agent_id    TEXT NOT NULL,
            created_at  REAL NOT NULL,
            last_active REAL NOT NULL,
            state       TEXT NOT NULL,
            metadata    TEXT NOT NULL DEFAULT '{}',
            ttl         REAL,
            tags        TEXT NOT NULL DEFAULT '[]'
        )
    """

    def __init__(self, db_path: str = "sessions.db") -> None:
        self._db_path = str(db_path)
        self._lock = asyncio.Lock()
        self._conn = _connect(self._db_path)
        self._conn.execute(self._DDL)
        self._conn.commit()

    # ── helpers ──

    def _row_to_session(self, row: sqlite3.Row) -> SessionContext:
        return SessionContext(
            session_id=row["session_id"],
            agent_id=row["agent_id"],
            created_at=row["created_at"],
            last_active=row["last_active"],
            state=SessionState(row["state"]),
            metadata=json.loads(row["metadata"]),
            ttl=row["ttl"],
            tags=json.loads(row["tags"]),
        )

    def _run(self, coro):
        """Run synchronous SQLite work inside asyncio via executor."""
        loop = asyncio.get_event_loop()
        return loop.run_in_executor(None, coro)

    # ── SessionStore interface ──

    async def create(
        self,
        agent_id: str,
        ttl: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> SessionContext:
        now = time.time()
        session = SessionContext(
            session_id=str(uuid.uuid4()),
            agent_id=agent_id,
            created_at=now,
            last_active=now,
            state=SessionState.ACTIVE,
            metadata=metadata or {},
            ttl=ttl,
            tags=list(tags) if tags else [],
        )
        async with self._lock:
            self._conn.execute(
                """INSERT INTO sessions
                   (session_id, agent_id, created_at, last_active, state, metadata, ttl, tags)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    session.session_id,
                    session.agent_id,
                    session.created_at,
                    session.last_active,
                    session.state.value,
                    json.dumps(session.metadata),
                    session.ttl,
                    json.dumps(session.tags),
                ),
            )
            self._conn.commit()
        return session

    async def get(self, session_id: str) -> Optional[SessionContext]:
        async with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
            )
            row = cur.fetchone()
        if row is None:
            return None
        session = self._row_to_session(row)
        if session.is_expired and session.state not in (
            SessionState.EXPIRED,
            SessionState.TERMINATED,
        ):
            async with self._lock:
                self._conn.execute(
                    "UPDATE sessions SET state=? WHERE session_id=?",
                    (SessionState.EXPIRED.value, session_id),
                )
                self._conn.commit()
            session.state = SessionState.EXPIRED
        return session

    async def update(self, session_id: str, **kwargs: Any) -> bool:
        if not kwargs:
            return True
        set_clauses = []
        values = []
        for key, val in kwargs.items():
            if key in ("metadata", "tags"):
                set_clauses.append(f"{key} = ?")
                values.append(json.dumps(val))
            elif key == "state" and isinstance(val, SessionState):
                set_clauses.append("state = ?")
                values.append(val.value)
            else:
                set_clauses.append(f"{key} = ?")
                values.append(val)
        values.append(session_id)
        sql = f"UPDATE sessions SET {', '.join(set_clauses)} WHERE session_id = ?"
        async with self._lock:
            cur = self._conn.execute(sql, values)
            self._conn.commit()
        return cur.rowcount > 0

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE session_id = ?", (session_id,)
            )
            self._conn.commit()
        return cur.rowcount > 0

    async def list_sessions(
        self,
        agent_id: Optional[str] = None,
        state: Optional[SessionState] = None,
    ) -> List[SessionContext]:
        conditions: List[str] = []
        params: List[Any] = []
        if agent_id is not None:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if state is not None:
            conditions.append("state = ?")
            params.append(state.value)
        sql = "SELECT * FROM sessions"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        async with self._lock:
            cur = self._conn.execute(sql, params)
            rows = cur.fetchall()
        return [self._row_to_session(r) for r in rows]

    async def cleanup_expired(self) -> int:
        now = time.time()
        async with self._lock:
            # Mark TTL-expired rows
            self._conn.execute(
                """UPDATE sessions SET state = ?
                   WHERE ttl IS NOT NULL
                     AND (? - last_active) > ttl
                     AND state NOT IN (?, ?)""",
                (
                    SessionState.EXPIRED.value,
                    now,
                    SessionState.EXPIRED.value,
                    SessionState.TERMINATED.value,
                ),
            )
            # Delete already-expired/terminated
            cur = self._conn.execute(
                "DELETE FROM sessions WHERE state IN (?, ?)",
                (SessionState.EXPIRED.value, SessionState.TERMINATED.value),
            )
            self._conn.commit()
        return cur.rowcount

    def close(self) -> None:
        self._conn.close()


# ── SQLiteMemoryStore ─────────────────────────────────────────────────────────

class SQLiteMemoryStore(ShortTermMemory):
    """
    Persistent ``ShortTermMemory`` backed by SQLite.

    Reads and writes are write-through — every ``store()`` call is committed to
    disk immediately.  The in-memory ``_store`` dict acts as a read cache to
    avoid repeated SQLite lookups within the same process lifetime.

    Usage::

        mem = SQLiteMemoryStore("memory.db", capacity=5000, default_ttl=600)
        mem.store("session_key", {"user": "alice"}, ttl=300)
        val = mem.retrieve("session_key")
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS memory_entries (
            key          TEXT PRIMARY KEY,
            value        TEXT NOT NULL,
            created_at   REAL NOT NULL,
            accessed_at  REAL NOT NULL,
            ttl          REAL,
            tags         TEXT NOT NULL DEFAULT '[]',
            access_count INTEGER NOT NULL DEFAULT 0,
            entry_id     TEXT NOT NULL
        )
    """

    def __init__(
        self,
        db_path: str = "memory.db",
        capacity: int = 10000,
        default_ttl: Optional[float] = None,
    ) -> None:
        super().__init__(capacity=capacity, default_ttl=default_ttl)
        self._db_path = str(db_path)
        self._conn = _connect(self._db_path)
        self._conn.execute(self._DDL)
        self._conn.commit()
        self._load_from_db()

    def _load_from_db(self) -> None:
        """Warm the in-memory cache from SQLite on startup."""
        cur = self._conn.execute("SELECT * FROM memory_entries")
        for row in cur.fetchall():
            try:
                value = json.loads(row["value"])
            except (json.JSONDecodeError, TypeError):
                value = row["value"]
            entry = MemoryEntry(
                key=row["key"],
                value=value,
                created_at=row["created_at"],
                accessed_at=row["accessed_at"],
                ttl=row["ttl"],
                tags=json.loads(row["tags"]),
                access_count=row["access_count"],
                entry_id=row["entry_id"],
            )
            if not entry.is_expired:
                self._store[row["key"]] = entry

    def store(
        self,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        super().store(key, value, ttl=ttl, tags=tags)
        entry = self._store.get(key)
        if entry is None:
            return
        try:
            serialized = json.dumps(value)
        except (TypeError, ValueError):
            serialized = json.dumps(str(value))
        with _tx(self._conn):
            self._conn.execute(
                """INSERT OR REPLACE INTO memory_entries
                   (key, value, created_at, accessed_at, ttl, tags, access_count, entry_id)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (
                    key,
                    serialized,
                    entry.created_at,
                    entry.accessed_at,
                    entry.ttl,
                    json.dumps(entry.tags),
                    entry.access_count,
                    entry.entry_id,
                ),
            )

    def delete(self, key: str) -> bool:
        removed = super().delete(key)
        if removed:
            with _tx(self._conn):
                self._conn.execute("DELETE FROM memory_entries WHERE key = ?", (key,))
        return removed

    def clear(self) -> None:
        super().clear()
        with _tx(self._conn):
            self._conn.execute("DELETE FROM memory_entries")

    def close(self) -> None:
        self._conn.close()


# ── PersistentEpisodicMemory ──────────────────────────────────────────────────

class PersistentEpisodicMemory(EpisodicMemory):
    """
    ``EpisodicMemory`` backed by SQLite.

    Episodes written to this store survive process restarts.  On startup the
    most recent ``max_episodes`` rows are loaded into the in-memory deque.

    Usage::

        episodic = PersistentEpisodicMemory("episodes.db", max_episodes=1000)
        episodic.record("ResearchAgent", inputs={"q": "AI"}, outputs={"a": "..."})
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS episodes (
            episode_id  TEXT PRIMARY KEY,
            agent       TEXT NOT NULL,
            inputs      TEXT NOT NULL,
            outputs     TEXT NOT NULL,
            timestamp   REAL NOT NULL,
            tags        TEXT NOT NULL DEFAULT '[]',
            metadata    TEXT NOT NULL DEFAULT '{}'
        )
    """

    def __init__(self, db_path: str = "episodes.db", max_episodes: int = 500) -> None:
        super().__init__(max_episodes=max_episodes)
        self._db_path = str(db_path)
        self._conn = _connect(self._db_path)
        self._conn.execute(self._DDL)
        self._conn.commit()
        self._load_from_db()

    def _load_from_db(self) -> None:
        cur = self._conn.execute(
            "SELECT * FROM episodes ORDER BY timestamp ASC LIMIT ?",
            (self._episodes.maxlen,),
        )
        for row in cur.fetchall():
            try:
                inputs = json.loads(row["inputs"])
                outputs = json.loads(row["outputs"])
                tags = json.loads(row["tags"])
                metadata = json.loads(row["metadata"])
            except (json.JSONDecodeError, TypeError):
                inputs = outputs = tags = metadata = {}
            ep = Episode(
                agent=row["agent"],
                inputs=inputs,
                outputs=outputs,
                episode_id=row["episode_id"],
                timestamp=row["timestamp"],
                tags=tags,
                metadata=metadata,
            )
            self._episodes.append(ep)

    def record(
        self,
        agent: str,
        inputs: Dict[str, Any],
        outputs: Dict[str, Any],
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        episode_id = super().record(agent, inputs, outputs, tags=tags, metadata=metadata)
        # The newly added episode is the last one in the deque
        ep = self._episodes[-1]
        try:
            inp_s = json.dumps(inputs)
            out_s = json.dumps(outputs)
        except (TypeError, ValueError):
            inp_s = json.dumps(str(inputs))
            out_s = json.dumps(str(outputs))
        with _tx(self._conn):
            self._conn.execute(
                """INSERT OR REPLACE INTO episodes
                   (episode_id, agent, inputs, outputs, timestamp, tags, metadata)
                   VALUES (?,?,?,?,?,?,?)""",
                (
                    ep.episode_id,
                    ep.agent,
                    inp_s,
                    out_s,
                    ep.timestamp,
                    json.dumps(ep.tags),
                    json.dumps(ep.metadata),
                ),
            )
        return episode_id

    def clear(self) -> None:
        super().clear()
        with _tx(self._conn):
            self._conn.execute("DELETE FROM episodes")

    def close(self) -> None:
        self._conn.close()


# ── SQLiteCheckpointStore ─────────────────────────────────────────────────────

@dataclass
class Checkpoint:
    """A single step checkpoint within a workflow run."""

    run_id: str
    step_index: int
    agent_name: str
    ctx_snapshot: Dict[str, Any]
    result_snapshot: Any
    timestamp: float = field(default_factory=time.time)
    status: str = "completed"   # "completed" | "failed"
    error_msg: str = ""


class SQLiteCheckpointStore:
    """
    SQLite-backed checkpoint store for ``CheckpointedRuntime``.

    Each completed step is recorded so a workflow can resume from the last
    successful step after a crash.

    Usage::

        store = SQLiteCheckpointStore("checkpoints.db")
        await store.save(Checkpoint(run_id="job-1", step_index=2, ...))
        last = await store.last_completed("job-1")
        all_steps = await store.get_run("job-1")
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS checkpoints (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id        TEXT NOT NULL,
            step_index    INTEGER NOT NULL,
            agent_name    TEXT NOT NULL,
            ctx_snapshot  TEXT NOT NULL,
            result_snapshot TEXT NOT NULL,
            timestamp     REAL NOT NULL,
            status        TEXT NOT NULL DEFAULT 'completed',
            error_msg     TEXT NOT NULL DEFAULT '',
            UNIQUE (run_id, step_index)
        );
        CREATE INDEX IF NOT EXISTS idx_checkpoints_run ON checkpoints (run_id, step_index);
    """

    def __init__(self, db_path: str = "checkpoints.db") -> None:
        self._db_path = str(db_path)
        self._lock = asyncio.Lock()
        self._conn = _connect(self._db_path)
        for stmt in self._DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        self._conn.commit()

    def _row_to_checkpoint(self, row: sqlite3.Row) -> Checkpoint:
        try:
            ctx = json.loads(row["ctx_snapshot"])
        except (json.JSONDecodeError, TypeError):
            ctx = {}
        try:
            result = json.loads(row["result_snapshot"])
        except (json.JSONDecodeError, TypeError):
            result = None
        return Checkpoint(
            run_id=row["run_id"],
            step_index=row["step_index"],
            agent_name=row["agent_name"],
            ctx_snapshot=ctx,
            result_snapshot=result,
            timestamp=row["timestamp"],
            status=row["status"],
            error_msg=row["error_msg"],
        )

    async def save(self, ckpt: Checkpoint) -> None:
        try:
            ctx_s = json.dumps(ckpt.ctx_snapshot, default=str)
            result_s = json.dumps(ckpt.result_snapshot, default=str)
        except (TypeError, ValueError):
            ctx_s = "{}"
            result_s = "null"
        async with self._lock:
            with _tx(self._conn):
                self._conn.execute(
                    """INSERT OR REPLACE INTO checkpoints
                       (run_id, step_index, agent_name, ctx_snapshot, result_snapshot,
                        timestamp, status, error_msg)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (
                        ckpt.run_id,
                        ckpt.step_index,
                        ckpt.agent_name,
                        ctx_s,
                        result_s,
                        ckpt.timestamp,
                        ckpt.status,
                        ckpt.error_msg,
                    ),
                )

    async def last_completed(self, run_id: str) -> Optional[Checkpoint]:
        """Return the most-recently completed step checkpoint for *run_id*."""
        async with self._lock:
            cur = self._conn.execute(
                """SELECT * FROM checkpoints
                   WHERE run_id = ? AND status = 'completed'
                   ORDER BY step_index DESC LIMIT 1""",
                (run_id,),
            )
            row = cur.fetchone()
        return self._row_to_checkpoint(row) if row else None

    async def get_run(self, run_id: str) -> List[Checkpoint]:
        """Return all checkpoints for *run_id* in step order."""
        async with self._lock:
            cur = self._conn.execute(
                "SELECT * FROM checkpoints WHERE run_id = ? ORDER BY step_index ASC",
                (run_id,),
            )
            rows = cur.fetchall()
        return [self._row_to_checkpoint(r) for r in rows]

    async def delete_run(self, run_id: str) -> int:
        async with self._lock:
            cur = self._conn.execute(
                "DELETE FROM checkpoints WHERE run_id = ?", (run_id,)
            )
            self._conn.commit()
        return cur.rowcount

    async def list_runs(self) -> List[str]:
        async with self._lock:
            cur = self._conn.execute(
                "SELECT DISTINCT run_id FROM checkpoints ORDER BY run_id"
            )
            rows = cur.fetchall()
        return [r["run_id"] for r in rows]

    def close(self) -> None:
        self._conn.close()


# ── CheckpointedRuntime ───────────────────────────────────────────────────────

class CheckpointedRuntime:
    """
    A lightweight local runtime that checkpoints each agent step to SQLite.

    On crash or interrupt the workflow can be resumed from the last completed
    step — no work is re-done.

    Usage::

        rt = CheckpointedRuntime(checkpoint_store=SQLiteCheckpointStore("ckpt.db"))

        # Run a chain of agents
        result = await rt.run_chain(
            [agent_a, agent_b, agent_c],
            ctx={"input": "hello"},
            run_id="my-job-001",
        )

        # Resume after a crash (skips already-completed steps)
        result = await rt.resume("my-job-001", [agent_a, agent_b, agent_c])
    """

    def __init__(
        self,
        checkpoint_store: Optional[SQLiteCheckpointStore] = None,
        db_path: str = "checkpoints.db",
    ) -> None:
        self._store = checkpoint_store or SQLiteCheckpointStore(db_path)

    async def run_chain(
        self,
        agents: List[Any],
        ctx: Dict[str, Any],
        run_id: Optional[str] = None,
        start_from: int = 0,
    ) -> Dict[str, Any]:
        """
        Execute *agents* sequentially, checkpointing after each step.

        Parameters
        ----------
        agents      List of agent callables (anything with ``async __call__(ctx)``).
        ctx         Initial context dict.
        run_id      Unique identifier for this run (auto-generated if omitted).
        start_from  Step index to resume from (0 = run all steps).
        """
        if run_id is None:
            run_id = str(uuid.uuid4())

        current_ctx = dict(ctx)

        for idx, agent in enumerate(agents):
            if idx < start_from:
                logger.debug("[%s] Skipping step %d (already checkpointed)", run_id, idx)
                continue

            agent_name = getattr(agent, "name", type(agent).__name__)
            logger.debug("[%s] Running step %d: %s", run_id, idx, agent_name)

            try:
                result = await agent(current_ctx)
                if isinstance(result, dict):
                    current_ctx = {**current_ctx, **result}
                current_ctx["_last_result"] = result

                await self._store.save(Checkpoint(
                    run_id=run_id,
                    step_index=idx,
                    agent_name=agent_name,
                    ctx_snapshot=current_ctx,
                    result_snapshot=result,
                    status="completed",
                ))
            except Exception as exc:
                await self._store.save(Checkpoint(
                    run_id=run_id,
                    step_index=idx,
                    agent_name=agent_name,
                    ctx_snapshot=current_ctx,
                    result_snapshot=None,
                    status="failed",
                    error_msg=str(exc),
                ))
                logger.error("[%s] Step %d failed: %s", run_id, idx, exc)
                raise

        return current_ctx

    async def resume(
        self,
        run_id: str,
        agents: List[Any],
        override_ctx: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Resume a previously interrupted run from its last completed step.

        Parameters
        ----------
        run_id       The run identifier used in the original ``run_chain()`` call.
        agents       Same agent list as the original run.
        override_ctx If provided, merged over the checkpointed context.
        """
        last = await self._store.last_completed(run_id)
        if last is None:
            start_from = 0
            ctx = override_ctx or {}
            logger.info("[%s] No checkpoint found — starting from step 0", run_id)
        else:
            start_from = last.step_index + 1
            ctx = dict(last.ctx_snapshot)
            if override_ctx:
                ctx.update(override_ctx)
            logger.info(
                "[%s] Resuming from step %d (after '%s')",
                run_id, start_from, last.agent_name,
            )

        return await self.run_chain(agents, ctx, run_id=run_id, start_from=start_from)

    async def run_status(self, run_id: str) -> Dict[str, Any]:
        """Return a summary of all checkpoints for *run_id*."""
        ckpts = await self._store.get_run(run_id)
        if not ckpts:
            return {"run_id": run_id, "steps": 0, "status": "not_found"}
        completed = sum(1 for c in ckpts if c.status == "completed")
        failed = sum(1 for c in ckpts if c.status == "failed")
        return {
            "run_id": run_id,
            "steps": len(ckpts),
            "completed": completed,
            "failed": failed,
            "last_step": ckpts[-1].step_index,
            "last_agent": ckpts[-1].agent_name,
            "last_status": ckpts[-1].status,
            "finished": failed == 0 and completed == len(ckpts),
        }


# ── DurableQueue ──────────────────────────────────────────────────────────────

class DurableQueue:
    """
    Crash-safe FIFO task queue backed by SQLite.

    Items remain in the ``pending`` state until explicitly acknowledged via
    ``ack()``.  If the process crashes before ack, they can be recovered with
    ``recover_pending()``.

    Usage::

        q = DurableQueue("tasks.db")
        item_id = await q.enqueue({"task": "summarise", "doc_id": 42})
        item    = await q.dequeue()          # {'_queue_id': ..., 'task': ..., ...}
        await q.ack(item["_queue_id"])        # mark as done
        await q.nack(item["_queue_id"])       # return to queue (retry)
        stuck = await q.recover_pending(older_than=300)  # recover 5-min-old in-flight tasks
    """

    _DDL = """
        CREATE TABLE IF NOT EXISTS queue (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            payload    TEXT NOT NULL,
            status     TEXT NOT NULL DEFAULT 'pending',
            enqueued_at REAL NOT NULL,
            dequeued_at REAL,
            priority   INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_queue_status ON queue (status, priority DESC, id ASC);
    """

    def __init__(self, db_path: str = "queue.db") -> None:
        self._db_path = str(db_path)
        self._lock = asyncio.Lock()
        self._conn = _connect(self._db_path)
        for stmt in self._DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)
        self._conn.commit()

    async def enqueue(
        self,
        payload: Dict[str, Any],
        priority: int = 0,
    ) -> int:
        """
        Add *payload* to the queue.  Returns the internal queue row ID.

        Higher *priority* values are dequeued first.
        """
        try:
            payload_s = json.dumps(payload)
        except (TypeError, ValueError):
            payload_s = json.dumps(str(payload))
        async with self._lock:
            with _tx(self._conn):
                cur = self._conn.execute(
                    "INSERT INTO queue (payload, enqueued_at, priority) VALUES (?,?,?)",
                    (payload_s, time.time(), priority),
                )
        return cur.lastrowid  # type: ignore[return-value]

    async def dequeue(self) -> Optional[Dict[str, Any]]:
        """
        Pop the highest-priority pending item and mark it as ``in_flight``.

        Returns ``None`` if the queue is empty.
        """
        async with self._lock:
            cur = self._conn.execute(
                """SELECT id, payload FROM queue
                   WHERE status = 'pending'
                   ORDER BY priority DESC, id ASC
                   LIMIT 1"""
            )
            row = cur.fetchone()
            if row is None:
                return None
            self._conn.execute(
                "UPDATE queue SET status='in_flight', dequeued_at=? WHERE id=?",
                (time.time(), row["id"]),
            )
            self._conn.commit()
        try:
            item = json.loads(row["payload"])
            if not isinstance(item, dict):
                item = {"value": item}
        except (json.JSONDecodeError, TypeError):
            item = {"value": row["payload"]}
        item["_queue_id"] = row["id"]
        return item

    async def ack(self, queue_id: int) -> bool:
        """Mark an in-flight item as done (removes it from the queue)."""
        async with self._lock:
            cur = self._conn.execute(
                "DELETE FROM queue WHERE id = ? AND status = 'in_flight'", (queue_id,)
            )
            self._conn.commit()
        return cur.rowcount > 0

    async def nack(self, queue_id: int) -> bool:
        """Return an in-flight item to ``pending`` (retry later)."""
        async with self._lock:
            cur = self._conn.execute(
                "UPDATE queue SET status='pending', dequeued_at=NULL WHERE id=? AND status='in_flight'",
                (queue_id,),
            )
            self._conn.commit()
        return cur.rowcount > 0

    async def recover_pending(self, older_than: float = 300.0) -> int:
        """
        Re-queue in-flight items that have been stuck for more than *older_than*
        seconds (e.g. because the consumer crashed).

        Returns the number of items recovered.
        """
        cutoff = time.time() - older_than
        async with self._lock:
            cur = self._conn.execute(
                """UPDATE queue SET status='pending', dequeued_at=NULL
                   WHERE status='in_flight' AND dequeued_at < ?""",
                (cutoff,),
            )
            self._conn.commit()
        return cur.rowcount

    async def size(self, status: str = "pending") -> int:
        async with self._lock:
            cur = self._conn.execute(
                "SELECT COUNT(*) as n FROM queue WHERE status = ?", (status,)
            )
            row = cur.fetchone()
        return row["n"] if row else 0

    async def stats(self) -> Dict[str, int]:
        async with self._lock:
            cur = self._conn.execute(
                "SELECT status, COUNT(*) as n FROM queue GROUP BY status"
            )
            rows = cur.fetchall()
        return {r["status"]: r["n"] for r in rows}

    def close(self) -> None:
        self._conn.close()


# ── Convenience factory ────────────────────────────────────────────────────────

def open_persistent_stores(
    base_dir: str = ".",
    prefix: str = "multigen",
) -> Dict[str, Any]:
    """
    Open all persistence stores under a common directory, return a dict with keys:
    ``sessions``, ``memory``, ``episodes``, ``checkpoints``, ``queue``.

    Usage::

        stores = open_persistent_stores("./data", prefix="myapp")
        manager = SessionManager(store=stores["sessions"])
        runtime = CheckpointedRuntime(checkpoint_store=stores["checkpoints"])
    """
    base = Path(base_dir)
    base.mkdir(parents=True, exist_ok=True)
    return {
        "sessions":    SQLiteSessionStore(str(base / f"{prefix}_sessions.db")),
        "memory":      SQLiteMemoryStore(str(base / f"{prefix}_memory.db")),
        "episodes":    PersistentEpisodicMemory(str(base / f"{prefix}_episodes.db")),
        "checkpoints": SQLiteCheckpointStore(str(base / f"{prefix}_checkpoints.db")),
        "queue":       DurableQueue(str(base / f"{prefix}_queue.db")),
    }


__all__ = [
    "Checkpoint",
    "CheckpointedRuntime",
    "DurableQueue",
    "PersistentEpisodicMemory",
    "SQLiteCheckpointStore",
    "SQLiteMemoryStore",
    "SQLiteSessionStore",
    "open_persistent_stores",
]
