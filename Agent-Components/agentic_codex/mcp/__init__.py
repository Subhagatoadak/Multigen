"""Minimal MCP server configuration and tool registry helpers.

This is intentionally lightweight and transport-agnostic so callers can
swap in their own HTTP/WebSocket clients while reusing the registry +
capability plumbing.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional

from ..core.capabilities import ToolCapability
from ..core.tools import ToolAdapter


@dataclass
class MCPServerConfig:
    """Describe how to reach an MCP server instance."""

    name: str
    base_url: str
    api_key: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class MCPToolDescriptor:
    """Metadata for a remote MCP tool."""

    name: str
    description: str
    path: str
    schema: Mapping[str, Any] = field(default_factory=dict)
    method: str = "POST"


class _DefaultMCPAdapter:
    """Stub adapter that echoes payloads for offline/dev setups."""

    def __init__(self, server: MCPServerConfig, descriptor: MCPToolDescriptor) -> None:
        self.server = server
        self.descriptor = descriptor
        self.name = descriptor.name

    def invoke(self, **kwargs: Any) -> Dict[str, Any]:
        payload = {
            "server": self.server.base_url,
            "tool": self.descriptor.name,
            "path": self.descriptor.path,
            "method": self.descriptor.method,
            "args": kwargs,
        }
        # Avoid network calls by default; callers can swap in a real transport adapter.
        return {"transport": "stub", "payload": payload, "note": "Attach a transport adapter for live MCP calls."}


class MCPToolRegistry:
    """Track MCP tool descriptors and expose them as tool capabilities."""

    def __init__(self, server: MCPServerConfig) -> None:
        self.server = server
        self.descriptors: Dict[str, MCPToolDescriptor] = {}
        self.adapters: Dict[str, ToolAdapter] = {}

    def register(
        self,
        descriptor: MCPToolDescriptor,
        *,
        adapter_factory: Optional[Callable[[MCPServerConfig, MCPToolDescriptor], ToolAdapter]] = None,
    ) -> ToolAdapter:
        """Register a tool descriptor and return the bound adapter."""

        factory = adapter_factory or _DefaultMCPAdapter
        adapter = factory(self.server, descriptor)
        self.descriptors[descriptor.name] = descriptor
        self.adapters[descriptor.name] = adapter
        return adapter

    def as_tools(self) -> Mapping[str, ToolAdapter]:
        """Expose registered adapters as a mapping suitable for ToolCapability."""

        return dict(self.adapters)

    def as_capability(self, *, name: str = "mcp_tools", permissions=None, budget=None) -> ToolCapability:
        """Build a ToolCapability that can be attached via AgentBuilder."""

        return ToolCapability(tools=self.adapters, permissions=permissions, budget=budget, name=name)


__all__ = [
    "MCPServerConfig",
    "MCPToolDescriptor",
    "MCPToolRegistry",
]
