"""
Multigen MCP Server
===================

Exposes the full Multigen orchestrator as MCP tools so any MCP-capable
host (Claude Code, Claude Desktop, Cursor, Windsurf, custom agents) can:

  - Start multi-agent workflows from natural language or structured DSL
  - Build and execute reasoning graphs with cycles, fan-out, reflection
  - Control running workflows: interrupt, jump, skip, reroute, prune
  - Read distributed state, health, and execution metrics live

Run standalone:
    python -m mcp_server.server

Run via MCP CLI:
    mcp run mcp_server/server.py

Install in Claude Desktop (~/Library/Application Support/Claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "multigen": {
          "command": "python",
          "args": ["-m", "mcp_server.server"],
          "env": {
            "MULTIGEN_BASE_URL": "http://localhost:8000",
            "MULTIGEN_API_KEY": ""
          }
        }
      }
    }

Environment variables:
    MULTIGEN_BASE_URL   Orchestrator URL   (default: http://localhost:8000)
    MULTIGEN_API_KEY    Bearer token       (default: empty — no auth)
    MULTIGEN_TIMEOUT    Request timeout s  (default: 30)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

import mcp.server.stdio
import mcp.types as types
from mcp.server import Server
from mcp.server.models import InitializationOptions

from mcp_server.tools import TOOL_REGISTRY, dispatch_tool
from mcp_server.resources import list_resources, read_resource
from mcp_server.prompts import PROMPT_REGISTRY, render_prompt

# Ensure the repo root is on the path when run directly
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("multigen.mcp")

# ── Server instance ────────────────────────────────────────────────────────────

server = Server("multigen-orchestrator")


# ── Tool handler ───────────────────────────────────────────────────────────────

@server.list_tools()
async def handle_list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=t["name"],
            description=t["description"],
            inputSchema=t["schema"],
        )
        for t in TOOL_REGISTRY
    ]


@server.call_tool()
async def handle_call_tool(
    name: str,
    arguments: dict[str, Any] | None,
) -> list[types.TextContent]:
    logger.info("Tool call: %s  args=%s", name, arguments)
    try:
        result = await dispatch_tool(name, arguments or {})
        text = json.dumps(result, indent=2, default=str)
    except Exception as exc:
        logger.exception("Tool %s failed", name)
        text = json.dumps({"error": str(exc), "tool": name})
    return [types.TextContent(type="text", text=text)]


# ── Resource handler ───────────────────────────────────────────────────────────

@server.list_resources()
async def handle_list_resources() -> list[types.Resource]:
    return await list_resources()


@server.read_resource()
async def handle_read_resource(uri: str) -> str:
    return await read_resource(uri)


# ── Prompt handler ─────────────────────────────────────────────────────────────

@server.list_prompts()
async def handle_list_prompts() -> list[types.Prompt]:
    return [
        types.Prompt(
            name=p["name"],
            description=p["description"],
            arguments=p.get("arguments", []),
        )
        for p in PROMPT_REGISTRY
    ]


@server.get_prompt()
async def handle_get_prompt(
    name: str,
    arguments: dict[str, str] | None,
) -> types.GetPromptResult:
    messages = render_prompt(name, arguments or {})
    return types.GetPromptResult(messages=messages)


# ── Entry point ────────────────────────────────────────────────────────────────

async def main() -> None:
    logger.info(
        "Multigen MCP Server starting — orchestrator at %s",
        os.getenv("MULTIGEN_BASE_URL", "http://localhost:8000"),
    )
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="multigen-orchestrator",
                server_version="0.2.0",
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
