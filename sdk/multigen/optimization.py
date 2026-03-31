"""
Online prompt optimization, few-shot library management, agent specialisation,
and EpisodicMemory → prompt feedback loop.

Problems solved
---------------
- No online prompt optimization from workflow outcome signals
- No few-shot example library that improves over time
- No automatic agent specialisation based on performance history
- No feedback loop from EpisodicMemory → prompt templates

Classes
-------
- ``PromptVariant``         — a versioned prompt template with performance stats
- ``PromptBandit``          — epsilon-greedy multi-armed bandit over prompt variants
- ``FewShotLibrary``        — scored example store with retrieval by similarity
- ``FewShotLibraryEntry``   — a single few-shot example with score + metadata
- ``AgentSpecialisation``   — tracks per-topic performance to route to experts
- ``EpisodicFeedbackLoop``  — mines EpisodicMemory to extract few-shot examples
- ``OptimizationManager``   — unified façade combining all optimization components

Usage::

    from multigen.optimization import (
        PromptBandit, FewShotLibrary, AgentSpecialisation,
        EpisodicFeedbackLoop, OptimizationManager,
    )

    # Prompt bandit
    bandit = PromptBandit(["Be concise: {q}", "Think step by step: {q}"])
    variant = bandit.select()
    bandit.record(variant.id, reward=0.9)
    print(bandit.best_variant().template)

    # Few-shot library
    lib = FewShotLibrary(capacity=500)
    lib.add("What is AI?", "AI is artificial intelligence.")
    shots = lib.retrieve("Tell me about machine learning", n=3)

    # Agent specialisation
    spec = AgentSpecialisation(["agent_a", "agent_b", "agent_c"])
    spec.record("agent_a", topic="coding", score=0.9)
    best = spec.best_agent_for("coding")
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── PromptVariant / PromptBandit ───────────────────────────────────────────────

@dataclass
class PromptVariant:
    """A versioned prompt template with running performance statistics."""
    id: str
    template: str
    wins: int = 0
    losses: int = 0
    total_reward: float = 0.0
    pulls: int = 0
    created_at: float = field(default_factory=time.monotonic)

    @property
    def mean_reward(self) -> float:
        return self.total_reward / self.pulls if self.pulls > 0 else 0.0

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return self.wins / total if total > 0 else 0.5

    def render(self, **kwargs: Any) -> str:
        """Format the template with *kwargs*, falling back to the raw template."""
        try:
            return self.template.format(**kwargs)
        except KeyError:
            return self.template

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "template": self.template,
            "pulls": self.pulls,
            "mean_reward": self.mean_reward,
            "win_rate": self.win_rate,
        }


class PromptBandit:
    """
    Epsilon-greedy multi-armed bandit over prompt variants.

    Automatically explores underused variants while exploiting high-reward ones.

    Parameters
    ----------
    templates   Initial list of prompt template strings
    epsilon     Exploration probability (0 = always exploit, 1 = always explore)
    ucb         If True, use UCB1 instead of epsilon-greedy (more principled)

    Usage::

        bandit = PromptBandit(["Summarise: {text}", "Give a brief summary of: {text}"])
        variant = bandit.select()
        prompt = variant.render(text="long document...")
        # ... call LLM, get score ...
        bandit.record(variant.id, reward=0.85)
        best = bandit.best_variant()
    """

    def __init__(
        self,
        templates: List[str],
        epsilon: float = 0.15,
        ucb: bool = False,
    ) -> None:
        self._variants: Dict[str, PromptVariant] = {}
        self.epsilon = epsilon
        self.ucb = ucb
        self._total_pulls = 0
        for t in templates:
            self.add_variant(t)

    def add_variant(self, template: str, variant_id: Optional[str] = None) -> PromptVariant:
        vid = variant_id or hashlib.md5(template.encode()).hexdigest()[:8]
        v = PromptVariant(id=vid, template=template)
        self._variants[vid] = v
        return v

    def select(self) -> PromptVariant:
        """Select a variant using the configured strategy."""
        variants = list(self._variants.values())
        if not variants:
            raise RuntimeError("PromptBandit: no variants registered")

        # Always pull unpulled variants first
        unpulled = [v for v in variants if v.pulls == 0]
        if unpulled:
            return unpulled[0]

        if self.ucb:
            return self._ucb_select(variants)

        if random.random() < self.epsilon:
            return random.choice(variants)
        return max(variants, key=lambda v: v.mean_reward)

    def _ucb_select(self, variants: List[PromptVariant]) -> PromptVariant:
        best, best_score = variants[0], -1.0
        for v in variants:
            score = v.mean_reward + math.sqrt(
                2 * math.log(max(1, self._total_pulls)) / max(1, v.pulls)
            )
            if score > best_score:
                best_score = score
                best = v
        return best

    def record(self, variant_id: str, reward: float) -> None:
        """Record a reward (0..1) for the chosen variant."""
        v = self._variants.get(variant_id)
        if v is None:
            return
        v.pulls += 1
        v.total_reward += reward
        self._total_pulls += 1
        if reward >= 0.5:
            v.wins += 1
        else:
            v.losses += 1

    def best_variant(self) -> PromptVariant:
        return max(self._variants.values(), key=lambda v: v.mean_reward)

    def stats(self) -> List[Dict[str, Any]]:
        return sorted(
            [v.to_dict() for v in self._variants.values()],
            key=lambda d: d["mean_reward"],
            reverse=True,
        )

    def variants(self) -> List[PromptVariant]:
        return list(self._variants.values())


# ── FewShotLibrary ────────────────────────────────────────────────────────────

@dataclass
class FewShotLibraryEntry:
    """A single stored few-shot example."""
    input: str
    output: str
    score: float = 1.0
    tags: List[str] = field(default_factory=list)
    used_count: int = 0
    created_at: float = field(default_factory=time.monotonic)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "input": self.input,
            "output": self.output,
            "score": self.score,
            "tags": self.tags,
            "used_count": self.used_count,
        }


class FewShotLibrary:
    """
    A growing, scored few-shot example store with similarity-based retrieval.

    Retrieval uses token-overlap (Jaccard similarity) as a zero-dependency
    proxy for semantic similarity.  Replace ``_similarity`` to plug in a
    vector embedding model.

    Parameters
    ----------
    capacity    Maximum number of examples stored (oldest by score evicted)
    min_score   Minimum score to admit an example

    Usage::

        lib = FewShotLibrary(capacity=200)
        lib.add("What is ML?", "ML is machine learning.", score=0.95)
        examples = lib.retrieve("Explain deep learning", n=3)
        for ex in examples:
            print(ex.input, "→", ex.output)
    """

    def __init__(self, capacity: int = 500, min_score: float = 0.0) -> None:
        self.capacity = capacity
        self.min_score = min_score
        self._entries: List[FewShotLibraryEntry] = []

    def add(
        self,
        input_text: str,
        output_text: str,
        score: float = 1.0,
        tags: Optional[List[str]] = None,
    ) -> Optional[FewShotLibraryEntry]:
        """Add an example to the library.  Returns the entry or None if rejected."""
        if score < self.min_score:
            return None
        entry = FewShotLibraryEntry(
            input=input_text,
            output=output_text,
            score=score,
            tags=tags or [],
        )
        self._entries.append(entry)
        if len(self._entries) > self.capacity:
            # Evict lowest-scoring entry
            self._entries.sort(key=lambda e: e.score, reverse=True)
            self._entries = self._entries[: self.capacity]
        return entry

    def retrieve(
        self,
        query: str,
        n: int = 3,
        min_score: Optional[float] = None,
        tags: Optional[List[str]] = None,
    ) -> List[FewShotLibraryEntry]:
        """Return the *n* most similar examples to *query*."""
        entries = self._entries
        if min_score is not None:
            entries = [e for e in entries if e.score >= min_score]
        if tags:
            tag_set = set(tags)
            entries = [e for e in entries if tag_set & set(e.tags)]

        scored = sorted(
            entries,
            key=lambda e: self._similarity(query, e.input) * e.score,
            reverse=True,
        )
        selected = scored[:n]
        for e in selected:
            e.used_count += 1
        return selected

    def format_shots(self, examples: List[FewShotLibraryEntry]) -> str:
        """Format examples as a few-shot block for prompt injection."""
        lines = []
        for i, ex in enumerate(examples, 1):
            lines.append(f"Example {i}:")
            lines.append(f"  Input:  {ex.input}")
            lines.append(f"  Output: {ex.output}")
        return "\n".join(lines)

    def update_score(self, input_text: str, delta: float) -> None:
        """Adjust the score of entries matching *input_text*."""
        for e in self._entries:
            if e.input == input_text:
                e.score = max(0.0, min(1.0, e.score + delta))

    def size(self) -> int:
        return len(self._entries)

    @staticmethod
    def _similarity(a: str, b: str) -> float:
        """Token-level Jaccard similarity."""
        sa = set(a.lower().split())
        sb = set(b.lower().split())
        if not sa and not sb:
            return 1.0
        return len(sa & sb) / max(1, len(sa | sb))


# ── AgentSpecialisation ───────────────────────────────────────────────────────

class AgentSpecialisation:
    """
    Tracks per-topic performance to route to the best specialist agent.

    Each agent accumulates an exponentially weighted moving average (EWMA)
    score per topic.  ``best_agent_for(topic)`` returns the agent with the
    highest score for that topic, falling back to global average if no
    topic-specific data exists.

    Usage::

        spec = AgentSpecialisation(["coder", "analyst", "writer"])
        spec.record("coder",   "code_generation", score=0.92)
        spec.record("analyst", "data_analysis",   score=0.87)
        spec.record("writer",  "code_generation", score=0.60)

        best = spec.best_agent_for("code_generation")
        print(best)   # "coder"
    """

    def __init__(
        self,
        agent_names: List[str],
        alpha: float = 0.1,    # EWMA smoothing
    ) -> None:
        self._agents = agent_names
        self.alpha = alpha
        # {agent_name: {topic: ewma_score}}
        self._scores: Dict[str, Dict[str, float]] = {a: {} for a in agent_names}
        self._counts: Dict[str, Dict[str, int]] = {a: {} for a in agent_names}

    def record(self, agent: str, topic: str, score: float) -> None:
        """Record an outcome score for *agent* on *topic*."""
        if agent not in self._scores:
            self._scores[agent] = {}
            self._counts[agent] = {}
        current = self._scores[agent].get(topic, score)
        self._scores[agent][topic] = (
            self.alpha * score + (1 - self.alpha) * current
        )
        self._counts[agent][topic] = self._counts[agent].get(topic, 0) + 1

    def best_agent_for(self, topic: str) -> str:
        """Return the agent name with the highest score for *topic*."""
        scores = []
        for agent in self._agents:
            topic_score = self._scores[agent].get(topic)
            if topic_score is None:
                # Fall back to mean across all topics
                all_scores = list(self._scores[agent].values())
                topic_score = sum(all_scores) / len(all_scores) if all_scores else 0.5
            scores.append((agent, topic_score))
        return max(scores, key=lambda x: x[1])[0]

    def rankings(self, topic: str) -> List[Tuple[str, float]]:
        """Return all agents sorted by their score for *topic* (best first)."""
        result = []
        for agent in self._agents:
            s = self._scores[agent].get(topic, 0.5)
            result.append((agent, s))
        return sorted(result, key=lambda x: x[1], reverse=True)

    def profile(self, agent: str) -> Dict[str, Any]:
        """Return performance profile for *agent*."""
        scores = self._scores.get(agent, {})
        counts = self._counts.get(agent, {})
        return {
            "agent": agent,
            "topics": {
                topic: {"score": scores[topic], "count": counts.get(topic, 0)}
                for topic in scores
            },
        }


# ── EpisodicFeedbackLoop ──────────────────────────────────────────────────────

class EpisodicFeedbackLoop:
    """
    Mines an ``EpisodicMemory`` store to extract high-quality few-shot
    examples and improve prompt templates over time.

    Assumes episodes have the shape::

        episode.content = {
            "input":  str,           # the query / task
            "output": str,           # the agent response
            "score":  float (0..1),  # optional quality score
        }

    Parameters
    ----------
    episodic_memory     An ``EpisodicMemory`` instance (or any object with
                        ``recent(n)`` returning a list of episodes).
    library             ``FewShotLibrary`` to populate with mined examples.
    bandit              ``PromptBandit`` to update with episode rewards.
    min_score           Only mine episodes with score ≥ this threshold.
    mine_every_n        Mine after every N new episodes (lazy evaluation).

    Usage::

        loop = EpisodicFeedbackLoop(
            episodic_memory=my_memory,
            library=my_library,
            bandit=my_bandit,
            min_score=0.7,
        )
        loop.process_episode(episode)
        loop.mine()
    """

    def __init__(
        self,
        episodic_memory: Any,
        library: FewShotLibrary,
        bandit: Optional[PromptBandit] = None,
        min_score: float = 0.6,
        mine_every_n: int = 10,
    ) -> None:
        self._memory = episodic_memory
        self._library = library
        self._bandit = bandit
        self.min_score = min_score
        self.mine_every_n = mine_every_n
        self._episode_count = 0
        self._mined_count = 0

    def process_episode(self, episode: Any) -> None:
        """Process a single episode, optionally triggering a mine."""
        self._episode_count += 1
        content = getattr(episode, "content", {})
        if not isinstance(content, dict):
            return

        score = float(content.get("score", 1.0))
        input_text = content.get("input", "")
        output_text = content.get("output", "")

        if score >= self.min_score and input_text and output_text:
            self._library.add(input_text, output_text, score=score)
            self._mined_count += 1
            logger.debug("EpisodicFeedbackLoop: mined episode score=%.2f", score)

        # Update bandit if variant_id is recorded in episode metadata
        if self._bandit:
            meta = content.get("metadata", {})
            variant_id = meta.get("prompt_variant_id")
            if variant_id:
                self._bandit.record(variant_id, score)

    def mine(self, n: int = 100) -> int:
        """
        Re-scan the last *n* episodes from EpisodicMemory.
        Returns the number of examples added.
        """
        before = self._library.size()
        try:
            episodes = self._memory.recent(n)
        except Exception:
            return 0
        for ep in episodes:
            self.process_episode(ep)
        return self._library.size() - before

    def stats(self) -> Dict[str, Any]:
        return {
            "episodes_processed": self._episode_count,
            "examples_mined": self._mined_count,
            "library_size": self._library.size(),
        }


# ── OptimizationManager ───────────────────────────────────────────────────────

class OptimizationManager:
    """
    Unified façade combining prompt bandit, few-shot library, agent
    specialisation, and episodic feedback loop.

    Usage::

        mgr = OptimizationManager(
            prompt_templates=["Answer: {q}", "Think carefully about: {q}"],
            agent_names=["fast_agent", "quality_agent"],
            episodic_memory=my_memory,
        )

        # At inference time:
        variant = mgr.select_prompt()
        shots   = mgr.get_few_shots(ctx["query"])
        agent   = mgr.best_agent(ctx.get("topic", "general"))

        # After execution:
        mgr.record_outcome(
            variant_id=variant.id,
            agent=agent,
            topic=ctx.get("topic", "general"),
            score=outcome_score,
            input_text=ctx["query"],
            output_text=response,
        )
    """

    def __init__(
        self,
        prompt_templates: Optional[List[str]] = None,
        agent_names: Optional[List[str]] = None,
        episodic_memory: Optional[Any] = None,
        library_capacity: int = 500,
        bandit_epsilon: float = 0.15,
    ) -> None:
        self.bandit = PromptBandit(prompt_templates or [], epsilon=bandit_epsilon)
        self.library = FewShotLibrary(capacity=library_capacity)
        self.specialisation = AgentSpecialisation(agent_names or [])
        self.feedback_loop = EpisodicFeedbackLoop(
            episodic_memory=episodic_memory or _NullMemory(),
            library=self.library,
            bandit=self.bandit,
        ) if True else None

    def select_prompt(self, **ctx_kwargs: Any) -> PromptVariant:
        """Select a prompt variant using the bandit strategy."""
        return self.bandit.select()

    def get_few_shots(
        self,
        query: str,
        n: int = 3,
        tags: Optional[List[str]] = None,
    ) -> List[FewShotLibraryEntry]:
        """Retrieve the top-*n* few-shot examples for *query*."""
        return self.library.retrieve(query, n=n, tags=tags)

    def best_agent(self, topic: str) -> str:
        """Return the best specialist agent name for *topic*."""
        return self.specialisation.best_agent_for(topic)

    def record_outcome(
        self,
        score: float,
        variant_id: Optional[str] = None,
        agent: Optional[str] = None,
        topic: str = "general",
        input_text: str = "",
        output_text: str = "",
    ) -> None:
        """Record a workflow outcome and update all components."""
        if variant_id:
            self.bandit.record(variant_id, score)
        if agent:
            self.specialisation.record(agent, topic, score)
        if input_text and output_text and score >= self.library.min_score:
            self.library.add(input_text, output_text, score=score)

    def stats(self) -> Dict[str, Any]:
        return {
            "bandit": self.bandit.stats(),
            "library_size": self.library.size(),
            "feedback_loop": self.feedback_loop.stats() if self.feedback_loop else {},
        }


class _NullMemory:
    """Stub for when no episodic memory is provided."""
    def recent(self, n: int) -> List[Any]:
        return []


__all__ = [
    "PromptVariant",
    "PromptBandit",
    "FewShotLibraryEntry",
    "FewShotLibrary",
    "AgentSpecialisation",
    "EpisodicFeedbackLoop",
    "OptimizationManager",
]
