"""Tool adapter abstractions with permissions and budgeting."""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
import ast
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol


class ToolAdapter(Protocol):
    """Protocol for tool adapters exposed to agents."""

    name: str

    def invoke(self, **kwargs: Any) -> Any:
        ...


@dataclass
class ToolPermissions:
    """Simple allow/deny permissions for tool usage."""

    allowed_skills: Mapping[str, set[str]] = field(default_factory=dict)

    def is_allowed(self, agent: str, tool: str) -> bool:
        if agent not in self.allowed_skills:
            return True
        return tool in self.allowed_skills[agent]


@dataclass
class BudgetGuard:
    """Track usage budgets per tool."""

    max_calls: int
    cost_per_call: float = 1.0
    _call_counts: Dict[str, int] = field(default_factory=dict)

    def note(self, tool: str) -> None:
        count = self._call_counts.get(tool, 0) + 1
        if count > self.max_calls:
            raise RuntimeError(f"Budget exceeded for tool {tool}")
        self._call_counts[tool] = count

    def reset(self) -> None:
        self._call_counts.clear()


@dataclass
class HTTPToolAdapter:
    """Minimal HTTP adapter using :mod:`urllib` for GET requests."""

    name: str = "http.get"
    timeout: float = 5.0

    def invoke(self, *, url: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        from urllib import request, parse  # imported lazily to avoid dependency at import time

        query = ""
        if params:
            query = "?" + parse.urlencode(params)
        with request.urlopen(url + query, timeout=self.timeout) as response:  # noqa: S310 - controlled usage
            body = response.read().decode("utf-8")
        return {"status": response.status, "body": body}


@dataclass
class RAGToolAdapter:
    """Retrieval adapter over an in-memory index."""

    index: Mapping[str, str]
    name: str = "rag.query"

    def invoke(self, *, query: str, k: int = 3) -> Dict[str, Any]:
        matches = [value for key, value in self.index.items() if query.lower() in key.lower()][:k]
        return {"results": matches}


@dataclass
class DBToolAdapter:
    """Very small SQLite helper for demo purposes."""

    connection: sqlite3.Connection
    name: str = "db.query"

    def invoke(self, *, sql: str, params: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
        cursor = self.connection.execute(sql, params or {})
        rows = cursor.fetchall()
        return {"rows": rows}


@dataclass
class CodeExecutionToolAdapter:
    """Sandboxed code execution tool."""

    name: str = "code.exec"
    allowed_builtins: Mapping[str, Any] = field(default_factory=lambda: {"range": range, "len": len, "sum": sum})

    def invoke(self, *, code: str) -> Dict[str, Any]:
        local_env: Dict[str, Any] = {}
        exec(code, {"__builtins__": self.allowed_builtins}, local_env)  # noqa: S102 - controlled globals
        return {"locals": local_env}


@dataclass
class MathToolAdapter:
    """Math evaluation tool using the :mod:`math` module scope."""

    name: str = "math.eval"

    def invoke(self, *, expression: str) -> Dict[str, Any]:
        result = eval(expression, {"__builtins__": {}}, math.__dict__)  # noqa: S307 - controlled math scope
        return {"result": float(result)}


@dataclass
class ReinforcementToolAdapter:
    """Wrap a tool and record rewards for downstream reinforcement learners."""

    wrapped: ToolAdapter
    reward_fn: Optional[Callable[[Any], float]] = None
    name: Optional[str] = None
    history: List[Dict[str, Any]] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.name is None:
            self.name = f"{self.wrapped.name}.rl"

    def invoke(self, **kwargs: Any) -> Dict[str, Any]:
        result = self.wrapped.invoke(**kwargs)
        reward = self.reward_fn(result) if self.reward_fn else None
        entry = {"result": result, "reward": reward}
        self.history.append(entry)
        return {"result": result, "reward": reward}


@dataclass
class SanitizedCodeExecutionToolAdapter:
    """AST-checked code execution that blocks imports/exec/eval."""

    name: str = "code.sanitized_exec"
    allowed_builtins: Mapping[str, Any] = field(default_factory=lambda: {"range": range, "len": len, "sum": sum})

    def _ensure_safe(self, code: str) -> None:
        tree = ast.parse(code, mode="exec")
        forbidden = (ast.Import, ast.ImportFrom, ast.Call, ast.Attribute)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                raise RuntimeError("Imports are not allowed in sanitized code execution.")
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in {"eval", "exec", "__import__"}:
                    raise RuntimeError("Dangerous builtins are not allowed.")
            if isinstance(node, ast.Attribute):
                # Basic guard to prevent __getattribute__/__class__ access
                if isinstance(node.attr, str) and node.attr.startswith("__"):
                    raise RuntimeError("Dunder attribute access is not allowed.")

    def invoke(self, *, code: str) -> Dict[str, Any]:
        self._ensure_safe(code)
        local_env: Dict[str, Any] = {}
        exec(code, {"__builtins__": self.allowed_builtins}, local_env)  # noqa: S102 - restricted builtins
        return {"locals": local_env}


@dataclass
class SafeFileReaderToolAdapter:
    """Read files from an allowed root with extension filters."""

    root: Path
    allowed_suffixes: tuple[str, ...] = (".txt", ".md", ".json", ".yaml", ".yml", ".py")
    name: str = "fs.safe_read"

    def invoke(self, *, path: str, encoding: str = "utf-8") -> Dict[str, Any]:
        target = (self.root / path).resolve()
        root = self.root.resolve()
        if root not in target.parents and target != root:
            raise PermissionError("Access outside the configured root is forbidden.")
        if target.suffix and target.suffix not in self.allowed_suffixes:
            raise PermissionError(f"File type {target.suffix} is not allowed.")
        content = target.read_text(encoding=encoding)
        return {"path": str(target), "content": content}


def guarded_tool(
    adapter: ToolAdapter,
    *,
    agent_name: str,
    permissions: Optional[ToolPermissions] = None,
    budget: Optional[BudgetGuard] = None,
    limiter: Optional["MultiLimiter"] = None,
) -> ToolAdapter:
    """Wrap a tool adapter with permission and budget checks."""

    def _invoke(**kwargs: Any) -> Any:
        if permissions and not permissions.is_allowed(agent_name, adapter.name):
            raise PermissionError(f"Agent {agent_name} not allowed to use tool {adapter.name}")
        if budget:
            budget.note(adapter.name)
        if limiter and not limiter.allow("tool", adapter.name):
            raise RuntimeError(f"Rate limit exceeded for tool {adapter.name}")
        return adapter.invoke(**kwargs)

    class _GuardedAdapter:
        name = adapter.name

        def invoke(self, **kwargs: Any) -> Any:
            return _invoke(**kwargs)

    return _GuardedAdapter()


__all__ = [
    "ToolAdapter",
    "ToolPermissions",
    "BudgetGuard",
    "HTTPToolAdapter",
    "RAGToolAdapter",
    "DBToolAdapter",
    "CodeExecutionToolAdapter",
    "MathToolAdapter",
    "ReinforcementToolAdapter",
    "SanitizedCodeExecutionToolAdapter",
    "SafeFileReaderToolAdapter",
    "guarded_tool",
]
