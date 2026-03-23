"""Structured logging helpers."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class StructuredLogger:
    """Lightweight JSON logger."""

    name: str = "agentic"
    level: int = logging.INFO
    _run_id: Optional[str] = None

    def __post_init__(self) -> None:
        self._logger = logging.getLogger(self.name)
        self._logger.setLevel(self.level)
        if not self._logger.handlers:
            handler = logging.StreamHandler()
            formatter = logging.Formatter("%(message)s")
            handler.setFormatter(formatter)
            self._logger.addHandler(handler)

    def set_run_id(self, run_id: str) -> None:
        self._run_id = run_id

    def log(self, kind: str, message: str = "", **fields: Any) -> None:
        payload = {"kind": kind, "message": message}
        if self._run_id:
            payload["run_id"] = self._run_id
        payload.update(fields)
        self._logger.info(json.dumps(payload))


__all__ = ["StructuredLogger"]
