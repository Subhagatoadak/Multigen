from __future__ import annotations

import json
import tempfile
from pathlib import Path

from agentic_codex.cli import codex_run


def test_codex_run(tmp_path: Path, monkeypatch):
    workflow = tmp_path / "workflow.yaml"
    workflow.write_text("name: demo\nsteps: []\n", encoding="utf-8")

    captured = []

    def fake_print(payload: str) -> None:
        captured.append(json.loads(payload))

    monkeypatch.setattr("builtins.print", fake_print)
    result = codex_run(["--workflow", str(workflow), "--goal", "ship", "--vars", "k=v"])
    assert result["workflow"] == "demo"
    assert result["steps"] == 0
    printed = captured[0]
    assert printed["vars"] == {"k": "v"}
    assert printed["manifest"]["name"] == "demo"
