"""Multi-tier agent memory system."""
from __future__ import annotations

import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── Memory Entry ──────────────────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    """A single stored value with metadata."""

    key: str
    value: Any
    created_at: float = field(default_factory=time.time)
    accessed_at: float = field(default_factory=time.time)
    ttl: Optional[float] = None        # seconds; None = immortal
    tags: List[str] = field(default_factory=list)
    access_count: int = 0
    entry_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def is_expired(self) -> bool:
        return self.ttl is not None and time.time() > self.created_at + self.ttl

    def touch(self) -> None:
        self.accessed_at = time.time()
        self.access_count += 1


# ── Short-Term Memory ─────────────────────────────────────────────────────────

class ShortTermMemory:
    """
    Session-scoped in-memory store with optional TTL and LRU eviction.

    Ideal for: current task context, intermediate results, scratchpad values.

    Usage::

        mem = ShortTermMemory(capacity=500, default_ttl=300)
        mem.store("user_query", "What is the capital of France?", tags=["input"])
        value = mem.retrieve("user_query")
        recent = mem.search_by_tags(["input"])
    """

    def __init__(
        self,
        capacity: int = 1000,
        default_ttl: Optional[float] = None,
    ) -> None:
        self._store: Dict[str, MemoryEntry] = {}
        self.capacity = capacity
        self.default_ttl = default_ttl

    def store(
        self,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        self._evict_expired()
        if len(self._store) >= self.capacity:
            self._evict_lru()
        self._store[key] = MemoryEntry(
            key=key,
            value=value,
            ttl=ttl if ttl is not None else self.default_ttl,
            tags=tags or [],
        )

    def retrieve(self, key: str) -> Optional[Any]:
        entry = self._store.get(key)
        if entry is None or entry.is_expired:
            self._store.pop(key, None)
            return None
        entry.touch()
        return entry.value

    def delete(self, key: str) -> bool:
        return self._store.pop(key, None) is not None

    def search_by_tags(self, tags: List[str]) -> List[Tuple[str, Any]]:
        """Return (key, value) pairs whose entry has at least one matching tag."""
        return [
            (k, e.value)
            for k, e in self._store.items()
            if not e.is_expired and any(t in e.tags for t in tags)
        ]

    def keys(self) -> List[str]:
        self._evict_expired()
        return list(self._store.keys())

    def clear(self) -> None:
        self._store.clear()

    def stats(self) -> Dict[str, Any]:
        valid = {k: e for k, e in self._store.items() if not e.is_expired}
        return {
            "total": len(valid),
            "capacity": self.capacity,
            "utilization": len(valid) / self.capacity if self.capacity else 0.0,
        }

    def __len__(self) -> int:
        self._evict_expired()
        return len(self._store)

    def _evict_expired(self) -> None:
        expired = [k for k, e in self._store.items() if e.is_expired]
        for k in expired:
            del self._store[k]

    def _evict_lru(self) -> None:
        if not self._store:
            return
        lru_key = min(self._store, key=lambda k: self._store[k].accessed_at)
        del self._store[lru_key]


# ── Episodic Memory ───────────────────────────────────────────────────────────

@dataclass
class Episode:
    """A single recorded agent interaction."""

    agent: str
    inputs: Dict[str, Any]
    outputs: Dict[str, Any]
    episode_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=time.time)
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


class EpisodicMemory:
    """
    Ordered record of agent interaction episodes — analogous to autobiographical memory.

    Supports recency retrieval, per-agent filtering, and keyword search.

    Usage::

        mem = EpisodicMemory(max_episodes=200)
        mem.record("ResearchAgent", inputs={"query": "..."}, outputs={"answer": "..."})
        recent = mem.recent(n=5)
        by_agent = mem.by_agent("ResearchAgent")
    """

    def __init__(self, max_episodes: int = 500) -> None:
        self._episodes: deque[Episode] = deque(maxlen=max_episodes)

    def record(
        self,
        agent: str,
        inputs: Dict[str, Any],
        outputs: Dict[str, Any],
        tags: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        ep = Episode(
            agent=agent,
            inputs=inputs,
            outputs=outputs,
            tags=tags or [],
            metadata=metadata or {},
        )
        self._episodes.append(ep)
        return ep.episode_id

    def recent(self, n: int = 10) -> List[Episode]:
        return list(self._episodes)[-n:]

    def by_agent(self, agent: str) -> List[Episode]:
        return [e for e in self._episodes if e.agent == agent]

    def search(self, query: str) -> List[Episode]:
        """Keyword search over inputs, outputs, and tags (case-insensitive)."""
        q = query.lower()
        return [
            e for e in self._episodes
            if q in (str(e.inputs) + str(e.outputs) + str(e.tags)).lower()
        ]

    def clear(self) -> None:
        self._episodes.clear()

    def __len__(self) -> int:
        return len(self._episodes)


# ── Working Memory ────────────────────────────────────────────────────────────

class WorkingMemory:
    """
    Bounded sliding window of recent items — analogous to a cognitive context window.

    Ideal for: maintaining a rolling conversation history or recent tool results
    that should be visible to the next agent call.

    Usage::

        wm = WorkingMemory(maxsize=10)
        wm.push({"role": "user", "content": "hello"})
        wm.push({"role": "assistant", "content": "hi!"})
        last5 = wm.peek(5)
        context_str = wm.summarize()
    """

    def __init__(self, maxsize: int = 20) -> None:
        self._buffer: deque = deque(maxlen=maxsize)

    def push(self, item: Any) -> None:
        self._buffer.append(item)

    def pop(self) -> Optional[Any]:
        return self._buffer.pop() if self._buffer else None

    def peek(self, n: int = 5) -> List[Any]:
        return list(self._buffer)[-n:]

    def all(self) -> List[Any]:
        return list(self._buffer)

    def clear(self) -> None:
        self._buffer.clear()

    def summarize(self, separator: str = "\n") -> str:
        """Concatenate string representations — useful for LLM context injection."""
        return separator.join(str(item) for item in self._buffer)

    def __len__(self) -> int:
        return len(self._buffer)


# ── Semantic Memory ───────────────────────────────────────────────────────────

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x ** 2 for x in a) ** 0.5
    norm_b = sum(x ** 2 for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticMemory:
    """
    Long-term factual store with pluggable embedding-based retrieval.

    If an ``embedding_fn`` is provided, similarity search uses cosine distance.
    Otherwise falls back to keyword overlap scoring.

    Usage::

        # Without embeddings (keyword fallback)
        mem = SemanticMemory()
        mem.store_fact("paris", "Paris is the capital of France", description="geography fact")
        results = mem.search("capital France", top_k=3)

        # With embeddings
        mem = SemanticMemory(embedding_fn=my_embed_fn)
        mem.store_fact("paris", ..., description="Paris is the capital of France")
        results = mem.search("French capital city")
    """

    def __init__(self, embedding_fn: Optional[Callable[[str], List[float]]] = None) -> None:
        self._facts: Dict[str, Dict[str, Any]] = {}
        self._embedding_fn = embedding_fn
        self._embeddings: Dict[str, List[float]] = {}

    def store_fact(
        self,
        key: str,
        fact: Any,
        description: str = "",
        tags: Optional[List[str]] = None,
    ) -> None:
        self._facts[key] = {
            "fact": fact,
            "description": description,
            "tags": tags or [],
            "created_at": time.time(),
        }
        if self._embedding_fn and description:
            self._embeddings[key] = self._embedding_fn(description)

    def retrieve_fact(self, key: str) -> Optional[Any]:
        entry = self._facts.get(key)
        return entry["fact"] if entry else None

    def search(self, query: str, top_k: int = 5) -> List[Tuple[str, Any, float]]:
        """Return [(key, fact, score)] sorted by descending relevance."""
        if self._embedding_fn and query:
            q_emb = self._embedding_fn(query)
            scored = [
                (k, self._facts[k]["fact"], _cosine_similarity(q_emb, emb))
                for k, emb in self._embeddings.items()
            ]
            scored.sort(key=lambda x: x[2], reverse=True)
            return scored[:top_k]

        # Keyword fallback
        q_words = set(query.lower().split())
        results: List[Tuple[str, Any, float]] = []
        for key, entry in self._facts.items():
            text = (entry["description"] + " " + " ".join(entry["tags"])).lower()
            score = float(sum(1 for w in q_words if w in text))
            if score > 0:
                results.append((key, entry["fact"], score))
        results.sort(key=lambda x: x[2], reverse=True)
        return results[:top_k]

    def forget(self, key: str) -> None:
        self._facts.pop(key, None)
        self._embeddings.pop(key, None)

    def all_keys(self) -> List[str]:
        return list(self._facts.keys())

    def __len__(self) -> int:
        return len(self._facts)


# ── Memory Manager ────────────────────────────────────────────────────────────

class MemoryManager:
    """
    Unified facade over all four memory tiers.

    Usage::

        mgr = MemoryManager()
        mgr.remember("session_id", "abc123", tier="short")
        mgr.remember("earth_radius", 6371, tier="semantic",
                     description="radius of Earth in km")
        mgr.working.push({"msg": "hello"})
        mgr.record_episode("ResearchAgent", inputs={...}, outputs={...})

        # Enrich an agent context dict with memory contents
        enriched_ctx = mgr.inject_context({"task": "analyse data"})

        # Promote short-term entries to semantic memory
        mgr.consolidate()
    """

    def __init__(
        self,
        short_term: Optional[ShortTermMemory] = None,
        episodic: Optional[EpisodicMemory] = None,
        working: Optional[WorkingMemory] = None,
        semantic: Optional[SemanticMemory] = None,
    ) -> None:
        self.short_term = short_term or ShortTermMemory()
        self.episodic = episodic or EpisodicMemory()
        self.working = working or WorkingMemory()
        self.semantic = semantic or SemanticMemory()

    def remember(self, key: str, value: Any, tier: str = "short", **kwargs: Any) -> None:
        """Store a value in the specified memory tier.

        Parameters
        ----------
        tier : str  One of ``"short"``, ``"semantic"``, ``"working"``.
        kwargs      Passed through to the tier's store method.
        """
        if tier == "short":
            self.short_term.store(key, value, **kwargs)
        elif tier == "semantic":
            self.semantic.store_fact(key, value, **kwargs)
        elif tier == "working":
            self.working.push({"key": key, "value": value})

    def recall(self, key: str, tier: str = "short") -> Optional[Any]:
        """Retrieve a value from the specified memory tier."""
        if tier == "short":
            return self.short_term.retrieve(key)
        if tier == "semantic":
            return self.semantic.retrieve_fact(key)
        return None

    def search(self, query: str, top_k: int = 5) -> List[Tuple[str, Any, float]]:
        """Semantic search across factual memory."""
        return self.semantic.search(query, top_k=top_k)

    def record_episode(
        self,
        agent: str,
        inputs: Dict[str, Any],
        outputs: Dict[str, Any],
        **kwargs: Any,
    ) -> str:
        return self.episodic.record(agent, inputs, outputs, **kwargs)

    def inject_context(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """Return a copy of *context* enriched with relevant memory snapshots."""
        enriched = dict(context)
        enriched["_working_memory"] = self.working.all()
        enriched["_recent_episodes"] = [
            {"agent": e.agent, "inputs": e.inputs, "outputs": e.outputs}
            for e in self.episodic.recent(5)
        ]
        return enriched

    def consolidate(self) -> int:
        """
        Promote all unexpired short-term entries to semantic memory
        (simulates a forgetting curve / memory consolidation pass).

        Returns the number of entries promoted.
        """
        promoted = 0
        for key in self.short_term.keys():
            value = self.short_term.retrieve(key)
            if value is not None:
                self.semantic.store_fact(key, value, description=str(value))
                promoted += 1
        self.short_term.clear()
        return promoted

    def stats(self) -> Dict[str, Any]:
        return {
            "short_term": self.short_term.stats(),
            "episodic": len(self.episodic),
            "working": len(self.working),
            "semantic": len(self.semantic),
        }


__all__ = [
    "Episode",
    "EpisodicMemory",
    "MemoryEntry",
    "MemoryManager",
    "SemanticMemory",
    "ShortTermMemory",
    "WorkingMemory",
]
