"""Lightweight run persistence for replay and audit."""
from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

from ..schemas import Message, RunEvent, RunResult


def _encode(obj: Any) -> Any:
    """Best-effort encoder for RunResult payloads."""

    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Mapping):
        return {k: _encode(v) for k, v in obj.items()}
    if isinstance(obj, Iterable) and not isinstance(obj, (str, bytes)):
        return [_encode(v) for v in obj]
    if isinstance(obj, (Message, RunEvent, RunResult)):
        serializer = getattr(obj, "model_dump", None) or getattr(obj, "dict", None)
        if serializer:
            return serializer()
    if is_dataclass(obj):
        return asdict(obj)
    return str(obj)


class RunStore:
    """Persist and replay runs as JSON files."""

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path)
        self.base_path.mkdir(parents=True, exist_ok=True)

    def _path_for(self, run_id: str) -> Path:
        return self.base_path / f"{run_id}.json"

    def save(self, run: RunResult | Mapping[str, Any]) -> Path:
        if isinstance(run, RunResult):
            run_id = run.run_id or "run"
            payload: Dict[str, Any] = {
                "run_id": run_id,
                "messages": _encode(run.messages),
                "events": _encode(run.events),
                "artifacts": _encode(run.artifacts),
                "meta": _encode(run.meta),
            }
        else:
            run_id = _encode(getattr(run, "run_id", None) or run.get("run_id") or "run")  # type: ignore[union-attr]
            payload = _encode(run)  # type: ignore[assignment]
        path = self._path_for(run_id)
        path.write_text(json.dumps(payload, indent=2))
        return path

    def load(self, run_id: str) -> Dict[str, Any]:
        path = self._path_for(run_id)
        if not path.exists():
            raise FileNotFoundError(f"Run {run_id!r} not found in store {self.base_path}")
        return json.loads(path.read_text())

    def list_runs(self) -> Iterable[str]:
        for file in self.base_path.glob("*.json"):
            yield file.stem


__all__ = ["RunStore"]
