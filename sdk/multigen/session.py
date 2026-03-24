from __future__ import annotations

import asyncio
import time
import uuid
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional

__all__ = [
    "SessionState",
    "SessionContext",
    "SessionEvent",
    "SessionStore",
    "InMemorySessionStore",
    "SessionManager",
    "SessionMiddleware",
]


class SessionState(str, Enum):
    ACTIVE = "active"
    IDLE = "idle"
    EXPIRED = "expired"
    TERMINATED = "terminated"


@dataclass
class SessionContext:
    session_id: str
    agent_id: str
    created_at: float
    last_active: float
    state: SessionState
    metadata: Dict[str, Any] = field(default_factory=dict)
    ttl: Optional[float] = None
    tags: List[str] = field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        if self.state == SessionState.TERMINATED:
            return False
        if self.ttl is not None and (time.time() - self.last_active) > self.ttl:
            return True
        return self.state == SessionState.EXPIRED

    @property
    def is_active(self) -> bool:
        return self.state == SessionState.ACTIVE and not self.is_expired

    @property
    def age(self) -> float:
        """Seconds elapsed since session was created."""
        return time.time() - self.created_at

    @property
    def idle_time(self) -> float:
        """Seconds elapsed since session was last touched."""
        return time.time() - self.last_active

    def touch(self) -> None:
        """Refresh last_active timestamp and set state to ACTIVE if applicable."""
        self.last_active = time.time()
        if self.state == SessionState.IDLE:
            self.state = SessionState.ACTIVE

    def terminate(self) -> None:
        """Mark session as TERMINATED."""
        self.state = SessionState.TERMINATED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "agent_id": self.agent_id,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "state": self.state.value,
            "metadata": dict(self.metadata),
            "ttl": self.ttl,
            "tags": list(self.tags),
        }


@dataclass
class SessionEvent:
    event_type: str
    session_id: str
    timestamp: float
    data: Dict[str, Any] = field(default_factory=dict)


class SessionStore(ABC):
    @abstractmethod
    async def create(
        self,
        agent_id: str,
        ttl: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> SessionContext:
        ...

    @abstractmethod
    async def get(self, session_id: str) -> Optional[SessionContext]:
        ...

    @abstractmethod
    async def update(self, session_id: str, **kwargs: Any) -> bool:
        ...

    @abstractmethod
    async def delete(self, session_id: str) -> bool:
        ...

    @abstractmethod
    async def list_sessions(
        self,
        agent_id: Optional[str] = None,
        state: Optional[SessionState] = None,
    ) -> List[SessionContext]:
        ...

    @abstractmethod
    async def cleanup_expired(self) -> int:
        ...


class InMemorySessionStore(SessionStore):
    """Thread-safe, in-memory implementation of SessionStore."""

    def __init__(self) -> None:
        self._sessions: Dict[str, SessionContext] = {}
        self._lock = asyncio.Lock()

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
            self._sessions[session.session_id] = session
        return session

    async def get(self, session_id: str) -> Optional[SessionContext]:
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            return None
        # Lazily mark as expired
        if session.is_expired and session.state not in (
            SessionState.EXPIRED,
            SessionState.TERMINATED,
        ):
            async with self._lock:
                if session.session_id in self._sessions:
                    self._sessions[session.session_id].state = SessionState.EXPIRED
        return session

    async def update(self, session_id: str, **kwargs: Any) -> bool:
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return False
            for key, value in kwargs.items():
                if hasattr(session, key):
                    setattr(session, key, value)
        return True

    async def delete(self, session_id: str) -> bool:
        async with self._lock:
            if session_id not in self._sessions:
                return False
            del self._sessions[session_id]
        return True

    async def list_sessions(
        self,
        agent_id: Optional[str] = None,
        state: Optional[SessionState] = None,
    ) -> List[SessionContext]:
        async with self._lock:
            sessions = list(self._sessions.values())

        results: List[SessionContext] = []
        for session in sessions:
            if agent_id is not None and session.agent_id != agent_id:
                continue
            if state is not None and session.state != state:
                continue
            results.append(session)
        return results

    async def cleanup_expired(self) -> int:
        to_delete: List[str] = []
        async with self._lock:
            for sid, session in list(self._sessions.items()):
                if session.is_expired or session.state == SessionState.EXPIRED:
                    to_delete.append(sid)
            for sid in to_delete:
                del self._sessions[sid]
        return len(to_delete)


class SessionManager:
    """High-level facade for managing agent sessions."""

    def __init__(
        self,
        store: Optional[SessionStore] = None,
        default_ttl: float = 3600.0,
    ) -> None:
        self._store: SessionStore = store if store is not None else InMemorySessionStore()
        self.default_ttl = default_ttl
        self.on_create: List[Callable[[SessionEvent], Awaitable[Any]]] = []
        self.on_terminate: List[Callable[[SessionEvent], Awaitable[Any]]] = []

    # ------------------------------------------------------------------
    # Hook management
    # ------------------------------------------------------------------

    def add_hook(
        self,
        event_type: str,
        fn: Callable[[SessionEvent], Awaitable[Any]],
    ) -> None:
        """Register a coroutine hook for a given event type ('create' or 'terminate')."""
        if event_type == "create":
            self.on_create.append(fn)
        elif event_type == "terminate":
            self.on_terminate.append(fn)
        else:
            raise ValueError(f"Unknown event_type {event_type!r}. Expected 'create' or 'terminate'.")

    async def _emit(
        self,
        event_type: str,
        session_id: str,
        data: Optional[Dict[str, Any]] = None,
    ) -> None:
        event = SessionEvent(
            event_type=event_type,
            session_id=session_id,
            timestamp=time.time(),
            data=data or {},
        )
        if event_type == "create":
            hooks = self.on_create
        elif event_type == "terminate":
            hooks = self.on_terminate
        else:
            hooks = []

        for hook in hooks:
            await hook(event)

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    async def create_session(
        self,
        agent_id: str,
        ttl: Optional[float] = None,
        **kwargs: Any,
    ) -> SessionContext:
        resolved_ttl = ttl if ttl is not None else self.default_ttl
        metadata = kwargs.pop("metadata", {})
        tags = kwargs.pop("tags", [])
        session = await self._store.create(
            agent_id=agent_id,
            ttl=resolved_ttl,
            metadata=metadata,
            tags=tags,
        )
        await self._emit("create", session.session_id, {"agent_id": agent_id})
        return session

    async def get_session(self, session_id: str) -> Optional[SessionContext]:
        return await self._store.get(session_id)

    async def touch_session(self, session_id: str) -> bool:
        """Refresh last_active for a session. Returns False if session not found."""
        session = await self._store.get(session_id)
        if session is None:
            return False
        session.touch()
        return await self._store.update(
            session_id,
            last_active=session.last_active,
            state=session.state,
        )

    async def terminate_session(self, session_id: str) -> bool:
        session = await self._store.get(session_id)
        if session is None:
            return False
        session.terminate()
        updated = await self._store.update(session_id, state=SessionState.TERMINATED)
        if updated:
            await self._emit("terminate", session_id, {"agent_id": session.agent_id})
        return updated

    async def list_active(self, agent_id: Optional[str] = None) -> List[SessionContext]:
        return await self._store.list_sessions(agent_id=agent_id, state=SessionState.ACTIVE)

    async def cleanup(self) -> int:
        return await self._store.cleanup_expired()

    @property
    async def session_count(self) -> int:
        sessions = await self._store.list_sessions()
        return len(sessions)

    # ------------------------------------------------------------------
    # Async context manager
    # ------------------------------------------------------------------

    @asynccontextmanager
    async def with_session(self, agent_id: str, **kwargs: Any):
        """Async context manager: creates a session on enter, terminates on exit."""
        session = await self.create_session(agent_id, **kwargs)
        try:
            yield session
        finally:
            await self.terminate_session(session.session_id)


class SessionMiddleware:
    """Wraps an agent callable to auto-create and restore session context."""

    def __init__(self, manager: SessionManager) -> None:
        self.manager = manager

    async def __call__(
        self,
        agent_fn: Callable[..., Awaitable[Any]],
        ctx: Dict[str, Any],
    ) -> Any:
        agent_id: str = ctx.get("agent_id", "unknown")

        # Reuse existing session if provided, otherwise create a new one
        existing_session_id: Optional[str] = ctx.get("session_id")
        if existing_session_id:
            session = await self.manager.get_session(existing_session_id)
            if session is None or not session.is_active:
                session = await self.manager.create_session(agent_id)
            else:
                await self.manager.touch_session(session.session_id)
        else:
            session = await self.manager.create_session(agent_id)

        ctx = dict(ctx)
        ctx["_session"] = session

        try:
            result = await agent_fn(ctx)
        finally:
            # Only terminate sessions that were freshly created by this middleware
            if not existing_session_id:
                await self.manager.terminate_session(session.session_id)

        return result
