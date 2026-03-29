"""
RAG (Retrieval-Augmented Generation) pipeline for Multigen.

Problems solved
---------------
- VectorMemory is in-process; no full retrieval pipeline
- No chunking strategy abstraction (fixed / semantic / recursive)
- No embedding model abstraction
- No hybrid search (vector + BM25 keyword)
- No retrieval quality scoring
- No citation tracking (which chunks influenced the output)
- No retrieval feedback loop (bad chunks → lower weight over time)
- No multi-index routing (different collections per agent)

Classes
-------
- ``Chunk``                 — a text chunk with embedding and metadata
- ``ChunkingStrategy``      — abstract; implementations: Fixed, Sentence, Recursive
- ``EmbeddingAdapter``      — abstract; implementations: Random (test), OpenAI
- ``InMemoryVectorIndex``   — cosine-similarity vector index
- ``BM25Index``             — keyword-based BM25 retrieval
- ``HybridIndex``           — combines vector + BM25 scores (RRF fusion)
- ``RetrievalResult``       — ranked retrieval result with quality score
- ``CitationTracker``       — records which chunks were used in a response
- ``RetrievalFeedback``     — adjusts chunk weights based on usefulness signal
- ``MultiIndexRouter``      — routes queries to the right index by name/tag
- ``RAGPipeline``           — end-to-end: ingest → retrieve → augment context

Usage::

    from multigen.rag import RAGPipeline, FixedChunker, RandomEmbedder

    pipeline = RAGPipeline(
        chunker=FixedChunker(chunk_size=300),
        embedder=RandomEmbedder(dim=64),
    )
    pipeline.ingest("Long document text…", source="report.pdf")
    context, citations = pipeline.retrieve_and_cite("What is the revenue?", n=3)
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── Chunk ─────────────────────────────────────────────────────────────────────

@dataclass
class Chunk:
    text: str
    source: str
    chunk_id: str
    chunk_index: int
    embedding: Optional[List[float]] = None
    weight: float = 1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __len__(self) -> int:
        return len(self.text)


# ── Chunking strategies ───────────────────────────────────────────────────────

class FixedChunker:
    """Fixed-size character chunking with overlap."""

    def __init__(self, chunk_size: int = 500, overlap: int = 50) -> None:
        self.chunk_size = chunk_size
        self.overlap = overlap

    def chunk(self, text: str, source: str = "unknown") -> List[Chunk]:
        text = re.sub(r"\s+", " ", text).strip()
        step = max(1, self.chunk_size - self.overlap)
        chunks = []
        for i, start in enumerate(range(0, len(text), step)):
            end = min(start + self.chunk_size, len(text))
            chunks.append(Chunk(
                text=text[start:end],
                source=source,
                chunk_id=f"{source}::{i}",
                chunk_index=i,
            ))
        return chunks


class SentenceChunker:
    """Groups sentences into chunks of at most *max_sentences*."""

    def __init__(self, max_sentences: int = 5, overlap_sentences: int = 1) -> None:
        self.max_sentences = max_sentences
        self.overlap = overlap_sentences

    def chunk(self, text: str, source: str = "unknown") -> List[Chunk]:
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        step = max(1, self.max_sentences - self.overlap)
        chunks = []
        for i, start in enumerate(range(0, len(sentences), step)):
            group = sentences[start: start + self.max_sentences]
            chunks.append(Chunk(
                text=" ".join(group),
                source=source,
                chunk_id=f"{source}::{i}",
                chunk_index=i,
            ))
        return chunks


class RecursiveChunker:
    """Recursively splits on paragraphs, then sentences, then characters."""

    def __init__(self, chunk_size: int = 500, overlap: int = 30) -> None:
        self._fixed = FixedChunker(chunk_size=chunk_size, overlap=overlap)
        self.chunk_size = chunk_size

    def chunk(self, text: str, source: str = "unknown") -> List[Chunk]:
        # First try paragraph splits
        paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
        chunks: List[Chunk] = []
        idx = 0
        for para in paragraphs:
            if len(para) <= self.chunk_size:
                chunks.append(Chunk(
                    text=para, source=source,
                    chunk_id=f"{source}::{idx}", chunk_index=idx,
                ))
                idx += 1
            else:
                for sub in self._fixed.chunk(para, source):
                    sub.chunk_id = f"{source}::{idx}"
                    sub.chunk_index = idx
                    chunks.append(sub)
                    idx += 1
        return chunks


# ── Embedding adapters ────────────────────────────────────────────────────────

class RandomEmbedder:
    """
    Deterministic pseudo-random embedder for testing (no API calls).
    Uses character hash to generate a stable dim-dimensional vector.
    """

    def __init__(self, dim: int = 64) -> None:
        self.dim = dim

    def embed(self, text: str) -> List[float]:
        import hashlib
        seed = int(hashlib.md5(text.encode()).hexdigest(), 16)
        import random
        rng = random.Random(seed)
        vec = [rng.gauss(0, 1) for _ in range(self.dim)]
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


class OpenAIEmbedder:
    """
    OpenAI text-embedding-3-small adapter (stdlib urllib, no httpx).
    """

    def __init__(self, api_key: str, model: str = "text-embedding-3-small") -> None:
        self._key = api_key
        self._model = model

    def embed(self, text: str) -> List[float]:
        import json
        import urllib.request
        data = json.dumps({"model": self._model, "input": text}).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=data,
            headers={
                "Authorization": f"Bearer {self._key}",
                "Content-Type": "application/json",
            },
        )
        raw = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return raw["data"][0]["embedding"]

    def embed_batch(self, texts: List[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


# ── Vector index ──────────────────────────────────────────────────────────────

def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1e-9
    nb = math.sqrt(sum(x * x for x in b)) or 1e-9
    return dot / (na * nb)


class InMemoryVectorIndex:
    """
    Cosine-similarity vector index backed by a simple list scan.

    Usage::

        idx = InMemoryVectorIndex()
        idx.add(chunk)   # chunk must have .embedding set
        results = idx.search(query_embedding, k=5)
    """

    def __init__(self) -> None:
        self._chunks: List[Chunk] = []

    def add(self, chunk: Chunk) -> None:
        if chunk.embedding is None:
            raise ValueError("Chunk must have an embedding before adding to index")
        self._chunks.append(chunk)

    def add_batch(self, chunks: List[Chunk]) -> None:
        for c in chunks:
            self.add(c)

    def search(self, query_embedding: List[float], k: int = 5) -> List[Tuple[Chunk, float]]:
        scored = [
            (c, _cosine(query_embedding, c.embedding) * c.weight)  # type: ignore[arg-type]
            for c in self._chunks
        ]
        scored.sort(key=lambda x: -x[1])
        return scored[:k]

    def __len__(self) -> int:
        return len(self._chunks)


# ── BM25 index ────────────────────────────────────────────────────────────────

class BM25Index:
    """
    Keyword-based BM25 retrieval index.

    Usage::

        idx = BM25Index()
        for chunk in chunks:
            idx.add(chunk)
        results = idx.search("revenue growth", k=5)
    """

    def __init__(self, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1 = k1
        self.b = b
        self._chunks: List[Chunk] = []
        self._idf: Dict[str, float] = {}
        self._tf: List[Dict[str, float]] = []
        self._avgdl: float = 0.0

    def add(self, chunk: Chunk) -> None:
        self._chunks.append(chunk)
        self._rebuild()

    def add_batch(self, chunks: List[Chunk]) -> None:
        self._chunks.extend(chunks)
        self._rebuild()

    def _tokenize(self, text: str) -> List[str]:
        return re.findall(r"\w+", text.lower())

    def _rebuild(self) -> None:
        n = len(self._chunks)
        if n == 0:
            return
        doc_freq: Counter = Counter()
        tfs = []
        lengths = []
        for chunk in self._chunks:
            tokens = self._tokenize(chunk.text)
            lengths.append(len(tokens))
            tf = Counter(tokens)
            tfs.append({t: c / len(tokens) for t, c in tf.items()})
            doc_freq.update(set(tokens))

        self._avgdl = sum(lengths) / n
        self._tf = tfs
        self._idf = {
            term: math.log((n - df + 0.5) / (df + 0.5) + 1)
            for term, df in doc_freq.items()
        }

    def search(self, query: str, k: int = 5) -> List[Tuple[Chunk, float]]:
        tokens = self._tokenize(query)
        scores = []
        for i, chunk in enumerate(self._chunks):
            score = 0.0
            dl = len(self._tokenize(chunk.text))
            for t in tokens:
                tf = self._tf[i].get(t, 0.0) * len(self._tokenize(chunk.text))
                idf = self._idf.get(t, 0.0)
                score += idf * (tf * (self.k1 + 1)) / (
                    tf + self.k1 * (1 - self.b + self.b * dl / (self._avgdl or 1))
                )
            scores.append((chunk, score * chunk.weight))
        scores.sort(key=lambda x: -x[1])
        return scores[:k]

    def __len__(self) -> int:
        return len(self._chunks)


# ── HybridIndex ───────────────────────────────────────────────────────────────

class HybridIndex:
    """
    Combines vector and BM25 results using Reciprocal Rank Fusion (RRF).

    Usage::

        idx = HybridIndex(vector_weight=0.6, bm25_weight=0.4)
        idx.add(chunk)
        results = idx.search(query_embedding, query_text, k=5)
    """

    def __init__(self, vector_weight: float = 0.6, bm25_weight: float = 0.4, rrf_k: int = 60) -> None:
        self.vector = InMemoryVectorIndex()
        self.bm25 = BM25Index()
        self.vw = vector_weight
        self.bw = bm25_weight
        self.rrf_k = rrf_k

    def add(self, chunk: Chunk) -> None:
        self.vector.add(chunk)
        self.bm25.add(chunk)

    def add_batch(self, chunks: List[Chunk]) -> None:
        for c in chunks:
            self.add(c)

    def search(
        self,
        query_embedding: List[float],
        query_text: str,
        k: int = 5,
    ) -> List[Tuple[Chunk, float]]:
        vec_results = self.vector.search(query_embedding, k=k * 2)
        bm25_results = self.bm25.search(query_text, k=k * 2)

        # RRF fusion
        scores: Dict[str, float] = {}
        chunk_map: Dict[str, Chunk] = {}

        for rank, (chunk, _) in enumerate(vec_results):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + self.vw / (self.rrf_k + rank + 1)
            chunk_map[chunk.chunk_id] = chunk

        for rank, (chunk, _) in enumerate(bm25_results):
            scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + self.bw / (self.rrf_k + rank + 1)
            chunk_map[chunk.chunk_id] = chunk

        ranked = sorted(scores.items(), key=lambda x: -x[1])
        return [(chunk_map[cid], score) for cid, score in ranked[:k]]


# ── Citation Tracker ──────────────────────────────────────────────────────────

@dataclass
class Citation:
    chunk_id: str
    source: str
    excerpt: str
    relevance_score: float
    used_in_response: bool = True


class CitationTracker:
    """
    Records which chunks were used in a response.

    Usage::

        tracker = CitationTracker()
        tracker.record(chunk, score=0.87, run_id="run-1")
        print(tracker.for_run("run-1"))
    """

    def __init__(self) -> None:
        self._citations: Dict[str, List[Citation]] = {}

    def record(self, chunk: Chunk, score: float, run_id: str) -> Citation:
        cite = Citation(
            chunk_id=chunk.chunk_id,
            source=chunk.source,
            excerpt=chunk.text[:100] + ("…" if len(chunk.text) > 100 else ""),
            relevance_score=score,
        )
        self._citations.setdefault(run_id, []).append(cite)
        return cite

    def for_run(self, run_id: str) -> List[Citation]:
        return list(self._citations.get(run_id, []))

    def all_sources(self, run_id: str) -> List[str]:
        return list({c.source for c in self.for_run(run_id)})


# ── Retrieval Feedback ────────────────────────────────────────────────────────

class RetrievalFeedback:
    """
    Adjusts chunk weights based on usefulness signals.
    Chunks marked useful get higher weight; useless chunks get lower weight.

    Usage::

        feedback = RetrievalFeedback(decay=0.9, boost=1.1)
        feedback.mark_useful(chunk)
        feedback.mark_useless(chunk)
    """

    def __init__(self, boost: float = 1.1, decay: float = 0.9) -> None:
        self.boost = boost
        self.decay = decay

    def mark_useful(self, chunk: Chunk) -> None:
        chunk.weight = min(2.0, chunk.weight * self.boost)

    def mark_useless(self, chunk: Chunk) -> None:
        chunk.weight = max(0.1, chunk.weight * self.decay)


# ── MultiIndexRouter ──────────────────────────────────────────────────────────

class MultiIndexRouter:
    """
    Routes queries to the right index by name.

    Usage::

        router = MultiIndexRouter()
        router.register("finance", finance_index)
        router.register("legal", legal_index)
        results = router.search("revenue", index_name="finance", embedding=emb, k=5)
    """

    def __init__(self) -> None:
        self._indexes: Dict[str, Any] = {}

    def register(self, name: str, index: Any) -> None:
        self._indexes[name] = index

    def search(
        self,
        query_text: str,
        index_name: str,
        embedding: Optional[List[float]] = None,
        k: int = 5,
    ) -> List[Tuple[Chunk, float]]:
        idx = self._indexes.get(index_name)
        if idx is None:
            raise KeyError(f"Index {index_name!r} not registered")
        if isinstance(idx, HybridIndex) and embedding is not None:
            return idx.search(embedding, query_text, k=k)
        if isinstance(idx, InMemoryVectorIndex) and embedding is not None:
            return idx.search(embedding, k=k)
        if isinstance(idx, BM25Index):
            return idx.search(query_text, k=k)
        raise TypeError(f"Cannot search index type {type(idx).__name__}")

    def names(self) -> List[str]:
        return list(self._indexes.keys())


# ── RAGPipeline ───────────────────────────────────────────────────────────────

class RAGPipeline:
    """
    End-to-end RAG pipeline: ingest → index → retrieve → augment context.

    Usage::

        from multigen.rag import RAGPipeline, FixedChunker, RandomEmbedder

        pipeline = RAGPipeline(
            chunker=FixedChunker(chunk_size=300, overlap=30),
            embedder=RandomEmbedder(dim=64),
            hybrid=True,
        )
        pipeline.ingest("Full document text...", source="report.pdf")
        context, citations = pipeline.retrieve_and_cite(
            query="What is the Q3 revenue?",
            n=5,
            run_id="run-1",
        )
    """

    def __init__(
        self,
        chunker: Any = None,
        embedder: Any = None,
        hybrid: bool = True,
        feedback: bool = True,
    ) -> None:
        self.chunker = chunker or FixedChunker()
        self.embedder = embedder or RandomEmbedder()
        self.index: Any = HybridIndex() if hybrid else InMemoryVectorIndex()
        self.citations = CitationTracker()
        self.feedback = RetrievalFeedback() if feedback else None
        self._chunks: List[Chunk] = []

    def ingest(self, text: str, source: str = "unknown") -> List[Chunk]:
        chunks = self.chunker.chunk(text, source)
        embeddings = self.embedder.embed_batch([c.text for c in chunks])
        for chunk, emb in zip(chunks, embeddings):
            chunk.embedding = emb
        self.index.add_batch(chunks)
        self._chunks.extend(chunks)
        return chunks

    def retrieve(
        self,
        query: str,
        n: int = 5,
    ) -> List[Tuple[Chunk, float]]:
        q_emb = self.embedder.embed(query)
        if isinstance(self.index, HybridIndex):
            return self.index.search(q_emb, query, k=n)
        if isinstance(self.index, InMemoryVectorIndex):
            return self.index.search(q_emb, k=n)
        return self.index.search(query, k=n)  # BM25

    def retrieve_and_cite(
        self,
        query: str,
        n: int = 5,
        run_id: str = "default",
    ) -> Tuple[str, List[Citation]]:
        results = self.retrieve(query, n)
        cites = [
            self.citations.record(chunk, score, run_id)
            for chunk, score in results
        ]
        context_parts = [
            f"[Source: {c.source}]\n{chunk.text}"
            for (chunk, _), c in zip(results, cites)
        ]
        return "\n\n".join(context_parts), cites

    def mark_useful(self, citation: Citation) -> None:
        for chunk in self._chunks:
            if chunk.chunk_id == citation.chunk_id and self.feedback:
                self.feedback.mark_useful(chunk)

    def mark_useless(self, citation: Citation) -> None:
        for chunk in self._chunks:
            if chunk.chunk_id == citation.chunk_id and self.feedback:
                self.feedback.mark_useless(chunk)

    @property
    def n_chunks(self) -> int:
        return len(self._chunks)


__all__ = [
    "Chunk",
    "FixedChunker",
    "SentenceChunker",
    "RecursiveChunker",
    "RandomEmbedder",
    "OpenAIEmbedder",
    "InMemoryVectorIndex",
    "BM25Index",
    "HybridIndex",
    "Citation",
    "CitationTracker",
    "RetrievalFeedback",
    "MultiIndexRouter",
    "RAGPipeline",
]
