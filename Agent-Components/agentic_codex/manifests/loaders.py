"""Manifest loader utilities."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from .validators import WorkflowManifest, validate_workflow


def _load_yaml_library():  # pragma: no cover - simple import helper
    try:
        import yaml  # type: ignore

        return yaml.safe_load
    except ModuleNotFoundError:
        return None


def _basic_parse(text: str) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value == "[]":
                result[key] = []
            else:
                result[key] = value
    return result


def load_yaml(path: str | Path) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        data = handle.read()
    loader = _load_yaml_library()
    if loader is not None:
        return loader(data)
    return _basic_parse(data)


def build_from_manifest(workflow_yaml: str | Path) -> WorkflowManifest:
    """Load and validate a workflow manifest from disk."""

    manifest = load_yaml(workflow_yaml)
    return validate_workflow(manifest)
