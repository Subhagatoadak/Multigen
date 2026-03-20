from __future__ import annotations

import pytest

from agentic_codex.manifests.validators import ValidationError, validate_workflow


def test_validate_workflow_missing():
    with pytest.raises(ValidationError):
        validate_workflow({"name": "demo"})


def test_validate_workflow_ok():
    manifest = validate_workflow({"name": "demo", "steps": []})
    assert manifest.summary() == "Workflow(name='demo', steps=0, agents=0)"


def test_validate_workflow_with_agents_tools():
    manifest = validate_workflow(
        {
            "name": "demo",
            "steps": [{"name": "research"}],
            "agents": {
                "planner": {"name": "planner", "role": "lead", "tools": ["search"]},
            },
            "tools": {
                "search": {"name": "search", "type": "http", "config": {"url": "https://example.com"}},
            },
            "policies": {
                "pii": {"name": "pii", "type": "redaction"},
            },
            "patterns": {
                "line": {"name": "line", "type": "assembly", "stages": ["research"]},
            },
        }
    )
    assert manifest.agents["planner"].tools == ["search"]
