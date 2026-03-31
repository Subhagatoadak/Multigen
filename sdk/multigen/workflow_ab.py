"""
DSL versioning compatibility, A/B testing, canary rollout, and rollback.

Problems solved
---------------
- Changing a workflow definition breaks old in-flight runs
- No way to test v2 against real traffic before full rollout
- Canary deployments require manual routing code
- Rollback is destructive without snapshots

Classes
-------
- ``CompatibilityReport``   — result of checking two definitions for compatibility
- ``CompatibilityChecker``  — detect breaking vs non-breaking changes
- ``MigrationFn``           — transform a context from old format to new format
- ``TrafficSplit``           — percentage weights per version
- ``WorkflowRouter``        — routes each request to a version per split weights
- ``ABTest``                — run two versions in parallel, collect metrics
- ``CanaryRollout``         — ramp traffic from 0% → 100% based on health
- ``RollbackManager``       — snapshot + restore workflow definitions

Usage::

    from multigen.workflow_ab import (
        CompatibilityChecker, WorkflowRouter, ABTest, CanaryRollout,
    )

    checker = CompatibilityChecker()
    report  = checker.check(v1_definition, v2_definition)
    if report.has_breaking_changes:
        print(report.breaking)           # list of breaking field changes

    # A/B test two agent versions on live traffic
    ab = ABTest(agent_a=old_agent, agent_b=new_agent, split=0.2)
    result = await ab.route(ctx)         # 80% old, 20% new

    # Canary: start at 5%, auto-promote on success
    canary = CanaryRollout(stable=old_agent, canary=new_agent,
                           initial_pct=5, target_pct=100)
    result = await canary.call(ctx)
    canary.record_outcome(success=True)
    canary.maybe_promote()               # bumps % if health is good
"""
from __future__ import annotations

import asyncio
import copy
import random
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── CompatibilityReport ────────────────────────────────────────────────────────

@dataclass
class CompatibilityReport:
    """Result of a compatibility check between two workflow definitions."""

    from_version: str
    to_version: str
    breaking: List[str] = field(default_factory=list)
    non_breaking: List[str] = field(default_factory=list)
    added: List[str] = field(default_factory=list)
    removed: List[str] = field(default_factory=list)
    changed: List[str] = field(default_factory=list)

    @property
    def has_breaking_changes(self) -> bool:
        return bool(self.breaking)

    @property
    def is_compatible(self) -> bool:
        return not self.has_breaking_changes

    def summary(self) -> str:
        status = "BREAKING" if self.has_breaking_changes else "compatible"
        lines = [f"Compatibility {self.from_version}→{self.to_version}: {status}"]
        if self.breaking:
            lines.append(f"  BREAKING: {self.breaking}")
        if self.non_breaking:
            lines.append(f"  safe:     {self.non_breaking}")
        if self.added:
            lines.append(f"  added:    {self.added}")
        if self.removed:
            lines.append(f"  removed:  {self.removed}")
        return "\n".join(lines)


# ── CompatibilityChecker ───────────────────────────────────────────────────────

class CompatibilityChecker:
    """
    Detects breaking vs non-breaking changes between two workflow definitions.

    Breaking changes (by default):
    - Required fields removed
    - Field type changed (str → int, list → dict, etc.)
    - Key renamed (treated as remove+add)

    Non-breaking:
    - New optional fields added
    - Field default value changed
    - Metadata / description changes

    The set of "breaking keys" is configurable.

    Usage::

        checker = CompatibilityChecker(breaking_keys=["steps", "timeout"])
        report  = checker.check(v1_def, v2_def, from_v="1.0.0", to_v="2.0.0")
    """

    def __init__(self, breaking_keys: Optional[List[str]] = None) -> None:
        # Keys whose removal or type change is always breaking
        self._breaking_keys: set = set(breaking_keys or ["steps", "timeout", "schema"])

    def check(
        self,
        old: Dict[str, Any],
        new: Dict[str, Any],
        from_v: str = "old",
        to_v: str = "new",
    ) -> CompatibilityReport:
        report = CompatibilityReport(from_version=from_v, to_version=to_v)
        old_keys = set(old.keys())
        new_keys = set(new.keys())

        report.added   = sorted(new_keys - old_keys)
        report.removed = sorted(old_keys - new_keys)

        for key in sorted(old_keys & new_keys):
            ov, nv = old[key], new[key]
            if not isinstance(ov, type(nv)) or not isinstance(nv, type(ov)):
                report.changed.append(key)
                if key in self._breaking_keys:
                    report.breaking.append(f"type_change:{key}({type(ov).__name__}→{type(nv).__name__})")
                else:
                    report.non_breaking.append(f"type_change:{key}")
            elif ov != nv:
                report.changed.append(key)
                if key in self._breaking_keys:
                    report.breaking.append(f"value_change:{key}")
                else:
                    report.non_breaking.append(f"value_change:{key}")

        for key in report.removed:
            if key in self._breaking_keys:
                report.breaking.append(f"removed:{key}")
            else:
                report.non_breaking.append(f"removed:{key}")

        return report


# ── MigrationFn ───────────────────────────────────────────────────────────────

class MigrationFn:
    """
    Transforms a runtime context dict from an old workflow format to a new one.

    Register field renames and transformers; the migration is applied before
    the new workflow version executes.

    Usage::

        migration = MigrationFn("1.0.0", "2.0.0")
        migration.rename("query", "user_query")
        migration.transform("retries", lambda v: int(v) if v else 0)
        migration.add("version", "2.0.0")

        new_ctx = migration.apply(old_ctx)
    """

    def __init__(self, from_v: str, to_v: str) -> None:
        self.from_v = from_v
        self.to_v = to_v
        self._renames: Dict[str, str] = {}
        self._transforms: Dict[str, Callable] = {}
        self._additions: Dict[str, Any] = {}
        self._removals: List[str] = []

    def rename(self, old_key: str, new_key: str) -> "MigrationFn":
        self._renames[old_key] = new_key
        return self

    def transform(self, key: str, fn: Callable) -> "MigrationFn":
        self._transforms[key] = fn
        return self

    def add(self, key: str, value: Any) -> "MigrationFn":
        self._additions[key] = value
        return self

    def remove(self, key: str) -> "MigrationFn":
        self._removals.append(key)
        return self

    def apply(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        result = copy.deepcopy(ctx)
        # Renames
        for old_key, new_key in self._renames.items():
            if old_key in result:
                result[new_key] = result.pop(old_key)
        # Transforms
        for key, fn in self._transforms.items():
            if key in result:
                result[key] = fn(result[key])
        # Removals
        for key in self._removals:
            result.pop(key, None)
        # Additions
        for key, val in self._additions.items():
            if key not in result:
                result[key] = val
        result["_schema_version"] = self.to_v
        return result


# ── TrafficSplit ───────────────────────────────────────────────────────────────

@dataclass
class TrafficSplit:
    """Percentage weights for traffic distribution across named versions."""

    weights: Dict[str, float]  # version_label → percentage (0.0–1.0)

    def __post_init__(self) -> None:
        total = sum(self.weights.values())
        if abs(total - 1.0) > 0.001:
            # Normalise
            self.weights = {k: v / total for k, v in self.weights.items()}

    def sample(self, rng: Optional[random.Random] = None) -> str:
        """Return a version label sampled according to weights."""
        r = (rng or random).random()
        cumulative = 0.0
        for label, weight in self.weights.items():
            cumulative += weight
            if r < cumulative:
                return label
        return list(self.weights.keys())[-1]


# ── WorkflowRouter ─────────────────────────────────────────────────────────────

@dataclass
class RouteDecision:
    version: str
    agent_label: str
    traffic_pct: float
    request_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])


class WorkflowRouter:
    """
    Routes each incoming request to one of several agent versions according
    to a ``TrafficSplit``.

    Usage::

        router = WorkflowRouter(
            versions={"stable": agent_v1, "canary": agent_v2},
            split=TrafficSplit({"stable": 0.9, "canary": 0.1}),
        )
        result = await router.call(ctx)
        print(router.stats())
    """

    def __init__(
        self,
        versions: Dict[str, Any],
        split: Optional[TrafficSplit] = None,
        seed: Optional[int] = None,
        on_decision: Optional[Callable[[RouteDecision], None]] = None,
    ) -> None:
        self._versions = versions
        labels = list(versions.keys())
        if split is None:
            equal = 1.0 / len(labels)
            split = TrafficSplit({k: equal for k in labels})
        self._split = split
        self._rng = random.Random(seed)
        self._on_decision = on_decision
        self._counts: Dict[str, int] = {k: 0 for k in labels}
        self._errors: Dict[str, int] = {k: 0 for k in labels}

    async def call(self, ctx: Dict[str, Any]) -> Tuple[Any, RouteDecision]:
        label = self._split.sample(self._rng)
        agent = self._versions[label]
        decision = RouteDecision(
            version=label,
            agent_label=label,
            traffic_pct=self._split.weights.get(label, 0.0),
        )
        if self._on_decision:
            self._on_decision(decision)
        self._counts[label] += 1
        try:
            if asyncio.iscoroutinefunction(agent):
                result = await agent(ctx)
            else:
                result = agent(ctx)
            return result, decision
        except Exception:
            self._errors[label] += 1
            raise

    def stats(self) -> Dict[str, Any]:
        return {
            "split": self._split.weights,
            "counts": dict(self._counts),
            "errors": dict(self._errors),
        }

    def update_split(self, new_split: TrafficSplit) -> None:
        self._split = new_split


# ── ABTest ─────────────────────────────────────────────────────────────────────

@dataclass
class ABTestResult:
    """Aggregated comparison between A and B."""

    label_a: str
    label_b: str
    calls_a: int
    calls_b: int
    errors_a: int
    errors_b: int
    scores_a: List[float]
    scores_b: List[float]

    @property
    def mean_score_a(self) -> float:
        return sum(self.scores_a) / len(self.scores_a) if self.scores_a else 0.0

    @property
    def mean_score_b(self) -> float:
        return sum(self.scores_b) / len(self.scores_b) if self.scores_b else 0.0

    @property
    def winner(self) -> str:
        if self.mean_score_a > self.mean_score_b:
            return self.label_a
        if self.mean_score_b > self.mean_score_a:
            return self.label_b
        return "tie"

    def summary(self) -> str:
        return (
            f"A/B Test: {self.label_a} vs {self.label_b}\n"
            f"  {self.label_a}: calls={self.calls_a} errors={self.errors_a} "
            f"mean_score={self.mean_score_a:.3f}\n"
            f"  {self.label_b}: calls={self.calls_b} errors={self.errors_b} "
            f"mean_score={self.mean_score_b:.3f}\n"
            f"  Winner: {self.winner}"
        )


class ABTest:
    """
    A/B test two agent versions on live traffic.

    Usage::

        ab = ABTest(agent_a=old_agent, agent_b=new_agent,
                    label_a="v1", label_b="v2", split=0.2)
        result, decision = await ab.call(ctx)   # 80% v1, 20% v2
        ab.record_score(decision.version, score=0.85)
        print(ab.result().summary())
    """

    def __init__(
        self,
        agent_a: Any,
        agent_b: Any,
        label_a: str = "A",
        label_b: str = "B",
        split: float = 0.5,  # fraction sent to B
        seed: Optional[int] = None,
    ) -> None:
        self._router = WorkflowRouter(
            versions={label_a: agent_a, label_b: agent_b},
            split=TrafficSplit({label_a: 1.0 - split, label_b: split}),
            seed=seed,
        )
        self._label_a = label_a
        self._label_b = label_b
        self._scores: Dict[str, List[float]] = {label_a: [], label_b: []}

    async def call(self, ctx: Dict[str, Any]) -> Tuple[Any, RouteDecision]:
        return await self._router.call(ctx)

    def record_score(self, version: str, score: float) -> None:
        if version in self._scores:
            self._scores[version].append(score)

    def result(self) -> ABTestResult:
        s = self._router.stats()
        return ABTestResult(
            label_a=self._label_a,
            label_b=self._label_b,
            calls_a=s["counts"].get(self._label_a, 0),
            calls_b=s["counts"].get(self._label_b, 0),
            errors_a=s["errors"].get(self._label_a, 0),
            errors_b=s["errors"].get(self._label_b, 0),
            scores_a=list(self._scores[self._label_a]),
            scores_b=list(self._scores[self._label_b]),
        )


# ── CanaryRollout ──────────────────────────────────────────────────────────────

class CanaryRollout:
    """
    Gradual canary deployment: ramp traffic from *initial_pct* → *target_pct*.

    Auto-promotes when ``consecutive_successes >= promote_after`` and the
    canary error rate stays below ``max_error_rate``.
    Auto-rolls-back when ``consecutive_failures >= rollback_after``.

    Usage::

        canary = CanaryRollout(
            stable=v1_agent, canary=v2_agent,
            initial_pct=5, target_pct=100,
            step_pct=10, promote_after=20, rollback_after=3,
        )
        result, decision = await canary.call(ctx)
        canary.record_outcome(success=True)
        canary.maybe_promote()
    """

    def __init__(
        self,
        stable: Any,
        canary: Any,
        initial_pct: float = 5.0,
        target_pct: float = 100.0,
        step_pct: float = 10.0,
        promote_after: int = 20,
        rollback_after: int = 3,
        max_error_rate: float = 0.05,
        seed: Optional[int] = None,
    ) -> None:
        self._stable = stable
        self._canary_agent = canary
        self.canary_pct = initial_pct / 100.0
        self.target_pct = target_pct / 100.0
        self.step_pct = step_pct / 100.0
        self.promote_after = promote_after
        self.rollback_after = rollback_after
        self.max_error_rate = max_error_rate
        self._rng = random.Random(seed)
        self._consecutive_success = 0
        self._consecutive_failure = 0
        self._total_canary = 0
        self._total_canary_errors = 0
        self.rolled_back = False
        self.promoted = False

    def _router(self) -> WorkflowRouter:
        return WorkflowRouter(
            versions={"stable": self._stable, "canary": self._canary_agent},
            split=TrafficSplit({"stable": 1.0 - self.canary_pct, "canary": self.canary_pct}),
            seed=self._rng.randint(0, 2**31),
        )

    async def call(self, ctx: Dict[str, Any]) -> Tuple[Any, RouteDecision]:
        return await self._router().call(ctx)

    def record_outcome(self, success: bool, is_canary: bool = True) -> None:
        if not is_canary:
            return
        self._total_canary += 1
        if success:
            self._consecutive_success += 1
            self._consecutive_failure = 0
        else:
            self._consecutive_failure += 1
            self._consecutive_success = 0
            self._total_canary_errors += 1

    def maybe_promote(self) -> bool:
        """Advance canary % by one step if health is good. Returns True if promoted."""
        if self.rolled_back or self.promoted:
            return False
        error_rate = self._total_canary_errors / max(1, self._total_canary)
        if (self._consecutive_success >= self.promote_after
                and error_rate <= self.max_error_rate):
            self.canary_pct = min(self.target_pct, self.canary_pct + self.step_pct)
            self._consecutive_success = 0
            if self.canary_pct >= self.target_pct:
                self.promoted = True
            return True
        if self._consecutive_failure >= self.rollback_after:
            self.rollback()
        return False

    def rollback(self) -> None:
        self.canary_pct = 0.0
        self.rolled_back = True

    def status(self) -> Dict[str, Any]:
        return {
            "canary_pct": round(self.canary_pct * 100, 1),
            "target_pct": round(self.target_pct * 100, 1),
            "consecutive_success": self._consecutive_success,
            "consecutive_failure": self._consecutive_failure,
            "error_rate": self._total_canary_errors / max(1, self._total_canary),
            "rolled_back": self.rolled_back,
            "fully_promoted": self.promoted,
        }


# ── RollbackManager ────────────────────────────────────────────────────────────

class RollbackManager:
    """
    Snapshot and restore workflow agent configurations.

    Each snapshot stores an agent reference + its configuration dict.
    Rollback swaps the live agent reference back to a previous snapshot.

    Usage::

        rm = RollbackManager()
        snap_id = rm.snapshot("pipeline", agent_v1, config_v1)
        rm.set_live("pipeline", agent_v2, config_v2)

        # Something went wrong
        rm.rollback("pipeline", snap_id)
        live_agent, live_cfg = rm.get_live("pipeline")
    """

    def __init__(self) -> None:
        self._snapshots: Dict[str, List[Dict[str, Any]]] = {}
        self._live: Dict[str, Dict[str, Any]] = {}

    def snapshot(self, name: str, agent: Any, config: Dict[str, Any]) -> str:
        snap_id = str(uuid.uuid4())[:8]
        record = {
            "snap_id": snap_id,
            "agent": agent,
            "config": copy.deepcopy(config),
            "timestamp": time.time(),
        }
        self._snapshots.setdefault(name, []).append(record)
        return snap_id

    def set_live(self, name: str, agent: Any, config: Dict[str, Any]) -> None:
        self._live[name] = {"agent": agent, "config": copy.deepcopy(config)}

    def get_live(self, name: str) -> Tuple[Optional[Any], Dict[str, Any]]:
        entry = self._live.get(name)
        if entry is None:
            return None, {}
        return entry["agent"], entry["config"]

    def rollback(self, name: str, snap_id: Optional[str] = None) -> bool:
        """Roll back to *snap_id* (or the most recent snapshot if None)."""
        snaps = self._snapshots.get(name, [])
        if not snaps:
            return False
        if snap_id is None:
            snap = snaps[-1]
        else:
            matching = [s for s in snaps if s["snap_id"] == snap_id]
            if not matching:
                return False
            snap = matching[0]
        self._live[name] = {"agent": snap["agent"], "config": copy.deepcopy(snap["config"])}
        return True

    def history(self, name: str) -> List[Dict[str, Any]]:
        return [
            {"snap_id": s["snap_id"], "timestamp": s["timestamp"]}
            for s in self._snapshots.get(name, [])
        ]


__all__ = [
    "ABTest",
    "ABTestResult",
    "CanaryRollout",
    "CompatibilityChecker",
    "CompatibilityReport",
    "MigrationFn",
    "RollbackManager",
    "RouteDecision",
    "TrafficSplit",
    "WorkflowRouter",
]
