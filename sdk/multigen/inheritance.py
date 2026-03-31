"""
Agent inheritance, mixins, traits, and method overloading.

Concepts
--------
- **Inheritance** — ``BaseAgent`` subclasses can override ``run()``, ``pre_run()``,
  ``post_run()``, and ``on_error()`` with a well-defined call order.
- **Mixins / Traits** — composable behaviours (logging, retry, caching, auth)
  that are orthogonal to the core agent logic.
- **Method overloading** — ``@overload`` dispatch by argument type, plus
  ``MultiMethod`` for n-ary dispatch across multiple argument positions.
- **Agent factory** — ``build_agent(base, *mixins, **attrs)`` creates a new
  class dynamically, useful for generating specialised agents at runtime.

Usage
-----
    from multigen.inheritance import (
        InheritableAgent, LoggingTrait, RetryTrait,
        overload, MultiMethod, build_agent,
    )

    # ① Inheritance with hook overrides
    class MyAgent(InheritableAgent):
        async def run(self, ctx):
            return {"answer": 42}

        async def pre_run(self, ctx):
            ctx["_started"] = True

    # ② Trait composition
    agent = build_agent(MyAgent, LoggingTrait, RetryTrait(max_retries=3))

    # ③ Method overloading
    @overload(int)
    def process(x):
        return x * 2

    @overload(str)
    def process(x):
        return x.upper()
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable, Dict, List, Optional, Type

logger = logging.getLogger(__name__)


# ── Inheritable agent base ────────────────────────────────────────────────────

class InheritableAgent:
    """
    Rich base class for agents that support hook overrides and cooperative
    multiple inheritance.

    Subclass and override any of the hook methods; all hooks are optional.

    Call order
    ----------
    1. ``pre_run(ctx)``
    2. ``run(ctx)``        ← only required override
    3. ``post_run(ctx, result)``
    4. ``on_error(ctx, exc)``  (only on exception)
    """

    name: str = "agent"

    async def pre_run(self, ctx: Dict[str, Any]) -> None:
        """Called before ``run()``.  Mutate *ctx* in-place for setup."""

    async def run(self, ctx: Dict[str, Any]) -> Any:  # noqa: ARG002
        """Override this method with the agent's core logic."""
        raise NotImplementedError(f"{type(self).__name__}.run() is not implemented")

    async def post_run(self, ctx: Dict[str, Any], result: Any) -> Any:
        """Called after a successful ``run()``.  Return value replaces result."""
        return result

    async def on_error(self, ctx: Dict[str, Any], exc: Exception) -> Any:
        """Called when ``run()`` raises.  Re-raise to propagate, or return a fallback."""
        raise exc

    async def __call__(self, ctx: Dict[str, Any]) -> Any:
        """Orchestrates the full pre→run→post→error lifecycle."""
        await self.pre_run(ctx)
        try:
            result = await self.run(ctx)
            return await self.post_run(ctx, result)
        except Exception as exc:
            return await self.on_error(ctx, exc)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if "name" not in cls.__dict__:
            cls.name = cls.__name__


# ── Trait base ────────────────────────────────────────────────────────────────

class Trait:
    """
    Base class for composable agent behaviours.

    A trait wraps the agent's ``run()`` call via cooperative multiple inheritance.
    Override ``_wrap_run(fn)`` to inject behaviour around the actual run call.
    """

    async def run(self, ctx: Dict[str, Any]) -> Any:
        # Cooperative super() call — works in MRO chain
        return await super().run(ctx)  # type: ignore[misc]


# ── Built-in traits ───────────────────────────────────────────────────────────

class LoggingTrait(Trait):
    """Logs agent entry, exit, and duration at DEBUG level."""

    async def run(self, ctx: Dict[str, Any]) -> Any:
        agent_name = getattr(self, "name", type(self).__name__)
        logger.debug("[%s] run started", agent_name)
        start = time.perf_counter()
        try:
            result = await super().run(ctx)
            ms = round((time.perf_counter() - start) * 1000, 2)
            logger.debug("[%s] run completed in %s ms", agent_name, ms)
            return result
        except Exception as exc:
            logger.debug("[%s] run failed: %s", agent_name, exc)
            raise


class RetryTrait(Trait):
    """Retries ``run()`` up to *max_retries* times on transient failures."""

    def __init__(self, *args: Any, max_retries: int = 3, delay: float = 0.1, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.max_retries = max_retries
        self.delay = delay

    async def run(self, ctx: Dict[str, Any]) -> Any:
        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            try:
                return await super().run(ctx)
            except Exception as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(self.delay * (2 ** attempt))
        raise last_exc  # type: ignore[misc]


class CachingTrait(Trait):
    """Caches ``run()`` results keyed by a subset of *ctx* keys."""

    def __init__(self, *args: Any, cache_keys: Optional[List[str]] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cache_keys = cache_keys or []
        self._cache: Dict[str, Any] = {}

    async def run(self, ctx: Dict[str, Any]) -> Any:
        cache_key = tuple((k, ctx.get(k)) for k in sorted(self._cache_keys))
        if cache_key in self._cache:
            return self._cache[cache_key]
        result = await super().run(ctx)
        self._cache[cache_key] = result
        return result

    def clear_cache(self) -> None:
        self._cache.clear()


class TimingTrait(Trait):
    """Injects ``_duration_ms`` into the returned dict (if result is a dict)."""

    async def run(self, ctx: Dict[str, Any]) -> Any:
        start = time.perf_counter()
        result = await super().run(ctx)
        ms = round((time.perf_counter() - start) * 1000, 2)
        if isinstance(result, dict):
            result["_duration_ms"] = ms
        return result


class ValidatingTrait(Trait):
    """Validates *ctx* against a required-keys list before calling ``run()``."""

    def __init__(self, *args: Any, required_keys: Optional[List[str]] = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._required_keys = required_keys or []

    async def run(self, ctx: Dict[str, Any]) -> Any:
        missing = [k for k in self._required_keys if k not in ctx]
        if missing:
            raise ValueError(f"Missing required context keys: {missing}")
        return await super().run(ctx)


# ── Dynamic agent builder ─────────────────────────────────────────────────────

def build_agent(
    base: Type[InheritableAgent],
    *traits: Any,
    **class_attrs: Any,
) -> Type[InheritableAgent]:
    """
    Construct a new agent class that inherits from *base* and all *traits*
    in MRO order (traits applied left-to-right, outermost first).

    Usage::

        AgentCls = build_agent(
            MyAgent,
            LoggingTrait,
            RetryTrait(max_retries=2),   # instance → class extracted
            name="SpecialAgent",
        )
        agent = AgentCls()
    """
    bases: List[type] = []
    trait_inits: Dict[str, Any] = {}

    for trait in traits:
        if isinstance(trait, type):
            bases.append(trait)
        else:
            # trait instance — extract its class and capture init kwargs
            bases.append(type(trait))
            # copy per-instance attrs set by __init__
            for k, v in trait.__dict__.items():
                if not k.startswith("__"):
                    trait_inits[k] = v

    bases.append(base)
    new_cls = type(
        class_attrs.pop("name", base.__name__ + "WithTraits"),
        tuple(bases),
        {**trait_inits, **class_attrs},
    )
    return new_cls  # type: ignore[return-value]


# ── Single-dispatch method overloading ────────────────────────────────────────

class _OverloadRegistry:
    """Internal registry for a single overloaded function."""

    def __init__(self, name: str) -> None:
        self.name = name
        self._handlers: List[tuple] = []   # [(types_tuple, fn), ...]
        self._default: Optional[Callable] = None

    def register(self, types: tuple, fn: Callable) -> None:
        self._handlers.append((types, fn))

    def set_default(self, fn: Callable) -> None:
        self._default = fn

    def dispatch(self, *args: Any) -> Any:
        for types, fn in self._handlers:
            if len(args) >= len(types) and all(
                isinstance(a, t) for a, t in zip(args, types)
            ):
                return fn(*args)
        if self._default:
            return self._default(*args)
        raise TypeError(
            f"No overload of '{self.name}' matches argument types "
            f"{tuple(type(a).__name__ for a in args)}"
        )

    def __call__(self, *args: Any) -> Any:
        return self.dispatch(*args)


_overload_registries: Dict[str, _OverloadRegistry] = {}


def overload(*types: Type) -> Callable:
    """
    Decorator for type-based method overloading (single and multi-dispatch).

    Usage::

        @overload(int)
        def process(x):
            return x * 2

        @overload(str)
        def process(x):
            return x.upper()

        process(5)       # → 10
        process("hi")    # → "HI"
    """
    def decorator(fn: Callable) -> _OverloadRegistry:
        name = fn.__qualname__
        if name not in _overload_registries:
            _overload_registries[name] = _OverloadRegistry(name)
        registry = _overload_registries[name]
        registry.register(types, fn)
        return registry
    return decorator


# ── MultiMethod (n-ary dispatch) ──────────────────────────────────────────────

class MultiMethod:
    """
    Multi-method dispatch: select a handler based on the types of *all* arguments.

    Unlike ``functools.singledispatch`` which only looks at the first argument,
    ``MultiMethod`` considers the full type signature.

    Usage::

        mm = MultiMethod("combine")

        @mm.register(int, int)
        def _(a, b):
            return a + b

        @mm.register(str, str)
        def _(a, b):
            return a + " " + b

        mm(1, 2)           # → 3
        mm("hello", "world")  # → "hello world"
    """

    def __init__(self, name: str = "") -> None:
        self.name = name
        self._table: Dict[tuple, Callable] = {}
        self._default: Optional[Callable] = None

    def register(self, *types: Type) -> Callable:
        def decorator(fn: Callable) -> Callable:
            self._table[types] = fn
            return fn
        return decorator

    def default(self, fn: Callable) -> Callable:
        self._default = fn
        return fn

    def __call__(self, *args: Any) -> Any:
        key = tuple(type(a) for a in args)
        if key in self._table:
            return self._table[key](*args)
        # Try base-class matches
        for sig, fn in self._table.items():
            if len(sig) == len(args) and all(isinstance(a, t) for a, t in zip(args, sig)):
                return fn(*args)
        if self._default:
            return self._default(*args)
        raise TypeError(
            f"MultiMethod '{self.name}': no handler for types {key}"
        )


# ── Protocol-based duck typing helper ─────────────────────────────────────────

def implements(*protocols: type) -> Callable:
    """
    Class decorator that checks the decorated class implements all methods
    required by *protocols*.

    Raises ``TypeError`` at class definition time if a required method is missing.

    Usage::

        class Runnable:
            def run(self, ctx): ...

        @implements(Runnable)
        class MyAgent:
            async def run(self, ctx): return {}
    """
    def decorator(cls: type) -> type:
        for protocol in protocols:
            for attr in vars(protocol):
                if attr.startswith("_"):
                    continue
                if not hasattr(cls, attr):
                    raise TypeError(
                        f"{cls.__name__} does not implement '{attr}' "
                        f"required by {protocol.__name__}"
                    )
        return cls
    return decorator


# ── Convenience: mixin decorator ─────────────────────────────────────────────

def mixin(*trait_classes: Type[Trait]) -> Callable:
    """
    Class decorator that prepends *trait_classes* to the MRO of the decorated
    agent class, enabling trait injection without explicit inheritance.

    Usage::

        @mixin(LoggingTrait, RetryTrait)
        class MyAgent(InheritableAgent):
            async def run(self, ctx):
                return {"ok": True}
    """
    def decorator(cls: Type[InheritableAgent]) -> Type[InheritableAgent]:
        bases = tuple(trait_classes) + cls.__bases__
        return type(cls.__name__, bases, dict(cls.__dict__))  # type: ignore[return-value]
    return decorator


# ── Super-call helper for cooperative __init__ ────────────────────────────────

def cooperative_init(cls: type, instance: Any, *args: Any, **kwargs: Any) -> None:
    """
    Call ``super().__init__`` cooperatively so all mixin ``__init__`` methods
    fire even in a diamond inheritance chain.

    Usage (inside ``__init__``)::

        def __init__(self, *args, **kwargs):
            cooperative_init(MyClass, self, *args, **kwargs)
    """
    super(cls, instance).__init__(*args, **kwargs)


__all__ = [
    "CachingTrait",
    "InheritableAgent",
    "LoggingTrait",
    "MultiMethod",
    "RetryTrait",
    "TimingTrait",
    "Trait",
    "ValidatingTrait",
    "build_agent",
    "cooperative_init",
    "implements",
    "mixin",
    "overload",
]
