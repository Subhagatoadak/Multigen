"""
Agent state initialization, reactive state, and state validation.

Features
--------
- ``StateSchema``    — declare the shape and defaults of an agent's state
- ``StateInitializer`` — factory that produces a validated, fully-populated state dict
- ``ReactiveState``  — observable state container (watchers fire on mutation)
- ``StateValidator`` — composable validation rules with error accumulation
- ``ComputedState``  — derived properties that auto-recompute when dependencies change
- ``StateTransition`` — typed (from, to) transition with guard and side-effect
- ``StateMachineState`` — named state node used by graph/state-machine backends

Usage
-----
    from multigen.state_init import StateSchema, StateInitializer, ReactiveState

    schema = StateSchema({
        "query":    str,
        "results":  list,
        "retries":  (int, 0),     # (type, default)
        "done":     (bool, False),
    })
    init = StateInitializer(schema)
    state = init.create({"query": "hello"})
    # → {"query": "hello", "results": [], "retries": 0, "done": False}

    rs = ReactiveState(state)
    rs.watch("done", lambda old, new: print(f"done changed {old}→{new}"))
    rs["done"] = True   # fires watcher
"""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, Type


# ── StateSchema ───────────────────────────────────────────────────────────────

class StateSchema:
    """
    Declares the expected keys, types, and defaults for an agent's state dict.

    Each entry in ``fields`` can be:
    - ``key: type``               — required field, no default
    - ``key: (type, default)``    — optional field with a default value

    Usage::

        schema = StateSchema({
            "query":   str,
            "retries": (int, 0),
            "tags":    (list, None),   # None → [] via _coerce_default
        })
    """

    def __init__(self, fields: Dict[str, Any]) -> None:
        self._fields: Dict[str, Tuple[Type, Any, bool]] = {}  # name → (type, default, required)
        for key, spec in fields.items():
            if isinstance(spec, tuple) and len(spec) == 2:
                typ, default = spec
                self._fields[key] = (typ, default, False)
            else:
                self._fields[key] = (spec, None, True)

    def required_keys(self) -> List[str]:
        return [k for k, (_, _, req) in self._fields.items() if req]

    def optional_keys(self) -> List[str]:
        return [k for k, (_, _, req) in self._fields.items() if not req]

    def default_for(self, key: str) -> Any:
        typ, default, _ = self._fields[key]
        # Produce a fresh copy for mutable defaults
        if isinstance(default, (list, dict, set)):
            return copy.deepcopy(default)
        if default is None:
            if typ is list:
                return []
            if typ is dict:
                return {}
            if typ is set:
                return set()
            return None
        return default

    def type_for(self, key: str) -> Type:
        typ, _, _ = self._fields[key]
        return typ

    def keys(self) -> List[str]:
        return list(self._fields.keys())

    def __repr__(self) -> str:
        return f"StateSchema({list(self._fields.keys())})"


# ── StateValidator ────────────────────────────────────────────────────────────

@dataclass
class ValidationError:
    key: str
    message: str


class StateValidator:
    """
    Composable validation rules applied to a state dict.

    Usage::

        v = StateValidator()
        v.require("query")
        v.type_check("retries", int)
        v.range("retries", min_val=0, max_val=10)
        v.custom("query", lambda q: len(q) > 0, "query must be non-empty")

        errors = v.validate(state)
        if errors:
            raise ValueError(errors)
    """

    def __init__(self) -> None:
        self._rules: List[Callable[[Dict[str, Any]], Optional[ValidationError]]] = []

    def require(self, key: str) -> "StateValidator":
        def _check(state: Dict) -> Optional[ValidationError]:
            if key not in state or state[key] is None:
                return ValidationError(key, f"'{key}' is required")
            return None
        self._rules.append(_check)
        return self

    def type_check(self, key: str, expected_type: Type) -> "StateValidator":
        def _check(state: Dict) -> Optional[ValidationError]:
            if key in state and state[key] is not None:
                if not isinstance(state[key], expected_type):
                    return ValidationError(
                        key,
                        f"'{key}' expected {expected_type.__name__}, got {type(state[key]).__name__}",
                    )
            return None
        self._rules.append(_check)
        return self

    def range(
        self,
        key: str,
        min_val: Optional[float] = None,
        max_val: Optional[float] = None,
    ) -> "StateValidator":
        def _check(state: Dict) -> Optional[ValidationError]:
            v = state.get(key)
            if v is None:
                return None
            if min_val is not None and v < min_val:
                return ValidationError(key, f"'{key}' must be >= {min_val}, got {v}")
            if max_val is not None and v > max_val:
                return ValidationError(key, f"'{key}' must be <= {max_val}, got {v}")
            return None
        self._rules.append(_check)
        return self

    def custom(
        self,
        key: str,
        predicate: Callable[[Any], bool],
        message: str,
    ) -> "StateValidator":
        def _check(state: Dict) -> Optional[ValidationError]:
            v = state.get(key)
            if v is not None and not predicate(v):
                return ValidationError(key, message)
            return None
        self._rules.append(_check)
        return self

    def validate(self, state: Dict[str, Any]) -> List[ValidationError]:
        errors = []
        for rule in self._rules:
            err = rule(state)
            if err is not None:
                errors.append(err)
        return errors

    def validate_raise(self, state: Dict[str, Any]) -> None:
        errors = self.validate(state)
        if errors:
            msgs = "; ".join(f"{e.key}: {e.message}" for e in errors)
            raise ValueError(f"State validation failed: {msgs}")


# ── StateInitializer ──────────────────────────────────────────────────────────

class StateInitializer:
    """
    Factory that produces a fully-populated, validated state dict from a schema.

    Usage::

        init = StateInitializer(schema, validator=v)
        state = init.create({"query": "hello"})
    """

    def __init__(
        self,
        schema: StateSchema,
        validator: Optional[StateValidator] = None,
    ) -> None:
        self._schema = schema
        self._validator = validator

    def create(self, overrides: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Build a state dict applying defaults then overrides, then validate."""
        state: Dict[str, Any] = {}
        for key in self._schema.keys():
            state[key] = self._schema.default_for(key)
        if overrides:
            state.update(overrides)
        if self._validator:
            self._validator.validate_raise(state)
        return state

    def merge(self, base: Dict[str, Any], patch: Dict[str, Any]) -> Dict[str, Any]:
        """Return a new dict that is a deep merge of *base* updated with *patch*."""
        merged = copy.deepcopy(base)
        for k, v in patch.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = self.merge(merged[k], v)
            else:
                merged[k] = v
        return merged


# ── ReactiveState ─────────────────────────────────────────────────────────────

class ReactiveState:
    """
    Observable dict-like container.  Watchers fire when a key's value changes.

    Usage::

        rs = ReactiveState({"count": 0})
        rs.watch("count", lambda old, new: print(f"{old}→{new}"))
        rs["count"] = 1   # prints "0→1"

        # Async watchers are also supported
        async def on_change(old, new): ...
        rs.watch("status", on_change)
    """

    def __init__(self, initial: Optional[Dict[str, Any]] = None) -> None:
        self._data: Dict[str, Any] = dict(initial or {})
        self._watchers: Dict[str, List[Callable]] = {}

    def watch(self, key: str, fn: Callable) -> None:
        self._watchers.setdefault(key, []).append(fn)

    def unwatch(self, key: str, fn: Optional[Callable] = None) -> None:
        if fn is None:
            self._watchers.pop(key, None)
        else:
            self._watchers[key] = [f for f in self._watchers.get(key, []) if f is not fn]

    def __setitem__(self, key: str, value: Any) -> None:
        old = self._data.get(key)
        self._data[key] = value
        if old != value:
            for fn in self._watchers.get(key, []):
                if asyncio.iscoroutinefunction(fn):
                    asyncio.create_task(fn(old, value))
                else:
                    fn(old, value)

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def update(self, mapping: Dict[str, Any]) -> None:
        for k, v in mapping.items():
            self[k] = v

    def snapshot(self) -> Dict[str, Any]:
        return copy.deepcopy(self._data)

    def keys(self):
        return self._data.keys()

    def items(self):
        return self._data.items()

    def __contains__(self, key: str) -> bool:
        return key in self._data

    def __repr__(self) -> str:
        return f"ReactiveState({self._data!r})"


# ── ComputedState ──────────────────────────────────────────────────────────────

class ComputedState:
    """
    Derived properties that auto-recompute when their declared dependencies change.

    Usage::

        rs = ReactiveState({"price": 10.0, "qty": 3})
        cs = ComputedState(rs)
        cs.define("total", ["price", "qty"], lambda s: s["price"] * s["qty"])
        print(cs["total"])  # 30.0
        rs["price"] = 20.0
        print(cs["total"])  # 60.0  (recomputed lazily)
    """

    def __init__(self, reactive: ReactiveState) -> None:
        self._reactive = reactive
        self._computeds: Dict[str, Tuple[List[str], Callable]] = {}
        self._cache: Dict[str, Any] = {}
        self._dirty: Dict[str, bool] = {}

    def define(
        self,
        name: str,
        depends_on: List[str],
        fn: Callable[[ReactiveState], Any],
    ) -> None:
        self._computeds[name] = (depends_on, fn)
        self._dirty[name] = True

        def invalidate(old, new, _name=name):  # noqa: ARG001
            self._dirty[_name] = True

        for dep in depends_on:
            self._reactive.watch(dep, invalidate)

    def __getitem__(self, name: str) -> Any:
        if name not in self._computeds:
            raise KeyError(name)
        if self._dirty.get(name, True):
            _, fn = self._computeds[name]
            self._cache[name] = fn(self._reactive)
            self._dirty[name] = False
        return self._cache[name]

    def keys(self) -> List[str]:
        return list(self._computeds.keys())


# ── StateTransition ───────────────────────────────────────────────────────────

@dataclass
class StateTransition:
    """
    A typed (from_state → to_state) transition with optional guard and side-effect.

    Usage::

        t = StateTransition(
            from_state="idle",
            to_state="running",
            guard=lambda ctx: ctx.get("ready", False),
            on_enter=lambda ctx: print("entering running"),
        )
        if t.can_apply(ctx):
            await t.apply(ctx)
    """

    from_state: str
    to_state: str
    guard: Optional[Callable[[Dict[str, Any]], bool]] = None
    on_enter: Optional[Callable[[Dict[str, Any]], Any]] = None
    on_exit: Optional[Callable[[Dict[str, Any]], Any]] = None
    label: str = ""

    def can_apply(self, ctx: Dict[str, Any]) -> bool:
        if self.guard is None:
            return True
        return bool(self.guard(ctx))

    async def apply(self, ctx: Dict[str, Any]) -> Dict[str, Any]:
        if self.on_exit:
            result = self.on_exit(ctx)
            if asyncio.iscoroutine(result):
                await result
        ctx = dict(ctx)
        ctx["_state"] = self.to_state
        if self.on_enter:
            result = self.on_enter(ctx)
            if asyncio.iscoroutine(result):
                await result
        return ctx


# ── StateMachineState ─────────────────────────────────────────────────────────

@dataclass
class StateMachineState:
    """
    Named state node used by graph / state-machine backends.

    Carries entry/exit hooks and a list of outgoing transitions.
    """

    name: str
    is_terminal: bool = False
    on_enter: Optional[Callable] = None
    on_exit: Optional[Callable] = None
    transitions: List[StateTransition] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_transition(self, transition: StateTransition) -> None:
        self.transitions.append(transition)

    def next_state(self, ctx: Dict[str, Any]) -> Optional[str]:
        """Return the to_state of the first applicable transition."""
        for t in self.transitions:
            if t.can_apply(ctx):
                return t.to_state
        return None


__all__ = [
    "ComputedState",
    "ReactiveState",
    "StateInitializer",
    "StateMachineState",
    "StateSchema",
    "StateTransition",
    "StateValidator",
    "ValidationError",
]
