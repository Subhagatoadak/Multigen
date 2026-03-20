# Agentic CLI Guide

The `agentic` CLI manages manifests, runs workflows, validates configs, and replays stored runs. All commands are offline-safe by default. (`codex` remains as a legacy alias.)

## Commands

- `agentic run --workflow wf.yaml --goal "Ship feature" --vars key=value --run-store runs/`
  - Validates and executes the workflow; optional `--run-store` writes a JSON record for replay/audit.
- `agentic validate --workflow wf.yaml`
  - Parses and validates the manifest without running it.
- `agentic register --workflow wf.yaml --registry ./registry`
  - Copies a manifest into a local registry directory named after the manifest (`<name>.yaml`) after validation.
- `agentic replay --run-store runs/ --run-id <id>`
  - Loads a stored run JSON and prints events/meta for inspection.
- `agentic run --workflow wf.yaml --goal ... --vars ... --run-store runs/ --metrics true`
  - When combined with tracing/metrics inside your agents (e.g., `Tracer.metric`), the stored run will include metric events for monitoring.

## Notebook example

- See `manifest_tool_registration.ipynb` for a hands-on example that writes a manifest, then invokes `agentic register` to add it to a local registry.

## Notes

- Use manifests’ `mcp_servers` / `mcp_tools` blocks to describe MCP endpoints and tools; `codex run` will validate these shapes.
- Combine `--run-store` with `GraphRunner`/`with_steps` runs to persist traces; each run captures messages, meta, and events.
- `codex-run` is still supported as a legacy alias for `codex run`.
