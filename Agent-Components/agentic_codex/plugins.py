"""Plugin loading via entry points for adapters and extensions."""
from __future__ import annotations

from importlib import metadata
from typing import Any, Dict, Mapping


def _load_entry_points(group: str) -> Dict[str, Any]:
    loaded: Dict[str, Any] = {}
    for ep in metadata.entry_points().select(group=group):
        try:
            loaded[ep.name] = ep.load()
        except Exception:
            continue
    return loaded


def load_plugins() -> Mapping[str, Any]:
    """Load all registered plugin entry points."""

    groups = [
        "agentic_codex.llm_adapters",
        "agentic_codex.tool_adapters",
        "agentic_codex.vector_adapters",
        "agentic_codex.embedding_adapters",
    ]
    plugins: Dict[str, Any] = {}
    for group in groups:
        plugins[group] = _load_entry_points(group)
    return plugins


__all__ = ["load_plugins"]
