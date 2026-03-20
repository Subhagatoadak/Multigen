"""Run and artifact storage primitives."""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


class RunStoreJSONL:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: Mapping[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event) + "\n")

    def query(self, **filters: Any) -> List[Dict[str, Any]]:
        results: List[Dict[str, Any]] = []
        if not self.path.exists():
            return results
        with self.path.open("r", encoding="utf-8") as handle:
            for line in handle:
                record = json.loads(line)
                if all(record.get(k) == v for k, v in filters.items()):
                    results.append(record)
        return results


class RunStoreSQL:
    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.execute("CREATE TABLE IF NOT EXISTS events (data TEXT)")

    def append(self, event: Mapping[str, Any]) -> None:
        self._conn.execute("INSERT INTO events(data) VALUES (?)", (json.dumps(event),))
        self._conn.commit()

    def query(self, **filters: Any) -> List[Dict[str, Any]]:
        cursor = self._conn.execute("SELECT data FROM events")
        results = []
        for (row,) in cursor.fetchall():
            data = json.loads(row)
            if all(data.get(k) == v for k, v in filters.items()):
                results.append(data)
        return results


class ArtifactStoreFS:
    def __init__(self, base_path: str | os.PathLike[str]) -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def put(self, artifact_id: str, data: bytes, *, mime: str, meta: Optional[Mapping[str, Any]] = None) -> str:
        path = self.base_path / artifact_id
        path.write_bytes(data)
        return str(path)

    def get(self, artifact_id: str) -> bytes:
        return (self.base_path / artifact_id).read_bytes()


class ArtifactStoreS3:  # pragma: no cover - placeholder
    def __init__(self, bucket: str) -> None:
        self.bucket = bucket

    def put(self, *args: Any, **kwargs: Any) -> str:
        raise NotImplementedError

    def get(self, *args: Any, **kwargs: Any) -> bytes:
        raise NotImplementedError


@dataclass
class KVCache:
    capacity: int = 128

    def __post_init__(self) -> None:
        self._store: Dict[str, Any] = {}
        self._order: List[str] = []

    def get(self, key: str) -> Any:
        return self._store.get(key)

    def put(self, key: str, value: Any) -> None:
        if key in self._store:
            self._order.remove(key)
        elif len(self._order) >= self.capacity:
            oldest = self._order.pop(0)
            self._store.pop(oldest, None)
        self._store[key] = value
        self._order.append(key)


class PromptCache(KVCache):
    def make_key(self, prompt: str, *, model: str) -> str:
        return f"{model}:{hash(prompt)}"


class CostMeter:
    def __init__(self) -> None:
        self._costs: List[Dict[str, Any]] = []

    def track(self, payload: Dict[str, Any]) -> None:
        self._costs.append(payload)

    @property
    def total_calls(self) -> int:
        return len(self._costs)

    def by_class(self) -> Dict[str, int]:
        buckets: Dict[str, int] = {}
        for payload in self._costs:
            cls = payload.get("cost_class", "unknown")
            buckets[cls] = buckets.get(cls, 0) + 1
        return buckets


__all__ = [
    "RunStoreJSONL",
    "RunStoreSQL",
    "ArtifactStoreFS",
    "ArtifactStoreS3",
    "KVCache",
    "PromptCache",
    "CostMeter",
]
