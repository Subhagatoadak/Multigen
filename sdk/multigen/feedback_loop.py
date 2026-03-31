"""
Closed-loop learning feedback primitives.

Problems solved
---------------
- No outcome ingestion API (no way to tell the system "this workflow won/lost")
- Delayed reward attribution (outcome arrives days after workflow)
- Cross-workflow signal aggregation (each workflow's learning is isolated)
- Human preference capture (thumbs-up/down beyond approval gate)
- Reward shaping pipeline (raw outcomes → reward signal)

Classes
-------
- ``Outcome``               — real-world result of a workflow run
- ``OutcomeStore``          — persists outcomes; matches to workflow runs
- ``DelayedRewardBuffer``   — holds pending outcomes until attribution resolves
- ``RewardShaper``          — raw outcome → normalised scalar reward (0..1)
- ``HumanFeedback``         — structured thumbs-up/down with annotation
- ``HumanFeedbackStore``    — collects human preferences
- ``CrossWorkflowAggregator`` — pools signals across workflows for bandit/learner
- ``FeedbackLoopManager``   — high-level facade that wires everything together

Usage::

    from multigen.feedback_loop import FeedbackLoopManager

    mgr = FeedbackLoopManager()

    # Ingest outcome right away
    mgr.ingest("run-abc", outcome_value=1.0, labels={"task": "summarise"})

    # Or buffer it for delayed attribution
    mgr.buffer("run-xyz", expected_delay_s=3600)
    # ... hours later ...
    mgr.resolve("run-xyz", outcome_value=0.3)

    # Human feedback
    mgr.record_human("run-abc", score=1, comment="Great summary!")

    # Get aggregated reward signal for an agent
    signal = mgr.aggregate_for_agent("summariser-v2")
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


# ── Outcome ───────────────────────────────────────────────────────────────────

@dataclass
class Outcome:
    """A real-world result of a workflow run."""
    run_id: str
    value: float                      # raw outcome (e.g. conversion=1.0, churn=0.0)
    labels: Dict[str, str] = field(default_factory=dict)
    agent_name: Optional[str] = None
    workflow_name: Optional[str] = None
    prompt_variant: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    delay_s: float = 0.0              # how long after the run this arrived


@dataclass
class PendingOutcome:
    """An outcome that has been registered but not yet resolved."""
    run_id: str
    registered_at: float = field(default_factory=time.time)
    expected_delay_s: float = 3600.0
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── OutcomeStore ──────────────────────────────────────────────────────────────

class OutcomeStore:
    """
    Stores and indexes resolved outcomes by run_id and agent name.

    Usage::

        store = OutcomeStore()
        store.add(Outcome(run_id="abc", value=1.0, agent_name="summariser"))
        outcomes = store.for_agent("summariser")
    """

    def __init__(self) -> None:
        self._outcomes: List[Outcome] = []

    def add(self, outcome: Outcome) -> None:
        self._outcomes.append(outcome)

    def for_run(self, run_id: str) -> List[Outcome]:
        return [o for o in self._outcomes if o.run_id == run_id]

    def for_agent(self, agent_name: str) -> List[Outcome]:
        return [o for o in self._outcomes if o.agent_name == agent_name]

    def for_workflow(self, workflow_name: str) -> List[Outcome]:
        return [o for o in self._outcomes if o.workflow_name == workflow_name]

    def all(self) -> List[Outcome]:
        return list(self._outcomes)

    def mean_value(self, agent_name: Optional[str] = None) -> Optional[float]:
        outcomes = self.for_agent(agent_name) if agent_name else self._outcomes
        values = [o.value for o in outcomes]
        return statistics.mean(values) if values else None


# ── DelayedRewardBuffer ───────────────────────────────────────────────────────

class DelayedRewardBuffer:
    """
    Holds pending outcomes until the real-world result is known.

    A run can be "buffered" at execution time, then "resolved" hours or
    days later when the outcome is known.  Expired pending entries (past
    ``expected_delay_s * 2``) are auto-cleaned.

    Usage::

        buf = DelayedRewardBuffer()
        buf.register("run-xyz", expected_delay_s=3600)
        # ... later ...
        outcome = buf.resolve("run-xyz", value=0.8, agent_name="analyser")
    """

    def __init__(self) -> None:
        self._pending: Dict[str, PendingOutcome] = {}
        self._resolved: List[Outcome] = []

    def register(
        self,
        run_id: str,
        expected_delay_s: float = 3600.0,
        **metadata: Any,
    ) -> PendingOutcome:
        entry = PendingOutcome(
            run_id=run_id,
            expected_delay_s=expected_delay_s,
            metadata=metadata,
        )
        self._pending[run_id] = entry
        return entry

    def resolve(
        self,
        run_id: str,
        value: float,
        agent_name: Optional[str] = None,
        **labels: str,
    ) -> Optional[Outcome]:
        pending = self._pending.pop(run_id, None)
        if pending is None:
            return None
        delay = time.time() - pending.registered_at
        outcome = Outcome(
            run_id=run_id,
            value=value,
            agent_name=agent_name,
            labels=labels,
            delay_s=delay,
        )
        self._resolved.append(outcome)
        return outcome

    def resolved(self) -> List[Outcome]:
        return list(self._resolved)

    def pending(self) -> List[PendingOutcome]:
        return list(self._pending.values())

    def expire_old(self) -> int:
        now = time.time()
        to_remove = [
            rid for rid, p in self._pending.items()
            if now - p.registered_at > p.expected_delay_s * 2
        ]
        for rid in to_remove:
            del self._pending[rid]
        return len(to_remove)


# ── RewardShaper ──────────────────────────────────────────────────────────────

class RewardShaper:
    """
    Transforms raw outcome values into normalised scalar rewards [0..1].

    Supports:
    - ``clip``    — simple min/max clipping
    - ``zscore``  — z-score normalisation over recent window
    - ``rank``    — rank-based normalisation (robust to outliers)
    - ``custom``  — supply your own transform function

    Usage::

        shaper = RewardShaper(mode="zscore", window=50)
        reward = shaper.shape(raw_value=42.7)
    """

    def __init__(
        self,
        mode: str = "clip",
        window: int = 100,
        min_val: float = 0.0,
        max_val: float = 1.0,
        custom_fn: Optional[Callable] = None,
    ) -> None:
        self._mode = mode
        self._window = window
        self._min_val = min_val
        self._max_val = max_val
        self._custom = custom_fn
        self._history: List[float] = []

    def shape(self, value: float) -> float:
        self._history.append(value)
        if len(self._history) > self._window:
            self._history.pop(0)

        if self._mode == "clip":
            span = self._max_val - self._min_val or 1.0
            return max(0.0, min(1.0, (value - self._min_val) / span))

        if self._mode == "zscore" and len(self._history) >= 2:
            mu = statistics.mean(self._history)
            sigma = statistics.stdev(self._history) or 1.0
            z = (value - mu) / sigma
            return max(0.0, min(1.0, (z + 3) / 6))  # map ±3σ → [0,1]

        if self._mode == "rank" and self._history:
            rank = sorted(self._history).index(value) + 1
            return rank / len(self._history)

        if self._mode == "custom" and self._custom:
            return float(self._custom(value))

        # fallback: clip
        span = self._max_val - self._min_val or 1.0
        return max(0.0, min(1.0, (value - self._min_val) / span))


# ── HumanFeedback ─────────────────────────────────────────────────────────────

@dataclass
class HumanFeedback:
    """Structured thumbs-up/down with optional annotation."""
    run_id: str
    reviewer_id: str
    score: int                  # +1 = thumbs up, -1 = thumbs down, 0 = neutral
    comment: str = ""
    flagged_outputs: List[str] = field(default_factory=list)
    labels: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    @property
    def positive(self) -> bool:
        return self.score > 0

    @property
    def normalized(self) -> float:
        return max(0.0, min(1.0, (self.score + 1) / 2))


class HumanFeedbackStore:
    """
    Collects and queries human feedback entries.

    Usage::

        hfs = HumanFeedbackStore()
        hfs.record(HumanFeedback(run_id="abc", reviewer_id="alice", score=1))
        mean = hfs.mean_score("abc")
    """

    def __init__(self) -> None:
        self._entries: List[HumanFeedback] = []

    def record(self, feedback: HumanFeedback) -> None:
        self._entries.append(feedback)

    def for_run(self, run_id: str) -> List[HumanFeedback]:
        return [e for e in self._entries if e.run_id == run_id]

    def mean_score(self, run_id: str) -> Optional[float]:
        entries = self.for_run(run_id)
        if not entries:
            return None
        return statistics.mean(e.normalized for e in entries)

    def flagged(self) -> List[HumanFeedback]:
        return [e for e in self._entries if e.flagged_outputs]

    def all(self) -> List[HumanFeedback]:
        return list(self._entries)


# ── CrossWorkflowAggregator ───────────────────────────────────────────────────

@dataclass
class AggregatedSignal:
    """Aggregated reward signal ready for a learner / bandit."""
    agent_name: str
    n_outcomes: int
    mean_reward: float
    std_reward: float
    n_human_positive: int
    n_human_negative: int
    human_agreement_rate: float


class CrossWorkflowAggregator:
    """
    Pools outcome signals and human feedback across workflows to produce
    a single ``AggregatedSignal`` per agent or prompt variant.

    Usage::

        agg = CrossWorkflowAggregator()
        agg.add_outcome(Outcome(run_id="x", value=0.9, agent_name="agent-a"))
        agg.add_human(HumanFeedback(run_id="x", reviewer_id="bob", score=1))
        sig = agg.signal("agent-a")
    """

    def __init__(self, shaper: Optional[RewardShaper] = None) -> None:
        self._shaper = shaper or RewardShaper()
        self._outcomes: List[Outcome] = []
        self._human: List[HumanFeedback] = []

    def add_outcome(self, outcome: Outcome) -> None:
        outcome = Outcome(
            run_id=outcome.run_id,
            value=self._shaper.shape(outcome.value),
            labels=outcome.labels,
            agent_name=outcome.agent_name,
            workflow_name=outcome.workflow_name,
            prompt_variant=outcome.prompt_variant,
            timestamp=outcome.timestamp,
            delay_s=outcome.delay_s,
        )
        self._outcomes.append(outcome)

    def add_human(self, feedback: HumanFeedback) -> None:
        self._human.append(feedback)

    def signal(self, agent_name: str) -> AggregatedSignal:
        outcomes = [o for o in self._outcomes if o.agent_name == agent_name]
        rewards = [o.value for o in outcomes]

        # Match human feedback to outcomes for this agent
        run_ids = {o.run_id for o in outcomes}
        human = [h for h in self._human if h.run_id in run_ids]
        pos = sum(1 for h in human if h.score > 0)
        neg = sum(1 for h in human if h.score < 0)
        total_human = pos + neg
        agreement = pos / total_human if total_human else 0.0

        return AggregatedSignal(
            agent_name=agent_name,
            n_outcomes=len(rewards),
            mean_reward=statistics.mean(rewards) if rewards else 0.0,
            std_reward=statistics.stdev(rewards) if len(rewards) > 1 else 0.0,
            n_human_positive=pos,
            n_human_negative=neg,
            human_agreement_rate=agreement,
        )

    def all_agent_names(self) -> List[str]:
        return list({o.agent_name for o in self._outcomes if o.agent_name})


# ── FeedbackLoopManager ───────────────────────────────────────────────────────

class FeedbackLoopManager:
    """
    High-level facade that wires OutcomeStore, DelayedRewardBuffer,
    HumanFeedbackStore, RewardShaper, and CrossWorkflowAggregator.

    Usage::

        mgr = FeedbackLoopManager()
        mgr.ingest("run-1", value=1.0, agent_name="qa-agent")
        mgr.buffer("run-2", expected_delay_s=86400)
        mgr.resolve("run-2", value=0.4, agent_name="qa-agent")
        mgr.record_human("run-1", reviewer="alice", score=1)
        sig = mgr.aggregate_for_agent("qa-agent")
    """

    def __init__(self, shaper_mode: str = "clip") -> None:
        self.store = OutcomeStore()
        self.buffer = DelayedRewardBuffer()
        self.human = HumanFeedbackStore()
        self.aggregator = CrossWorkflowAggregator(RewardShaper(mode=shaper_mode))

    def ingest(
        self,
        run_id: str,
        value: float,
        agent_name: Optional[str] = None,
        workflow_name: Optional[str] = None,
        **labels: str,
    ) -> Outcome:
        outcome = Outcome(
            run_id=run_id,
            value=value,
            agent_name=agent_name,
            workflow_name=workflow_name,
            labels=labels,
        )
        self.store.add(outcome)
        self.aggregator.add_outcome(outcome)
        return outcome

    def defer(self, run_id: str, expected_delay_s: float = 3600.0, **meta: Any) -> PendingOutcome:
        return self.buffer.register(run_id, expected_delay_s, **meta)

    def resolve(self, run_id: str, value: float, **kwargs: Any) -> Optional[Outcome]:
        outcome = self.buffer.resolve(run_id, value, **kwargs)
        if outcome:
            self.store.add(outcome)
            self.aggregator.add_outcome(outcome)
        return outcome

    def record_human(
        self,
        run_id: str,
        reviewer: str,
        score: int,
        comment: str = "",
        **labels: str,
    ) -> HumanFeedback:
        fb = HumanFeedback(
            run_id=run_id,
            reviewer_id=reviewer,
            score=score,
            comment=comment,
            labels=labels,
        )
        self.human.record(fb)
        self.aggregator.add_human(fb)
        return fb

    def aggregate_for_agent(self, agent_name: str) -> AggregatedSignal:
        return self.aggregator.signal(agent_name)


__all__ = [
    "Outcome",
    "PendingOutcome",
    "OutcomeStore",
    "DelayedRewardBuffer",
    "RewardShaper",
    "HumanFeedback",
    "HumanFeedbackStore",
    "AggregatedSignal",
    "CrossWorkflowAggregator",
    "FeedbackLoopManager",
]
