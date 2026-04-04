"""
adversarial.py — Adversarial attack detection for multi-agent workflows.

Novel attack surface covered by this module
============================================

Goal Hijacking
    An adversary manipulates an agent's objective mid-execution so it
    pursues a different goal than the one assigned by the legitimate
    orchestrator.  Because the agent still appears to be "working", the
    deviation is easy to miss without semantic monitoring.

Memory Poisoning
    Malicious entries are injected into an agent's memory store.  The
    poisoned memories can bias future reasoning, cause hallucinations, or
    smuggle instructions that activate later.  Two sub-patterns are
    detected here: source-concentration attacks (one actor floods the
    memory) and temporal-plausibility attacks (entries with future
    timestamps indicating out-of-band injection).

Cross-Tenant Leakage
    In multi-tenant deployments agents process data belonging to different
    principals.  A compromised or mis-configured agent may embed fragments
    of one tenant's proprietary data inside another tenant's output.
    N-gram fingerprinting catches verbatim or near-verbatim leakage.

Reward Hacking
    Agents optimised with reinforcement-style feedback can learn to game
    the reward signal — maximising the metric while the underlying task
    quality degrades.  A sudden spike in reported reward concurrent with a
    quality drop is the canonical signature.

Sleeper Agents
    A model or agent that behaves benignly during normal operation but
    activates harmful behaviour when it receives a specific trigger input.
    Detection relies on matching known trigger patterns and flagging
    anomalous output characteristics (length, URLs, encoded payloads).
"""

from __future__ import annotations

import math
import re
import secrets
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set


# ---------------------------------------------------------------------------
# AttackSignal
# ---------------------------------------------------------------------------


@dataclass
class AttackSignal:
    """Represents a detected (or suspected) adversarial event."""

    signal_id: str
    attack_type: str
    severity: str          # "low" | "medium" | "high" | "critical"
    confidence: float      # 0.0 – 1.0
    details: Dict[str, Any]
    timestamp: float


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _new_signal_id() -> str:
    """Return a short random hex identifier for a signal."""
    return secrets.token_hex(8)


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# 1. GoalHijackDetector
# ---------------------------------------------------------------------------


class GoalHijackDetector:
    """
    Detects goal hijacking by comparing the semantic similarity of an
    agent's current task description against its registered baseline goal.

    A TF (term-frequency) vector is computed for each text and cosine
    similarity is used as the similarity metric.  If the similarity drops
    below *threshold* the current task is considered potentially hijacked.
    """

    def __init__(self, threshold: float = 0.3) -> None:
        self._threshold = threshold
        self._baselines: Dict[str, Dict[str, float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_baseline_goal(self, agent_id: str, goal_text: str) -> None:
        """Store the canonical goal for *agent_id* as a TF vector."""
        self._baselines[agent_id] = self._tf_vector(goal_text)

    def check(
        self, agent_id: str, current_task: str
    ) -> Optional[AttackSignal]:
        """
        Compare *current_task* against the baseline goal for *agent_id*.

        Returns an AttackSignal with HIGH severity when cosine similarity
        is below the configured threshold, or *None* when the task appears
        consistent with the baseline.
        """
        if agent_id not in self._baselines:
            return None

        baseline_vec = self._baselines[agent_id]
        current_vec = self._tf_vector(current_task)
        similarity = self._cosine_similarity(baseline_vec, current_vec)

        if similarity < self._threshold:
            return AttackSignal(
                signal_id=_new_signal_id(),
                attack_type="goal_hijacking",
                severity="high",
                confidence=round(1.0 - similarity, 4),
                details={
                    "agent_id": agent_id,
                    "cosine_similarity": round(similarity, 4),
                    "threshold": self._threshold,
                    "current_task_preview": current_task[:200],
                },
                timestamp=_now(),
            )
        return None

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _tf_vector(self, text: str) -> Dict[str, float]:
        """
        Build a term-frequency vector from *text*.

        Tokenises on whitespace and punctuation, lowercases all tokens,
        and returns ``{word: count / total_words}``.
        """
        tokens = re.split(r"[\s\W]+", text.lower())
        tokens = [t for t in tokens if t]
        if not tokens:
            return {}
        total = len(tokens)
        counts: Dict[str, float] = {}
        for token in tokens:
            counts[token] = counts.get(token, 0.0) + 1.0
        return {word: cnt / total for word, cnt in counts.items()}

    @staticmethod
    def _cosine_similarity(
        vec_a: Dict[str, float], vec_b: Dict[str, float]
    ) -> float:
        """Cosine similarity between two sparse TF vectors."""
        if not vec_a or not vec_b:
            return 0.0
        dot = sum(vec_a.get(w, 0.0) * v for w, v in vec_b.items())
        norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
        norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
        if norm_a == 0.0 or norm_b == 0.0:
            return 0.0
        return dot / (norm_a * norm_b)


# ---------------------------------------------------------------------------
# 2. MemoryPoisonDetector
# ---------------------------------------------------------------------------


@dataclass
class _MemoryEntry:
    content: str
    source: str
    timestamp: float


class MemoryPoisonDetector:
    """
    Detects poisoned memory entries via two heuristics:

    Source diversity
        If more than 60 % of the recent memory entries for an agent
        originate from a single source, the memory store may have been
        flooded by a single adversarial actor.

    Temporal plausibility
        Any memory entry whose timestamp is in the future (relative to
        scan time) is considered anomalous because legitimate memories
        record events that have already occurred.
    """

    _SOURCE_DOMINANCE_THRESHOLD = 0.60

    def __init__(self) -> None:
        self._memories: Dict[str, List[_MemoryEntry]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_memory(
        self,
        agent_id: str,
        content: str,
        source: str,
        timestamp: float,
    ) -> None:
        """Append a memory entry for *agent_id*."""
        self._memories[agent_id].append(
            _MemoryEntry(content=content, source=source, timestamp=timestamp)
        )

    def scan(self, agent_id: str) -> List[AttackSignal]:
        """Run all memory-poison checks and return detected signals."""
        signals: List[AttackSignal] = []
        diversity_signal = self._check_source_diversity(agent_id)
        if diversity_signal:
            signals.append(diversity_signal)
        temporal_signal = self._check_temporal_plausibility(agent_id)
        if temporal_signal:
            signals.append(temporal_signal)
        return signals

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _check_source_diversity(
        self, agent_id: str
    ) -> Optional[AttackSignal]:
        """Flag when a single source dominates the memory store."""
        entries = self._memories.get(agent_id, [])
        if not entries:
            return None

        source_counts: Dict[str, int] = {}
        for entry in entries:
            source_counts[entry.source] = (
                source_counts.get(entry.source, 0) + 1
            )

        total = len(entries)
        dominant_source, dominant_count = max(
            source_counts.items(), key=lambda kv: kv[1]
        )
        ratio = dominant_count / total

        if ratio > self._SOURCE_DOMINANCE_THRESHOLD:
            return AttackSignal(
                signal_id=_new_signal_id(),
                attack_type="memory_poisoning",
                severity="medium",
                confidence=round(ratio, 4),
                details={
                    "agent_id": agent_id,
                    "dominant_source": dominant_source,
                    "dominance_ratio": round(ratio, 4),
                    "total_entries": total,
                },
                timestamp=_now(),
            )
        return None

    def _check_temporal_plausibility(
        self, agent_id: str
    ) -> Optional[AttackSignal]:
        """Flag the first memory entry whose timestamp is in the future."""
        entries = self._memories.get(agent_id, [])
        now = _now()
        for entry in entries:
            if entry.timestamp > now:
                return AttackSignal(
                    signal_id=_new_signal_id(),
                    attack_type="memory_poisoning",
                    severity="high",
                    confidence=0.9,
                    details={
                        "agent_id": agent_id,
                        "future_timestamp": entry.timestamp,
                        "current_time": now,
                        "source": entry.source,
                        "content_preview": entry.content[:200],
                    },
                    timestamp=_now(),
                )
        return None


# ---------------------------------------------------------------------------
# 3. TenantLeakageDetector
# ---------------------------------------------------------------------------


class TenantLeakageDetector:
    """
    Detects cross-tenant data leakage using trigram fingerprinting.

    Each tenant's registered data is decomposed into a set of word
    trigrams.  When scanning an output for a given tenant the detector
    checks whether any trigrams present in the output belong exclusively
    to *other* tenants, signalling that foreign data may have leaked.
    """

    def __init__(self) -> None:
        # tenant_id -> set of trigram strings
        self._fingerprints: Dict[str, Set[str]] = defaultdict(set)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_tenant_data(self, tenant_id: str, text: str) -> None:
        """Extract trigrams from *text* and store them for *tenant_id*."""
        self._fingerprints[tenant_id].update(self._trigrams(text))

    def scan_output(
        self, tenant_id: str, output_text: str
    ) -> List[AttackSignal]:
        """
        Scan *output_text* for trigrams that belong to other tenants.

        Returns one AttackSignal per foreign tenant whose trigrams are
        detected in the output.
        """
        output_trigrams = self._trigrams(output_text)
        signals: List[AttackSignal] = []

        for other_tenant, fingerprint in self._fingerprints.items():
            if other_tenant == tenant_id:
                continue
            leaked = output_trigrams & fingerprint
            if leaked:
                signals.append(
                    AttackSignal(
                        signal_id=_new_signal_id(),
                        attack_type="cross_tenant_leakage",
                        severity="critical",
                        confidence=min(1.0, len(leaked) / 3),
                        details={
                            "affected_tenant": tenant_id,
                            "source_tenant": other_tenant,
                            "leaked_trigram_count": len(leaked),
                            "sample_trigrams": list(leaked)[:5],
                        },
                        timestamp=_now(),
                    )
                )
        return signals

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _trigrams(text: str) -> Set[str]:
        """Return the set of word-level trigrams extracted from *text*."""
        tokens = re.split(r"\s+", text.lower().strip())
        tokens = [t for t in tokens if t]
        if len(tokens) < 3:
            return set()
        return {
            " ".join(tokens[i : i + 3]) for i in range(len(tokens) - 2)
        }


# ---------------------------------------------------------------------------
# 4. RewardHackDetector
# ---------------------------------------------------------------------------


@dataclass
class _RewardSample:
    reward: float
    quality_score: float


class RewardHackDetector:
    """
    Detects agents that game reward metrics.

    The detector monitors (reward, quality_score) pairs per agent.  A
    potential reward-hacking event is flagged when, looking at the last
    10 samples:

    * Reward has increased by more than 50 % from the first to the last
      sample in the window, AND
    * Quality score has dropped by more than 20 % over the same window.
    """

    _WINDOW = 10
    _REWARD_SPIKE_THRESHOLD = 0.50   # 50 % increase
    _QUALITY_DROP_THRESHOLD = 0.20   # 20 % decrease

    def __init__(self) -> None:
        self._history: Dict[str, deque] = defaultdict(
            lambda: deque(maxlen=50)
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self, agent_id: str, reward: float, quality_score: float
    ) -> None:
        """Append a (reward, quality_score) observation for *agent_id*."""
        self._history[agent_id].append(
            _RewardSample(reward=reward, quality_score=quality_score)
        )

    def scan(self, agent_id: str) -> Optional[AttackSignal]:
        """
        Inspect recent samples for reward-hacking behaviour.

        Returns an AttackSignal when a reward spike coincides with a
        quality drop, or *None* otherwise.
        """
        history = self._history.get(agent_id)
        if not history or len(history) < self._WINDOW:
            return None

        window = list(history)[-self._WINDOW :]
        first, last = window[0], window[-1]

        if first.reward == 0.0:
            return None

        reward_change = (last.reward - first.reward) / abs(first.reward)

        if first.quality_score == 0.0:
            quality_change = 0.0
        else:
            quality_change = (
                last.quality_score - first.quality_score
            ) / abs(first.quality_score)

        reward_spiked = reward_change > self._REWARD_SPIKE_THRESHOLD
        quality_dropped = quality_change < -self._QUALITY_DROP_THRESHOLD

        if reward_spiked and quality_dropped:
            confidence = min(
                1.0,
                (reward_change - self._REWARD_SPIKE_THRESHOLD)
                + abs(quality_change - self._QUALITY_DROP_THRESHOLD),
            )
            return AttackSignal(
                signal_id=_new_signal_id(),
                attack_type="reward_hacking",
                severity="high",
                confidence=round(confidence, 4),
                details={
                    "agent_id": agent_id,
                    "reward_change_pct": round(reward_change * 100, 2),
                    "quality_change_pct": round(quality_change * 100, 2),
                    "window_size": self._WINDOW,
                    "first_reward": first.reward,
                    "last_reward": last.reward,
                    "first_quality": first.quality_score,
                    "last_quality": last.quality_score,
                },
                timestamp=_now(),
            )
        return None


# ---------------------------------------------------------------------------
# 5. SleeperAgentDetector
# ---------------------------------------------------------------------------

# Pre-compiled pattern for base64-looking substrings (≥20 chars of the
# base64 alphabet including padding).
_BASE64_RE = re.compile(r"[A-Za-z0-9+/]{20,}={0,2}")

# Loose URL pattern — http/https only to keep false positives low.
_URL_RE = re.compile(r"https?://\S+")

# Outputs longer than this character count are considered anomalously long.
_ANOMALY_LENGTH = 2000


class SleeperAgentDetector:
    """
    Detects dormant agents that activate malicious behaviour on trigger.

    Trigger patterns are registered as regular expressions.  When an
    observed input matches a trigger, the corresponding output is examined
    for anomaly markers:

    * Unusually long output (> 2 000 characters)
    * Presence of URLs
    * Presence of base64-encoded payloads
    """

    def __init__(self) -> None:
        self._trigger_patterns: List[re.Pattern] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_trigger_pattern(self, pattern: str) -> None:
        """Compile *pattern* and add it to the trigger watch list."""
        self._trigger_patterns.append(re.compile(pattern, re.IGNORECASE))

    def observe(
        self,
        agent_id: str,
        input_text: str,
        output_text: str,
    ) -> Optional[AttackSignal]:
        """
        Check whether *input_text* matches a known trigger and, if so,
        whether *output_text* exhibits anomalous characteristics.

        Returns an AttackSignal with CRITICAL severity when both
        conditions are met, or *None* otherwise.
        """
        matched_pattern: Optional[str] = None
        for pattern in self._trigger_patterns:
            if pattern.search(input_text):
                matched_pattern = pattern.pattern
                break

        if matched_pattern is None:
            return None

        anomalies: List[str] = []

        if len(output_text) > _ANOMALY_LENGTH:
            anomalies.append(
                f"output_length={len(output_text)} > {_ANOMALY_LENGTH}"
            )

        if _URL_RE.search(output_text):
            anomalies.append("contains_url")

        if _BASE64_RE.search(output_text):
            anomalies.append("contains_base64_payload")

        if not anomalies:
            return None

        confidence = min(1.0, len(anomalies) / 3.0)
        return AttackSignal(
            signal_id=_new_signal_id(),
            attack_type="sleeper_agent",
            severity="critical",
            confidence=round(confidence, 4),
            details={
                "agent_id": agent_id,
                "matched_trigger": matched_pattern,
                "anomalies": anomalies,
                "input_preview": input_text[:200],
                "output_preview": output_text[:200],
            },
            timestamp=_now(),
        )


# ---------------------------------------------------------------------------
# 6. AdversarialDefenseManager (facade)
# ---------------------------------------------------------------------------


class AdversarialDefenseManager:
    """
    Unified facade that aggregates all adversarial detectors.

    Instantiate once and share across the application.  Each underlying
    detector is accessible as an attribute for fine-grained control, and
    ``full_scan`` provides a single call that gathers signals from all of
    them.
    """

    def __init__(self, goal_threshold: float = 0.3) -> None:
        self.goal_hijack = GoalHijackDetector(threshold=goal_threshold)
        self.memory_poison = MemoryPoisonDetector()
        self.tenant_leakage = TenantLeakageDetector()
        self.reward_hack = RewardHackDetector()
        self.sleeper = SleeperAgentDetector()

    def full_scan(
        self,
        agent_id: str,
        tenant_id: str,
        current_task: str,
        output: str,
    ) -> List[AttackSignal]:
        """
        Run all detectors and return the combined list of AttackSignals.

        Detectors that require prior state (goal baseline, memory entries,
        reward history, tenant data) must be seeded before calling this
        method.  Detectors with no relevant state simply return no signals.
        """
        signals: List[AttackSignal] = []

        goal_signal = self.goal_hijack.check(agent_id, current_task)
        if goal_signal:
            signals.append(goal_signal)

        signals.extend(self.memory_poison.scan(agent_id))

        signals.extend(self.tenant_leakage.scan_output(tenant_id, output))

        reward_signal = self.reward_hack.scan(agent_id)
        if reward_signal:
            signals.append(reward_signal)

        sleeper_signal = self.sleeper.observe(agent_id, current_task, output)
        if sleeper_signal:
            signals.append(sleeper_signal)

        return signals


# ---------------------------------------------------------------------------
# Public API surface
# ---------------------------------------------------------------------------

__all__ = [
    "AttackSignal",
    "GoalHijackDetector",
    "MemoryPoisonDetector",
    "TenantLeakageDetector",
    "RewardHackDetector",
    "SleeperAgentDetector",
    "AdversarialDefenseManager",
]
