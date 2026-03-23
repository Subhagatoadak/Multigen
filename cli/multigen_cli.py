"""
multigen — Developer CLI for the Multigen orchestration framework.

Commands
────────
  run      Submit a workflow from a YAML/JSON DSL file
  status   Poll workflow state and print a summary
  logs     Stream real-time node-completion events (SSE)
  signal   Send a runtime signal to a running workflow
  init     Scaffold a new Multigen project

Usage
─────
  multigen run  workflow.yaml --payload '{"company":"NovaSemi"}'
  multigen status <workflow-id>
  multigen logs <workflow-id>
  multigen signal interrupt <workflow-id>
  multigen signal resume <workflow-id>
  multigen signal skip-node <workflow-id> --node analyse
  multigen signal jump-to <workflow-id> --node report
  multigen init my-project

Install as a script
───────────────────
  pip install -e .        # if pyproject.toml / setup.py lists the entry point
  # OR run directly:
  python -m cli.multigen_cli <command> [args]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Optional

import click

# ── Base URL resolution ───────────────────────────────────────────────────────

def _base_url(ctx: click.Context) -> str:
    return ctx.obj.get("base_url", "http://localhost:8000")


# ── Async helper ──────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.run(coro)


# ── HTTP helpers (httpx preferred, requests fallback) ─────────────────────────

def _get_http():
    try:
        import httpx
        return httpx
    except ImportError:
        pass
    try:
        import requests as _r
        class _Shim:
            @staticmethod
            def get(url, **kw):
                return _r.get(url, **kw)
            @staticmethod
            def post(url, **kw):
                return _r.post(url, **kw)
        return _Shim()
    except ImportError:
        raise ImportError("Install httpx or requests: pip install httpx")


# ─────────────────────────────────────────────────────────────────────────────
# CLI entry point
# ─────────────────────────────────────────────────────────────────────────────

@click.group()
@click.option(
    "--base-url", "-u",
    default=os.getenv("MULTIGEN_BASE_URL", "http://localhost:8000"),
    show_default=True,
    help="Orchestrator base URL.",
)
@click.pass_context
def cli(ctx: click.Context, base_url: str) -> None:
    """Multigen developer CLI — build, run, and manage agent workflows."""
    ctx.ensure_object(dict)
    ctx.obj["base_url"] = base_url.rstrip("/")


# ─────────────────────────────────────────────────────────────────────────────
# multigen run
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("run")
@click.argument("workflow_file", type=click.Path(exists=True, dir_okay=False))
@click.option("--payload", "-p", default="{}", help="JSON payload string.")
@click.option("--payload-file", "-P", type=click.Path(exists=True), help="JSON payload file.")
@click.option("--workflow-id", "-i", default=None, help="Override workflow ID.")
@click.option("--local", is_flag=True, help="Run in local mode (no Temporal).")
@click.option("--watch", "-w", is_flag=True, help="Stream events after submission (implies --local off).")
@click.pass_context
def cmd_run(
    ctx: click.Context,
    workflow_file: str,
    payload: str,
    payload_file: Optional[str],
    workflow_id: Optional[str],
    local: bool,
    watch: bool,
) -> None:
    """Submit a workflow DSL file (.yaml or .json) and print the workflow ID."""
    import yaml  # soft dep

    path = Path(workflow_file)
    raw = path.read_text()
    dsl = yaml.safe_load(raw) if path.suffix in (".yaml", ".yml") else json.loads(raw)

    if payload_file:
        pl = json.loads(Path(payload_file).read_text())
    else:
        pl = json.loads(payload)

    wf_id = workflow_id or f"cli-{uuid.uuid4().hex[:8]}"

    if local:
        _run_local(dsl, pl, wf_id)
        return

    base = _base_url(ctx)
    http = _get_http()

    # Determine endpoint: graph vs sequence
    if "nodes" in dsl:
        endpoint = f"{base}/workflows/graph/run"
        body = {"graph_def": dsl, "payload": pl, "workflow_id": wf_id}
    else:
        endpoint = f"{base}/workflows/run"
        body = {"dsl": dsl, "payload": pl, "workflow_id": wf_id}

    resp = http.post(endpoint, json=body)
    resp.raise_for_status()
    data = resp.json()
    instance_id = data.get("instance_id") or data.get("workflow_id") or wf_id

    click.echo(click.style("✓ Workflow submitted", fg="green") + f"  id={instance_id}")

    if watch:
        ctx.invoke(cmd_logs, workflow_id=instance_id)


def _run_local(dsl: dict, payload: dict, workflow_id: str) -> None:
    """Execute workflow in-process without Temporal (local dev mode)."""
    try:
        from sdk.multigen.local_runner import LocalWorkflowRunner
    except ImportError:
        click.echo(click.style("local_runner not available", fg="red"), err=True)
        sys.exit(1)

    click.echo(click.style("⚙  Running in local mode (no Temporal)", fg="yellow"))
    results = asyncio.run(LocalWorkflowRunner().run(dsl, payload, workflow_id))
    click.echo(json.dumps(results, indent=2, default=str))


# ─────────────────────────────────────────────────────────────────────────────
# multigen status
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("status")
@click.argument("workflow_id")
@click.option("--json-out", is_flag=True, help="Output raw JSON.")
@click.pass_context
def cmd_status(ctx: click.Context, workflow_id: str, json_out: bool) -> None:
    """Print the current state of a running or completed workflow."""
    base = _base_url(ctx)
    http = _get_http()

    resp = http.get(f"{base}/workflows/{workflow_id}/state")
    if resp.status_code == 404:
        click.echo(click.style(f"Workflow '{workflow_id}' not found.", fg="red"), err=True)
        sys.exit(1)
    resp.raise_for_status()
    data = resp.json()

    if json_out:
        click.echo(json.dumps(data, indent=2, default=str))
        return

    nodes = data.get("nodes") or []
    click.echo(f"\nWorkflow: {click.style(workflow_id, bold=True)}")
    click.echo(f"Status:   {click.style(data.get('status', 'unknown'), fg='cyan')}")
    click.echo(f"Nodes:    {len(nodes)} completed\n")

    for n in nodes:
        conf = n.get("confidence", 0)
        color = "green" if conf >= 0.75 else ("yellow" if conf >= 0.5 else "red")
        click.echo(
            f"  {click.style(n.get('node_id','?'), bold=True):30s}"
            f"  conf={click.style(f'{conf:.2f}', fg=color)}"
        )

    # Epistemic summary if available
    ep = data.get("epistemic_report", {}).get("summary", {})
    if ep:
        click.echo("\nEpistemic summary:")
        click.echo(f"  avg_confidence={ep.get('avg_confidence',0):.2f}  "
                   f"flagged={ep.get('nodes_flagged_for_human_review',0)}")


# ─────────────────────────────────────────────────────────────────────────────
# multigen logs
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("logs")
@click.argument("workflow_id")
@click.option("--since", default=-1, help="Resume from event index N.", type=int)
@click.option("--poll", is_flag=True, help="Use polling fallback instead of SSE.")
@click.pass_context
def cmd_logs(ctx: click.Context, workflow_id: str, since: int, poll: bool) -> None:
    """Stream real-time node-completion events for a workflow.

    Uses SSE by default; pass --poll for JSON polling fallback.
    Press Ctrl+C to stop.
    """
    base = _base_url(ctx)

    if poll:
        _poll_logs(base, workflow_id, since)
        return

    try:
        import requests
    except ImportError:
        click.echo("Install requests: pip install requests", err=True)
        sys.exit(1)

    url = f"{base}/workflows/{workflow_id}/stream"
    headers = {"Accept": "text/event-stream"}
    if since >= 0:
        headers["Last-Event-ID"] = str(since)

    click.echo(click.style(f"Streaming events for {workflow_id} …  (Ctrl+C to stop)\n", dim=True))

    try:
        with requests.get(url, headers=headers, stream=True, timeout=300) as resp:
            resp.raise_for_status()
            for raw in resp.iter_lines():
                if not raw:
                    continue
                line = raw.decode("utf-8") if isinstance(raw, bytes) else raw
                if not line.startswith("data:"):
                    continue
                ev = json.loads(line[5:].strip())
                _print_event(ev)
                if ev.get("done"):
                    click.echo(click.style("\n✓ Workflow complete.", fg="green"))
                    break
    except KeyboardInterrupt:
        click.echo("\nInterrupted.")


def _poll_logs(base: str, workflow_id: str, since: int) -> None:
    import time
    http = _get_http()
    cursor = since
    click.echo(click.style(f"Polling events for {workflow_id} …  (Ctrl+C to stop)\n", dim=True))
    try:
        while True:
            resp = http.get(f"{base}/workflows/{workflow_id}/events", params={"since": cursor})
            if resp.status_code == 404:
                click.echo("Workflow not found.", err=True)
                return
            resp.raise_for_status()
            data = resp.json()
            for ev in data.get("events", []):
                _print_event(ev)
                cursor = ev["index"]
            if data.get("done"):
                click.echo(click.style("\n✓ Workflow complete.", fg="green"))
                return
            time.sleep(0.75)
    except KeyboardInterrupt:
        click.echo("\nInterrupted.")


def _print_event(ev: dict) -> None:
    if ev.get("done"):
        return
    idx  = ev.get("index", "?")
    nid  = ev.get("node_id", "?")
    conf = ev.get("confidence", 0)
    ts   = ev.get("timestamp", "")[:19]
    color = "green" if conf >= 0.75 else ("yellow" if conf >= 0.5 else "red")
    click.echo(
        f"[{ts}] #{idx:>3}  "
        f"{click.style(nid, bold=True):35s}"
        f"  conf={click.style(f'{conf:.2f}', fg=color)}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# multigen signal
# ─────────────────────────────────────────────────────────────────────────────

@cli.group("signal")
def cmd_signal() -> None:
    """Send a runtime signal to a running workflow."""


def _send_signal(ctx: click.Context, workflow_id: str, signal: str, body: dict) -> None:
    base = _base_url(ctx)
    http = _get_http()
    resp = http.post(f"{base}/workflows/{workflow_id}/signal/{signal}", json=body)
    if resp.status_code == 404:
        click.echo(click.style(f"Workflow '{workflow_id}' not found.", fg="red"), err=True)
        sys.exit(1)
    resp.raise_for_status()
    click.echo(click.style(f"✓ Signal '{signal}' sent to {workflow_id}", fg="green"))


@cmd_signal.command("interrupt")
@click.argument("workflow_id")
@click.pass_context
def sig_interrupt(ctx: click.Context, workflow_id: str) -> None:
    """Pause workflow at the next node boundary."""
    _send_signal(ctx, workflow_id, "interrupt", {})


@cmd_signal.command("resume")
@click.argument("workflow_id")
@click.pass_context
def sig_resume(ctx: click.Context, workflow_id: str) -> None:
    """Resume a paused workflow."""
    _send_signal(ctx, workflow_id, "resume", {})


@cmd_signal.command("skip-node")
@click.argument("workflow_id")
@click.option("--node", "-n", required=True, help="Node ID to skip.")
@click.pass_context
def sig_skip(ctx: click.Context, workflow_id: str, node: str) -> None:
    """Silently drop a node when it would next execute."""
    _send_signal(ctx, workflow_id, "skip_node", {"node_id": node})


@cmd_signal.command("jump-to")
@click.argument("workflow_id")
@click.option("--node", "-n", required=True, help="Node ID to prioritise.")
@click.pass_context
def sig_jump(ctx: click.Context, workflow_id: str, node: str) -> None:
    """Push a node to the front of the execution queue."""
    _send_signal(ctx, workflow_id, "jump_to", {"node_id": node})


@cmd_signal.command("inject-node")
@click.argument("workflow_id")
@click.option("--spec", "-s", required=True, help="JSON node spec or @file.json.")
@click.pass_context
def sig_inject(ctx: click.Context, workflow_id: str, spec: str) -> None:
    """Inject a new node into a running workflow."""
    node_spec = json.loads(Path(spec[1:]).read_text() if spec.startswith("@") else spec)
    _send_signal(ctx, workflow_id, "inject_node", node_spec)


@cmd_signal.command("prune")
@click.argument("workflow_id")
@click.option("--node", "-n", required=True, help="Root node of branch to cancel.")
@click.pass_context
def sig_prune(ctx: click.Context, workflow_id: str, node: str) -> None:
    """Cancel a node and all its downstream descendants."""
    _send_signal(ctx, workflow_id, "prune_branch", {"node_id": node})


@cmd_signal.command("approve-agent")
@click.argument("workflow_id")
@click.option("--agent", "-a", required=True, help="Agent name to approve.")
@click.pass_context
def sig_approve(ctx: click.Context, workflow_id: str, agent: str) -> None:
    """Approve a pending dynamic agent spec (human-in-the-loop gate)."""
    _send_signal(ctx, workflow_id, "approve_agent", {"agent_name": agent})


@cmd_signal.command("reject-agent")
@click.argument("workflow_id")
@click.option("--agent", "-a", required=True, help="Agent name to reject.")
@click.option("--reason", "-r", default="Rejected via CLI", help="Rejection reason.")
@click.pass_context
def sig_reject(ctx: click.Context, workflow_id: str, agent: str, reason: str) -> None:
    """Reject a pending dynamic agent spec."""
    _send_signal(ctx, workflow_id, "reject_agent", {"agent_name": agent, "reason": reason})


# ─────────────────────────────────────────────────────────────────────────────
# multigen init
# ─────────────────────────────────────────────────────────────────────────────

@cli.command("init")
@click.argument("project_name")
@click.option("--template", "-t", default="basic",
              type=click.Choice(["basic", "graph", "sequence"]),
              help="Project template.")
def cmd_init(project_name: str, template: str) -> None:
    """Scaffold a new Multigen project with agents, workflows, and tests.

    \b
    Creates:
      <project>/
        agents/my_agent.py          — example agent with epistemic envelope
        workflows/my_workflow.yaml  — example workflow DSL
        tests/test_my_agent.py      — pytest unit test skeleton
        .env.example                — environment variable template
        requirements.txt            — dependency list
        README.md                   — project readme
    """
    from cli.scaffold import scaffold_project
    scaffold_project(project_name, template)


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    cli()
