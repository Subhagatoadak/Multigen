"""
Behavioral fingerprinting and anomaly detection for multi-agent systems.

This module provides tools to monitor and detect behavioral drift in multi-agent pipelines:

- **Behavioral Drift Detection**: Tracks statistical properties of agent outputs over time
  (response length, vocabulary size, sentence structure) and compares against a saved
  baseline to detect significant shifts in agent behavior.

- **KL Divergence Monitoring**: Models agent output as a word-frequency distribution and
  uses Kullback-Leibler divergence (KL(P||Q)) to quantify how much the current output
  distribution has diverged from an established baseline distribution.

- **Latency Anomaly Detection**: Records per-agent response latencies and uses z-score
  analysis to flag individual responses that deviate significantly (|z| > 3.0) from the
  agent's historical latency profile.

- **Inter-Agent Communication Monitoring**: Tracks message flows between agents, detects
  pathological patterns such as excessive messaging between a pair, circular communication
  loops (A->B->A), and agents messaging themselves.
"""

import math
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import mean, stdev
from typing import Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Behavioral Fingerprint
# ---------------------------------------------------------------------------

@dataclass
class BehavioralFingerprint:
    """Statistical fingerprint of an agent's output behavior."""

    agent_id: str
    response_length_mean: float
    response_length_std: float
    vocabulary_size: int
    avg_sentence_count: float
    top_bigrams: List[Tuple[str, int]]
    computed_at: float
    sample_count: int


# ---------------------------------------------------------------------------
# Behavioral Profiler
# ---------------------------------------------------------------------------

@dataclass
class DriftResult:
    """Describes a detected behavioral drift on a single metric."""

    agent_id: str
    metric: str
    baseline_value: float
    current_value: float
    drift_magnitude: float


class BehavioralProfiler:
    """Collects agent output samples and detects statistical drift vs. a baseline."""

    _MAX_SAMPLES = 100

    def __init__(self) -> None:
        self._samples: Dict[str, List[str]] = defaultdict(list)
        self._baselines: Dict[str, BehavioralFingerprint] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def observe(self, agent_id: str, output_text: str) -> None:
        """Record a new output sample for *agent_id* (capped at last 100)."""
        buf = self._samples[agent_id]
        buf.append(output_text)
        if len(buf) > self._MAX_SAMPLES:
            self._samples[agent_id] = buf[-self._MAX_SAMPLES:]

    def build_fingerprint(self, agent_id: str) -> BehavioralFingerprint:
        """Compute a :class:`BehavioralFingerprint` from the collected samples."""
        samples = self._samples.get(agent_id, [])
        if not samples:
            return BehavioralFingerprint(
                agent_id=agent_id,
                response_length_mean=0.0,
                response_length_std=0.0,
                vocabulary_size=0,
                avg_sentence_count=0.0,
                top_bigrams=[],
                computed_at=time.time(),
                sample_count=0,
            )

        lengths = [len(s) for s in samples]
        length_mean = mean(lengths)
        length_std = stdev(lengths) if len(lengths) > 1 else 0.0

        all_words: List[str] = []
        sentence_counts: List[int] = []
        bigram_counter: Counter = Counter()

        for text in samples:
            words = text.lower().split()
            all_words.extend(words)
            sentences = [s.strip() for s in text.replace("!", ".").replace("?", ".").split(".") if s.strip()]
            sentence_counts.append(len(sentences))
            for w1, w2 in zip(words, words[1:]):
                bigram_counter[(w1, w2)] += 1

        vocab_size = len(set(all_words))
        avg_sentences = mean(sentence_counts) if sentence_counts else 0.0
        top_bigrams: List[Tuple[str, int]] = [
            (f"{w1} {w2}", cnt) for (w1, w2), cnt in bigram_counter.most_common(10)
        ]

        return BehavioralFingerprint(
            agent_id=agent_id,
            response_length_mean=length_mean,
            response_length_std=length_std,
            vocabulary_size=vocab_size,
            avg_sentence_count=avg_sentences,
            top_bigrams=top_bigrams,
            computed_at=time.time(),
            sample_count=len(samples),
        )

    def set_baseline(self, agent_id: str) -> None:
        """Save the current fingerprint as the baseline for *agent_id*."""
        self._baselines[agent_id] = self.build_fingerprint(agent_id)

    def compare(self, agent_id: str) -> Optional[DriftResult]:
        """Compare current fingerprint to baseline; return the largest drift found."""
        baseline = self._baselines.get(agent_id)
        if baseline is None:
            return None

        current = self.build_fingerprint(agent_id)
        candidates: List[DriftResult] = []

        def _check(metric: str, bval: float, cval: float) -> None:
            if bval == 0.0:
                magnitude = abs(cval)
            else:
                magnitude = abs(cval - bval) / abs(bval)
            if magnitude > 0.2:
                candidates.append(DriftResult(
                    agent_id=agent_id,
                    metric=metric,
                    baseline_value=bval,
                    current_value=cval,
                    drift_magnitude=magnitude,
                ))

        _check("response_length_mean", baseline.response_length_mean, current.response_length_mean)
        _check("response_length_std", baseline.response_length_std, current.response_length_std)
        _check("vocabulary_size", float(baseline.vocabulary_size), float(current.vocabulary_size))
        _check("avg_sentence_count", baseline.avg_sentence_count, current.avg_sentence_count)

        if not candidates:
            return None

        return max(candidates, key=lambda r: r.drift_magnitude)


# ---------------------------------------------------------------------------
# KL Divergence Monitor
# ---------------------------------------------------------------------------

@dataclass
class BehavioralDriftAlert:
    """Alert raised when KL divergence exceeds the configured threshold."""

    agent_id: str
    kl_divergence: float
    threshold: float
    timestamp: float


class KLDriftMonitor:
    """Monitors output token distribution drift using KL divergence."""

    _EPSILON = 1e-10

    def __init__(self) -> None:
        self._baselines: Dict[str, Dict[str, float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_baseline(self, agent_id: str, texts: List[str]) -> None:
        """Build and store a word-frequency baseline distribution for *agent_id*."""
        self._baselines[agent_id] = self._word_dist(texts)

    def check(
        self,
        agent_id: str,
        new_texts: List[str],
        threshold: float = 0.5,
    ) -> Optional[BehavioralDriftAlert]:
        """
        Compute KL(current || baseline).  Return a :class:`DriftAlert` if the
        divergence exceeds *threshold*, otherwise ``None``.
        """
        baseline = self._baselines.get(agent_id)
        if baseline is None:
            return None

        current = self._word_dist(new_texts)
        kl = self._kl_divergence(current, baseline)
        if kl > threshold:
            return BehavioralDriftAlert(
                agent_id=agent_id,
                kl_divergence=kl,
                threshold=threshold,
                timestamp=time.time(),
            )
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _word_dist(self, texts: List[str]) -> Dict[str, float]:
        """Return a normalised word-frequency distribution over *texts*."""
        counter: Counter = Counter()
        for text in texts:
            counter.update(text.lower().split())
        total = sum(counter.values())
        if total == 0:
            return {}
        return {word: count / total for word, count in counter.items()}

    def _kl_divergence(self, p: Dict[str, float], q: Dict[str, float]) -> float:
        """Compute KL(P || Q) with epsilon smoothing."""
        vocab = set(p) | set(q)
        result = 0.0
        for word in vocab:
            pi = p.get(word, 0.0) + self._EPSILON
            qi = q.get(word, 0.0) + self._EPSILON
            result += pi * math.log(pi / qi)
        return result


# ---------------------------------------------------------------------------
# Latency Anomaly Detector
# ---------------------------------------------------------------------------

@dataclass
class LatencyProfile:
    """Summary statistics for an agent's response latency distribution."""

    agent_id: str
    mean_ms: float
    std_ms: float
    p95_ms: float
    p99_ms: float
    sample_count: int


@dataclass
class LatencyAnomaly:
    """Flagged latency observation that deviates > 3 standard deviations."""

    agent_id: str
    latency_ms: float
    z_score: float
    timestamp: float


class LatencyAnomalyDetector:
    """Records per-agent latencies and flags anomalies via z-score (|z| > 3)."""

    _MAX_SAMPLES = 200
    _Z_THRESHOLD = 3.0

    def __init__(self) -> None:
        self._samples: Dict[str, List[float]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(self, agent_id: str, latency_ms: float) -> Optional[LatencyAnomaly]:
        """
        Store *latency_ms* for *agent_id*.  Return a :class:`LatencyAnomaly` if
        the z-score of the new observation exceeds the threshold; otherwise ``None``.
        """
        buf = self._samples[agent_id]
        buf.append(latency_ms)
        if len(buf) > self._MAX_SAMPLES:
            self._samples[agent_id] = buf[-self._MAX_SAMPLES:]

        if len(buf) < 2:
            return None

        mu = mean(buf)
        sigma = stdev(buf)
        if sigma == 0.0:
            return None

        z = (latency_ms - mu) / sigma
        if abs(z) > self._Z_THRESHOLD:
            return LatencyAnomaly(
                agent_id=agent_id,
                latency_ms=latency_ms,
                z_score=z,
                timestamp=time.time(),
            )
        return None

    def get_profile(self, agent_id: str) -> Optional[LatencyProfile]:
        """Return a :class:`LatencyProfile` for *agent_id*, or ``None`` if no data."""
        buf = self._samples.get(agent_id, [])
        if not buf:
            return None

        sorted_buf = sorted(buf)
        n = len(sorted_buf)

        def _percentile(data: List[float], pct: float) -> float:
            idx = (pct / 100.0) * (len(data) - 1)
            lo = int(idx)
            hi = min(lo + 1, len(data) - 1)
            frac = idx - lo
            return data[lo] + frac * (data[hi] - data[lo])

        return LatencyProfile(
            agent_id=agent_id,
            mean_ms=mean(sorted_buf),
            std_ms=stdev(sorted_buf) if n > 1 else 0.0,
            p95_ms=_percentile(sorted_buf, 95),
            p99_ms=_percentile(sorted_buf, 99),
            sample_count=n,
        )


# ---------------------------------------------------------------------------
# Inter-Agent Communication Monitor
# ---------------------------------------------------------------------------

@dataclass
class MessagePattern:
    """Observed communication pattern between two agents."""

    from_agent: str
    to_agent: str
    message_count: int
    last_seen: float
    avg_message_length: float


class InterAgentCommunicationMonitor:
    """Tracks messages flowing between agents and detects anomalous patterns."""

    def __init__(self) -> None:
        # Key: (from_agent, to_agent) -> list of message lengths
        self._messages: Dict[Tuple[str, str], List[int]] = defaultdict(list)
        self._last_seen: Dict[Tuple[str, str], float] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_message(self, from_agent: str, to_agent: str, content: str) -> None:
        """Record a message transmission between two agents."""
        key = (from_agent, to_agent)
        self._messages[key].append(len(content))
        self._last_seen[key] = time.time()

    def get_patterns(self) -> List[MessagePattern]:
        """Return all observed communication patterns as :class:`MessagePattern` objects."""
        patterns: List[MessagePattern] = []
        for (from_agent, to_agent), lengths in self._messages.items():
            patterns.append(MessagePattern(
                from_agent=from_agent,
                to_agent=to_agent,
                message_count=len(lengths),
                last_seen=self._last_seen.get((from_agent, to_agent), 0.0),
                avg_message_length=mean(lengths) if lengths else 0.0,
            ))
        return patterns

    def detect_anomalies(self, threshold_messages: int = 100) -> List[str]:
        """
        Identify anomalous communication patterns.

        Checks for:
        - Excessive messaging (> *threshold_messages*) between any pair.
        - Circular loops: A -> B -> A where both directions have messages.
        - Self-messaging: agent sends to itself.

        Returns a list of human-readable anomaly description strings.
        """
        anomalies: List[str] = []
        seen_pairs: set = set()

        for (from_agent, to_agent), lengths in self._messages.items():
            # Self-messaging
            if from_agent == to_agent:
                anomalies.append(
                    f"Self-messaging detected: agent '{from_agent}' sent "
                    f"{len(lengths)} message(s) to itself."
                )

            # Excessive messaging
            if len(lengths) > threshold_messages:
                anomalies.append(
                    f"Excessive messaging: '{from_agent}' -> '{to_agent}' "
                    f"({len(lengths)} messages, threshold={threshold_messages})."
                )

            # Circular loop A -> B -> A
            pair = tuple(sorted([from_agent, to_agent]))
            if pair not in seen_pairs and from_agent != to_agent:
                reverse_key = (to_agent, from_agent)
                if reverse_key in self._messages:
                    anomalies.append(
                        f"Circular communication loop detected: "
                        f"'{from_agent}' <-> '{to_agent}'."
                    )
                    seen_pairs.add(pair)

        return anomalies

    def communication_graph(self) -> Dict[str, List[str]]:
        """Return an adjacency list representing observed agent communications."""
        graph: Dict[str, List[str]] = defaultdict(list)
        for from_agent, to_agent in self._messages:
            if to_agent not in graph[from_agent]:
                graph[from_agent].append(to_agent)
        return dict(graph)


# ---------------------------------------------------------------------------
# Behavioral Anomaly Report
# ---------------------------------------------------------------------------

@dataclass
class BehavioralAnomalyReport:
    """Consolidated anomaly report across all behavioral dimensions."""

    report_id: str
    timestamp: float
    drift_alerts: List
    latency_anomalies: List
    communication_anomalies: List[str]
    risk_score: float


# ---------------------------------------------------------------------------
# BehavioralAnomalyDetector — facade
# ---------------------------------------------------------------------------

class BehavioralAnomalyDetector:
    """
    Facade that aggregates all behavioral monitoring subsystems and produces
    a unified :class:`BehavioralAnomalyReport`.
    """

    def __init__(self) -> None:
        self.profiler = BehavioralProfiler()
        self.kl_monitor = KLDriftMonitor()
        self.latency = LatencyAnomalyDetector()
        self.comm_monitor = InterAgentCommunicationMonitor()

    def full_report(self, agent_ids: List[str]) -> BehavioralAnomalyReport:
        """
        Generate a :class:`BehavioralAnomalyReport` for the given *agent_ids*.

        - Checks behavioral drift (vs. stored baselines) for each agent.
        - Collects any communication anomalies from the comm monitor.
        - Computes a ``risk_score`` in [0, 1] proportional to the number of
          issues found relative to the agents monitored.

        Note: latency anomalies are surfaced in real-time via
        :meth:`LatencyAnomalyDetector.record`; this report includes a
        ``latency_anomalies`` list that callers may populate externally and pass
        back if desired (stored as empty list here since anomalies are event-driven).
        """
        drift_alerts: List[DriftResult] = []
        for agent_id in agent_ids:
            result = self.profiler.compare(agent_id)
            if result is not None:
                drift_alerts.append(result)

        comm_anomalies = self.comm_monitor.detect_anomalies()

        total_issues = len(drift_alerts) + len(comm_anomalies)
        denominator = max(len(agent_ids), 1)
        risk_score = min(1.0, total_issues / (denominator * 2.0))

        return BehavioralAnomalyReport(
            report_id=str(uuid.uuid4()),
            timestamp=time.time(),
            drift_alerts=drift_alerts,
            latency_anomalies=[],
            communication_anomalies=comm_anomalies,
            risk_score=risk_score,
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "BehavioralFingerprint",
    "DriftResult",
    "BehavioralProfiler",
    "BehavioralDriftAlert",
    "KLDriftMonitor",
    "LatencyProfile",
    "LatencyAnomaly",
    "LatencyAnomalyDetector",
    "MessagePattern",
    "InterAgentCommunicationMonitor",
    "BehavioralAnomalyReport",
    "BehavioralAnomalyDetector",
]
