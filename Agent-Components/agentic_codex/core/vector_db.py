"""Vector DB integration primitives with a FAISS-backed default."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, List, Mapping, Protocol, Sequence, Tuple

try:
    import faiss  # type: ignore
except ImportError:  # pragma: no cover - optional dependency
    faiss = None  # type: ignore


class VectorDBAdapter(Protocol):
    """Protocol for vector database adapters."""

    def add(
        self, ids: Sequence[str], vectors: Sequence[Sequence[float]],
        metadata: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        ...

    def search(self, query: Sequence[float], k: int = 5) -> List[Tuple[str, float, Mapping[str, Any]]]:
        ...

    def delete(self, ids: Iterable[str]) -> None:
        ...

    def persist(self) -> None:
        ...


@dataclass
class FaissAdapter:
    """FAISS-backed in-memory vector index with optional on-disk persistence."""

    dimension: int
    path: str | Path | None = None
    metric: str = "l2"
    _index: Any = field(init=False, repr=False)
    _metadata: dict[str, Mapping[str, Any]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        if faiss is None:
            raise ImportError("faiss must be installed to use FaissAdapter")
        if self.metric == "ip":
            self._index = faiss.IndexFlatIP(self.dimension)
        else:
            self._index = faiss.IndexFlatL2(self.dimension)
        if self.path:
            path = Path(self.path)
            if path.exists():
                self._index = faiss.read_index(str(path))

    def add(
        self, ids: Sequence[str], vectors: Sequence[Sequence[float]],
        metadata: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        import numpy as np

        meta_list = list(metadata or [{} for _ in ids])
        np_vectors = np.array(vectors, dtype="float32")
        self._index.add(np_vectors)
        for idx, key in enumerate(ids):
            self._metadata[key] = meta_list[idx]

    def search(self, query: Sequence[float], k: int = 5) -> List[Tuple[str, float, Mapping[str, Any]]]:
        import numpy as np

        q = np.array([query], dtype="float32")
        distances, indices = self._index.search(q, k)
        results: List[Tuple[str, float, Mapping[str, Any]]] = []
        flat_indices = indices[0]
        flat_distances = distances[0]
        keys = list(self._metadata.keys())
        for idx, score in zip(flat_indices, flat_distances):
            if idx < 0 or idx >= len(keys):
                continue
            key = keys[idx]
            results.append((key, float(score), self._metadata.get(key, {})))
        return results

    def delete(self, ids: Iterable[str]) -> None:
        # FAISS flat indexes don't support delete; noop with metadata removal
        for key in ids:
            self._metadata.pop(key, None)

    def persist(self) -> None:
        if not self.path:
            return
        path = Path(self.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        faiss.write_index(self._index, str(path))


@dataclass
class ExternalVectorDBConfig:
    """Configuration for external vector DBs (e.g., Postgres/pgvector, MySQL, MongoDB, FerretDB)."""

    kind: str  # e.g., "pgvector", "mysql", "mongodb", "ferretdb", "custom"
    dsn: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)


def create_adapter(config: ExternalVectorDBConfig) -> VectorDBAdapter:
    """Factory hook to integrate external vector DBs; currently raises unless faiss/pgvector/etc. provided."""

    adapters = {
        "faiss": lambda cfg: FaissAdapter(dimension=int(cfg.params.get("dimension", 384)), path=cfg.params.get("path")),
    }
    if config.kind in adapters:
        return adapters[config.kind](config)
    raise NotImplementedError(f"Adapter kind {config.kind!r} not implemented; supply a custom adapter.")


__all__ = ["VectorDBAdapter", "FaissAdapter", "ExternalVectorDBConfig", "create_adapter"]
