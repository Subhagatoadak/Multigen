"""
MCP Resource handlers — expose workflow state as browseable resources.

URI scheme:
    multigen://workflows/{workflow_id}/state          all node outputs
    multigen://workflows/{workflow_id}/state/{node}   single node output
    multigen://workflows/{workflow_id}/health         live health
    multigen://workflows/{workflow_id}/metrics        live metrics
    multigen://agents                                 registered agents
"""
from __future__ import annotations

import json
import os
from typing import Any

import mcp.types as types

from sdk.multigen.client import MultigenClient


def _client() -> MultigenClient:
    return MultigenClient(
        base_url=os.getenv("MULTIGEN_BASE_URL", "http://localhost:8000"),
        api_key=os.getenv("MULTIGEN_API_KEY") or None,
    )


async def list_resources() -> list[types.Resource]:
    """
    Returns well-known static resources.  Dynamic per-workflow resources
    are accessed directly via read_resource once the caller knows the workflow_id.
    """
    return [
        types.Resource(
            uri="multigen://agents",
            name="Registered Agents",
            description="All agent names and classes currently loaded in the Multigen worker",
            mimeType="application/json",
        ),
        types.Resource(
            uri="multigen://capabilities",
            name="Capability Directory",
            description="All agent capabilities registered in the Capability Directory",
            mimeType="application/json",
        ),
    ]


async def read_resource(uri: str) -> str:
    """Route resource URIs to the appropriate API calls."""
    async with _client() as c:
        if uri == "multigen://agents":
            agents = await c.list_agents()
            return json.dumps({"agents": agents}, indent=2)

        if uri == "multigen://capabilities":
            caps = await c.list_capabilities()
            return json.dumps({"capabilities": [cap.model_dump() for cap in caps]}, indent=2)

        # multigen://workflows/{id}/state
        # multigen://workflows/{id}/state/{node_id}
        # multigen://workflows/{id}/health
        # multigen://workflows/{id}/metrics
        if uri.startswith("multigen://workflows/"):
            parts = uri.removeprefix("multigen://workflows/").split("/")
            workflow_id = parts[0]

            if len(parts) == 1:
                return json.dumps({"workflow_id": workflow_id, "hint": "append /state /health or /metrics"})

            section = parts[1]

            if section == "state" and len(parts) == 2:
                state = await c.get_state(workflow_id)
                return json.dumps({
                    "workflow_id": state.workflow_id,
                    "count": state.count,
                    "nodes": [
                        {"node_id": n.node_id, "output": n.output, "updated_at": n.updated_at}
                        for n in state.nodes
                    ],
                }, indent=2, default=str)

            if section == "state" and len(parts) == 3:
                node_id = parts[2]
                node = await c.get_node_state(workflow_id, node_id)
                return json.dumps({"node_id": node.node_id, "output": node.output}, indent=2, default=str)

            if section == "health":
                h = await c.get_health(workflow_id)
                return json.dumps(h.model_dump(), indent=2)

            if section == "metrics":
                m = await c.get_metrics(workflow_id)
                return json.dumps(m.model_dump(), indent=2)

    return json.dumps({"error": f"Unknown resource URI: {uri}"})
