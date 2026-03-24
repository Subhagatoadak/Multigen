"""
Agent Polymorphism and Shape-Shifting System
============================================

This module implements a flexible polymorphism framework for agents in the Multigen
SDK. It draws from classical OOP polymorphism and extends it to runtime agent
behavior through the concept of "shapes" — named execution strategies that an
agent can adopt dynamically based on context.

Core concepts:

- **AgentShape**: A named, callable unit of behavior. Think of it as a strategy
  or persona that an agent can embody. Shapes are registered globally and can be
  selected at runtime.

- **ShapeRegistry**: A global catalog of AgentShapes. Agents consult the registry
  to discover and switch between available behaviors. The `@shape` decorator
  factory provides ergonomic registration.

- **PolymorphicAgent**: An agent that delegates execution to whatever shape its
  `morph_fn` selects from the registry given the current context. The agent
  itself is stateless with respect to behavior — its personality is entirely
  determined by the active shape.

- **TypeAdapter**: A type-conversion layer that allows agent outputs to flow
  between shapes that speak different data "dialects". Adapters are registered
  as (from_type, to_type) pairs and composed automatically.

- **MultiDispatch**: Method overloading keyed on runtime argument types. Provides
  a lightweight alternative to isinstance chains when an agent needs to handle
  many input varieties.

- **AgentMixin / LoggingMixin / RetryMixin / CachingMixin**: Composable behavioral
  fragments following the mixin pattern. They add cross-cutting concerns (logging,
  retry, caching) without polluting the core agent class hierarchy.

- **DynamicAgent**: A class factory that assembles a new agent class at runtime
  from a base class and an arbitrary list of mixin classes. This enables
  zero-boilerplate agent customization in plugin or multi-tenant scenarios.

Usage example::

    registry = ShapeRegistry()

    @registry.shape("summarizer", description="Summarizes text")
    async def summarize(ctx):
        return f"Summary of: {ctx['text']}"

    agent = PolymorphicAgent(
        default_shape="summarizer",
        registry=registry,
    )

    result = await agent.run({"text": "Hello world"})
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
)

__all__ = [
    "AgentShape",
    "ShapeRegistry",
    "PolymorphicAgent",
    "TypeAdapter",
    "MultiDispatch",
    "AgentMixin",
    "LoggingMixin",
    "RetryMixin",
    "CachingMixin",
    "DynamicAgent",
]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. AgentShape
# ---------------------------------------------------------------------------


@dataclass
class AgentShape:
    """A named execution strategy that an agent can adopt.

    Attributes:
        name: Unique identifier for the shape within a registry.
        run_fn: Async (or sync) callable that implements the shape's behavior.
            It receives a context ``dict`` and returns any value.
        description: Human-readable description of what this shape does.
        tags: Arbitrary labels for discovery and filtering.
        schema: Optional JSON-Schema-style dict describing the expected context
            keys and the output structure.
    """

    name: str
    run_fn: Callable
    description: str = ""
    tags: List[str] = field(default_factory=list)
    schema: Optional[Dict] = None

    async def __call__(self, ctx: dict) -> Any:
        """Execute the shape's run function, awaiting if necessary."""
        if asyncio.iscoroutinefunction(self.run_fn):
            return await self.run_fn(ctx)
        return self.run_fn(ctx)


# ---------------------------------------------------------------------------
# 2. ShapeRegistry
# ---------------------------------------------------------------------------


class ShapeRegistry:
    """Global registry of named :class:`AgentShape` instances.

    A registry is typically created once per application (or per module) and
    shared among multiple :class:`PolymorphicAgent` instances.

    Example::

        registry = ShapeRegistry()

        @registry.shape("echo", description="Echoes context back")
        async def echo_shape(ctx):
            return ctx
    """

    def __init__(self) -> None:
        self._shapes: Dict[str, AgentShape] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        run_fn: Callable,
        description: str = "",
        tags: Optional[List[str]] = None,
        schema: Optional[Dict] = None,
    ) -> AgentShape:
        """Create and store a new :class:`AgentShape`.

        Args:
            name: Unique shape identifier. Overwrites any existing shape with
                the same name.
            run_fn: The callable that implements the shape.
            description: Optional human-readable description.
            tags: Optional list of string tags.
            schema: Optional dict describing expected inputs/outputs.

        Returns:
            The newly created :class:`AgentShape`.
        """
        agent_shape = AgentShape(
            name=name,
            run_fn=run_fn,
            description=description,
            tags=tags or [],
            schema=schema,
        )
        self._shapes[name] = agent_shape
        logger.debug("Registered shape %r", name)
        return agent_shape

    def get(self, name: str) -> Optional[AgentShape]:
        """Retrieve a shape by name, or ``None`` if not found."""
        return self._shapes.get(name)

    def list_shapes(self) -> List[str]:
        """Return a sorted list of all registered shape names."""
        return sorted(self._shapes.keys())

    def shape(
        self,
        name: str,
        *,
        description: str = "",
        tags: Optional[List[str]] = None,
        schema: Optional[Dict] = None,
    ) -> Callable:
        """Decorator factory that registers the decorated callable as a shape.

        Args:
            name: Shape name to register under.
            description: Optional description forwarded to :meth:`register`.
            tags: Optional tags forwarded to :meth:`register`.
            schema: Optional schema forwarded to :meth:`register`.

        Returns:
            A decorator that registers and then returns the original function
            unchanged (so it can still be called directly if desired).

        Example::

            @registry.shape("my_shape", description="Does something cool")
            async def my_shape_fn(ctx):
                ...
        """

        def decorator(fn: Callable) -> Callable:
            self.register(
                name=name,
                run_fn=fn,
                description=description,
                tags=tags,
                schema=schema,
            )
            return fn

        return decorator

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __contains__(self, name: str) -> bool:
        return name in self._shapes

    def __len__(self) -> int:
        return len(self._shapes)

    def __repr__(self) -> str:
        return f"ShapeRegistry(shapes={self.list_shapes()})"


# ---------------------------------------------------------------------------
# 3. PolymorphicAgent
# ---------------------------------------------------------------------------


class PolymorphicAgent:
    """An agent that morphs into different execution shapes at runtime.

    The agent holds a reference to a :class:`ShapeRegistry` and uses a
    *morph function* to decide which shape to activate for each incoming
    context. If no morph function is supplied the agent always uses
    ``default_shape``.

    Args:
        default_shape: Name of the shape to use when ``morph_fn`` returns
            ``None`` or is not provided.
        registry: The :class:`ShapeRegistry` to consult. A new empty registry
            is created if ``None``.
        morph_fn: Optional callable ``(ctx: dict) -> str`` that inspects the
            incoming context and returns the name of the desired shape.

    Example::

        def pick_shape(ctx):
            return "fast" if ctx.get("urgent") else "thorough"

        agent = PolymorphicAgent(
            default_shape="thorough",
            registry=my_registry,
            morph_fn=pick_shape,
        )

        result = await agent.run({"urgent": True, "text": "..."})
    """

    def __init__(
        self,
        default_shape: str,
        registry: Optional[ShapeRegistry] = None,
        morph_fn: Optional[Callable[[dict], str]] = None,
    ) -> None:
        self._default_shape = default_shape
        self._registry: ShapeRegistry = registry if registry is not None else ShapeRegistry()
        self._morph_fn: Optional[Callable[[dict], str]] = morph_fn
        # _forced_shape is set by the force_shape context manager
        self._forced_shape: Optional[str] = None

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def current_shape(self) -> str:
        """The shape name that is currently locked in (via :meth:`force_shape`),
        or the default shape if no lock is active."""
        return self._forced_shape if self._forced_shape is not None else self._default_shape

    @property
    def available_shapes(self) -> List[str]:
        """All shape names present in the registry."""
        return self._registry.list_shapes()

    # ------------------------------------------------------------------
    # Core execution
    # ------------------------------------------------------------------

    async def run(self, ctx: dict) -> Any:
        """Resolve the active shape and execute it against ``ctx``.

        Shape resolution order:
        1. ``_forced_shape`` if set by :meth:`force_shape`.
        2. Return value of ``morph_fn(ctx)`` if a morph function is provided
           and the returned name exists in the registry.
        3. ``default_shape``.

        Args:
            ctx: Arbitrary context dictionary passed through to the shape.

        Returns:
            Whatever the active shape's ``run_fn`` returns.

        Raises:
            KeyError: If the resolved shape name is not in the registry.
        """
        shape_name = self._resolve_shape(ctx)
        shape = self._registry.get(shape_name)
        if shape is None:
            raise KeyError(
                f"Shape {shape_name!r} not found in registry. "
                f"Available: {self.available_shapes}"
            )
        logger.debug("PolymorphicAgent running shape %r", shape_name)
        return await shape(ctx)

    def _resolve_shape(self, ctx: dict) -> str:
        """Internal: determine which shape name to use for this invocation."""
        if self._forced_shape is not None:
            return self._forced_shape
        if self._morph_fn is not None:
            try:
                candidate = self._morph_fn(ctx)
                if candidate and candidate in self._registry:
                    return candidate
                logger.warning(
                    "morph_fn returned unknown shape %r; falling back to default %r",
                    candidate,
                    self._default_shape,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "morph_fn raised %s; falling back to default shape %r",
                    exc,
                    self._default_shape,
                )
        return self._default_shape

    # ------------------------------------------------------------------
    # force_shape context manager
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def force_shape(self, name: str):
        """Context manager that temporarily locks the agent to a specific shape.

        All :meth:`run` calls inside the ``with`` block will use ``name``
        regardless of the morph function.

        Args:
            name: Shape name to lock to. Must exist in the registry at call
                time or a :exc:`KeyError` is raised immediately.

        Example::

            with agent.force_shape("debug"):
                result = await agent.run(ctx)
        """
        if name not in self._registry:
            raise KeyError(
                f"Cannot force shape {name!r}: not in registry. "
                f"Available: {self.available_shapes}"
            )
        previous = self._forced_shape
        self._forced_shape = name
        try:
            yield self
        finally:
            self._forced_shape = previous

    # ------------------------------------------------------------------
    # Dunder helpers
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"PolymorphicAgent("
            f"default_shape={self._default_shape!r}, "
            f"shapes={self.available_shapes})"
        )


# ---------------------------------------------------------------------------
# 4. TypeAdapter
# ---------------------------------------------------------------------------


class TypeAdapter:
    """Bidirectional type-conversion registry for agent outputs.

    Adapters are registered as ``(from_type, to_type)`` pairs.  :meth:`adapt`
    looks up the registered function and applies it.  If a direct adapter
    does not exist but the value already satisfies ``to_type``, it is returned
    as-is.

    Example::

        adapter = TypeAdapter()
        adapter.register_adapter(str, int, int)
        result = adapter.adapt("42", str, int)  # → 42
    """

    def __init__(self) -> None:
        # Maps (from_type, to_type) → conversion callable
        self._adapters: Dict[Tuple[type, type], Callable] = {}

    def register_adapter(
        self,
        from_type: type,
        to_type: type,
        fn: Callable,
    ) -> None:
        """Register a conversion function from ``from_type`` to ``to_type``.

        Args:
            from_type: The source type.
            to_type: The target type.
            fn: Callable that accepts a single value of ``from_type`` and
                returns a value of ``to_type``.
        """
        self._adapters[(from_type, to_type)] = fn
        logger.debug("Registered adapter %s → %s", from_type.__name__, to_type.__name__)

    def adapt(self, value: Any, from_type: type, to_type: type) -> Any:
        """Convert ``value`` from ``from_type`` to ``to_type``.

        Args:
            value: The value to convert.
            from_type: Declared source type (used for registry lookup).
            to_type: Desired target type.

        Returns:
            Converted value.

        Raises:
            TypeError: If no adapter is registered and the value is not
                already an instance of ``to_type``.
        """
        if from_type == to_type:
            return value
        key = (from_type, to_type)
        if key in self._adapters:
            return self._adapters[key](value)
        # Graceful fall-through: value already satisfies target
        if isinstance(value, to_type):
            return value
        raise TypeError(
            f"No adapter registered for {from_type.__name__} → {to_type.__name__} "
            f"and value {value!r} is not an instance of {to_type.__name__}."
        )

    def can_adapt(self, from_type: type, to_type: type) -> bool:
        """Return ``True`` if :meth:`adapt` would succeed for the given types.

        This checks only for a registered adapter; it does not inspect actual
        values.
        """
        return from_type == to_type or (from_type, to_type) in self._adapters

    def __repr__(self) -> str:
        pairs = [f"{f.__name__}→{t.__name__}" for f, t in self._adapters]
        return f"TypeAdapter(adapters=[{', '.join(pairs)}])"


# ---------------------------------------------------------------------------
# 5. MultiDispatch
# ---------------------------------------------------------------------------


class MultiDispatch:
    """Method overloading keyed on runtime argument type signatures.

    Handlers are registered with the :meth:`dispatch` decorator by providing
    the expected argument types.  On ``__call__``, the dispatcher matches the
    actual types of the positional arguments against registered signatures and
    invokes the best match.

    "Best match" is determined by the number of argument types that are
    satisfied via ``isinstance`` (exact matches are preferred over subclass
    matches since they are checked first).  If no signature matches, the
    *base handler* (the callable passed to the constructor, if any) is used.

    Example::

        md = MultiDispatch()

        @md.dispatch(int, int)
        def add_ints(a, b):
            return a + b

        @md.dispatch(str, str)
        def concat(a, b):
            return a + b

        md(1, 2)       # → 3
        md("x", "y")   # → "xy"
    """

    def __init__(self, base_handler: Optional[Callable] = None) -> None:
        # List of (type_tuple, handler) in registration order
        self._handlers: List[Tuple[Tuple[type, ...], Callable]] = []
        self._base_handler = base_handler

    def dispatch(self, *types: type) -> Callable:
        """Decorator that registers the decorated function for the given types.

        Args:
            *types: Positional argument types, in order.

        Returns:
            A decorator that registers and returns the original function.
        """

        def decorator(fn: Callable) -> Callable:
            self._handlers.append((types, fn))
            return fn

        return decorator

    def __call__(self, *args: Any) -> Any:
        """Dispatch to the best-matching handler.

        Args:
            *args: Positional arguments whose types are used for matching.

        Returns:
            Result of the matched handler.

        Raises:
            TypeError: If no handler matches and no base handler was provided.
        """
        for type_sig, handler in self._handlers:
            if len(type_sig) == len(args) and all(
                isinstance(arg, t) for arg, t in zip(args, type_sig)
            ):
                return handler(*args)
        if self._base_handler is not None:
            return self._base_handler(*args)
        raise TypeError(
            f"No handler registered for argument types "
            f"({', '.join(type(a).__name__ for a in args)}) "
            f"and no base handler was provided."
        )

    def __repr__(self) -> str:
        sigs = [
            f"({', '.join(t.__name__ for t in sig)})"
            for sig, _ in self._handlers
        ]
        return f"MultiDispatch(handlers={sigs})"


# ---------------------------------------------------------------------------
# 6. AgentMixin and concrete mixins
# ---------------------------------------------------------------------------


class AgentMixin(ABC):
    """Abstract base class for composable agent behavioral fragments.

    Mixin classes should not define ``__init__`` with required parameters so
    they can participate safely in multiple-inheritance chains.  They add
    methods to the class they are mixed into without replacing the core
    behavior.
    """


class LoggingMixin(AgentMixin):
    """Adds structured before/after logging to any async function.

    Example::

        class MyAgent(LoggingMixin):
            async def run(self, ctx):
                return await self.log_call(self._inner_run)(ctx)
    """

    def log_call(self, fn: Callable) -> Callable:
        """Wrap ``fn`` so that entry and exit are logged at DEBUG level.

        Args:
            fn: Async callable to wrap.

        Returns:
            A new async callable that logs before and after invoking ``fn``.
        """

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            fn_name = getattr(fn, "__name__", repr(fn))
            logger.debug("[LoggingMixin] → calling %s args=%r kwargs=%r", fn_name, args, kwargs)
            start = time.monotonic()
            try:
                result = await fn(*args, **kwargs) if asyncio.iscoroutinefunction(fn) else fn(*args, **kwargs)
                elapsed = time.monotonic() - start
                logger.debug(
                    "[LoggingMixin] ← %s returned in %.4fs result=%r",
                    fn_name,
                    elapsed,
                    result,
                )
                return result
            except Exception as exc:
                elapsed = time.monotonic() - start
                logger.debug(
                    "[LoggingMixin] ← %s raised %s after %.4fs",
                    fn_name,
                    exc,
                    elapsed,
                )
                raise

        return wrapper


class RetryMixin(AgentMixin):
    """Adds automatic retry logic to any async function.

    Example::

        class MyAgent(RetryMixin):
            async def run(self, ctx):
                return await self.with_retry(self._flaky_call, max_retries=3)(ctx)
    """

    def with_retry(
        self,
        fn: Callable,
        max_retries: int = 3,
        delay: float = 1.0,
    ) -> Callable:
        """Wrap ``fn`` with retry-on-exception logic.

        Args:
            fn: Async callable to wrap.
            max_retries: Maximum number of additional attempts after the first
                failure (so total attempts = ``max_retries + 1``).
            delay: Seconds to sleep between attempts.

        Returns:
            Wrapped async callable.
        """

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            fn_name = getattr(fn, "__name__", repr(fn))
            last_exc: Optional[Exception] = None
            for attempt in range(max_retries + 1):
                try:
                    if asyncio.iscoroutinefunction(fn):
                        return await fn(*args, **kwargs)
                    return fn(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    if attempt < max_retries:
                        logger.warning(
                            "[RetryMixin] %s failed (attempt %d/%d): %s — retrying in %.1fs",
                            fn_name,
                            attempt + 1,
                            max_retries + 1,
                            exc,
                            delay,
                        )
                        await asyncio.sleep(delay)
                    else:
                        logger.error(
                            "[RetryMixin] %s exhausted %d retries: %s",
                            fn_name,
                            max_retries + 1,
                            exc,
                        )
            raise last_exc  # type: ignore[misc]

        return wrapper


class CachingMixin(AgentMixin):
    """Adds memoization to any async function using an in-memory dict cache.

    The cache is instance-local (stored on ``self``), so distinct agent
    instances do not share cached values.

    Example::

        class MyAgent(CachingMixin):
            async def run(self, ctx):
                return await self.with_cache(
                    self._expensive_call,
                    cache_key_fn=lambda ctx: ctx["id"],
                )(ctx)
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._cache: Dict[Any, Any] = {}

    def with_cache(
        self,
        fn: Callable,
        cache_key_fn: Optional[Callable] = None,
    ) -> Callable:
        """Wrap ``fn`` so repeated calls with the same cache key are memoized.

        Args:
            fn: Async callable to wrap.
            cache_key_fn: Callable that accepts the same positional arguments
                as ``fn`` and returns a hashable cache key.  Defaults to
                ``repr(args)`` if not provided.

        Returns:
            Wrapped async callable.
        """

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            if cache_key_fn is not None:
                key = cache_key_fn(*args, **kwargs)
            else:
                key = repr(args)

            if key in self._cache:
                logger.debug("[CachingMixin] cache hit for key=%r", key)
                return self._cache[key]

            logger.debug("[CachingMixin] cache miss for key=%r", key)
            if asyncio.iscoroutinefunction(fn):
                result = await fn(*args, **kwargs)
            else:
                result = fn(*args, **kwargs)
            self._cache[key] = result
            return result

        return wrapper

    def clear_cache(self) -> None:
        """Evict all entries from the instance cache."""
        self._cache.clear()


# ---------------------------------------------------------------------------
# 7. DynamicAgent
# ---------------------------------------------------------------------------


class DynamicAgent:
    """Class factory that assembles agent classes at runtime from mixins.

    :meth:`build` uses Python's built-in :func:`type` to create a new class
    that inherits from ``base_class`` and every class in ``mixins``.  This
    enables plugin systems and multi-tenant platforms to compose specialized
    agent classes without writing boilerplate subclasses.

    Example::

        MyAgent = DynamicAgent.build(
            base_class=PolymorphicAgent,
            mixins=[LoggingMixin, RetryMixin],
        )
        agent = MyAgent(default_shape="echo", registry=registry)
    """

    @staticmethod
    def build(
        base_class: Type,
        mixins: List[Type],
        *,
        class_name: Optional[str] = None,
    ) -> Type:
        """Create a new class that inherits from ``base_class`` and all ``mixins``.

        Args:
            base_class: The primary base class (e.g. ``PolymorphicAgent``).
            mixins: Additional mixin classes to fold in.  They are prepended to
                the MRO so their methods shadow base class methods, consistent
                with the standard mixin convention.
            class_name: Name of the generated class.  Defaults to
                ``"Dynamic" + base_class.__name__``.

        Returns:
            A new ``type`` object representing the assembled class.  Instances
            can be created and used exactly like any hand-written subclass.

        Raises:
            TypeError: If ``base_class`` or any element of ``mixins`` is not a
                type.
        """
        if not isinstance(base_class, type):
            raise TypeError(f"base_class must be a type, got {type(base_class)!r}")
        for i, mixin in enumerate(mixins):
            if not isinstance(mixin, type):
                raise TypeError(
                    f"mixins[{i}] must be a type, got {type(mixin)!r}"
                )

        name = class_name or f"Dynamic{base_class.__name__}"
        # Mixin classes come first in the MRO so they override base class
        # methods in a predictable order (left-to-right).
        bases: Tuple[type, ...] = tuple(mixins) + (base_class,)
        cls = type(name, bases, {"__module__": __name__})
        logger.debug(
            "DynamicAgent.build created %r with MRO bases %s",
            name,
            [c.__name__ for c in bases],
        )
        return cls
