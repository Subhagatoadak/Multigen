"""Advanced memory system for Multigen agents.

This module extends the basic :mod:`memory` module with richer storage tiers:

* :class:`VectorMemory` — embedding-based similarity store (pure-Python cosine).
* :class:`ForgettingCurve` — Ebbinghaus-style memory decay and spaced repetition.
* :class:`MemoryIndex` — inverted TF-IDF index for fast tag/keyword retrieval.
* :class:`ContextualMemory` — auto-selects relevant items from the current context.
* :class:`PersistentMemory` — JSON-backed file store that survives process restarts.
* :class:`AdvancedMemoryManager` — unified facade wiring all advanced tiers together.

No external dependencies are required; all similarity and indexing logic is
implemented with the Python standard library (``math``, ``json``, ``pathlib``,
``collections``, ``dataclasses``).

Example usage::

    from multigen.advanced_memory import AdvancedMemoryManager

    mgr = AdvancedMemoryManager(persistent_path="/tmp/myagent_mem.json")
    mgr.remember_contextual("task_1", {"result": 42}, "compute square root of 1764")
    results = mgr.recall_for_context({"current_task": "square root computation"})
    mgr.persist("final_answer", 42)
    answer = mgr.restore("final_answer")
    print(mgr.stats())
"""

from __future__ import annotations

import json
import math
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

__all__ = [
    "VectorRecord",
    "VectorMemory",
    "MemoryTrace",
    "ForgettingCurve",
    "MemoryIndex",
    "ContextualMemory",
    "PersistentMemory",
    "AdvancedMemoryManager",
]

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Pure-Python cosine similarity between two equal-length float vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _tokenize(text: str) -> List[str]:
    """Lowercase, split on non-alphanumeric characters, drop empties."""
    import re
    return [t for t in re.split(r"[^a-z0-9]+", text.lower()) if t]


# ---------------------------------------------------------------------------
# 1. VectorMemory
# ---------------------------------------------------------------------------

@dataclass
class VectorRecord:
    """A single record stored in :class:`VectorMemory`."""

    key: str
    content: Any
    embedding: List[float]
    metadata: Dict[str, Any]
    created_at: float
    tags: List[str]


class VectorMemory:
    """Embedding-based in-memory similarity store.

    If no *embedding_fn* is provided the store still works — embeddings will
    be empty lists and :meth:`search` will return items scored 0.0 (all equal).

    Parameters
    ----------
    embedding_fn:
        Callable that maps a plain-text string to a ``List[float]`` embedding.
        Defaults to ``None`` (no embeddings; similarity search returns uniform scores).
    capacity:
        Maximum number of records to hold.  Oldest record is evicted when the
        limit is exceeded.

    Usage::

        vm = VectorMemory(embedding_fn=my_embed, capacity=500)
        rid = vm.store("k1", {"data": 1}, text_for_embedding="some document text")
        results = vm.search("similar document", top_k=3)
    """

    def __init__(
        self,
        embedding_fn: Optional[Callable[[str], List[float]]] = None,
        capacity: int = 10_000,
    ) -> None:
        self._embedding_fn = embedding_fn
        self.capacity = capacity
        # Ordered dict preserves insertion order for LRU-style eviction.
        self._records: Dict[str, VectorRecord] = {}
        # key → record_id mapping so callers can look up by original key.
        self._key_to_id: Dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(
        self,
        key: str,
        content: Any,
        text_for_embedding: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
    ) -> str:
        """Store *content* under *key* and return the unique record id.

        If a record with the same *key* already exists it is replaced.
        """
        # Replace existing record by the same key.
        if key in self._key_to_id:
            old_rid = self._key_to_id[key]
            self._records.pop(old_rid, None)

        # Evict oldest record if at capacity.
        if len(self._records) >= self.capacity:
            oldest_rid = next(iter(self._records))
            oldest_rec = self._records.pop(oldest_rid)
            self._key_to_id.pop(oldest_rec.key, None)

        embedding: List[float] = []
        if self._embedding_fn and text_for_embedding:
            embedding = self._embedding_fn(text_for_embedding)

        record_id = str(uuid.uuid4())
        record = VectorRecord(
            key=key,
            content=content,
            embedding=embedding,
            metadata=dict(metadata or {}),
            created_at=time.time(),
            tags=list(tags or []),
        )
        self._records[record_id] = record
        self._key_to_id[key] = record_id
        return record_id

    def search(
        self,
        query: str,
        top_k: int = 5,
        threshold: float = 0.0,
    ) -> List[Tuple[str, Any, float]]:
        """Search by query text.  Returns ``[(key, content, score)]`` sorted by score desc."""
        query_vector: List[float] = []
        if self._embedding_fn and query:
            query_vector = self._embedding_fn(query)
        return self.search_by_vector(query_vector, top_k=top_k, threshold=threshold)

    def search_by_vector(
        self,
        vector: List[float],
        top_k: int = 5,
        threshold: float = 0.0,
    ) -> List[Tuple[str, Any, float]]:
        """Search using a pre-computed embedding vector.

        Returns ``[(key, content, score)]`` sorted by cosine similarity descending.
        """
        scored: List[Tuple[str, Any, float]] = []
        for rec in self._records.values():
            score = _cosine_similarity(vector, rec.embedding)
            if score >= threshold:
                scored.append((rec.key, rec.content, score))
        scored.sort(key=lambda t: t[2], reverse=True)
        return scored[:top_k]

    def delete(self, key: str) -> bool:
        """Remove the record identified by *key*.  Returns ``True`` if found."""
        rid = self._key_to_id.pop(key, None)
        if rid is None:
            return False
        self._records.pop(rid, None)
        return True

    def __len__(self) -> int:
        return len(self._records)


# ---------------------------------------------------------------------------
# 2. ForgettingCurve
# ---------------------------------------------------------------------------

@dataclass
class MemoryTrace:
    """Mutable trace tracking a single memory item's retention state."""

    key: str
    strength: float          # 0.0–1.0 (normalised initial strength)
    last_reviewed: float     # Unix timestamp
    review_count: int        # How many times it has been reviewed
    stability: float         # Retention half-life in days (grows with each review)


class ForgettingCurve:
    """Ebbinghaus forgetting curve with spaced-repetition support.

    The retention at time *t* (days since last review) is modelled as:

    .. math::

        R(t) = S_0 \\cdot e^{-t / S}

    where :math:`S_0` is the current strength and :math:`S` is the stability
    (half-life in days).  Each call to :meth:`review` multiplies the stability
    by ``2`` (bounded by ``180`` days) and resets the strength to ``1.0``.

    Parameters
    ----------
    decay_factor:
        Initial stability (in days) assigned to new traces.  Defaults to ``0.5``.

    Usage::

        fc = ForgettingCurve(decay_factor=1.0)
        fc.add("concept_x")
        time.sleep(0.001)
        fc.review("concept_x")
        print(fc.strength("concept_x"))          # ~1.0 right after review
        print(fc.forgotten(threshold=0.2))        # likely empty
        print(fc.due_for_review(horizon_hours=24))
    """

    _MAX_STABILITY_DAYS: float = 180.0

    def __init__(self, decay_factor: float = 0.5) -> None:
        self.decay_factor = decay_factor
        self._traces: Dict[str, MemoryTrace] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add(self, key: str, initial_strength: float = 1.0) -> None:
        """Register a new memory trace for *key*."""
        self._traces[key] = MemoryTrace(
            key=key,
            strength=max(0.0, min(1.0, initial_strength)),
            last_reviewed=time.time(),
            review_count=0,
            stability=self.decay_factor,
        )

    def review(self, key: str) -> None:
        """Strengthen and stabilise the trace for *key*.

        Doubles the stability (up to :attr:`_MAX_STABILITY_DAYS`) and resets
        strength to ``1.0``.  Creates the trace if it does not exist.
        """
        if key not in self._traces:
            self.add(key)
            return
        trace = self._traces[key]
        trace.last_reviewed = time.time()
        trace.review_count += 1
        trace.strength = 1.0
        trace.stability = min(trace.stability * 2.0, self._MAX_STABILITY_DAYS)

    def strength(self, key: str) -> float:
        """Current retention value for *key* using the Ebbinghaus formula.

        Returns ``0.0`` if the key is unknown.
        """
        trace = self._traces.get(key)
        if trace is None:
            return 0.0
        t_days = (time.time() - trace.last_reviewed) / 86_400.0
        return trace.strength * math.exp(-t_days / trace.stability)

    def forgotten(self, threshold: float = 0.1) -> List[str]:
        """Return keys whose current retention has fallen below *threshold*."""
        return [k for k in self._traces if self.strength(k) < threshold]

    def due_for_review(self, horizon_hours: float = 24.0) -> List[str]:
        """Return keys that would fall below 50 % retention within *horizon_hours*.

        A key is considered "due" if its projected retention after
        ``now + horizon_hours`` drops below half of its current retention.
        """
        due: List[str] = []
        horizon_days = horizon_hours / 24.0
        for key, trace in self._traces.items():
            t_days = (time.time() - trace.last_reviewed) / 86_400.0
            current = trace.strength * math.exp(-t_days / trace.stability)
            projected = trace.strength * math.exp(-(t_days + horizon_days) / trace.stability)
            if current > 0.0 and projected < current * 0.5:
                due.append(key)
        return due


# ---------------------------------------------------------------------------
# 3. MemoryIndex
# ---------------------------------------------------------------------------

class MemoryIndex:
    """Inverted TF-IDF index for fast keyword and tag-based lookup.

    Supports indexing arbitrary string keys associated with free-form text and
    optional tags.  Retrieval is scored with a lightweight TF-IDF heuristic
    computed entirely in pure Python.

    Usage::

        idx = MemoryIndex()
        idx.index("doc_a", "the quick brown fox jumps", tags=["animals"])
        idx.index("doc_b", "a lazy dog sleeps all day", tags=["animals", "sleep"])
        print(idx.search("quick fox"))          # [("doc_a", score)]
        print(idx.search_tags(["sleep"]))       # ["doc_b"]
        idx.remove("doc_a")
    """

    def __init__(self) -> None:
        # term → {key: term_freq}
        self._inverted: Dict[str, Dict[str, int]] = defaultdict(dict)
        # key → total term count in that document
        self._doc_lengths: Dict[str, int] = {}
        # tag → set of keys
        self._tag_index: Dict[str, set] = defaultdict(set)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def index(self, key: str, text: str, tags: Optional[List[str]] = None) -> None:
        """Tokenize *text* and update the inverted index for *key*.

        Re-indexing the same *key* with new text replaces the old entry.
        """
        # Remove old entries first to avoid stale data.
        self.remove(key)

        tokens = _tokenize(text)
        tf: Dict[str, int] = defaultdict(int)
        for token in tokens:
            tf[token] += 1

        for term, freq in tf.items():
            self._inverted[term][key] = freq
        self._doc_lengths[key] = max(len(tokens), 1)

        for tag in tags or []:
            self._tag_index[tag].add(key)

    def search(self, query: str) -> List[Tuple[str, float]]:
        """Return ``[(key, tfidf_score)]`` sorted by descending relevance.

        Uses a simplified TF-IDF: term frequency divided by document length,
        multiplied by ``log(N / df)`` where *N* is total indexed documents and
        *df* is the number of docs containing the term.
        """
        tokens = _tokenize(query)
        if not tokens:
            return []

        n_docs = len(self._doc_lengths)
        scores: Dict[str, float] = defaultdict(float)

        for token in tokens:
            candidates = self._inverted.get(token, {})
            df = max(len(candidates), 1)
            idf = math.log(max(n_docs, 1) / df + 1)
            for key, tf in candidates.items():
                doc_len = self._doc_lengths.get(key, 1)
                scores[key] += (tf / doc_len) * idf

        ranked = sorted(scores.items(), key=lambda t: t[1], reverse=True)
        return ranked

    def search_tags(self, tags: List[str]) -> List[str]:
        """Return keys that carry *all* of the given tags."""
        if not tags:
            return []
        result_sets = [self._tag_index.get(tag, set()) for tag in tags]
        intersection: set = result_sets[0].copy()
        for s in result_sets[1:]:
            intersection &= s
        return list(intersection)

    def remove(self, key: str) -> None:
        """Remove all index entries for *key*."""
        # Remove from inverted index.
        empty_terms: List[str] = []
        for term, doc_map in self._inverted.items():
            doc_map.pop(key, None)
            if not doc_map:
                empty_terms.append(term)
        for term in empty_terms:
            del self._inverted[term]

        self._doc_lengths.pop(key, None)

        # Remove from tag index.
        for tag_set in self._tag_index.values():
            tag_set.discard(key)


# ---------------------------------------------------------------------------
# 4. ContextualMemory
# ---------------------------------------------------------------------------

class ContextualMemory:
    """Memory that auto-selects relevant items based on the current context.

    Combines :class:`VectorMemory` (semantic similarity) and
    :class:`MemoryIndex` (keyword/tag match) to surface the most relevant
    stored items for any given context dictionary.

    Parameters
    ----------
    vector_memory:
        Pre-created :class:`VectorMemory` instance.  A default instance
        (no embedding function) is created if ``None``.
    index:
        Pre-created :class:`MemoryIndex` instance.  A default instance is
        created if ``None``.
    max_context_items:
        Maximum number of items returned by :meth:`retrieve_for_context`.

    Usage::

        cm = ContextualMemory(max_context_items=5)
        cm.store("fact_1", {"answer": 42}, "the answer to life the universe", tags=["trivia"])
        ctx = {"question": "what is the answer to life?"}
        items = cm.retrieve_for_context(ctx)
    """

    def __init__(
        self,
        vector_memory: Optional[VectorMemory] = None,
        index: Optional[MemoryIndex] = None,
        max_context_items: int = 10,
    ) -> None:
        self.vector = vector_memory if vector_memory is not None else VectorMemory()
        self.index = index if index is not None else MemoryIndex()
        self.max_context_items = max_context_items
        # Track all stored keys for forget_irrelevant.
        self._all_keys: List[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(
        self,
        key: str,
        content: Any,
        context_text: str,
        tags: Optional[List[str]] = None,
    ) -> None:
        """Store *content* under *key*, indexing it by *context_text* and *tags*."""
        self.vector.store(key, content, text_for_embedding=context_text, tags=tags)
        self.index.index(key, context_text, tags=tags)
        if key not in self._all_keys:
            self._all_keys.append(key)

    def retrieve_for_context(self, ctx: dict) -> List[Any]:
        """Return up to :attr:`max_context_items` contents relevant to *ctx*.

        The context dictionary is flattened into a single query string by
        joining the string representations of all values.  Both vector search
        and keyword search are executed and their results are merged (deduped)
        before the top-*N* are returned.
        """
        query_text = " ".join(str(v) for v in ctx.values() if v is not None)

        # Vector search.
        vector_hits: Dict[str, float] = {}
        for key, content, score in self.vector.search(query_text, top_k=self.max_context_items * 2):
            vector_hits[key] = score

        # Keyword/index search.
        index_hits: Dict[str, float] = {k: s for k, s in self.index.search(query_text)}

        # Merge scores (simple additive fusion).
        combined: Dict[str, float] = defaultdict(float)
        for k, s in vector_hits.items():
            combined[k] += s
        for k, s in index_hits.items():
            combined[k] += s

        ranked_keys = sorted(combined, key=lambda k: combined[k], reverse=True)
        ranked_keys = ranked_keys[: self.max_context_items]

        # Retrieve actual content from vector store.
        contents: List[Any] = []
        for key in ranked_keys:
            hits = self.vector.search_by_vector([], top_k=len(self.vector))
            # Fall back: search by key name in the vector store internal records.
            content = self._fetch_content_by_key(key)
            if content is not None:
                contents.append(content)
        return contents

    def forget_irrelevant(self, ctx: dict, keep_fraction: float = 0.5) -> None:
        """Remove the least relevant items, keeping the top *keep_fraction*.

        Parameters
        ----------
        ctx:
            Current context dict used to score relevance.
        keep_fraction:
            Fraction of stored items to retain (e.g. ``0.5`` keeps the top half).
        """
        query_text = " ".join(str(v) for v in ctx.values() if v is not None)

        combined: Dict[str, float] = defaultdict(float)
        for k, s in self.index.search(query_text):
            combined[k] += s
        for k, _, s in self.vector.search(query_text, top_k=len(self._all_keys)):
            combined[k] += s

        ranked = sorted(self._all_keys, key=lambda k: combined.get(k, 0.0), reverse=True)
        n_keep = max(1, int(math.ceil(len(ranked) * keep_fraction)))
        to_remove = ranked[n_keep:]

        for key in to_remove:
            self.vector.delete(key)
            self.index.remove(key)
            self._all_keys.remove(key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_content_by_key(self, key: str) -> Optional[Any]:
        """Retrieve raw content from the underlying vector store by key."""
        rid = self.vector._key_to_id.get(key)
        if rid is None:
            return None
        record = self.vector._records.get(rid)
        return record.content if record else None


# ---------------------------------------------------------------------------
# 5. PersistentMemory
# ---------------------------------------------------------------------------

class PersistentMemory:
    """File-backed memory store using JSON serialisation.

    Values that are not natively JSON-serialisable are coerced to strings via
    ``default=str`` so the store never raises on unusual types.

    Parameters
    ----------
    path:
        Filesystem path of the backing JSON file.  Parent directories are
        created automatically.
    auto_save:
        When ``True`` (default), every :meth:`store` and :meth:`delete`
        call immediately writes the current state to disk.

    Usage::

        pm = PersistentMemory("/tmp/agent_state.json")
        pm.store("model", "gpt-4o", metadata={"tier": "inference"})
        pm.store("threshold", 0.95)
        pm.save()                      # explicit flush (no-op when auto_save=True)
        pm2 = PersistentMemory("/tmp/agent_state.json")
        pm2.load()
        print(pm2.retrieve("model"))   # "gpt-4o"
    """

    def __init__(self, path: str, auto_save: bool = True) -> None:
        self.path = Path(path)
        self.auto_save = auto_save
        self._data: Dict[str, Dict[str, Any]] = {}
        # Auto-load if file exists.
        if self.path.exists():
            self.load()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store(self, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Persist *value* under *key* with optional *metadata*."""
        self._data[key] = {
            "value": value,
            "metadata": dict(metadata or {}),
            "stored_at": time.time(),
        }
        if self.auto_save:
            self.save()

    def retrieve(self, key: str) -> Optional[Any]:
        """Return the stored value for *key*, or ``None`` if absent."""
        entry = self._data.get(key)
        return entry["value"] if entry is not None else None

    def delete(self, key: str) -> bool:
        """Remove *key* from the store.  Returns ``True`` if the key existed."""
        if key not in self._data:
            return False
        del self._data[key]
        if self.auto_save:
            self.save()
        return True

    def save(self) -> None:
        """Write current state to disk as JSON."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as fh:
            json.dump(self._data, fh, indent=2, default=str)

    def load(self) -> None:
        """Reload state from the backing JSON file.

        Silently no-ops if the file does not exist or contains invalid JSON.
        """
        if not self.path.exists():
            return
        try:
            with self.path.open("r", encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self._data = loaded
        except (json.JSONDecodeError, OSError):
            pass

    def keys(self) -> List[str]:
        """Return all stored keys."""
        return list(self._data.keys())

    def __len__(self) -> int:
        return len(self._data)


# ---------------------------------------------------------------------------
# 6. AdvancedMemoryManager
# ---------------------------------------------------------------------------

class AdvancedMemoryManager:
    """Unified facade over all advanced memory tiers.

    Wires together :class:`VectorMemory`, :class:`ContextualMemory`,
    :class:`PersistentMemory`, :class:`ForgettingCurve`, and
    :class:`MemoryIndex` into a single coherent interface.

    Parameters
    ----------
    embedding_fn:
        Optional embedding callable forwarded to the internal
        :class:`VectorMemory`.
    vector_capacity:
        Capacity passed to the internal :class:`VectorMemory`.
    persistent_path:
        File path for the :class:`PersistentMemory` backing store.
        Defaults to ``"./agent_memory.json"``.
    auto_save:
        Forwarded to :class:`PersistentMemory`.
    decay_factor:
        Forwarded to :class:`ForgettingCurve`.
    max_context_items:
        Forwarded to :class:`ContextualMemory`.

    All five tiers can also be injected directly via the keyword-only
    constructor parameters *vector*, *contextual*, *persistent*,
    *forgetting*, and *index*.

    Usage::

        mgr = AdvancedMemoryManager(persistent_path="/tmp/myagent.json")
        mgr.remember_contextual("doc_1", {"body": "..."}, "machine learning tutorial")
        results = mgr.recall_for_context({"topic": "machine learning"})
        mgr.persist("config", {"model": "gpt-4o"})
        cfg = mgr.restore("config")
        print(mgr.stats())
    """

    def __init__(
        self,
        embedding_fn: Optional[Callable[[str], List[float]]] = None,
        vector_capacity: int = 10_000,
        persistent_path: str = "./agent_memory.json",
        auto_save: bool = True,
        decay_factor: float = 0.5,
        max_context_items: int = 10,
        *,
        vector: Optional[VectorMemory] = None,
        contextual: Optional[ContextualMemory] = None,
        persistent: Optional[PersistentMemory] = None,
        forgetting: Optional[ForgettingCurve] = None,
        index: Optional[MemoryIndex] = None,
    ) -> None:
        shared_index = index if index is not None else MemoryIndex()
        shared_vector = vector if vector is not None else VectorMemory(
            embedding_fn=embedding_fn,
            capacity=vector_capacity,
        )

        self.vector: VectorMemory = shared_vector
        self.index: MemoryIndex = shared_index
        self.contextual: ContextualMemory = contextual if contextual is not None else ContextualMemory(
            vector_memory=self.vector,
            index=self.index,
            max_context_items=max_context_items,
        )
        self.persistent: PersistentMemory = persistent if persistent is not None else PersistentMemory(
            path=persistent_path,
            auto_save=auto_save,
        )
        self.forgetting: ForgettingCurve = forgetting if forgetting is not None else ForgettingCurve(
            decay_factor=decay_factor,
        )

    # ------------------------------------------------------------------
    # Contextual tier
    # ------------------------------------------------------------------

    def remember_contextual(
        self,
        key: str,
        content: Any,
        text: str,
        tags: Optional[List[str]] = None,
    ) -> None:
        """Store *content* in the contextual memory tier and register a forgetting trace."""
        self.contextual.store(key, content, context_text=text, tags=tags)
        self.forgetting.add(key)

    def recall_for_context(self, ctx: dict) -> List[Any]:
        """Return the most relevant stored items for the given context dict."""
        # Refresh forgetting traces for returned items.
        items = self.contextual.retrieve_for_context(ctx)
        return items

    # ------------------------------------------------------------------
    # Persistent tier
    # ------------------------------------------------------------------

    def persist(self, key: str, value: Any, metadata: Optional[Dict[str, Any]] = None) -> None:
        """Write *value* to durable persistent storage."""
        self.persistent.store(key, value, metadata=metadata)

    def restore(self, key: str) -> Optional[Any]:
        """Read *key* back from persistent storage."""
        return self.persistent.retrieve(key)

    # ------------------------------------------------------------------
    # Consolidation
    # ------------------------------------------------------------------

    def consolidate_to_persistent(self, keys: Optional[List[str]] = None) -> None:
        """Move items from contextual memory to persistent storage.

        Parameters
        ----------
        keys:
            Explicit list of keys to consolidate.  When ``None``, all keys
            currently tracked in the contextual store are consolidated.
        """
        target_keys = keys if keys is not None else list(self.contextual._all_keys)
        for key in target_keys:
            content = self.contextual._fetch_content_by_key(key)
            if content is not None:
                self.persistent.store(key, content)
                # Remove from transient contextual store.
                self.contextual.vector.delete(key)
                self.contextual.index.remove(key)
                if key in self.contextual._all_keys:
                    self.contextual._all_keys.remove(key)

    # ------------------------------------------------------------------
    # Forgetting / pruning
    # ------------------------------------------------------------------

    def prune_forgotten(self, threshold: float = 0.1) -> int:
        """Remove entries whose forgetting-curve retention has fallen below *threshold*.

        Returns the number of entries removed.
        """
        forgotten_keys = self.forgetting.forgotten(threshold=threshold)
        removed = 0
        for key in forgotten_keys:
            # Remove from contextual store.
            self.contextual.vector.delete(key)
            self.contextual.index.remove(key)
            if key in self.contextual._all_keys:
                self.contextual._all_keys.remove(key)
            # Remove the trace itself.
            self.forgetting._traces.pop(key, None)
            removed += 1
        return removed

    # ------------------------------------------------------------------
    # Statistics
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Return a snapshot of all tier sizes and forgetting-curve health."""
        n_traces = len(self.forgetting._traces)
        n_forgotten = len(self.forgetting.forgotten())
        return {
            "vector_memory_size": len(self.vector),
            "contextual_memory_keys": len(self.contextual._all_keys),
            "persistent_memory_size": len(self.persistent),
            "forgetting_traces": n_traces,
            "forgotten_below_0.1": n_forgotten,
            "due_for_review_24h": len(self.forgetting.due_for_review(horizon_hours=24)),
            "index_documents": len(self.index._doc_lengths),
        }
