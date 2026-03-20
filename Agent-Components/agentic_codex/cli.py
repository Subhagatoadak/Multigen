"""Command line entry-points."""
from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Dict

from .manifests.loaders import build_from_manifest
from .core.observability.run_store import RunStore
from .core.safety.rate_limit import MultiLimiter
from .core.safety.policy_apply import apply_rate_limits, load_prompts
from .core.message_bus import CommunicationHub
from .core.prompting import PromptManager


def _parse_vars(pairs: list[str]) -> Dict[str, str]:
    return dict(pair.split("=", 1) for pair in pairs)


def _cmd_run(args: argparse.Namespace) -> Dict[str, Any]:
    manifest = build_from_manifest(args.workflow)
    hub = CommunicationHub()
    limiter = MultiLimiter()
    apply_rate_limits(manifest, limiter=limiter, hub=hub)
    prompt_manager = PromptManager()
    load_prompts(manifest, prompt_manager)
    variables = _parse_vars(args.vars)
    result = {
        "workflow": manifest.name,
        "goal": args.goal,
        "vars": variables,
        "steps": len(manifest.steps),
    }
    manifest_dump = getattr(manifest, "model_dump", None)
    manifest_payload = manifest_dump() if manifest_dump else manifest.dict()
    payload = {**result, "manifest": manifest_payload}
    if args.run_store:
        store = RunStore(args.run_store)
        run_id = f"{manifest.name}-{abs(hash(args.goal)) % 1_000_000}"
        payload["run_id"] = run_id
        stored_path = store.save(payload)
        payload["stored_path"] = str(stored_path)
    print(json.dumps(payload))
    return result


def _cmd_validate(args: argparse.Namespace) -> Dict[str, Any]:
    manifest = build_from_manifest(args.workflow)
    summary = manifest.summary()
    payload = {"ok": True, "summary": summary}
    print(json.dumps(payload))
    return payload


def _cmd_replay(args: argparse.Namespace) -> Dict[str, Any]:
    store = RunStore(args.run_store)
    data = store.load(args.run_id)
    print(json.dumps({"run_id": args.run_id, "events": data.get("events", []), "meta": data.get("meta", {})}))
    return data


def _cmd_register(args: argparse.Namespace) -> Dict[str, Any]:
    manifest = build_from_manifest(args.workflow)
    registry = Path(args.registry)
    registry.mkdir(parents=True, exist_ok=True)
    target = registry / f"{manifest.name}.yaml"
    shutil.copy2(args.workflow, target)
    payload = {"ok": True, "name": manifest.name, "path": str(target)}
    print(json.dumps(payload))
    return payload


def codex_cli(argv: list[str] | None = None) -> Dict[str, Any]:
    parser = argparse.ArgumentParser(description="Agentic CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run a workflow manifest")
    run_p.add_argument("--workflow", required=True, help="Path to workflow YAML")
    run_p.add_argument("--goal", required=True, help="Goal for the run")
    run_p.add_argument("--vars", nargs="*", default=[], help="Key=Value pairs")
    run_p.add_argument("--run-store", help="Directory to persist run output/manifest for replay")
    run_p.set_defaults(func=_cmd_run)

    val_p = sub.add_parser("validate", help="Validate a workflow manifest")
    val_p.add_argument("--workflow", required=True, help="Path to workflow YAML")
    val_p.set_defaults(func=_cmd_validate)

    replay_p = sub.add_parser("replay", help="Replay a stored run JSON")
    replay_p.add_argument("--run-store", required=True, help="Directory containing stored runs")
    replay_p.add_argument("--run-id", required=True, help="Run identifier to replay")
    replay_p.set_defaults(func=_cmd_replay)

    reg_p = sub.add_parser("register", help="Register a manifest into a local registry directory")
    reg_p.add_argument("--workflow", required=True, help="Path to workflow YAML")
    reg_p.add_argument("--registry", required=True, help="Directory where manifests are stored")
    reg_p.set_defaults(func=_cmd_register)

    args = parser.parse_args(argv)
    return args.func(args)


def codex_run(argv: list[str] | None = None) -> Dict[str, Any]:
    """Compatibility shim for legacy single-command usage."""

    prefix = ["run"]
    merged = (prefix + argv) if argv else prefix
    return codex_cli(merged)


def agentic_cli(argv: list[str] | None = None) -> Dict[str, Any]:
    """Primary entrypoint using the new agentic CLI name."""

    return codex_cli(argv)


def main() -> None:  # pragma: no cover - CLI passthrough
    agentic_cli()


if __name__ == "__main__":  # pragma: no cover - CLI entry
    main()
