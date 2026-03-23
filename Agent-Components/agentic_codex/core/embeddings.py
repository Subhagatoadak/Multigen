"""Embedding adapters for vector DB usage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, List, Protocol, Sequence

import math


class EmbeddingAdapter(Protocol):
    """Protocol for embedding providers."""

    def embed(self, text: str) -> List[float]:
        ...

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        ...


@dataclass
class FunctionEmbeddingAdapter:
    """Wrap a callable to produce embeddings."""

    fn: Callable[[str], List[float]]

    def embed(self, text: str) -> List[float]:
        return self.fn(text)

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        return [self.fn(t) for t in texts]


@dataclass
class SpacyEmbeddingAdapter:
    """spaCy-based embeddings (requires spaCy + model installed)."""

    model_name: str = "en_core_web_md"
    _nlp: Any = None

    def __post_init__(self) -> None:
        try:
            import spacy  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise ImportError("spaCy must be installed to use SpacyEmbeddingAdapter") from exc
        self._nlp = spacy.load(self.model_name)

    def embed(self, text: str) -> List[float]:
        doc = self._nlp(text)
        return [float(x) for x in doc.vector]

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        return [[float(x) for x in doc.vector] for doc in self._nlp.pipe(texts)]


@dataclass
class HashEmbeddingAdapter:
    """Deterministic toy embeddings using hashing (for offline demos)."""

    dimension: int = 64

    def embed(self, text: str) -> List[float]:
        vec = [0.0] * self.dimension
        for idx, ch in enumerate(text):
            vec[idx % self.dimension] += (ord(ch) % 31) / 100.0
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        return [self.embed(t) for t in texts]


@dataclass
class EnvOpenAIEmbeddingAdapter:
    """OpenAI embedding adapter that reads OPENAI_API_KEY from environment.

    Uses the OpenAI `text-embedding-3-small` model by default.
    Requires: `pip install openai` and `OPENAI_API_KEY` env var set.
    """

    model: str = "text-embedding-3-small"
    _api_key: str | None = None
    _openai: Any = None

    def __post_init__(self) -> None:
        import os

        self._api_key = os.getenv("OPENAI_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "OPENAI_API_KEY environment variable not set. "
                "Set it to use EnvOpenAIEmbeddingAdapter or use HashEmbeddingAdapter for offline demos."
            )
        try:
            import openai  # type: ignore

            self._openai = openai
        except ImportError as exc:
            raise ImportError(
                "openai package not installed. Install via: pip install openai"
            ) from exc

    def embed(self, text: str) -> List[float]:
        """Embed a single text string using OpenAI API."""
        if self._api_key is None or self._openai is None:  # pragma: no cover
            raise RuntimeError("EnvOpenAIEmbeddingAdapter not initialized")
        self._openai.api_key = self._api_key
        response: Any = self._openai.Embedding.create(model=self.model, input=text)
        embedding: list[Any] = response["data"][0]["embedding"]
        return [float(x) for x in embedding]

    def embed_batch(self, texts: Sequence[str]) -> List[List[float]]:
        """Embed a batch of texts using OpenAI API (more efficient than embed in a loop)."""
        if self._api_key is None or self._openai is None:  # pragma: no cover
            raise RuntimeError("EnvOpenAIEmbeddingAdapter not initialized")
        self._openai.api_key = self._api_key
        response: Any = self._openai.Embedding.create(model=self.model, input=list(texts))
        embeddings: list[Any] = sorted(response["data"], key=lambda x: x["index"])
        return [[float(x) for x in item["embedding"]] for item in embeddings]


__all__ = [
    "EmbeddingAdapter",
    "FunctionEmbeddingAdapter",
    "SpacyEmbeddingAdapter",
    "HashEmbeddingAdapter",
    "EnvOpenAIEmbeddingAdapter",
]
