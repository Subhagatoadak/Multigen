"""
Multi-model routing for Multigen agents.

Routes LLM calls to the appropriate model based on cost, quality, latency,
load, content, or custom strategies — with health tracking and fallback chains.

Classes
-------
- ModelSpec          — describes a model (name, provider, capabilities, cost, latency)
- ModelHealth        — live health state of a model endpoint
- ModelPool          — pool of ModelSpec objects with health tracking
- RoutingDecision    — records which model was chosen and why
- RoutingStrategy    — base class for routing strategies
- CostRouter         — minimize cost per token
- QualityRouter      — maximize quality score
- LatencyRouter      — minimize expected latency
- RoundRobinRouter   — even distribution across models
- FallbackRouter     — try models in order; move to next on error
- ContentRouter      — route based on input content rules
- ModelRouter        — unified facade (wraps any strategy + fallback)
- AdaptiveRouter     — tracks per-model success rates and routes to the best

Usage::

    from multigen.routing import (
        ModelSpec, ModelPool, ModelRouter, FallbackRouter,
        ContentRouter, CostRouter, QualityRouter, RoutingDecision,
    )

    pool = ModelPool([
        ModelSpec("gpt-4o",       provider="openai",    cost_per_1k=0.005, quality=0.95, latency_ms=800),
        ModelSpec("gpt-3.5-turbo",provider="openai",    cost_per_1k=0.001, quality=0.75, latency_ms=300),
        ModelSpec("claude-3-haiku",provider="anthropic", cost_per_1k=0.00025, quality=0.80, latency_ms=250),
    ])

    router = ModelRouter(pool=pool, strategy=CostRouter())
    decision = await router.route({"prompt": "hello", "_token_estimate": 500})
    print(decision.model.name, decision.reason)

    # Fallback chain
    fb = FallbackRouter([ModelSpec("primary"), ModelSpec("backup")])
    model, err = await fb.route_with_fallback(call_fn)
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class ModelCapability(Enum):
    """Capabilities a model may support."""

    TEXT = auto()
    CODE = auto()
    VISION = auto()
    EMBEDDINGS = auto()
    FUNCTION_CALLING = auto()

    def __repr__(self) -> str:  # noqa: D401
        return f"ModelCapability.{self.name}"


# ---------------------------------------------------------------------------
# ModelSpec
# ---------------------------------------------------------------------------


@dataclass
class ModelSpec:
    """Immutable description of a model endpoint.

    Parameters
    ----------
    name:
        Unique identifier for the model (e.g. ``"gpt-4o"``).
    provider:
        Backend provider string (e.g. ``"openai"``, ``"anthropic"``).
    cost_per_1k_tokens:
        Estimated USD cost per 1 000 tokens (input + output blended).
    quality_score:
        Subjective quality rating in the range ``[0.0, 1.0]``.
    expected_latency_ms:
        Typical time-to-first-token in milliseconds.
    max_tokens:
        Maximum context window in tokens.
    capabilities:
        List of :class:`ModelCapability` values the model supports.
    metadata:
        Arbitrary key/value pairs for consumer use.
    tags:
        Free-form string labels (e.g. ``["fast", "cheap"]``).
    """

    name: str
    provider: str = "unknown"
    cost_per_1k_tokens: float = 0.0
    quality_score: float = 1.0
    expected_latency_ms: float = 500.0
    max_tokens: int = 4096
    capabilities: List[ModelCapability] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not 0.0 <= self.quality_score <= 1.0:
            raise ValueError(
                f"quality_score must be in [0, 1], got {self.quality_score!r}"
            )
        if self.cost_per_1k_tokens < 0:
            raise ValueError(
                f"cost_per_1k_tokens must be >= 0, got {self.cost_per_1k_tokens!r}"
            )
        if self.expected_latency_ms < 0:
            raise ValueError(
                f"expected_latency_ms must be >= 0, got {self.expected_latency_ms!r}"
            )

    def estimated_cost(self, token_count: int = 1000) -> float:
        """Return estimated cost in USD for *token_count* tokens."""
        return self.cost_per_1k_tokens * (token_count / 1000.0)

    def supports(self, capability: ModelCapability) -> bool:
        """Return ``True`` if *capability* is in this spec's capabilities list."""
        return capability in self.capabilities

    def __repr__(self) -> str:
        caps = [c.name for c in self.capabilities]
        return (
            f"ModelSpec(name={self.name!r}, provider={self.provider!r}, "
            f"cost_per_1k={self.cost_per_1k_tokens}, quality={self.quality_score}, "
            f"latency_ms={self.expected_latency_ms}, capabilities={caps})"
        )


# ---------------------------------------------------------------------------
# ModelHealth
# ---------------------------------------------------------------------------


@dataclass
class ModelHealth:
    """Live health state for a single model endpoint.

    Parameters
    ----------
    model_name:
        Must match the corresponding :attr:`ModelSpec.name`.
    is_healthy:
        Whether the endpoint is currently considered healthy.
    error_count:
        Cumulative number of errors recorded.
    success_count:
        Cumulative number of successes recorded.
    last_error:
        String description of the most recent error, if any.
    last_checked:
        Unix timestamp of the most recent health update.
    """

    model_name: str
    is_healthy: bool = True
    error_count: int = 0
    success_count: int = 0
    last_error: Optional[str] = None
    last_checked: float = field(default_factory=time.time)

    @property
    def success_rate(self) -> float:
        """Fraction of calls that succeeded; ``1.0`` when no calls recorded."""
        total = self.success_count + self.error_count
        if total == 0:
            return 1.0
        return self.success_count / total

    def record_success(self) -> None:
        """Increment success counter and refresh timestamp."""
        self.success_count += 1
        self.last_checked = time.time()
        self.is_healthy = True

    def record_failure(self, error: Optional[str] = None) -> None:
        """Increment error counter, store message, and refresh timestamp."""
        self.error_count += 1
        self.last_error = error
        self.last_checked = time.time()

    def __repr__(self) -> str:
        return (
            f"ModelHealth(model={self.model_name!r}, healthy={self.is_healthy}, "
            f"success_rate={self.success_rate:.2%}, errors={self.error_count})"
        )


# ---------------------------------------------------------------------------
# ModelPool
# ---------------------------------------------------------------------------


class ModelPool:
    """Registry of :class:`ModelSpec` objects with live health tracking.

    Parameters
    ----------
    models:
        Initial list of :class:`ModelSpec` objects to register.
    health_check_fn:
        Optional async callable ``(ModelSpec) -> bool`` invoked by
        :meth:`check_health`.  When ``None``, health is derived solely from
        recorded successes/failures.
    """

    def __init__(
        self,
        models: List[ModelSpec],
        health_check_fn: Optional[Callable[[ModelSpec], Awaitable[bool]]] = None,
    ) -> None:
        self._specs: Dict[str, ModelSpec] = {}
        self._health: Dict[str, ModelHealth] = {}
        self._health_check_fn = health_check_fn

        for spec in models:
            self.register(spec)

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, spec: ModelSpec) -> None:
        """Add or replace a :class:`ModelSpec` in the pool."""
        self._specs[spec.name] = spec
        if spec.name not in self._health:
            self._health[spec.name] = ModelHealth(model_name=spec.name)
        logger.debug("ModelPool: registered %r", spec.name)

    def unregister(self, name: str) -> None:
        """Remove a model from the pool by name."""
        self._specs.pop(name, None)
        self._health.pop(name, None)
        logger.debug("ModelPool: unregistered %r", name)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get(self, name: str) -> Optional[ModelSpec]:
        """Return the :class:`ModelSpec` for *name*, or ``None``."""
        return self._specs.get(name)

    def available(self) -> List[ModelSpec]:
        """Return specs whose health state is currently marked healthy."""
        return [
            spec
            for name, spec in self._specs.items()
            if self._health[name].is_healthy
        ]

    def all_models(self) -> List[ModelSpec]:
        """Return all registered specs regardless of health."""
        return list(self._specs.values())

    # ------------------------------------------------------------------
    # Health mutation
    # ------------------------------------------------------------------

    def mark_success(self, name: str) -> None:
        """Record a successful call for model *name*."""
        if name in self._health:
            self._health[name].record_success()

    def mark_failure(self, name: str, error: Optional[str] = None) -> None:
        """Record a failed call for model *name*."""
        if name in self._health:
            self._health[name].record_failure(error)
            # Simple circuit-breaker: mark unhealthy after 5 consecutive errors
            h = self._health[name]
            if h.error_count > 0 and h.success_count == 0 and h.error_count >= 5:
                h.is_healthy = False
                logger.warning(
                    "ModelPool: marking %r unhealthy after %d errors",
                    name,
                    h.error_count,
                )

    def reset_health(self, name: str) -> None:
        """Reset health state for *name* to a fresh healthy baseline."""
        if name in self._health:
            self._health[name] = ModelHealth(model_name=name)

    # ------------------------------------------------------------------
    # External health check
    # ------------------------------------------------------------------

    async def check_health(self) -> None:
        """Run the optional external health-check function against every model.

        Does nothing when no *health_check_fn* was provided at construction.
        """
        if self._health_check_fn is None:
            return
        for name, spec in self._specs.items():
            try:
                healthy = await self._health_check_fn(spec)
                self._health[name].is_healthy = healthy
                self._health[name].last_checked = time.time()
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "ModelPool.check_health failed for %r: %s", name, exc
                )
                self._health[name].is_healthy = False

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    def health_report(self) -> Dict[str, Any]:
        """Return a dictionary snapshot of all models' health states."""
        return {
            name: {
                "is_healthy": h.is_healthy,
                "success_count": h.success_count,
                "error_count": h.error_count,
                "success_rate": round(h.success_rate, 4),
                "last_error": h.last_error,
                "last_checked": h.last_checked,
            }
            for name, h in self._health.items()
        }

    def __len__(self) -> int:
        return len(self._specs)

    def __repr__(self) -> str:
        names = list(self._specs.keys())
        return f"ModelPool(models={names})"


# ---------------------------------------------------------------------------
# RoutingDecision
# ---------------------------------------------------------------------------


@dataclass
class RoutingDecision:
    """Record of a routing outcome for observability and audit.

    Parameters
    ----------
    model:
        The :class:`ModelSpec` that was selected.
    strategy_name:
        Human-readable name of the strategy that made the decision.
    reason:
        Short explanation of why this model was chosen.
    estimated_cost:
        Projected USD cost for the call, if calculable.
    metadata:
        Arbitrary extra data attached by the strategy or router.
    """

    model: ModelSpec
    strategy_name: str
    reason: str
    estimated_cost: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return (
            f"RoutingDecision(model={self.model.name!r}, "
            f"strategy={self.strategy_name!r}, reason={self.reason!r}, "
            f"estimated_cost={self.estimated_cost:.6f})"
        )


# ---------------------------------------------------------------------------
# RoutingStrategy — Abstract Base
# ---------------------------------------------------------------------------


class RoutingStrategy(ABC):
    """Abstract base class for routing strategies.

    Subclasses must implement :meth:`select`, which picks one
    :class:`ModelSpec` from a list of candidates given a call context.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name (defaults to class name)."""
        return self.__class__.__name__

    @abstractmethod
    async def select(
        self, candidates: List[ModelSpec], ctx: Dict[str, Any]
    ) -> ModelSpec:
        """Select one model from *candidates*.

        Parameters
        ----------
        candidates:
            Non-empty list of healthy :class:`ModelSpec` objects.
        ctx:
            Routing context dictionary (e.g. prompt, token estimate).

        Returns
        -------
        ModelSpec
            The chosen model.

        Raises
        ------
        ValueError
            If *candidates* is empty.
        """

    def _require_candidates(self, candidates: List[ModelSpec]) -> None:
        if not candidates:
            raise ValueError(
                f"{self.name}: no candidate models available for routing."
            )

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}()"


# ---------------------------------------------------------------------------
# CostRouter
# ---------------------------------------------------------------------------


class CostRouter(RoutingStrategy):
    """Select the cheapest model by estimated cost.

    Uses ``ctx.get("_token_estimate", 1000)`` to weight cost per token.
    """

    async def select(
        self, candidates: List[ModelSpec], ctx: Dict[str, Any]
    ) -> ModelSpec:
        """Return the candidate with the lowest estimated cost."""
        self._require_candidates(candidates)
        token_estimate: int = int(ctx.get("_token_estimate", 1000))
        best = min(candidates, key=lambda m: m.estimated_cost(token_estimate))
        logger.debug(
            "CostRouter: selected %r (cost=%.6f for %d tokens)",
            best.name,
            best.estimated_cost(token_estimate),
            token_estimate,
        )
        return best


# ---------------------------------------------------------------------------
# QualityRouter
# ---------------------------------------------------------------------------


class QualityRouter(RoutingStrategy):
    """Select the model with the highest :attr:`ModelSpec.quality_score`."""

    async def select(
        self, candidates: List[ModelSpec], ctx: Dict[str, Any]
    ) -> ModelSpec:
        """Return the candidate with the highest quality score."""
        self._require_candidates(candidates)
        best = max(candidates, key=lambda m: m.quality_score)
        logger.debug(
            "QualityRouter: selected %r (quality=%.3f)", best.name, best.quality_score
        )
        return best


# ---------------------------------------------------------------------------
# LatencyRouter
# ---------------------------------------------------------------------------


class LatencyRouter(RoutingStrategy):
    """Select the model with the lowest :attr:`ModelSpec.expected_latency_ms`."""

    async def select(
        self, candidates: List[ModelSpec], ctx: Dict[str, Any]
    ) -> ModelSpec:
        """Return the candidate with the lowest expected latency."""
        self._require_candidates(candidates)
        best = min(candidates, key=lambda m: m.expected_latency_ms)
        logger.debug(
            "LatencyRouter: selected %r (latency=%.0f ms)",
            best.name,
            best.expected_latency_ms,
        )
        return best


# ---------------------------------------------------------------------------
# RoundRobinRouter
# ---------------------------------------------------------------------------


class RoundRobinRouter(RoutingStrategy):
    """Distribute calls evenly across models in round-robin order.

    The internal counter is *not* reset between calls, so distribution
    remains balanced across many routing decisions.
    """

    def __init__(self) -> None:
        self._counter: int = 0

    async def select(
        self, candidates: List[ModelSpec], ctx: Dict[str, Any]
    ) -> ModelSpec:
        """Return the next candidate in round-robin order."""
        self._require_candidates(candidates)
        # Sort for determinism so the ordering is consistent across calls.
        ordered = sorted(candidates, key=lambda m: m.name)
        chosen = ordered[self._counter % len(ordered)]
        self._counter += 1
        logger.debug(
            "RoundRobinRouter: selected %r (counter=%d)", chosen.name, self._counter
        )
        return chosen

    def __repr__(self) -> str:
        return f"RoundRobinRouter(counter={self._counter})"


# ---------------------------------------------------------------------------
# ContentRouter
# ---------------------------------------------------------------------------


class ContentRouter(RoutingStrategy):
    """Route based on predicate rules evaluated against the call context.

    Rules are evaluated in insertion order; the first matching rule wins.
    If no rule matches, the router falls back to (1) the configured default
    model name, then (2) the cheapest available candidate.

    Example::

        router = ContentRouter()
        router.add_rule(lambda ctx: "image" in ctx.get("prompt", ""), "gpt-4o")
        router.add_rule(lambda ctx: len(ctx.get("prompt", "")) > 2000, "claude-3-haiku")
        router.set_default("gpt-3.5-turbo")
    """

    def __init__(self) -> None:
        self._rules: List[Tuple[Callable[[Dict[str, Any]], bool], str]] = []
        self._default_model_name: Optional[str] = None

    def add_rule(
        self,
        predicate_fn: Callable[[Dict[str, Any]], bool],
        model_name: str,
    ) -> None:
        """Append a routing rule.

        Parameters
        ----------
        predicate_fn:
            Callable that receives the routing context and returns ``True``
            when this rule should apply.
        model_name:
            Name of the :class:`ModelSpec` to route to when *predicate_fn*
            returns ``True``.
        """
        self._rules.append((predicate_fn, model_name))

    def set_default(self, model_name: str) -> None:
        """Set the fallback model name used when no rule matches."""
        self._default_model_name = model_name

    async def select(
        self, candidates: List[ModelSpec], ctx: Dict[str, Any]
    ) -> ModelSpec:
        """Evaluate rules in order; return first match, then default, then cheapest."""
        self._require_candidates(candidates)
        candidate_map: Dict[str, ModelSpec] = {m.name: m for m in candidates}

        for predicate, model_name in self._rules:
            try:
                if predicate(ctx):
                    if model_name in candidate_map:
                        logger.debug(
                            "ContentRouter: rule matched -> %r", model_name
                        )
                        return candidate_map[model_name]
                    logger.warning(
                        "ContentRouter: rule matched %r but it is not in candidates",
                        model_name,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("ContentRouter: predicate raised %s — skipping", exc)

        # Fall back to configured default
        if self._default_model_name and self._default_model_name in candidate_map:
            logger.debug(
                "ContentRouter: using default model %r", self._default_model_name
            )
            return candidate_map[self._default_model_name]

        # Final fallback: cheapest
        cheapest = min(candidates, key=lambda m: m.cost_per_1k_tokens)
        logger.debug(
            "ContentRouter: no match — falling back to cheapest %r", cheapest.name
        )
        return cheapest

    def __repr__(self) -> str:
        return (
            f"ContentRouter(rules={len(self._rules)}, "
            f"default={self._default_model_name!r})"
        )


# ---------------------------------------------------------------------------
# AdaptiveRouter
# ---------------------------------------------------------------------------


class AdaptiveRouter(RoutingStrategy):
    """Epsilon-greedy adaptive router that learns from call outcomes.

    With probability *epsilon* a random candidate is chosen (exploration);
    otherwise the candidate with the highest recorded success rate is chosen
    (exploitation).

    Parameters
    ----------
    epsilon:
        Exploration probability in ``[0.0, 1.0]`` (default ``0.1``).
    """

    def __init__(self, epsilon: float = 0.1) -> None:
        if not 0.0 <= epsilon <= 1.0:
            raise ValueError(f"epsilon must be in [0, 1], got {epsilon!r}")
        self.epsilon = epsilon
        self._success: Dict[str, int] = {}
        self._failure: Dict[str, int] = {}

    def _success_rate(self, name: str) -> float:
        s = self._success.get(name, 0)
        f = self._failure.get(name, 0)
        total = s + f
        return s / total if total > 0 else 1.0  # optimistic prior

    def record_outcome(self, model_name: str, success: bool) -> None:
        """Update internal stats after a call completes.

        Parameters
        ----------
        model_name:
            Name of the model that handled the call.
        success:
            ``True`` if the call succeeded, ``False`` otherwise.
        """
        if success:
            self._success[model_name] = self._success.get(model_name, 0) + 1
        else:
            self._failure[model_name] = self._failure.get(model_name, 0) + 1

    async def select(
        self, candidates: List[ModelSpec], ctx: Dict[str, Any]
    ) -> ModelSpec:
        """Apply epsilon-greedy selection."""
        self._require_candidates(candidates)
        if random.random() < self.epsilon:
            chosen = random.choice(candidates)
            logger.debug("AdaptiveRouter: exploring -> %r", chosen.name)
            return chosen
        best = max(candidates, key=lambda m: self._success_rate(m.name))
        logger.debug(
            "AdaptiveRouter: exploiting -> %r (rate=%.3f)",
            best.name,
            self._success_rate(best.name),
        )
        return best

    def stats(self) -> Dict[str, Any]:
        """Return a snapshot of per-model success/failure counts."""
        all_names = set(self._success) | set(self._failure)
        return {
            name: {
                "success": self._success.get(name, 0),
                "failure": self._failure.get(name, 0),
                "success_rate": round(self._success_rate(name), 4),
            }
            for name in sorted(all_names)
        }

    def __repr__(self) -> str:
        return f"AdaptiveRouter(epsilon={self.epsilon})"


# ---------------------------------------------------------------------------
# FallbackRouter
# ---------------------------------------------------------------------------


class FallbackRouter:
    """Try models in order, advancing to the next on failure.

    Parameters
    ----------
    pool_or_list:
        Either a :class:`ModelPool` or a plain ``List[ModelSpec]``.
    strategy:
        Optional :class:`RoutingStrategy` used to order candidates.
        Defaults to :class:`CostRouter`.
    max_retries:
        Maximum number of models to attempt before giving up (default ``3``).
    """

    def __init__(
        self,
        pool_or_list: ModelPool | List[ModelSpec],
        strategy: Optional[RoutingStrategy] = None,
        max_retries: int = 3,
    ) -> None:
        if isinstance(pool_or_list, ModelPool):
            self._pool: Optional[ModelPool] = pool_or_list
            self._static_models: Optional[List[ModelSpec]] = None
        else:
            self._pool = None
            self._static_models = list(pool_or_list)
        self._strategy: RoutingStrategy = strategy or CostRouter()
        self.max_retries = max_retries

    def _candidates(self) -> List[ModelSpec]:
        if self._pool is not None:
            return self._pool.available()
        return list(self._static_models or [])

    async def route_with_fallback(
        self,
        call_fn: Callable[[ModelSpec], Awaitable[Any]],
        ctx: Dict[str, Any] | None = None,
    ) -> Tuple[Any, Optional[Exception]]:
        """Attempt *call_fn* with successive models until success or exhaustion.

        Parameters
        ----------
        call_fn:
            Async callable that receives a :class:`ModelSpec` and performs the
            actual LLM call.  Should raise an exception on failure.
        ctx:
            Routing context forwarded to the strategy.

        Returns
        -------
        Tuple[Any, Optional[Exception]]
            ``(result, None)`` on success, or ``(None, last_exception)`` when
            all attempts fail.
        """
        ctx = ctx or {}
        candidates = self._candidates()
        if not candidates:
            err = RuntimeError("FallbackRouter: no models available.")
            return None, err

        attempts: List[ModelSpec] = []
        tried: set[str] = set()
        last_exc: Optional[Exception] = None

        for _ in range(min(self.max_retries, len(candidates))):
            remaining = [m for m in candidates if m.name not in tried]
            if not remaining:
                break
            try:
                model = await self._strategy.select(remaining, ctx)
            except ValueError:
                break

            tried.add(model.name)
            attempts.append(model)
            try:
                result = await call_fn(model)
                if self._pool is not None:
                    self._pool.mark_success(model.name)
                logger.debug(
                    "FallbackRouter: succeeded on %r after %d attempt(s)",
                    model.name,
                    len(attempts),
                )
                return result, None
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if self._pool is not None:
                    self._pool.mark_failure(model.name, str(exc))
                logger.warning(
                    "FallbackRouter: %r failed (%s), trying next model.", model.name, exc
                )

        logger.error(
            "FallbackRouter: all %d attempt(s) exhausted. Last error: %s",
            len(attempts),
            last_exc,
        )
        return None, last_exc

    def __repr__(self) -> str:
        return (
            f"FallbackRouter(strategy={self._strategy!r}, "
            f"max_retries={self.max_retries})"
        )


# ---------------------------------------------------------------------------
# ModelRouter — unified facade
# ---------------------------------------------------------------------------


class ModelRouter:
    """Unified routing facade that combines a pool, strategy, and fallback.

    Parameters
    ----------
    pool:
        :class:`ModelPool` containing the registered models.
    strategy:
        Primary :class:`RoutingStrategy`.  Defaults to :class:`CostRouter`.
    fallback_strategy:
        Secondary strategy used by :class:`FallbackRouter` during
        :meth:`call`.  Defaults to :class:`LatencyRouter`.
    on_decision:
        Optional callback ``(RoutingDecision) -> None`` invoked after every
        successful :meth:`route` call (useful for logging/metrics).
    """

    def __init__(
        self,
        pool: ModelPool,
        strategy: Optional[RoutingStrategy] = None,
        fallback_strategy: Optional[RoutingStrategy] = None,
        on_decision: Optional[Callable[[RoutingDecision], None]] = None,
    ) -> None:
        self._pool = pool
        self._strategy: RoutingStrategy = strategy or CostRouter()
        self._fallback_strategy: RoutingStrategy = fallback_strategy or LatencyRouter()
        self._on_decision = on_decision
        self._total_routes: int = 0
        self._total_calls: int = 0
        self._call_errors: int = 0

    # ------------------------------------------------------------------
    # Core routing
    # ------------------------------------------------------------------

    async def route(self, ctx: Dict[str, Any]) -> RoutingDecision:
        """Select a model for *ctx* and return a :class:`RoutingDecision`.

        Parameters
        ----------
        ctx:
            Routing context.  May contain ``"_token_estimate"`` (int) for
            cost-aware strategies.

        Raises
        ------
        RuntimeError
            If no healthy models are available in the pool.
        """
        candidates = self._pool.available()
        if not candidates:
            raise RuntimeError(
                "ModelRouter.route: no healthy models available in the pool."
            )

        model = await self._strategy.select(candidates, ctx)
        token_estimate: int = int(ctx.get("_token_estimate", 1000))
        cost = model.estimated_cost(token_estimate)

        decision = RoutingDecision(
            model=model,
            strategy_name=self._strategy.name,
            reason=(
                f"Selected by {self._strategy.name} "
                f"from {len(candidates)} candidate(s)."
            ),
            estimated_cost=cost,
            metadata={"token_estimate": token_estimate, "candidates": len(candidates)},
        )
        self._total_routes += 1

        if self._on_decision is not None:
            try:
                self._on_decision(decision)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ModelRouter: on_decision callback raised %s", exc)

        logger.info(
            "ModelRouter.route -> %r via %s (cost=%.6f)",
            model.name,
            self._strategy.name,
            cost,
        )
        return decision

    # ------------------------------------------------------------------
    # Full call (route + execute + fallback)
    # ------------------------------------------------------------------

    async def call(
        self,
        ctx: Dict[str, Any],
        call_fn: Callable[[ModelSpec], Awaitable[Any]],
    ) -> Tuple[Any, RoutingDecision]:
        """Route *ctx* to a model and invoke *call_fn*, with automatic fallback.

        On success the pool is updated with a success mark.
        On failure the pool is updated with a failure mark and a
        :class:`FallbackRouter` is used to attempt remaining healthy models.

        Parameters
        ----------
        ctx:
            Routing context forwarded to the strategy.
        call_fn:
            Async callable ``(ModelSpec) -> Any``.

        Returns
        -------
        Tuple[Any, RoutingDecision]
            ``(result, decision)`` where *decision* reflects the model that
            ultimately handled the call.

        Raises
        ------
        RuntimeError
            If all models are exhausted without a successful response.
        """
        self._total_calls += 1

        # Primary attempt
        decision = await self.route(ctx)
        model = decision.model
        try:
            result = await call_fn(model)
            self._pool.mark_success(model.name)
            return result, decision
        except Exception as primary_exc:  # noqa: BLE001
            self._call_errors += 1
            self._pool.mark_failure(model.name, str(primary_exc))
            logger.warning(
                "ModelRouter.call: primary model %r failed (%s); engaging fallback.",
                model.name,
                primary_exc,
            )

        # Fallback attempt
        fb = FallbackRouter(
            self._pool, strategy=self._fallback_strategy, max_retries=3
        )
        result, err = await fb.route_with_fallback(call_fn, ctx)
        if err is not None:
            raise RuntimeError(
                f"ModelRouter.call: all fallback attempts exhausted. "
                f"Last error: {err}"
            ) from err

        # Build a new decision for the fallback model (we don't know which
        # one succeeded, so we do a best-effort route for the decision record).
        fallback_decision = await self.route(ctx)
        return result, fallback_decision

    # ------------------------------------------------------------------
    # Observability
    # ------------------------------------------------------------------

    def stats(self) -> Dict[str, Any]:
        """Return aggregate routing statistics and pool health."""
        return {
            "total_routes": self._total_routes,
            "total_calls": self._total_calls,
            "call_errors": self._call_errors,
            "strategy": self._strategy.name,
            "fallback_strategy": self._fallback_strategy.name,
            "pool_size": len(self._pool),
            "healthy_models": len(self._pool.available()),
            "health_report": self._pool.health_report(),
        }

    def __repr__(self) -> str:
        return (
            f"ModelRouter(strategy={self._strategy!r}, "
            f"fallback_strategy={self._fallback_strategy!r}, "
            f"pool={self._pool!r})"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

__all__ = [
    "ModelCapability",
    "ModelSpec",
    "ModelHealth",
    "ModelPool",
    "RoutingDecision",
    "RoutingStrategy",
    "CostRouter",
    "QualityRouter",
    "LatencyRouter",
    "RoundRobinRouter",
    "ContentRouter",
    "AdaptiveRouter",
    "FallbackRouter",
    "ModelRouter",
]
