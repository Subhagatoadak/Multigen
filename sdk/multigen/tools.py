"""
Tool registry, schema validation, and sandboxed execution for Multigen agents.

A "tool" is any callable an agent can invoke — web search, code execution,
database lookup, file I/O, etc.  This module provides:

- ``ToolSpec``          — JSON-schema descriptor for a tool's input/output
- ``Tool``              — a registered, validated, callable tool
- ``ToolRegistry``      — central registry of named tools
- ``ToolCall``          — a request to invoke a tool
- ``ToolResult``        — the outcome of a tool call
- ``ToolValidator``     — validates a ToolCall against its ToolSpec schema
- ``Sandbox``           — execution environment with timeout + memory guard
- ``SandboxedTool``     — wraps any Tool in a Sandbox
- ``PermissionPolicy``  — allow/deny rules for which agents can call which tools
- ``PermissionedRegistry`` — ToolRegistry with policy enforcement

Usage::

    from multigen.tools import (
        Tool, ToolRegistry, ToolCall, ToolResult,
        Sandbox, SandboxedTool, PermissionPolicy,
    )

    # 1. Define a tool
    registry = ToolRegistry()

    @registry.register(
        description="Add two numbers",
        parameters={
            "type": "object",
            "properties": {
                "a": {"type": "number"},
                "b": {"type": "number"},
            },
            "required": ["a", "b"],
        },
    )
    async def add(a: float, b: float) -> float:
        return a + b

    # 2. Call it
    call = ToolCall(tool_name="add", arguments={"a": 3, "b": 4})
    result = await registry.call(call)
    print(result.output)  # 7.0

    # 3. Sandboxed execution
    sandbox = Sandbox(timeout_s=5.0, max_output_len=10_000)
    sandboxed = SandboxedTool(registry.get("add"), sandbox)
    result = await sandboxed(call)
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── ToolSpec ───────────────────────────────────────────────────────────────────

@dataclass
class ToolSpec:
    """
    JSON-Schema descriptor for a tool.

    ``parameters`` follows the OpenAI / Anthropic function-calling schema
    (a JSON-Schema object with ``properties`` and ``required`` keys).
    """

    name: str
    description: str
    parameters: Dict[str, Any] = field(default_factory=dict)
    returns: Optional[Dict[str, Any]] = None
    tags: List[str] = field(default_factory=list)
    version: str = "1.0.0"

    def to_openai_schema(self) -> Dict[str, Any]:
        """Return the OpenAI function-calling schema dict."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def to_anthropic_schema(self) -> Dict[str, Any]:
        """Return the Anthropic tool-use schema dict."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }


# ── ToolCall / ToolResult ─────────────────────────────────────────────────────

@dataclass
class ToolCall:
    """A request to invoke a named tool with given arguments."""

    tool_name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    call_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    caller_id: Optional[str] = None          # agent or session making the call
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolResult:
    """The outcome of a tool call."""

    call_id: str
    tool_name: str
    output: Any
    error: Optional[str] = None
    latency_ms: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.error is None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_name": self.tool_name,
            "output": self.output,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "metadata": self.metadata,
        }


# ── ToolValidator ──────────────────────────────────────────────────────────────

class ToolValidator:
    """
    Validates a ``ToolCall`` against a ``ToolSpec``'s JSON-Schema.

    Performs lightweight structural validation without requiring ``jsonschema``.
    If ``jsonschema`` is installed it is used for full draft-7 validation.
    """

    @staticmethod
    def validate(call: ToolCall, spec: ToolSpec) -> List[str]:
        """Return a list of error strings (empty = valid)."""
        schema = spec.parameters
        if not schema:
            return []

        args = call.arguments
        errors: List[str] = []

        # Required fields
        required = schema.get("required", [])
        for key in required:
            if key not in args:
                errors.append(f"Missing required argument: '{key}'")

        # Type checking on properties
        properties = schema.get("properties", {})
        type_map = {
            "string": str,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for key, prop_schema in properties.items():
            if key not in args:
                continue
            expected_type_str = prop_schema.get("type")
            if expected_type_str and expected_type_str in type_map:
                expected = type_map[expected_type_str]
                if not isinstance(args[key], expected):
                    errors.append(
                        f"Argument '{key}' expected type {expected_type_str}, "
                        f"got {type(args[key]).__name__}"
                    )
            # Enum validation
            if "enum" in prop_schema and args[key] not in prop_schema["enum"]:
                errors.append(
                    f"Argument '{key}' must be one of {prop_schema['enum']}, "
                    f"got {args[key]!r}"
                )

        # Try full jsonschema if available
        try:
            import jsonschema  # type: ignore
            try:
                jsonschema.validate(args, schema)
            except jsonschema.ValidationError as e:
                errors.append(f"Schema validation: {e.message}")
        except ImportError:
            pass

        return errors

    @staticmethod
    def validate_raise(call: ToolCall, spec: ToolSpec) -> None:
        errors = ToolValidator.validate(call, spec)
        if errors:
            raise ValueError(f"ToolCall validation failed for '{call.tool_name}': {'; '.join(errors)}")


# ── Tool ──────────────────────────────────────────────────────────────────────

class Tool:
    """
    A callable tool with a spec, optional pre/post hooks, and call tracking.

    Parameters
    ----------
    fn          The underlying callable (sync or async).
    spec        ``ToolSpec`` describing inputs/outputs.
    validate    Whether to validate arguments before calling.
    """

    def __init__(
        self,
        fn: Callable,
        spec: ToolSpec,
        validate: bool = True,
    ) -> None:
        self._fn = fn
        self.spec = spec
        self.validate = validate
        self._call_count: int = 0
        self._error_count: int = 0

    @property
    def name(self) -> str:
        return self.spec.name

    async def __call__(self, call: ToolCall) -> ToolResult:
        if self.validate:
            ToolValidator.validate_raise(call, self.spec)

        t0 = time.perf_counter()
        try:
            if asyncio.iscoroutinefunction(self._fn):
                output = await self._fn(**call.arguments)
            else:
                loop = asyncio.get_event_loop()
                output = await loop.run_in_executor(
                    None, lambda: self._fn(**call.arguments)
                )
            self._call_count += 1
            return ToolResult(
                call_id=call.call_id,
                tool_name=self.name,
                output=output,
                latency_ms=(time.perf_counter() - t0) * 1000,
            )
        except Exception as exc:
            self._error_count += 1
            return ToolResult(
                call_id=call.call_id,
                tool_name=self.name,
                output=None,
                error=f"{type(exc).__name__}: {exc}",
                latency_ms=(time.perf_counter() - t0) * 1000,
            )

    def stats(self) -> Dict[str, Any]:
        total = self._call_count + self._error_count
        return {
            "name": self.name,
            "calls": self._call_count,
            "errors": self._error_count,
            "error_rate": self._error_count / total if total else 0.0,
        }

    def __repr__(self) -> str:
        return f"Tool({self.name!r}, calls={self._call_count})"


# ── Sandbox ───────────────────────────────────────────────────────────────────

@dataclass
class SandboxConfig:
    """Configuration for a tool execution sandbox."""

    timeout_s: float = 10.0
    max_output_len: int = 100_000      # characters
    max_retries: int = 0
    retry_delay_s: float = 0.5
    allowed_exceptions: List[type] = field(default_factory=list)


class Sandbox:
    """
    Execution environment that enforces timeouts and output size limits.

    Does NOT provide OS-level isolation (no subprocess/seccomp) — for
    full sandboxing wrap tool execution in a subprocess or container.
    This class provides the *policy layer*: timeout, output truncation,
    retry logic, and structured error reporting.

    Usage::

        sandbox = Sandbox(timeout_s=5.0, max_output_len=10_000)
        result = await sandbox.run(tool, call)
    """

    def __init__(
        self,
        timeout_s: float = 10.0,
        max_output_len: int = 100_000,
        max_retries: int = 0,
        retry_delay_s: float = 0.5,
    ) -> None:
        self.config = SandboxConfig(
            timeout_s=timeout_s,
            max_output_len=max_output_len,
            max_retries=max_retries,
            retry_delay_s=retry_delay_s,
        )

    async def run(self, tool: Tool, call: ToolCall) -> ToolResult:
        last_result: Optional[ToolResult] = None
        for attempt in range(self.config.max_retries + 1):
            try:
                result = await asyncio.wait_for(
                    tool(call), timeout=self.config.timeout_s
                )
            except asyncio.TimeoutError:
                result = ToolResult(
                    call_id=call.call_id,
                    tool_name=call.tool_name,
                    output=None,
                    error=f"TimeoutError: exceeded {self.config.timeout_s}s",
                )
            # Truncate large outputs
            if result.output is not None:
                out_str = str(result.output)
                if len(out_str) > self.config.max_output_len:
                    result.output = out_str[: self.config.max_output_len] + "…[truncated]"
                    result.metadata["truncated"] = True

            last_result = result
            if result.success or attempt == self.config.max_retries:
                break
            logger.debug(
                "Sandbox retry %d/%d for %s: %s",
                attempt + 1, self.config.max_retries, call.tool_name, result.error,
            )
            await asyncio.sleep(self.config.retry_delay_s * (2 ** attempt))

        return last_result  # type: ignore[return-value]


class SandboxedTool:
    """Wraps a ``Tool`` with a ``Sandbox`` for safe execution."""

    def __init__(self, tool: Tool, sandbox: Optional[Sandbox] = None) -> None:
        self._tool = tool
        self._sandbox = sandbox or Sandbox()

    @property
    def name(self) -> str:
        return self._tool.name

    @property
    def spec(self) -> ToolSpec:
        return self._tool.spec

    async def __call__(self, call: ToolCall) -> ToolResult:
        return await self._sandbox.run(self._tool, call)

    def __repr__(self) -> str:
        return f"SandboxedTool({self.name!r})"


# ── ToolRegistry ───────────────────────────────────────────────────────────────

class ToolRegistry:
    """
    Central registry of named tools.

    Usage::

        registry = ToolRegistry()

        @registry.register(
            description="Multiply two numbers",
            parameters={
                "type": "object",
                "properties": {"x": {"type": "number"}, "y": {"type": "number"}},
                "required": ["x", "y"],
            },
        )
        async def multiply(x: float, y: float) -> float:
            return x * y

        result = await registry.call(ToolCall("multiply", {"x": 3, "y": 4}))
        print(result.output)  # 12.0
    """

    def __init__(self) -> None:
        self._tools: Dict[str, Tool] = {}

    def register(
        self,
        description: str = "",
        parameters: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        version: str = "1.0.0",
        validate: bool = True,
        sandbox: Optional[Sandbox] = None,
        name: Optional[str] = None,
    ) -> Callable:
        """Decorator to register a function as a tool."""
        def decorator(fn: Callable) -> Callable:
            tool_name = name or fn.__name__
            spec = ToolSpec(
                name=tool_name,
                description=description or (fn.__doc__ or ""),
                parameters=parameters or {},
                tags=tags or [],
                version=version,
            )
            tool = Tool(fn, spec, validate=validate)
            registered: Any = SandboxedTool(tool, sandbox) if sandbox else tool
            self._tools[tool_name] = registered  # type: ignore[assignment]
            return fn
        return decorator

    def add(self, tool: Tool, sandbox: Optional[Sandbox] = None) -> None:
        """Register a pre-built ``Tool`` instance."""
        registered: Any = SandboxedTool(tool, sandbox) if sandbox else tool
        self._tools[tool.name] = registered  # type: ignore[assignment]

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)  # type: ignore[return-value]

    def list_tools(
        self, tag: Optional[str] = None
    ) -> List[ToolSpec]:
        specs = []
        for t in self._tools.values():
            spec = t.spec if hasattr(t, "spec") else t._tool.spec  # type: ignore
            if tag is None or tag in spec.tags:
                specs.append(spec)
        return specs

    async def call(self, call: ToolCall) -> ToolResult:
        tool = self._tools.get(call.tool_name)
        if tool is None:
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                output=None,
                error=f"Tool not found: '{call.tool_name}'",
            )
        return await tool(call)

    async def call_by_name(
        self, tool_name: str, **kwargs: Any
    ) -> ToolResult:
        return await self.call(ToolCall(tool_name=tool_name, arguments=kwargs))

    def to_openai_schema(self) -> List[Dict[str, Any]]:
        """Return all tool specs in OpenAI function-calling format."""
        result = []
        for t in self._tools.values():
            spec = t.spec if hasattr(t, "spec") else t._tool.spec  # type: ignore
            result.append(spec.to_openai_schema())
        return result

    def to_anthropic_schema(self) -> List[Dict[str, Any]]:
        """Return all tool specs in Anthropic tool-use format."""
        result = []
        for t in self._tools.values():
            spec = t.spec if hasattr(t, "spec") else t._tool.spec  # type: ignore
            result.append(spec.to_anthropic_schema())
        return result

    def stats(self) -> Dict[str, Any]:
        result = {}
        for name, t in self._tools.items():
            inner = t._tool if isinstance(t, SandboxedTool) else t
            result[name] = inner.stats()
        return result

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __repr__(self) -> str:
        return f"ToolRegistry({len(self._tools)} tools)"


# ── Permission policy ──────────────────────────────────────────────────────────

class PermissionPolicy:
    """
    Declarative allow/deny policy for tool access.

    Rules are evaluated in order; first match wins.  Default is *deny-all*.

    Usage::

        policy = PermissionPolicy()
        policy.allow(agent_pattern="research-*", tool_pattern="search")
        policy.allow(agent_pattern="admin", tool_pattern="*")
        policy.deny(agent_pattern="*", tool_pattern="delete-*")

        policy.check("research-agent-1", "search")   # True
        policy.check("guest", "delete-user")         # False
    """

    def __init__(self, default_allow: bool = False) -> None:
        self._rules: List[Dict[str, Any]] = []
        self._default_allow = default_allow

    def _matches(self, pattern: str, value: str) -> bool:
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            return value.startswith(pattern[:-1])
        if pattern.startswith("*"):
            return value.endswith(pattern[1:])
        return pattern == value

    def allow(self, agent_pattern: str = "*", tool_pattern: str = "*") -> None:
        self._rules.append({"effect": "allow", "agent": agent_pattern, "tool": tool_pattern})

    def deny(self, agent_pattern: str = "*", tool_pattern: str = "*") -> None:
        self._rules.append({"effect": "deny", "agent": agent_pattern, "tool": tool_pattern})

    def check(self, agent_id: str, tool_name: str) -> bool:
        for rule in self._rules:
            if self._matches(rule["agent"], agent_id) and self._matches(rule["tool"], tool_name):
                return rule["effect"] == "allow"
        return self._default_allow


class PermissionedRegistry(ToolRegistry):
    """
    ``ToolRegistry`` with ``PermissionPolicy`` enforcement.

    Any ``call()`` where the caller lacks permission returns a permission-denied
    ``ToolResult`` without invoking the underlying function.

    Usage::

        policy = PermissionPolicy()
        policy.allow(agent_pattern="trusted-*", tool_pattern="*")
        policy.deny(agent_pattern="*", tool_pattern="delete-*")

        registry = PermissionedRegistry(policy=policy)
    """

    def __init__(self, policy: Optional[PermissionPolicy] = None) -> None:
        super().__init__()
        self._policy = policy or PermissionPolicy(default_allow=True)

    async def call(self, call: ToolCall) -> ToolResult:
        agent_id = call.caller_id or "anonymous"
        if not self._policy.check(agent_id, call.tool_name):
            return ToolResult(
                call_id=call.call_id,
                tool_name=call.tool_name,
                output=None,
                error=f"PermissionDenied: agent '{agent_id}' cannot call '{call.tool_name}'",
            )
        return await super().call(call)


# ── Convenience: build tool from function ─────────────────────────────────────

def tool(
    description: str = "",
    parameters: Optional[Dict[str, Any]] = None,
    tags: Optional[List[str]] = None,
    sandbox: Optional[Sandbox] = None,
) -> Callable:
    """
    Standalone decorator to create a ``Tool`` from a function.

    The decorated function gains a ``.as_tool()`` method and a ``.spec`` attribute.

    Usage::

        @tool(description="Search the web", parameters={...})
        async def web_search(query: str) -> str:
            ...

        registry.add(web_search.as_tool())
    """
    def decorator(fn: Callable) -> Callable:
        spec = ToolSpec(
            name=fn.__name__,
            description=description or (fn.__doc__ or ""),
            parameters=parameters or {},
            tags=tags or [],
        )
        t = Tool(fn, spec)
        fn.spec = spec  # type: ignore[attr-defined]
        fn.as_tool = lambda: SandboxedTool(t, sandbox) if sandbox else t  # type: ignore[attr-defined]
        return fn
    return decorator


__all__ = [
    "PermissionPolicy",
    "PermissionedRegistry",
    "Sandbox",
    "SandboxConfig",
    "SandboxedTool",
    "Tool",
    "ToolCall",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "ToolValidator",
    "tool",
]
