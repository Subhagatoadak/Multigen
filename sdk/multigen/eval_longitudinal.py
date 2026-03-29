"""
Longitudinal evaluation — regression suites, golden datasets, drift alerting,
eval-triggered rollback, and cross-version performance tracking.

Problems solved
---------------
- No regression test suite per agent (can't detect when agent quality degrades)
- No golden dataset management (no curated ground truth per workflow type)
- No eval-triggered rollback (poor score doesn't auto-revert)
- No cross-version performance tracking (no trend line)
- No drift alerting (silent quality degradation)

Classes
-------
- ``GoldenExample``         — a ground-truth input/output pair
- ``GoldenDataset``         — a named, versioned collection of golden examples
- ``RegressionSuite``       — run an agent against a golden dataset, track scores
- ``VersionScoreHistory``   — per-agent time series of eval scores
- ``DriftAlerter``          — fires a callback when score drops below threshold
- ``EvalRollbackManager``   — integrates with VersionedWorkflow to auto-rollback
- ``LongitudinalEvalManager`` — high-level facade

Usage::

    from multigen.eval_longitudinal import LongitudinalEvalManager, GoldenExample

    mgr = LongitudinalEvalManager()
    ds = mgr.create_dataset("summariser-v1")
    ds.add(GoldenExample(input="Summarise: ...", expected="Short text."))

    suite = mgr.create_suite("summariser", dataset=ds, metric_fn=exact_match)
    report = await suite.run(my_agent)

    mgr.record(agent_name="summariser", version="v2", score=report.mean_score)
    if mgr.alerter.check("summariser"):
        print("Drift detected!")
"""
from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── GoldenExample / GoldenDataset ─────────────────────────────────────────────

@dataclass
class GoldenExample:
    input: Any
    expected: Any
    tags: List[str] = field(default_factory=list)
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)


class GoldenDataset:
    """
    A named, versioned collection of ground-truth examples.

    Usage::

        ds = GoldenDataset("qa-v1")
        ds.add(GoldenExample(input="What is 2+2?", expected="4"))
        subset = ds.filter(tags=["arithmetic"])
    """

    def __init__(self, name: str, version: str = "1.0") -> None:
        self.name = name
        self.version = version
        self._examples: List[GoldenExample] = []

    def add(self, example: GoldenExample) -> None:
        self._examples.append(example)

    def add_batch(self, examples: List[GoldenExample]) -> None:
        self._examples.extend(examples)

    def filter(self, tags: Optional[List[str]] = None) -> List[GoldenExample]:
        if not tags:
            return list(self._examples)
        return [e for e in self._examples if any(t in e.tags for t in tags)]

    def sample(self, n: int) -> List[GoldenExample]:
        import random
        return random.sample(self._examples, min(n, len(self._examples)))

    def __len__(self) -> int:
        return len(self._examples)


# ── RegressionResult ──────────────────────────────────────────────────────────

@dataclass
class RegressionResult:
    agent_name: str
    dataset_name: str
    version: str
    mean_score: float
    p50: float
    p95: float
    passed: int
    failed: int
    total: int
    elapsed_s: float
    timestamp: float = field(default_factory=time.time)

    @property
    def pass_rate(self) -> float:
        return self.passed / self.total if self.total else 0.0


# ── RegressionSuite ───────────────────────────────────────────────────────────

class RegressionSuite:
    """
    Runs an agent against a ``GoldenDataset`` and tracks history.

    The *metric_fn* is called with ``(output, expected) → float [0..1]``.
    A score ≥ *pass_threshold* marks the example as passed.

    Usage::

        suite = RegressionSuite(
            agent_name="summariser",
            dataset=golden_ds,
            metric_fn=lambda out, exp: 1.0 if out.strip() == exp.strip() else 0.0,
            pass_threshold=0.8,
        )
        result = await suite.run(my_agent, version="v3")
    """

    def __init__(
        self,
        agent_name: str,
        dataset: GoldenDataset,
        metric_fn: Callable,
        pass_threshold: float = 0.7,
        concurrency: int = 4,
    ) -> None:
        self.agent_name = agent_name
        self.dataset = dataset
        self.metric_fn = metric_fn
        self.pass_threshold = pass_threshold
        self.concurrency = concurrency
        self._history: List[RegressionResult] = []

    async def run(
        self,
        agent: Callable,
        version: str = "latest",
        tags: Optional[List[str]] = None,
    ) -> RegressionResult:
        start = time.monotonic()
        examples = self.dataset.filter(tags)
        sem = asyncio.Semaphore(self.concurrency)
        scores: List[float] = []
        passed = failed = 0

        async def eval_one(ex: GoldenExample) -> float:
            async with sem:
                try:
                    ctx = {"input": ex.input, "_expected": ex.expected}
                    out = agent(ctx)
                    if asyncio.iscoroutine(out):
                        out = await out
                    score = self.metric_fn(out, ex.expected)
                    if asyncio.iscoroutine(score):
                        score = await score
                    return float(score) * ex.weight
                except Exception:
                    return 0.0

        results = await asyncio.gather(*[eval_one(e) for e in examples])
        for s in results:
            scores.append(s)
            if s >= self.pass_threshold:
                passed += 1
            else:
                failed += 1

        sorted_scores = sorted(scores)
        n = len(sorted_scores)

        result = RegressionResult(
            agent_name=self.agent_name,
            dataset_name=self.dataset.name,
            version=version,
            mean_score=statistics.mean(scores) if scores else 0.0,
            p50=sorted_scores[n // 2] if n else 0.0,
            p95=sorted_scores[int(n * 0.95)] if n else 0.0,
            passed=passed,
            failed=failed,
            total=len(examples),
            elapsed_s=time.monotonic() - start,
        )
        self._history.append(result)
        return result

    def history(self) -> List[RegressionResult]:
        return list(self._history)

    def trend(self) -> List[Tuple[str, float]]:
        """Return ``[(version, mean_score), …]`` ordered chronologically."""
        return [(r.version, r.mean_score) for r in self._history]


# ── VersionScoreHistory ───────────────────────────────────────────────────────

@dataclass
class ScoreEntry:
    version: str
    score: float
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


class VersionScoreHistory:
    """
    Per-agent time series of evaluation scores across versions.

    Usage::

        history = VersionScoreHistory("summariser")
        history.record("v1", 0.82)
        history.record("v2", 0.78)
        print(history.trend())   # [("v1", 0.82), ("v2", 0.78)]
        print(history.delta())   # -0.04 (degraded)
    """

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        self._entries: List[ScoreEntry] = []

    def record(self, version: str, score: float, **metadata: Any) -> None:
        self._entries.append(
            ScoreEntry(version=version, score=score, metadata=metadata)
        )

    def trend(self) -> List[Tuple[str, float]]:
        return [(e.version, e.score) for e in self._entries]

    def latest(self) -> Optional[ScoreEntry]:
        return self._entries[-1] if self._entries else None

    def best(self) -> Optional[ScoreEntry]:
        return max(self._entries, key=lambda e: e.score) if self._entries else None

    def delta(self) -> Optional[float]:
        """Score change between last two versions. Negative = degraded."""
        if len(self._entries) < 2:
            return None
        return self._entries[-1].score - self._entries[-2].score

    def moving_average(self, window: int = 3) -> List[float]:
        scores = [e.score for e in self._entries]
        return [
            statistics.mean(scores[max(0, i - window): i + 1])
            for i in range(len(scores))
        ]


# ── DriftAlerter ──────────────────────────────────────────────────────────────

@dataclass
class DriftAlert:
    agent_name: str
    version: str
    score: float
    baseline: float
    drop: float
    timestamp: float = field(default_factory=time.time)


class DriftAlerter:
    """
    Fires an alert callback when an agent's score drops more than
    *threshold* points below its rolling *baseline*.

    Usage::

        def on_drift(alert):
            print(f"DRIFT: {alert.agent_name} dropped {alert.drop:.2%}")

        alerter = DriftAlerter(threshold=0.05, on_alert=on_drift)
        alerter.observe("summariser", "v3", score=0.65, baseline=0.82)
    """

    def __init__(
        self,
        threshold: float = 0.05,
        on_alert: Optional[Callable] = None,
    ) -> None:
        self.threshold = threshold
        self._on_alert = on_alert or (lambda a: None)
        self._alerts: List[DriftAlert] = []

    def observe(
        self,
        agent_name: str,
        version: str,
        score: float,
        baseline: float,
    ) -> Optional[DriftAlert]:
        drop = baseline - score
        if drop >= self.threshold:
            alert = DriftAlert(
                agent_name=agent_name,
                version=version,
                score=score,
                baseline=baseline,
                drop=drop,
            )
            self._alerts.append(alert)
            self._on_alert(alert)
            return alert
        return None

    def alerts(self) -> List[DriftAlert]:
        return list(self._alerts)


# ── EvalRollbackManager ───────────────────────────────────────────────────────

class EvalRollbackManager:
    """
    Integrates eval scores with workflow versioning to auto-rollback
    when a new version's score drops below the previous version's score
    by more than *rollback_threshold*.

    Usage::

        from multigen.versioning import VersionedWorkflow

        vw = VersionedWorkflow(...)
        erm = EvalRollbackManager(vw, rollback_threshold=0.05)

        ok = erm.evaluate_and_maybe_rollback(
            new_version="v3",
            new_score=0.70,
            previous_score=0.82,
        )
        # ok=False → workflow was rolled back to previous version
    """

    def __init__(
        self,
        versioned_workflow: Any,          # VersionedWorkflow
        rollback_threshold: float = 0.05,
        on_rollback: Optional[Callable] = None,
    ) -> None:
        self._vw = versioned_workflow
        self.rollback_threshold = rollback_threshold
        self._on_rollback = on_rollback or (lambda v: None)

    def evaluate_and_maybe_rollback(
        self,
        new_version: str,
        new_score: float,
        previous_score: float,
    ) -> bool:
        """
        Return True if *new_version* is acceptable.
        Return False (and rollback) if score dropped ≥ threshold.
        """
        drop = previous_score - new_score
        if drop >= self.rollback_threshold:
            try:
                history = self._vw.history()
                if len(history) >= 2:
                    self._vw.rollback(history[-2].version)
            except Exception:
                pass
            self._on_rollback(new_version)
            return False
        return True


# ── LongitudinalEvalManager ───────────────────────────────────────────────────

class LongitudinalEvalManager:
    """
    High-level facade for longitudinal evaluation management.

    Manages golden datasets, regression suites, version history, drift
    alerting, and eval-triggered rollback in one place.

    Usage::

        mgr = LongitudinalEvalManager()
        ds = mgr.create_dataset("qa-gold", version="1.0")
        ds.add(GoldenExample(input="...", expected="..."))

        suite = mgr.create_suite(
            "qa-agent",
            dataset=ds,
            metric_fn=lambda o, e: float(o == e),
        )
        result = await suite.run(agent, version="v2")
        mgr.record("qa-agent", version="v2", score=result.mean_score)

        alerts = mgr.check_drift("qa-agent")
    """

    def __init__(
        self,
        drift_threshold: float = 0.05,
        on_drift: Optional[Callable] = None,
    ) -> None:
        self._datasets: Dict[str, GoldenDataset] = {}
        self._suites: Dict[str, RegressionSuite] = {}
        self._histories: Dict[str, VersionScoreHistory] = {}
        self._alerter = DriftAlerter(threshold=drift_threshold, on_alert=on_drift)

    def create_dataset(self, name: str, version: str = "1.0") -> GoldenDataset:
        ds = GoldenDataset(name, version)
        self._datasets[name] = ds
        return ds

    def dataset(self, name: str) -> GoldenDataset:
        return self._datasets[name]

    def create_suite(
        self,
        agent_name: str,
        dataset: GoldenDataset,
        metric_fn: Callable,
        pass_threshold: float = 0.7,
    ) -> RegressionSuite:
        suite = RegressionSuite(agent_name, dataset, metric_fn, pass_threshold)
        self._suites[agent_name] = suite
        return suite

    def record(self, agent_name: str, version: str, score: float, **meta: Any) -> None:
        if agent_name not in self._histories:
            self._histories[agent_name] = VersionScoreHistory(agent_name)
        self._histories[agent_name].record(version, score, **meta)

    def check_drift(self, agent_name: str) -> Optional[DriftAlert]:
        history = self._histories.get(agent_name)
        if not history or len(history._entries) < 2:
            return None
        latest = history.latest()
        baseline = history.moving_average(window=3)[-2] if len(history._entries) >= 2 else 0.0
        return self._alerter.observe(agent_name, latest.version, latest.score, baseline)

    def trend(self, agent_name: str) -> List[Tuple[str, float]]:
        h = self._histories.get(agent_name)
        return h.trend() if h else []

    def suite(self, agent_name: str) -> Optional[RegressionSuite]:
        return self._suites.get(agent_name)


__all__ = [
    "GoldenExample",
    "GoldenDataset",
    "RegressionResult",
    "RegressionSuite",
    "ScoreEntry",
    "VersionScoreHistory",
    "DriftAlert",
    "DriftAlerter",
    "EvalRollbackManager",
    "LongitudinalEvalManager",
]
