"""
Continuous learning for Multigen — agents that improve from feedback.

Features
--------
- ``FeedbackEntry``       — a single piece of feedback (rating/signal/correction)
- ``FeedbackStore``       — in-process or SQLite-backed feedback log
- ``ExperienceReplay``    — replay buffer for sampling past experiences
- ``FewShotSelector``     — pick best few-shot examples based on past performance
- ``AdaptivePrompt``      — prompt template that updates itself from feedback
- ``OnlineLearner``       — lightweight online learning loop (gradient-free)
- ``RewardSignal``        — RLHF-style reward signal tracker
- ``ContinuousLearner``   — orchestrates the full learning pipeline

All components are *model-agnostic* — they manage the data and selection
logic; the actual LLM call is always supplied by the caller.

Usage::

    from multigen.learning import (
        FeedbackEntry, FeedbackStore, FewShotSelector,
        AdaptivePrompt, ContinuousLearner, RewardSignal,
    )

    # 1. Collect feedback
    store = FeedbackStore()
    store.add(FeedbackEntry(
        input={"question": "What is AI?"},
        output="AI is...",
        score=0.9,
        label="good",
    ))

    # 2. Adaptive prompt
    prompt = AdaptivePrompt(
        base_template="Answer the following: {question}",
        store=store,
    )
    refined = await prompt.render({"question": "What is ML?"})

    # 3. Few-shot selector
    selector = FewShotSelector(store=store, n_shots=3)
    shots = selector.select({"question": "What is deep learning?"})

    # 4. Full continuous learner
    learner = ContinuousLearner(agent_fn=my_agent, store=store)
    result = await learner.run({"question": "What is GPT-4?"})
    await learner.record_feedback(result, score=0.85)
"""
from __future__ import annotations

import asyncio
import json
import math
import random
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── FeedbackEntry ──────────────────────────────────────────────────────────────

@dataclass
class FeedbackEntry:
    """
    A single feedback record tying an (input, output) pair to a quality signal.

    Attributes
    ----------
    input       The context/prompt passed to the agent.
    output      The agent's response.
    score       Numeric quality signal in [0, 1].  Higher is better.
    label       Optional categorical label (``"good"``, ``"bad"``, ``"neutral"``).
    correction  Optional human-provided correction for the output.
    tags        Arbitrary labels for filtering.
    metadata    Arbitrary extra data.
    """

    input: Any
    output: Any
    score: float = 0.5
    label: str = "neutral"
    correction: Optional[str] = None
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    timestamp: float = field(default_factory=time.time)
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "input": self.input,
            "output": self.output,
            "score": self.score,
            "label": self.label,
            "correction": self.correction,
            "timestamp": self.timestamp,
            "tags": self.tags,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "FeedbackEntry":
        return cls(
            input=d["input"],
            output=d["output"],
            score=d.get("score", 0.5),
            label=d.get("label", "neutral"),
            correction=d.get("correction"),
            entry_id=d.get("entry_id", str(uuid.uuid4())[:8]),
            timestamp=d.get("timestamp", time.time()),
            tags=d.get("tags", []),
            metadata=d.get("metadata", {}),
        )


# ── FeedbackStore ─────────────────────────────────────────────────────────────

class FeedbackStore:
    """
    In-memory feedback log with optional SQLite persistence.

    Usage::

        store = FeedbackStore()                       # in-memory
        store = FeedbackStore(db_path="feedback.db")  # durable

        store.add(FeedbackEntry(input={...}, output="...", score=0.9))
        recent = store.recent(n=20)
        good   = store.filter(min_score=0.7)
    """

    def __init__(self, db_path: Optional[str] = None) -> None:
        self._entries: List[FeedbackEntry] = []
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        if db_path:
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("""
                CREATE TABLE IF NOT EXISTS feedback (
                    entry_id  TEXT PRIMARY KEY,
                    data      TEXT NOT NULL,
                    score     REAL NOT NULL,
                    label     TEXT NOT NULL,
                    timestamp REAL NOT NULL
                )
            """)
            self._conn.commit()
            self._load_from_db()

    def _load_from_db(self) -> None:
        if self._conn is None:
            return
        cur = self._conn.execute("SELECT data FROM feedback ORDER BY timestamp ASC")
        for row in cur.fetchall():
            try:
                d = json.loads(row["data"])
                self._entries.append(FeedbackEntry.from_dict(d))
            except (json.JSONDecodeError, KeyError):
                pass

    def add(self, entry: FeedbackEntry) -> None:
        self._entries.append(entry)
        if self._conn is not None:
            try:
                serialized = json.dumps(entry.to_dict(), default=str)
            except (TypeError, ValueError):
                serialized = json.dumps({"entry_id": entry.entry_id, "error": "not serializable"})
            self._conn.execute(
                "INSERT OR REPLACE INTO feedback (entry_id, data, score, label, timestamp) VALUES (?,?,?,?,?)",
                (entry.entry_id, serialized, entry.score, entry.label, entry.timestamp),
            )
            self._conn.commit()

    def recent(self, n: int = 50) -> List[FeedbackEntry]:
        return self._entries[-n:]

    def filter(
        self,
        min_score: float = 0.0,
        max_score: float = 1.0,
        label: Optional[str] = None,
        tag: Optional[str] = None,
    ) -> List[FeedbackEntry]:
        result = []
        for e in self._entries:
            if not (min_score <= e.score <= max_score):
                continue
            if label is not None and e.label != label:
                continue
            if tag is not None and tag not in e.tags:
                continue
            result.append(e)
        return result

    def mean_score(self) -> float:
        if not self._entries:
            return 0.0
        return sum(e.score for e in self._entries) / len(self._entries)

    def score_trend(self, window: int = 20) -> float:
        """Return mean score of last *window* entries minus mean of preceding *window* entries."""
        if len(self._entries) < window * 2:
            return 0.0
        recent = self._entries[-window:]
        prior = self._entries[-window * 2: -window]
        return sum(e.score for e in recent) / window - sum(e.score for e in prior) / window

    def all(self) -> List[FeedbackEntry]:
        return list(self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def close(self) -> None:
        if self._conn:
            self._conn.close()


# ── ExperienceReplay ──────────────────────────────────────────────────────────

class ExperienceReplay:
    """
    Fixed-capacity replay buffer for sampling past experiences.

    Supports uniform sampling and **prioritised** sampling (experiences with
    higher absolute TD-error / score deviation get sampled more often).

    Usage::

        replay = ExperienceReplay(capacity=10_000)
        replay.push(FeedbackEntry(...))
        batch = replay.sample(32)
        batch_prio = replay.sample_prioritised(32)
    """

    def __init__(self, capacity: int = 10_000, seed: Optional[int] = None) -> None:
        self._buffer: List[FeedbackEntry] = []
        self.capacity = capacity
        self._rng = random.Random(seed)

    def push(self, entry: FeedbackEntry) -> None:
        if len(self._buffer) >= self.capacity:
            self._buffer.pop(0)
        self._buffer.append(entry)

    def push_all(self, entries: List[FeedbackEntry]) -> None:
        for e in entries:
            self.push(e)

    def sample(self, n: int) -> List[FeedbackEntry]:
        """Uniform random sample of *n* entries (with replacement if necessary)."""
        n = min(n, len(self._buffer))
        return self._rng.choices(self._buffer, k=n)

    def sample_prioritised(self, n: int, alpha: float = 0.6) -> List[FeedbackEntry]:
        """
        Prioritised sampling: entries with extreme scores (very good or very bad)
        are sampled with higher probability.
        """
        if not self._buffer:
            return []
        mean = sum(e.score for e in self._buffer) / len(self._buffer)
        weights = [abs(e.score - mean) ** alpha + 1e-6 for e in self._buffer]
        total = sum(weights)
        probs = [w / total for w in weights]
        n = min(n, len(self._buffer))
        indices = self._rng.choices(range(len(self._buffer)), weights=probs, k=n)
        return [self._buffer[i] for i in indices]

    def __len__(self) -> int:
        return len(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()


# ── FewShotSelector ───────────────────────────────────────────────────────────

class FewShotSelector:
    """
    Selects the best few-shot examples from the feedback store based on
    similarity to the current input and historical performance.

    Similarity is computed by shared token overlap (no embedding model required).
    Scores are used to rank examples — higher-scoring demonstrations are preferred.

    Usage::

        selector = FewShotSelector(store=store, n_shots=3, min_score=0.7)
        examples = selector.select({"question": "What is deep learning?"})
        # examples: List[FeedbackEntry] — ordered best-first
    """

    def __init__(
        self,
        store: FeedbackStore,
        n_shots: int = 3,
        min_score: float = 0.6,
        similarity_weight: float = 0.5,
        score_weight: float = 0.5,
    ) -> None:
        self._store = store
        self.n_shots = n_shots
        self.min_score = min_score
        self._sim_w = similarity_weight
        self._score_w = score_weight

    @staticmethod
    def _tokens(text: str) -> set:
        import re
        return set(re.sub(r"[^a-z0-9 ]", " ", text.lower()).split())

    def _similarity(self, a: Any, b: Any) -> float:
        ta = self._tokens(str(a))
        tb = self._tokens(str(b))
        if not ta or not tb:
            return 0.0
        return len(ta & tb) / len(ta | tb)

    def select(self, query_input: Any) -> List[FeedbackEntry]:
        candidates = self._store.filter(min_score=self.min_score)
        if not candidates:
            return []
        scored: List[Tuple[float, FeedbackEntry]] = []
        for entry in candidates:
            sim = self._similarity(query_input, entry.input)
            combined = self._sim_w * sim + self._score_w * entry.score
            scored.append((combined, entry))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[: self.n_shots]]

    def format_shots(
        self,
        examples: List[FeedbackEntry],
        input_key: str = "question",
        output_key: str = "answer",
    ) -> str:
        """Format selected examples as a few-shot prompt block."""
        lines: List[str] = []
        for i, ex in enumerate(examples, 1):
            inp = ex.input.get(input_key, str(ex.input)) if isinstance(ex.input, dict) else str(ex.input)
            out = ex.correction if ex.correction else str(ex.output)
            lines.append(f"Example {i}:\n{input_key}: {inp}\n{output_key}: {out}")
        return "\n\n".join(lines)


# ── AdaptivePrompt ─────────────────────────────────────────────────────────────

class AdaptivePrompt:
    """
    A prompt template that self-improves from the feedback store.

    On each call to ``render()`` it:
    1. Selects relevant high-scoring few-shot examples.
    2. Appends a ``Lessons learned`` block from low-scoring entries.
    3. Returns the enriched prompt string.

    Usage::

        prompt = AdaptivePrompt(
            base_template="Answer concisely: {question}",
            store=store,
            n_shots=2,
        )
        enriched = prompt.render({"question": "What is LoRA?"})
    """

    def __init__(
        self,
        base_template: str,
        store: FeedbackStore,
        n_shots: int = 2,
        include_lessons: bool = True,
    ) -> None:
        self._template = base_template
        self._selector = FewShotSelector(store=store, n_shots=n_shots)
        self._store = store
        self.include_lessons = include_lessons

    def render(self, variables: Dict[str, Any]) -> str:
        try:
            base = self._template.format(**variables)
        except KeyError:
            base = self._template

        sections: List[str] = []

        # Few-shot examples
        shots = self._selector.select(variables)
        if shots:
            example_block = self._selector.format_shots(shots)
            sections.append(f"Relevant examples:\n{example_block}")

        # Lessons from bad outputs
        if self.include_lessons:
            bad = self._store.filter(max_score=0.4)[-5:]
            if bad:
                lessons = []
                for e in bad:
                    if e.correction:
                        lessons.append(f"- Avoid: {e.output!r} → Prefer: {e.correction!r}")
                if lessons:
                    sections.append("Lessons learned:\n" + "\n".join(lessons))

        if sections:
            return "\n\n".join(sections) + "\n\n" + base
        return base


# ── RewardSignal ──────────────────────────────────────────────────────────────

class RewardSignal:
    """
    RLHF-style reward tracker — accumulates scores and computes running statistics.

    Can be used as a signal for selecting which agent / prompt / model to use.

    Usage::

        reward = RewardSignal(name="helpfulness")
        reward.record(0.9)
        reward.record(0.6)
        print(reward.mean())    # 0.75
        print(reward.trend())   # positive or negative trend
    """

    def __init__(self, name: str = "reward", window: int = 100) -> None:
        self.name = name
        self._window = window
        self._scores: List[float] = []

    def record(self, score: float) -> None:
        self._scores.append(max(0.0, min(1.0, score)))
        if len(self._scores) > self._window * 2:
            self._scores = self._scores[-self._window * 2:]

    def mean(self) -> float:
        if not self._scores:
            return 0.0
        return sum(self._scores) / len(self._scores)

    def trend(self) -> float:
        """Positive = improving, negative = degrading."""
        n = len(self._scores)
        if n < 2:
            return 0.0
        half = max(1, n // 2)
        recent = sum(self._scores[-half:]) / half
        prior = sum(self._scores[:half]) / half
        return recent - prior

    def stats(self) -> Dict[str, Any]:
        if not self._scores:
            return {"mean": 0.0, "trend": 0.0, "count": 0}
        return {"mean": self.mean(), "trend": self.trend(), "count": len(self._scores)}

    def __repr__(self) -> str:
        return f"RewardSignal({self.name!r}, mean={self.mean():.3f})"


# ── OnlineLearner ─────────────────────────────────────────────────────────────

class OnlineLearner:
    """
    Gradient-free online learner that adapts a prompt / config based on feedback.

    Uses a simple bandit-style algorithm (epsilon-greedy over prompt variants)
    to select the prompt variant with the best empirical reward.

    Usage::

        learner = OnlineLearner(
            variants={
                "concise": "Be concise. {question}",
                "detailed": "Explain in detail. {question}",
                "step_by_step": "Think step by step. {question}",
            },
            epsilon=0.1,
        )
        prompt = learner.select_prompt({"question": "What is attention?"})
        # … call LLM with prompt …
        learner.record("concise", score=0.85)
    """

    def __init__(
        self,
        variants: Dict[str, str],
        epsilon: float = 0.15,
        seed: Optional[int] = None,
    ) -> None:
        self._variants = variants
        self.epsilon = epsilon
        self._rng = random.Random(seed)
        self._rewards: Dict[str, RewardSignal] = {
            k: RewardSignal(name=k) for k in variants
        }

    def select_prompt(self, variables: Dict[str, Any]) -> Tuple[str, str]:
        """
        Return ``(variant_name, rendered_prompt)``.

        With probability *epsilon* picks a random variant (exploration),
        otherwise picks the best-performing variant (exploitation).
        """
        if self._rng.random() < self.epsilon:
            name = self._rng.choice(list(self._variants.keys()))
        else:
            name = max(self._rewards, key=lambda k: self._rewards[k].mean())
        template = self._variants[name]
        try:
            rendered = template.format(**variables)
        except KeyError:
            rendered = template
        return name, rendered

    def record(self, variant_name: str, score: float) -> None:
        if variant_name in self._rewards:
            self._rewards[variant_name].record(score)

    def stats(self) -> Dict[str, Any]:
        return {k: r.stats() for k, r in self._rewards.items()}

    def best_variant(self) -> str:
        return max(self._rewards, key=lambda k: self._rewards[k].mean())


# ── ContinuousLearner ──────────────────────────────────────────────────────────

class ContinuousLearner:
    """
    Orchestrates the full continuous learning pipeline:

    1. Select prompt via ``OnlineLearner`` (or ``AdaptivePrompt``).
    2. Run the agent.
    3. Record the feedback.
    4. Update the replay buffer.
    5. Periodically run a consolidation pass to retrain few-shot selection.

    Usage::

        learner = ContinuousLearner(
            agent_fn=my_async_agent,
            store=FeedbackStore("feedback.db"),
        )
        result = await learner.run({"question": "Explain transformers"})
        await learner.record_feedback(result, score=0.9)

        # After many runs:
        stats = learner.stats()
        print(stats["mean_score"])
    """

    def __init__(
        self,
        agent_fn: Callable,
        store: Optional[FeedbackStore] = None,
        replay: Optional[ExperienceReplay] = None,
        reward_signal: Optional[RewardSignal] = None,
        output_key: Optional[str] = None,
    ) -> None:
        self._agent = agent_fn
        self.store = store or FeedbackStore()
        self.replay = replay or ExperienceReplay(capacity=5000)
        self.reward = reward_signal or RewardSignal("agent")
        self._output_key = output_key
        self._run_count = 0

    async def run(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        """Run the agent and return ``{"input": ..., "output": ..., "ctx": ...}``."""
        if asyncio.iscoroutinefunction(self._agent):
            output = await self._agent(ctx)
        else:
            loop = asyncio.get_event_loop()
            output = await loop.run_in_executor(None, self._agent, ctx)

        if self._output_key and isinstance(output, dict):
            agent_output = output.get(self._output_key, output)
        else:
            agent_output = output

        self._run_count += 1
        return {"input": ctx, "output": agent_output, "ctx": ctx}

    async def record_feedback(
        self,
        run_result: Dict[str, Any],
        score: float,
        label: str = "auto",
        correction: Optional[str] = None,
        tags: Optional[List[str]] = None,
    ) -> FeedbackEntry:
        """
        Record feedback for a previous ``run()`` result.

        Parameters
        ----------
        run_result  Dict returned by ``run()``.
        score       Quality score in [0, 1].
        label       Categorical label (``"good"``, ``"bad"``, ``"neutral"``).
        correction  Optional corrected output.
        """
        if label == "auto":
            label = "good" if score >= 0.7 else ("bad" if score < 0.4 else "neutral")

        entry = FeedbackEntry(
            input=run_result.get("input"),
            output=run_result.get("output"),
            score=score,
            label=label,
            correction=correction,
            tags=tags or [],
        )
        self.store.add(entry)
        self.replay.push(entry)
        self.reward.record(score)
        return entry

    def stats(self) -> Dict[str, Any]:
        return {
            "run_count": self._run_count,
            "feedback_count": len(self.store),
            "mean_score": self.store.mean_score(),
            "score_trend": self.store.score_trend(),
            "reward": self.reward.stats(),
            "replay_size": len(self.replay),
        }

    def __repr__(self) -> str:
        return f"ContinuousLearner(runs={self._run_count}, feedback={len(self.store)})"


__all__ = [
    "AdaptivePrompt",
    "ContinuousLearner",
    "ExperienceReplay",
    "FeedbackEntry",
    "FeedbackStore",
    "FewShotSelector",
    "OnlineLearner",
    "RewardSignal",
]
