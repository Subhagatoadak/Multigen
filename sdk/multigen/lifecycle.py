"""
Agent lifecycle management for Multigen.

Problems solved
---------------
- No graceful agent deprecation workflow
- No agent dependency graph (don't know which workflows break if agent changes)
- No agent SLA contracts (no latency/quality promise per agent)
- No shadow mode execution (can't test new agent without affecting output)
- No agent capability versioning (v1 and v2 coexist without routing)

Classes
-------
- ``AgentSpec``             — descriptor of an agent with metadata
- ``DeprecationNotice``     — formal notice that an agent is being retired
- ``AgentDependencyGraph``  — tracks which workflows depend on which agents
- ``SLAContract``           — formal latency/quality promise for an agent
- ``SLAViolation``          — records when an SLA is breached
- ``SLAMonitor``            — checks metrics against contracts
- ``ShadowExecutor``        — runs new agent in parallel without affecting output
- ``CapabilityVersion``     — a versioned capability offered by an agent
- ``CapabilityRouter``      — routes requests to the right agent version
- ``AgentLifecycleManager`` — high-level facade

Usage::

    from multigen.lifecycle import AgentLifecycleManager

    mgr = AgentLifecycleManager()

    # Register agents
    mgr.register("summariser", summariser_v1, version="1.0", capabilities=["summarise"])
    mgr.register("summariser", summariser_v2, version="2.0", capabilities=["summarise", "translate"])

    # Shadow test v2 before promoting
    result, shadow = await mgr.shadow_call("summariser", ctx, shadow_version="2.0")

    # Deprecate v1
    mgr.deprecate("summariser", version="1.0", replacement="2.0", sunset_date="2026-06-01")

    # Check SLA
    mgr.monitor.record("summariser", latency_ms=250, score=0.88)
    violations = mgr.monitor.check_all()
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── AgentSpec ─────────────────────────────────────────────────────────────────

@dataclass
class AgentSpec:
    name: str
    version: str
    agent: Any                          # callable
    capabilities: List[str] = field(default_factory=list)
    status: str = "active"              # active | deprecated | sunset | shadow
    created_at: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_active(self) -> bool:
        return self.status == "active"


# ── DeprecationNotice ─────────────────────────────────────────────────────────

@dataclass
class DeprecationNotice:
    agent_name: str
    version: str
    replacement_version: str
    sunset_date: str                    # ISO date string
    reason: str = ""
    issued_at: float = field(default_factory=time.time)


# ── AgentDependencyGraph ──────────────────────────────────────────────────────

class AgentDependencyGraph:
    """
    Tracks which workflows depend on which agents.

    Lets you answer "if I change agent X, which workflows break?"

    Usage::

        graph = AgentDependencyGraph()
        graph.register_dependency("workflow-report", "summariser")
        graph.register_dependency("workflow-report", "analyser")
        affected = graph.affected_workflows("summariser")
    """

    def __init__(self) -> None:
        # workflow_name → set of agent names
        self._deps: Dict[str, set] = {}

    def register_dependency(self, workflow: str, agent: str) -> None:
        self._deps.setdefault(workflow, set()).add(agent)

    def affected_workflows(self, agent: str) -> List[str]:
        return [wf for wf, agents in self._deps.items() if agent in agents]

    def dependencies_of(self, workflow: str) -> List[str]:
        return list(self._deps.get(workflow, set()))

    def impact_report(self, agent: str) -> Dict[str, Any]:
        affected = self.affected_workflows(agent)
        return {
            "agent": agent,
            "affected_workflows": affected,
            "n_affected": len(affected),
        }


# ── SLA Contract / Monitor ────────────────────────────────────────────────────

@dataclass
class SLAContract:
    agent_name: str
    max_latency_ms: Optional[float] = None
    min_score: Optional[float] = None
    max_error_rate: float = 0.05      # 5% allowed errors
    description: str = ""


@dataclass
class SLAViolation:
    agent_name: str
    violation_type: str               # "latency" | "score" | "error_rate"
    observed: float
    limit: float
    timestamp: float = field(default_factory=time.time)

    def __str__(self) -> str:
        return (
            f"SLA Violation [{self.agent_name}] {self.violation_type}: "
            f"observed={self.observed:.3f} limit={self.limit:.3f}"
        )


class SLAMonitor:
    """
    Records agent metrics and checks them against SLA contracts.

    Usage::

        monitor = SLAMonitor()
        monitor.add_contract(SLAContract("summariser", max_latency_ms=500, min_score=0.8))
        monitor.record("summariser", latency_ms=600, score=0.75, error=False)
        violations = monitor.check_all()
    """

    def __init__(self) -> None:
        self._contracts: Dict[str, SLAContract] = {}
        self._metrics: Dict[str, List[Dict[str, Any]]] = {}
        self._violations: List[SLAViolation] = []

    def add_contract(self, contract: SLAContract) -> None:
        self._contracts[contract.agent_name] = contract

    def record(
        self,
        agent_name: str,
        latency_ms: float = 0.0,
        score: float = 1.0,
        error: bool = False,
    ) -> None:
        self._metrics.setdefault(agent_name, []).append({
            "latency_ms": latency_ms,
            "score": score,
            "error": error,
            "ts": time.time(),
        })

    def check(self, agent_name: str) -> List[SLAViolation]:
        contract = self._contracts.get(agent_name)
        if not contract:
            return []
        metrics = self._metrics.get(agent_name, [])
        if not metrics:
            return []

        violations = []
        recent = metrics[-100:]  # check last 100

        # Latency
        if contract.max_latency_ms is not None:
            avg_latency = sum(m["latency_ms"] for m in recent) / len(recent)
            if avg_latency > contract.max_latency_ms:
                v = SLAViolation(
                    agent_name=agent_name,
                    violation_type="latency",
                    observed=avg_latency,
                    limit=contract.max_latency_ms,
                )
                violations.append(v)
                self._violations.append(v)

        # Score
        if contract.min_score is not None:
            avg_score = sum(m["score"] for m in recent) / len(recent)
            if avg_score < contract.min_score:
                v = SLAViolation(
                    agent_name=agent_name,
                    violation_type="score",
                    observed=avg_score,
                    limit=contract.min_score,
                )
                violations.append(v)
                self._violations.append(v)

        # Error rate
        error_rate = sum(1 for m in recent if m["error"]) / len(recent)
        if error_rate > contract.max_error_rate:
            v = SLAViolation(
                agent_name=agent_name,
                violation_type="error_rate",
                observed=error_rate,
                limit=contract.max_error_rate,
            )
            violations.append(v)
            self._violations.append(v)

        return violations

    def check_all(self) -> List[SLAViolation]:
        all_violations = []
        for name in self._contracts:
            all_violations.extend(self.check(name))
        return all_violations

    def violation_history(self) -> List[SLAViolation]:
        return list(self._violations)


# ── ShadowExecutor ────────────────────────────────────────────────────────────

@dataclass
class ShadowResult:
    """Result of a shadow execution."""
    primary_output: Any
    shadow_output: Any
    shadow_agent: str
    agreement: bool
    primary_latency_ms: float
    shadow_latency_ms: float


class ShadowExecutor:
    """
    Runs a shadow agent in parallel with the primary agent.
    The primary's output is returned to the caller; the shadow's output
    is captured for comparison without affecting production.

    Usage::

        executor = ShadowExecutor()
        result = await executor.run(primary_agent, shadow_agent, ctx)
        print("Agreement:", result.agreement)
        print("Shadow output:", result.shadow_output)
    """

    async def run(
        self,
        primary: Callable,
        shadow: Callable,
        ctx: Dict[str, Any],
        shadow_name: str = "shadow",
    ) -> ShadowResult:
        async def timed(fn: Callable, c: Dict[str, Any]) -> Tuple[Any, float]:
            start = time.monotonic()
            result = fn(dict(c))
            if asyncio.iscoroutine(result):
                result = await result
            return result, (time.monotonic() - start) * 1000

        (primary_out, p_ms), (shadow_out, s_ms) = await asyncio.gather(
            timed(primary, ctx),
            timed(shadow, ctx),
            return_exceptions=True,
        )

        if isinstance(primary_out, Exception):
            raise primary_out
        if isinstance(shadow_out, Exception):
            shadow_out = {"error": str(shadow_out)}

        return ShadowResult(
            primary_output=primary_out,
            shadow_output=shadow_out,
            shadow_agent=shadow_name,
            agreement=primary_out == shadow_out,
            primary_latency_ms=p_ms,
            shadow_latency_ms=s_ms,
        )


# ── Capability Versioning ─────────────────────────────────────────────────────

@dataclass
class CapabilityVersion:
    capability: str
    version: str
    agent: Any                # callable
    status: str = "active"   # active | deprecated | canary


class CapabilityRouter:
    """
    Routes requests to the right agent version for a given capability.

    Supports ``active``, ``deprecated``, and ``canary`` (traffic split)
    versions per capability.

    Usage::

        router = CapabilityRouter()
        router.register("summarise", "1.0", summariser_v1)
        router.register("summarise", "2.0", summariser_v2, status="canary")
        agent = router.resolve("summarise")    # routes based on status
    """

    def __init__(self) -> None:
        # capability → {version: CapabilityVersion}
        self._cap: Dict[str, Dict[str, CapabilityVersion]] = {}

    def register(
        self,
        capability: str,
        version: str,
        agent: Any,
        status: str = "active",
    ) -> CapabilityVersion:
        cv = CapabilityVersion(capability, version, agent, status)
        self._cap.setdefault(capability, {})[version] = cv
        return cv

    def resolve(self, capability: str, prefer_version: Optional[str] = None) -> Any:
        versions = self._cap.get(capability, {})
        if not versions:
            raise KeyError(f"Capability {capability!r} not registered")

        if prefer_version and prefer_version in versions:
            return versions[prefer_version].agent

        # Prefer active → canary → deprecated
        for status in ("active", "canary", "deprecated"):
            matches = [v for v in versions.values() if v.status == status]
            if matches:
                return matches[-1].agent  # most recently registered

        raise KeyError(f"No usable version for capability {capability!r}")

    def deprecate(self, capability: str, version: str) -> None:
        cv = self._cap.get(capability, {}).get(version)
        if cv:
            cv.status = "deprecated"

    def versions(self, capability: str) -> List[CapabilityVersion]:
        return list(self._cap.get(capability, {}).values())


# ── AgentLifecycleManager ─────────────────────────────────────────────────────

class AgentLifecycleManager:
    """
    High-level facade for full agent lifecycle management.

    Usage::

        mgr = AgentLifecycleManager()
        mgr.register("summariser", agent_v1, "1.0", capabilities=["summarise"])
        mgr.register("summariser", agent_v2, "2.0", capabilities=["summarise"])

        result, shadow = await mgr.shadow_call("summariser", ctx, shadow_version="2.0")
        mgr.deprecate("summariser", "1.0", replacement="2.0", sunset_date="2026-06-01")

        mgr.monitor.add_contract(SLAContract("summariser", max_latency_ms=500))
        mgr.monitor.record("summariser", latency_ms=300, score=0.9)
    """

    def __init__(self) -> None:
        self._registry: Dict[str, Dict[str, AgentSpec]] = {}
        self._notices: List[DeprecationNotice] = []
        self.deps = AgentDependencyGraph()
        self.monitor = SLAMonitor()
        self.shadow = ShadowExecutor()
        self.capabilities = CapabilityRouter()

    def register(
        self,
        name: str,
        agent: Any,
        version: str = "1.0",
        capabilities: Optional[List[str]] = None,
        status: str = "active",
    ) -> AgentSpec:
        spec = AgentSpec(
            name=name,
            version=version,
            agent=agent,
            capabilities=capabilities or [],
            status=status,
        )
        self._registry.setdefault(name, {})[version] = spec
        for cap in spec.capabilities:
            self.capabilities.register(cap, version, agent, status=status)
        return spec

    def get(self, name: str, version: Optional[str] = None) -> AgentSpec:
        versions = self._registry.get(name, {})
        if not versions:
            raise KeyError(f"Agent {name!r} not registered")
        if version:
            return versions[version]
        # Return latest active
        active = [s for s in versions.values() if s.is_active()]
        return (active or list(versions.values()))[-1]

    def deprecate(
        self,
        name: str,
        version: str,
        replacement: str = "",
        sunset_date: str = "",
        reason: str = "",
    ) -> DeprecationNotice:
        spec = self._registry.get(name, {}).get(version)
        if spec:
            spec.status = "deprecated"
        notice = DeprecationNotice(
            agent_name=name,
            version=version,
            replacement_version=replacement,
            sunset_date=sunset_date,
            reason=reason,
        )
        self._notices.append(notice)
        return notice

    async def shadow_call(
        self,
        name: str,
        ctx: Dict[str, Any],
        shadow_version: str = "",
    ) -> Tuple[Any, ShadowResult]:
        primary_spec = self.get(name)
        versions = self._registry.get(name, {})
        shadow_spec = (
            versions.get(shadow_version) or
            next((s for s in versions.values() if s.status == "shadow"), None) or
            primary_spec
        )
        shadow_result = await self.shadow.run(
            primary_spec.agent,
            shadow_spec.agent,
            ctx,
            shadow_name=f"{name}@{shadow_spec.version}",
        )
        return shadow_result.primary_output, shadow_result

    def deprecation_notices(self) -> List[DeprecationNotice]:
        return list(self._notices)


__all__ = [
    "AgentSpec",
    "DeprecationNotice",
    "AgentDependencyGraph",
    "SLAContract",
    "SLAViolation",
    "SLAMonitor",
    "ShadowResult",
    "ShadowExecutor",
    "CapabilityVersion",
    "CapabilityRouter",
    "AgentLifecycleManager",
]
