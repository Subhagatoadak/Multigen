"""
Multi-tenancy primitives for Multigen.

Problems solved
---------------
- Tenant-scoped namespace isolation (Temporal / Kafka / MongoDB topic prefixing)
- Per-tenant agent registry (agents don't leak across tenants)
- Per-tenant billing / usage tracking (tokens, calls, cost)
- Tenant quota enforcement (rate limits per tenant)
- Tenant onboarding / offboarding lifecycle

Classes
-------
- ``Tenant``               — immutable tenant descriptor
- ``TenantRegistry``       — create / get / delete tenants
- ``TenantScope``          — context manager that binds operations to a tenant
- ``UsageRecord``          — single usage event (tokens, calls, cost)
- ``UsageTracker``         — per-tenant usage accumulation + quota enforcement
- ``TenantAwareRegistry``  — agent registry scoped to a tenant
- ``TenantManager``        — high-level facade combining all the above

Usage::

    from multigen.tenancy import TenantManager, TenantScope

    mgr = TenantManager()
    tenant = mgr.create_tenant("acme", plan="pro", quota_tokens=1_000_000)

    async with TenantScope(tenant):
        result = await my_pipeline(ctx)
        mgr.record_usage(tenant.id, tokens=450, calls=1, cost_usd=0.002)

    report = mgr.usage_report(tenant.id)
"""
from __future__ import annotations

import asyncio
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# ── Tenant descriptor ─────────────────────────────────────────────────────────

@dataclass
class Tenant:
    """Immutable descriptor for a single tenant."""
    id: str
    name: str
    plan: str = "free"
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    # Namespace helpers
    def prefix(self, key: str) -> str:
        """Return ``<tenant_id>:<key>`` for namespace isolation."""
        return f"{self.id}:{key}"

    def kafka_topic(self, base: str) -> str:
        return f"{self.id}.{base}"

    def temporal_namespace(self) -> str:
        return f"tenant-{self.id}"

    def mongodb_db(self) -> str:
        return f"multigen_{self.id}"


# ── TenantRegistry ────────────────────────────────────────────────────────────

class TenantRegistry:
    """
    In-memory registry of tenants.  In production wire this to a database.

    Usage::

        registry = TenantRegistry()
        t = registry.create("acme", plan="enterprise")
        t2 = registry.get("acme")
        registry.delete("acme")
    """

    def __init__(self) -> None:
        self._tenants: Dict[str, Tenant] = {}

    def create(
        self,
        name: str,
        plan: str = "free",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tenant:
        if name in self._tenants:
            raise ValueError(f"Tenant {name!r} already exists")
        tenant = Tenant(
            id=str(uuid.uuid4()),
            name=name,
            plan=plan,
            metadata=metadata or {},
        )
        self._tenants[name] = tenant
        return tenant

    def get(self, name: str) -> Tenant:
        if name not in self._tenants:
            raise KeyError(f"Tenant {name!r} not found")
        return self._tenants[name]

    def get_by_id(self, tenant_id: str) -> Tenant:
        for t in self._tenants.values():
            if t.id == tenant_id:
                return t
        raise KeyError(f"Tenant id {tenant_id!r} not found")

    def delete(self, name: str) -> Tenant:
        """Offboard a tenant — returns the removed record."""
        return self._tenants.pop(name)

    def list(self) -> List[Tenant]:
        return list(self._tenants.values())

    def __len__(self) -> int:
        return len(self._tenants)


# ── TenantScope ───────────────────────────────────────────────────────────────

# Thread-local / asyncio-task-local current tenant
_current_tenant: Optional[Tenant] = None


def current_tenant() -> Optional[Tenant]:
    """Return the tenant currently active in this async task, or None."""
    return _current_tenant


@asynccontextmanager
async def TenantScope(tenant: Tenant):
    """
    Async context manager that makes *tenant* the active tenant for the
    duration of the ``async with`` block.

    Usage::

        async with TenantScope(tenant):
            await run_workflow(ctx)  # ctx['_tenant'] is set automatically
    """
    global _current_tenant
    previous = _current_tenant
    _current_tenant = tenant
    try:
        yield tenant
    finally:
        _current_tenant = previous


def inject_tenant(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """
    Inject current tenant info into a context dict.

    Adds ``ctx['_tenant_id']``, ``ctx['_tenant_name']``, and
    ``ctx['_topic_prefix']`` if a tenant is active.
    """
    t = current_tenant()
    if t is not None:
        ctx = {
            **ctx,
            "_tenant_id": t.id,
            "_tenant_name": t.name,
            "_topic_prefix": t.id,
        }
    return ctx


# ── Usage Tracking ────────────────────────────────────────────────────────────

@dataclass
class UsageRecord:
    """A single usage event."""
    tenant_id: str
    tokens: int = 0
    calls: int = 0
    cost_usd: float = 0.0
    workflow_id: Optional[str] = None
    agent_name: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class UsageSummary:
    """Aggregated usage for a tenant over a period."""
    tenant_id: str
    total_tokens: int
    total_calls: int
    total_cost_usd: float
    quota_tokens: Optional[int]
    quota_calls: Optional[int]

    @property
    def tokens_remaining(self) -> Optional[int]:
        if self.quota_tokens is None:
            return None
        return max(0, self.quota_tokens - self.total_tokens)

    @property
    def calls_remaining(self) -> Optional[int]:
        if self.quota_calls is None:
            return None
        return max(0, self.quota_calls - self.total_calls)

    def over_quota(self) -> bool:
        if self.quota_tokens and self.total_tokens > self.quota_tokens:
            return True
        if self.quota_calls and self.total_calls > self.quota_calls:
            return True
        return False


class QuotaExceededError(Exception):
    """Raised when a tenant exceeds their usage quota."""
    pass


class UsageTracker:
    """
    Per-tenant usage accumulator with optional quota enforcement.

    Quotas are checked on ``record()``.  Exceeding a quota raises
    ``QuotaExceededError``.

    Usage::

        tracker = UsageTracker()
        tracker.set_quota("tenant-id", tokens=500_000, calls=10_000)
        tracker.record("tenant-id", tokens=100, calls=1, cost_usd=0.001)
        summary = tracker.summary("tenant-id")
    """

    def __init__(self) -> None:
        self._records: Dict[str, List[UsageRecord]] = {}
        self._quotas: Dict[str, Dict[str, int]] = {}

    def set_quota(
        self,
        tenant_id: str,
        tokens: Optional[int] = None,
        calls: Optional[int] = None,
    ) -> None:
        self._quotas[tenant_id] = {
            k: v for k, v in {"tokens": tokens, "calls": calls}.items()
            if v is not None
        }

    def record(
        self,
        tenant_id: str,
        tokens: int = 0,
        calls: int = 1,
        cost_usd: float = 0.0,
        workflow_id: Optional[str] = None,
        agent_name: Optional[str] = None,
    ) -> UsageRecord:
        rec = UsageRecord(
            tenant_id=tenant_id,
            tokens=tokens,
            calls=calls,
            cost_usd=cost_usd,
            workflow_id=workflow_id,
            agent_name=agent_name,
        )
        self._records.setdefault(tenant_id, []).append(rec)

        # Quota check
        summary = self.summary(tenant_id)
        if summary.over_quota():
            raise QuotaExceededError(
                f"Tenant {tenant_id!r} exceeded quota: "
                f"{summary.total_tokens} tokens / {summary.total_calls} calls"
            )
        return rec

    def summary(self, tenant_id: str) -> UsageSummary:
        records = self._records.get(tenant_id, [])
        quotas = self._quotas.get(tenant_id, {})
        return UsageSummary(
            tenant_id=tenant_id,
            total_tokens=sum(r.tokens for r in records),
            total_calls=sum(r.calls for r in records),
            total_cost_usd=sum(r.cost_usd for r in records),
            quota_tokens=quotas.get("tokens"),
            quota_calls=quotas.get("calls"),
        )

    def all_records(self, tenant_id: str) -> List[UsageRecord]:
        return list(self._records.get(tenant_id, []))

    def reset(self, tenant_id: str) -> None:
        """Clear accumulated usage (e.g. at billing period start)."""
        self._records.pop(tenant_id, None)


# ── Tenant-Aware Agent Registry ───────────────────────────────────────────────

class TenantAwareRegistry:
    """
    Agent registry scoped per tenant.

    Agents registered under one tenant are invisible to others.

    Usage::

        reg = TenantAwareRegistry()
        reg.register("acme-tenant-id", "summariser", summariser_agent)
        agent = reg.get("acme-tenant-id", "summariser")
    """

    def __init__(self) -> None:
        self._agents: Dict[str, Dict[str, Any]] = {}

    def register(self, tenant_id: str, name: str, agent: Any) -> None:
        self._agents.setdefault(tenant_id, {})[name] = agent

    def get(self, tenant_id: str, name: str) -> Any:
        agents = self._agents.get(tenant_id, {})
        if name not in agents:
            raise KeyError(
                f"Agent {name!r} not registered for tenant {tenant_id!r}"
            )
        return agents[name]

    def list(self, tenant_id: str) -> List[str]:
        return list(self._agents.get(tenant_id, {}).keys())

    def unregister(self, tenant_id: str, name: str) -> None:
        self._agents.get(tenant_id, {}).pop(name, None)

    def purge_tenant(self, tenant_id: str) -> int:
        """Remove all agents for *tenant_id*. Returns count removed."""
        agents = self._agents.pop(tenant_id, {})
        return len(agents)


# ── TenantManager Facade ──────────────────────────────────────────────────────

class TenantManager:
    """
    High-level facade combining TenantRegistry, UsageTracker, and
    TenantAwareRegistry.

    Usage::

        mgr = TenantManager()

        # Onboard
        tenant = mgr.create_tenant("acme", plan="pro", quota_tokens=1_000_000)

        # Use
        async with mgr.scope(tenant):
            result = await pipeline(ctx)
            mgr.record_usage(tenant.id, tokens=300, cost_usd=0.003)

        # Report
        print(mgr.usage_report(tenant.id))

        # Offboard
        mgr.delete_tenant("acme")
    """

    def __init__(self) -> None:
        self.registry = TenantRegistry()
        self.usage = UsageTracker()
        self.agents = TenantAwareRegistry()

    def create_tenant(
        self,
        name: str,
        plan: str = "free",
        quota_tokens: Optional[int] = None,
        quota_calls: Optional[int] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Tenant:
        tenant = self.registry.create(name, plan=plan, metadata=metadata or {})
        if quota_tokens or quota_calls:
            self.usage.set_quota(
                tenant.id,
                tokens=quota_tokens,
                calls=quota_calls,
            )
        return tenant

    def delete_tenant(self, name: str) -> Tenant:
        """Offboard: remove tenant + purge all its agents."""
        tenant = self.registry.get(name)
        self.agents.purge_tenant(tenant.id)
        self.usage.reset(tenant.id)
        return self.registry.delete(name)

    def record_usage(
        self,
        tenant_id: str,
        tokens: int = 0,
        calls: int = 1,
        cost_usd: float = 0.0,
        **kwargs: Any,
    ) -> UsageRecord:
        return self.usage.record(
            tenant_id, tokens=tokens, calls=calls, cost_usd=cost_usd, **kwargs
        )

    def usage_report(self, tenant_id: str) -> UsageSummary:
        return self.usage.summary(tenant_id)

    @asynccontextmanager
    async def scope(self, tenant: Tenant):
        """Shorthand for ``TenantScope(tenant)``."""
        async with TenantScope(tenant) as t:
            yield t

    def list_tenants(self) -> List[Tenant]:
        return self.registry.list()


__all__ = [
    "Tenant",
    "TenantRegistry",
    "TenantScope",
    "current_tenant",
    "inject_tenant",
    "UsageRecord",
    "UsageSummary",
    "UsageTracker",
    "QuotaExceededError",
    "TenantAwareRegistry",
    "TenantManager",
]
