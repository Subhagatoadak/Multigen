"""Memory abstractions."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Tuple

import json
import sqlite3


@dataclass
class Scratchpad:
    data: Dict[str, Any] = field(default_factory=dict)

    def put(self, key: str, value: Any) -> None:
        self.data[key] = value

    def get(self, key: str, default: Any | None = None) -> Any:
        return self.data.get(key, default)


class EpisodicMemory:
    def __init__(self) -> None:
        self._events: List[Mapping[str, Any]] = []

    def put(self, key: str, value: Any) -> None:
        self._events.append({"key": key, "value": value})

    def get(self, key: str) -> Any:
        for event in reversed(self._events):
            if event["key"] == key:
                return event["value"]
        raise KeyError(key)

    def search(self, query: str, *, k: int = 5) -> Sequence[Mapping[str, Any]]:
        return [event for event in reversed(self._events) if query in str(event["value"])][:k]

    def prune(self, *, max_events: int) -> None:
        """Keep only the most recent `max_events`."""

        if len(self._events) > max_events:
            self._events = self._events[-max_events:]

    def clear(self) -> None:
        self._events.clear()


class SemanticMemory:
    def __init__(self) -> None:
        self._items: Dict[str, str] = {}

    def put(self, key: str, value: str) -> None:
        self._items[key] = value

    def get(self, key: str) -> str:
        return self._items[key]

    def search(self, query: str, *, k: int = 5) -> Sequence[str]:
        return [value for value in self._items.values() if query.lower() in value.lower()][:k]

    def remove(self, key: str) -> None:
        self._items.pop(key, None)

    def clear(self) -> None:
        self._items.clear()


class GraphMemory:
    def __init__(self) -> None:
        self._edges: Dict[str, List[Dict[str, Any]]] = {}

    def add_edge(self, src: str, dest: str, meta: Mapping[str, Any] | None = None) -> None:
        self._edges.setdefault(src, []).append({"dest": dest, "meta": dict(meta or {})})

    def query(self, node: str) -> Sequence[Mapping[str, Any]]:
        return self._edges.get(node, [])

    def remove(self, src: str, dest: str | None = None) -> None:
        if src not in self._edges:
            return
        if dest is None:
            self._edges.pop(src, None)
            return
        self._edges[src] = [edge for edge in self._edges[src] if edge.get("dest") != dest]

    def clear(self) -> None:
        self._edges.clear()


@dataclass
class CachedMemory:
    """Cache wrapper with TTL-less eviction by size."""

    max_size: int = 128
    store: MutableMapping[str, Any] = field(default_factory=dict)

    def put(self, key: str, value: Any) -> None:
        if len(self.store) >= self.max_size:
            # FIFO eviction
            oldest = next(iter(self.store))
            self.store.pop(oldest, None)
        self.store[key] = value

    def get(self, key: str, default: Any | None = None) -> Any:
        return self.store.get(key, default)

    def clear(self) -> None:
        self.store.clear()


class PersistentMemory:
    """Simple JSONL-backed persistent memory."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("")

    def append(self, record: Mapping[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")

    def load(self, *, limit: int | None = None) -> List[Mapping[str, Any]]:
        rows: List[Mapping[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as handle:
            for idx, line in enumerate(handle):
                if limit is not None and idx >= limit:
                    break
                rows.append(json.loads(line))
        return rows

    def clear(self) -> None:
        self.path.write_text("")


class SQLiteMemory:
    """SQLite-backed key/value memory."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.conn = sqlite3.connect(path)
        self.conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
        self.conn.commit()

    def put(self, key: str, value: str) -> None:
        self.conn.execute("REPLACE INTO kv (k, v) VALUES (?, ?)", (key, value))
        self.conn.commit()

    def get(self, key: str) -> str:
        cur = self.conn.execute("SELECT v FROM kv WHERE k=?", (key,))
        row = cur.fetchone()
        if not row:
            raise KeyError(key)
        return row[0]

    def search(self, query: str, *, k: int = 5) -> Sequence[str]:
        cur = self.conn.execute("SELECT v FROM kv WHERE v LIKE ? LIMIT ?", (f"%{query}%", k))
        return [row[0] for row in cur.fetchall()]

    def remove(self, key: str) -> None:
        self.conn.execute("DELETE FROM kv WHERE k=?", (key,))
        self.conn.commit()

    def clear(self) -> None:
        self.conn.execute("DELETE FROM kv")
        self.conn.commit()


class VectorMemory:
    """Minimal in-memory vector store with cosine similarity."""

    def __init__(self, embed_fn: Any) -> None:
        self.embed_fn = embed_fn
        self._vectors: List[Tuple[str, List[float]]] = []

    def add(self, key: str, text: str) -> None:
        self._vectors.append((key, self.embed_fn(text)))

    def search(self, query: str, *, k: int = 5) -> List[Tuple[str, float]]:
        q = self.embed_fn(query)
        scores: List[Tuple[str, float]] = []
        for key, vec in self._vectors:
            scores.append((key, self._cosine(q, vec)))
        scores.sort(key=lambda pair: pair[1], reverse=True)
        return scores[:k]

    @staticmethod
    def _cosine(a: List[float], b: List[float]) -> float:
        if not a or not b or len(a) != len(b):
            return 0.0
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)


__all__ = [
    "Scratchpad",
    "EpisodicMemory",
    "SemanticMemory",
    "GraphMemory",
    "CachedMemory",
    "PersistentMemory",
    "SQLiteMemory",
    "VectorMemory",
]
