"""Lightweight message bus and rich communication hub for agent coordination."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set

from .safety.rate_limit import MultiLimiter, RateLimiter


@dataclass(frozen=True)
class MessageRecord:
    """Stored message emitted by an agent during coordination."""

    agent: str
    content: str
    iteration: int = 0
    channel: str = "default"
    meta: Mapping[str, Any] = field(default_factory=dict)


class MessageBus:
    """In-memory message bus for agents to publish and retrieve updates."""

    def __init__(self) -> None:
        self._records: List[MessageRecord] = []

    def publish(
        self,
        *,
        agent: str,
        content: str,
        iteration: int = 0,
        channel: str = "default",
        meta: Mapping[str, Any] | None = None,
    ) -> MessageRecord:
        record = MessageRecord(
            agent=agent, content=content, iteration=iteration,
            channel=channel, meta=dict(meta or {}),
        )
        self._records.append(record)
        return record

    def history(self, *, agent: str | None = None, channel: str | None = None) -> Sequence[MessageRecord]:
        records = self._records
        if agent is not None:
            records = [record for record in records if record.agent == agent]
        if channel is not None:
            records = [record for record in records if record.channel == channel]
        return tuple(records)

    def last(self, *, agent: str | None = None, channel: str | None = None) -> MessageRecord | None:
        for record in reversed(self._records):
            if agent is not None and record.agent != agent:
                continue
            if channel is not None and record.channel != channel:
                continue
            return record
        return None

    def conversation(self) -> Sequence[MessageRecord]:
        return tuple(self._records)


@dataclass(frozen=True)
class CommEnvelope:
    """Rich envelope describing how a message should be routed."""

    sender: str
    content: str
    mode: str = "direct"  # direct, broadcast, group, private_broadcast, encrypted, ephemeral
    targets: Sequence[str] = field(default_factory=tuple)
    group: str | None = None
    channel: str = "default"
    iteration: int = 0
    meta: Mapping[str, Any] = field(default_factory=dict)


class CommunicationHub:
    """Supports direct, broadcast, group, private broadcast, encrypted, and ephemeral messaging."""

    def __init__(self, *, cipher: Any | None = None) -> None:
        self.bus = MessageBus()
        self._participants: Set[str] = set()
        self._groups: Dict[str, Set[str]] = {}
        self._ephemeral: List[MessageRecord] = []
        self._namespaces: Dict[str, Set[str]] = {}
        self._namespace_links: Dict[str, Set[str]] = {}
        self._namespace_acls: Dict[str, Dict[str, Set[str]]] = {}
        self._cipher = cipher or (lambda payload: payload)
        self._rate_limits = MultiLimiter()
        self._handlers = {
            "direct": self._handle_direct,
            "broadcast": self._handle_broadcast,
            "group": self._handle_group,
            "private_broadcast": self._handle_private_broadcast,
            "encrypted": self._handle_encrypted,
            "ephemeral": self._handle_ephemeral,
            "namespace": self._handle_namespace,
        }

    def register_participant(self, name: str) -> None:
        self._participants.add(name)

    def set_group(self, name: str, members: Iterable[str]) -> None:
        self._groups[name] = set(members)

    def set_namespace_acl(
        self, namespace: str, allow: Iterable[str] | None = None, deny: Iterable[str] | None = None
    ) -> None:
        """Set allow/deny ACLs for namespace messaging."""

        self._namespace_acls[namespace] = {
            "allow": set(allow or []),
            "deny": set(deny or []),
        }

    def create_namespace(self, name: str, members: Iterable[str]) -> None:
        self._namespaces[name] = set(members)
        self._namespace_acls[name] = {"allow": set(members), "deny": set()}

    def set_namespace_rate_limit(self, namespace: str, rate: float, capacity: int) -> None:
        self._rate_limits.set_limit("namespace", namespace, RateLimiter(rate_per_sec=rate, capacity=capacity))

    def set_agent_rate_limit(self, agent: str, rate: float, capacity: int) -> None:
        self._rate_limits.set_limit("agent", agent, RateLimiter(rate_per_sec=rate, capacity=capacity))

    def set_tool_rate_limit(self, tool: str, rate: float, capacity: int) -> None:
        self._rate_limits.set_limit("tool", tool, RateLimiter(rate_per_sec=rate, capacity=capacity))

    def link_namespaces(self, source: str, target: str) -> None:
        self._namespace_links.setdefault(source, set()).add(target)

    def send(self, envelope: CommEnvelope) -> Sequence[MessageRecord]:
        handler = self._handlers.get(envelope.mode, self._handlers["direct"])
        return tuple(handler(envelope))

    def _emit(
        self, recipient: str, envelope: CommEnvelope, *,
        content: str | None = None, store: bool = True, meta_extra: Mapping[str, Any] | None = None,
    ) -> MessageRecord:
        meta = dict(envelope.meta)
        meta.update(meta_extra or {})
        meta.setdefault("sender", envelope.sender)
        builder = {
            True: lambda: self.bus.publish(
                agent=recipient, content=content or envelope.content,
                iteration=envelope.iteration, channel=envelope.channel, meta=meta,
            ),
            False: lambda: self._append_ephemeral(recipient, content or envelope.content, envelope, meta),
        }
        return builder[store]()

    def _append_ephemeral(
        self, recipient: str, content: str, envelope: CommEnvelope, meta: Mapping[str, Any]
    ) -> MessageRecord:
        record = MessageRecord(
            agent=recipient, content=content,
            iteration=envelope.iteration, channel=envelope.channel, meta=meta,
        )
        self._ephemeral.append(record)
        return record

    def _handle_direct(self, envelope: CommEnvelope) -> List[MessageRecord]:
        targets = list(envelope.targets)
        return [self._emit(target, envelope) for target in targets]

    def _handle_broadcast(self, envelope: CommEnvelope) -> List[MessageRecord]:
        audience = [participant for participant in self._participants if participant != envelope.sender]
        return [self._emit(target, envelope) for target in audience]

    def _handle_private_broadcast(self, envelope: CommEnvelope) -> List[MessageRecord]:
        audience = set(envelope.targets)
        audience &= self._participants
        return [self._emit(target, envelope) for target in audience]

    def _handle_group(self, envelope: CommEnvelope) -> List[MessageRecord]:
        members = self._groups.get(envelope.group or "", set())
        return [self._emit(target, envelope) for target in members]

    def _resolve_namespace_targets(self, ns: str) -> Set[str]:
        targets: Set[str] = set(self._namespaces.get(ns, set()))
        linked = self._namespace_links.get(ns, set())
        for linked_ns in linked:
            targets |= self._namespaces.get(linked_ns, set())
        return targets

    def _handle_namespace(self, envelope: CommEnvelope) -> List[MessageRecord]:
        targets = set()
        for ns in envelope.targets:
            targets |= self._resolve_namespace_targets(ns)
        ns_key = envelope.meta.get("namespace") or (envelope.targets[0] if envelope.targets else "")
        if not self._rate_limits.allow("namespace", ns_key):
            return []
        acl = self._namespace_acls.get(ns_key, {"allow": set(), "deny": set()})
        allow_set = acl.get("allow", set())
        deny_set = acl.get("deny", set())
        filtered = [t for t in targets if t not in deny_set and (not allow_set or t in allow_set)]
        return [self._emit(target, envelope, meta_extra={"namespace": tuple(envelope.targets)}) for target in filtered]

    def _handle_encrypted(self, envelope: CommEnvelope) -> List[MessageRecord]:
        secure_content = self._cipher(envelope.content)
        return [
            self._emit(target, envelope, content=secure_content, meta_extra={"encrypted": True})
            for target in envelope.targets
        ]

    def _handle_ephemeral(self, envelope: CommEnvelope) -> List[MessageRecord]:
        targets = list(envelope.targets)
        return [self._emit(target, envelope, meta_extra={"ephemeral": True}, store=False) for target in targets]

    def history(self, *, channel: str | None = None) -> Sequence[MessageRecord]:
        return self.bus.history(channel=channel)

    def ephemeral(self) -> Sequence[MessageRecord]:
        return tuple(self._ephemeral)


__all__ = ["MessageRecord", "MessageBus", "CommEnvelope", "CommunicationHub"]
