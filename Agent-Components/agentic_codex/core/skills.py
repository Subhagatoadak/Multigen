"""Skill registry and built-in skills."""
from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, Dict, Mapping


class SkillRegistry:
    """Simple registry for skills keyed by name."""

    def __init__(self) -> None:
        self._skills: Dict[str, Callable[..., Any]] = {}

    def register(self, name: str, skill: Callable[..., Any]) -> None:
        if name in self._skills:
            raise ValueError(f"Skill {name!r} already registered")
        self._skills[name] = skill

    def get(self, name: str) -> Callable[..., Any]:
        if name not in self._skills:
            raise KeyError(name)
        return self._skills[name]

    def list(self) -> Mapping[str, Callable[..., Any]]:
        return dict(self._skills)


def search_stub(query: str) -> str:
    return f"Search results for {query}"


def web_fetch(url: str) -> str:
    return f"Fetched content from {url}"


def code_exec_sandbox(code: str) -> str:
    allowed = {"__builtins__": {"range": range, "len": len, "sum": sum}}
    exec(code, allowed, allowed)  # nosec - trusted sandbox for examples
    return "; ".join(f"{k}={v}" for k, v in allowed.items() if not k.startswith("__"))


def math_solver(expression: str) -> float:
    return float(eval(expression, {"__builtins__": {}}, math.__dict__))  # noqa: S307 - controlled globals


def rag_query(index: Mapping[str, str], query: str, k: int = 3) -> list[str]:
    return list(index.values())[:k]


def kb_lookup(entity: str) -> str:
    return f"Info about {entity}"


def sql_query(dataset: Mapping[str, Any], sql: str) -> Any:
    return dataset.get(sql)


def file_read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def file_write(path: str, content: str) -> str:
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return path


def vectordb_upsert(index: Dict[str, list[float]], key: str, vector: list[float]) -> None:
    index[key] = vector


def vectordb_search(index: Mapping[str, list[float]], query: list[float], k: int = 3) -> list[str]:
    return list(index.keys())[:k]


def kg_upsert(graph: Dict[str, list[str]], subject: str, obj: str) -> None:
    graph.setdefault(subject, []).append(obj)


def kg_query(graph: Mapping[str, list[str]], subject: str) -> list[str]:
    return graph.get(subject, [])


BUILTIN_SKILLS = {
    "search_stub": search_stub,
    "web_fetch": web_fetch,
    "code_exec_sandbox": code_exec_sandbox,
    "math_solver": math_solver,
    "rag.query": rag_query,
    "kb.lookup": kb_lookup,
    "sql.query": sql_query,
    "file.read": file_read,
    "file.write": file_write,
    "vectordb.upsert": vectordb_upsert,
    "vectordb.search": vectordb_search,
    "kg.upsert": kg_upsert,
    "kg.query": kg_query,
}
