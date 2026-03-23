"""Composable capability system for agents."""
from __future__ import annotations

import inspect
from inspect import Parameter
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, Optional, Protocol, TYPE_CHECKING

from .interfaces import ReinforcementLearner

if TYPE_CHECKING:  # pragma: no cover - import-time typing helpers
    from .agent import Agent, Context
    from .interfaces import LLMAdapter, Memory
    from .skills import SkillRegistry
    from .tools import ToolAdapter, ToolPermissions, BudgetGuard
    from .safety.rate_limit import MultiLimiter
    from .message_bus import MessageBus


class Capability(Protocol):
    """Protocol describing an installable capability."""

    name: str

    def bind(self, *, agent: "Agent", context: "Context") -> Any:
        """Attach the capability to the agent/context and return the exposed resource."""


class CapabilityRegistry:
    """Holds capabilities and binds them onto a context."""

    def __init__(self, capabilities: Iterable[Capability] | None = None) -> None:
        self._capabilities: Dict[str, Capability] = {}
        self._resources: Dict[str, Any] = {}
        if capabilities:
            for capability in capabilities:
                self.register(capability)

    def register(self, capability: Capability, *, replace: bool = False) -> None:
        """Register a capability by name."""

        if capability.name in self._capabilities and not replace:
            raise ValueError(f"Capability {capability.name!r} already registered")
        self._capabilities[capability.name] = capability

    def unregister(self, name: str) -> None:
        """Remove a capability definition."""

        self._capabilities.pop(name, None)
        self._resources.pop(name, None)

    def bind_all(self, *, agent: "Agent", context: "Context") -> MutableMapping[str, Any]:
        """Bind all capabilities and return exposed resources."""

        bound: Dict[str, Any] = {}
        for name, capability in self._capabilities.items():
            resource = capability.bind(agent=agent, context=context)
            if resource is not None:
                bound[name] = resource
        self._resources = dict(bound)
        return dict(bound)

    def get(self, name: str) -> Capability:
        """Return a registered capability definition."""

        return self._capabilities[name]

    def get_resource(self, name: str) -> Any:
        """Return the most recently bound resource."""

        if name not in self._resources:
            raise KeyError(f"Capability resource {name!r} not bound")
        return self._resources[name]

    def items(self) -> Iterable[tuple[str, Capability]]:
        """Iterate over registered capabilities."""

        return self._capabilities.items()


@dataclass
class LLMCapability:
    """Attach an LLM adapter to the agent and expose it to the context."""

    adapter: "LLMAdapter"
    name: str = "llm"

    def bind(self, *, agent: "Agent", context: "Context") -> "LLMAdapter":
        agent.llm = self.adapter
        context.llm = self.adapter  # convenience alias
        return self.adapter


@dataclass
class ContextCapability:
    """Merge structured context into the agent scratchpad."""

    data: Mapping[str, Any]
    key: str = "context"
    name: str | None = None
    merge: bool = True

    def __post_init__(self) -> None:
        if self.name is None:
            self.name = self.key

    def bind(self, *, agent: "Agent", context: "Context") -> Mapping[str, Any]:
        scratch = context.scratch.setdefault(self.key, {})
        if self.merge and isinstance(scratch, Mapping):
            merged = dict(scratch)
            merged.update(self.data)
        else:
            merged = dict(self.data)
        context.scratch[self.key] = merged
        return merged


@dataclass
class MemoryCapability:
    """Install a memory store into the context."""

    store: "Memory"
    slot: str = "default"
    name: str | None = None

    def __post_init__(self) -> None:
        if self.name is None:
            self.name = f"memory:{self.slot}" if self.slot != "default" else "memory"

    def bind(self, *, agent: "Agent", context: "Context") -> "Memory":
        context.memory[self.slot] = self.store
        return self.store


@dataclass
class SkillRegistryCapability:
    """Expose a skill registry to the agent and context."""

    registry: "SkillRegistry"
    name: str = "skills"
    attach_to_agent: bool = True

    def bind(self, *, agent: "Agent", context: "Context") -> "SkillRegistry":
        if self.attach_to_agent:
            agent.registry = self.registry
        context.registry = self.registry
        return self.registry


@dataclass
class ResourceCapability:
    """Attach an arbitrary resource to the context components mapping."""

    name: str
    resource: Any
    target: str = "components"
    key: Optional[str] = None

    def bind(self, *, agent: "Agent", context: "Context") -> Any:
        if self.target == "components":
            context.components[self.name] = self.resource
        elif self.target == "scratch":
            slot = self.key or self.name
            context.scratch[slot] = self.resource
        elif self.target == "memory":
            slot = self.key or self.name
            context.memory[slot] = self.resource
        else:  # pragma: no cover - defensive path
            raise ValueError(f"Unknown resource target {self.target!r}")
        return self.resource


@dataclass
class ToolCapability:
    """Attach tool adapters with optional permissions and budgets."""

    tools: Mapping[str, "ToolAdapter"]
    permissions: Optional["ToolPermissions"] = None
    budget: Optional["BudgetGuard"] = None
    name: str = "tools"
    limiter: Optional["MultiLimiter"] = None

    def bind(self, *, agent: "Agent", context: "Context") -> Mapping[str, "ToolAdapter"]:
        from .tools import guarded_tool  # local import to avoid circular dependency

        bound: Dict[str, "ToolAdapter"] = {}
        for tool_name, adapter in self.tools.items():
            wrapped = guarded_tool(
                adapter,
                agent_name=agent.name,
                permissions=self.permissions,
                budget=self.budget,
                limiter=self.limiter,
            )
            context.add_tool(tool_name, wrapped)
            bound[tool_name] = wrapped
        context.components.setdefault("tools", {}).update(bound)
        return bound


@dataclass
class MessageBusCapability:
    """Attach a shared message bus to the context."""

    bus: "MessageBus" | None = None
    name: str = "message_bus"

    def __post_init__(self) -> None:
        from .message_bus import MessageBus

        if self.bus is None:
            self.bus = MessageBus()

    def bind(self, *, agent: "Agent", context: "Context") -> "MessageBus":
        assert self.bus is not None  # runtime invariant after __post_init__
        context.components.setdefault("message_bus", self.bus)
        return self.bus


@dataclass
class ReinforcementLearningCapability:
    """Install a reinforcement learning trainer that hooks into the learn loop.

    The provided trainer should expose an ``update(context, step, environment=None)``
    method (matching :class:`~agentic_codex.core.interfaces.ReinforcementLearner`)
    or be a callable accepting ``(context, step, environment=None)``. Optional
    lifecycle methods ``setup``/``reset``/``initialize`` will be invoked during
    kernel initialization when ``auto_setup`` is enabled.
    """

    trainer: "ReinforcementLearner | Callable[..., Any]"
    environment: Any | None = None
    name: str = "reinforcement_learning"
    auto_setup: bool = True

    def bind(self, *, agent: "Agent", context: "Context") -> Mapping[str, Any]:
        from .kernel import AgentKernel  # local import to avoid circular dependency

        resource = {"trainer": self.trainer, "environment": self.environment}
        context.components[self.name] = resource

        kernel = agent.kernel
        if kernel is None:
            metadata = {"name": agent.name, "role": agent.role, "cfg": agent.cfg}
            kernel = AgentKernel(decide_hook=agent.step, metadata=metadata)
            agent.kernel = kernel

        flag_name = f"_{self.name}_hooks_installed"
        if getattr(kernel, flag_name, False):
            return resource

        setattr(kernel, flag_name, True)
        original_init = kernel.init_hook
        original_learn = kernel.learn_hook

        def init_hook(ctx: "Context") -> None:
            if original_init:
                original_init(ctx)
            if self.auto_setup:
                self._maybe_setup(ctx)

        def learn_hook(ctx: "Context") -> None:
            if original_learn:
                original_learn(ctx)
            step = self._latest_step(ctx)
            if step is None:
                return
            self._invoke_trainer(ctx, step)

        kernel.init_hook = init_hook
        kernel.learn_hook = learn_hook
        return resource

    def _maybe_setup(self, context: "Context") -> None:
        trainer = self.trainer
        for candidate in ("setup", "reset", "initialize"):
            hook = getattr(trainer, candidate, None)
            if callable(hook):
                self._call_with_optional_environment(hook, (context,), self.environment)
                break

    @staticmethod
    def _latest_step(context: "Context") -> Any:
        history = context.scratch.get("_last_steps")
        if not history:
            return None
        return history[-1]

    def _invoke_trainer(self, context: "Context", step: Any) -> None:
        trainer = self.trainer
        environment = self.environment

        if isinstance(trainer, ReinforcementLearner):
            self._call_with_optional_environment(trainer.update, (context, step), environment)
            return

        for attr in ("update", "step", "learn"):
            method = getattr(trainer, attr, None)
            if callable(method):
                self._call_with_optional_environment(method, (context, step), environment)
                return

        if callable(trainer):
            self._call_with_optional_environment(trainer, (context, step), environment)
            return

        raise TypeError("Reinforcement learning trainer must be callable or expose an update/step/learn method.")

    @staticmethod
    def _call_with_optional_environment(func: Any, args: tuple[Any, ...], environment: Any | None) -> None:
        try:
            sig = inspect.signature(func)
        except (TypeError, ValueError):
            sig = None

        bound_args = list(args)
        kwargs: Dict[str, Any] = {}

        if environment is not None and sig is not None:
            params = list(sig.parameters.values())
            accepts_env_kw = any(
                p.kind in (Parameter.POSITIONAL_OR_KEYWORD, Parameter.KEYWORD_ONLY) and p.name == "environment"
                for p in params
            ) or any(p.kind == Parameter.VAR_KEYWORD for p in params)
            accepts_var_pos = any(p.kind == Parameter.VAR_POSITIONAL for p in params)
            positional_capacity = sum(
                1 for p in params if p.kind in (Parameter.POSITIONAL_ONLY, Parameter.POSITIONAL_OR_KEYWORD)
            )

            if accepts_env_kw:
                kwargs["environment"] = environment
            elif accepts_var_pos or positional_capacity > len(bound_args):
                bound_args.append(environment)
        elif environment is not None:
            bound_args.append(environment)

        func(*bound_args, **kwargs)


@dataclass
class JEPALearningCapability:
    """Attach a JEPA-style learner that observes predictor/target representations."""

    predictor: Callable[["Context"], Any]
    target: Callable[["Context"], Any]
    loss_fn: Callable[[Any, Any], float]
    update_fn: Callable[["Context", Any, Any, float], None]
    name: str = "jepa_learning"
    state_key: str = "jepa_history"

    def bind(self, *, agent: "Agent", context: "Context") -> Mapping[str, Any]:
        from .kernel import AgentKernel

        resource: Dict[str, Any] = {
            "predictor": self.predictor,
            "target": self.target,
            "loss_fn": self.loss_fn,
        }
        context.components[self.name] = resource

        kernel = agent.kernel
        if kernel is None:
            metadata = {"name": agent.name, "role": agent.role, "cfg": agent.cfg}
            kernel = AgentKernel(decide_hook=agent.step, metadata=metadata)
            agent.kernel = kernel

        flag_name = f"_{self.name}_hooks_installed"
        if getattr(kernel, flag_name, False):
            return resource

        setattr(kernel, flag_name, True)
        original_learn = kernel.learn_hook

        def learn(ctx: "Context") -> None:
            if original_learn:
                original_learn(ctx)
            predictor_value = self.predictor(ctx)
            target_value = self.target(ctx)
            loss = self.loss_fn(predictor_value, target_value)
            ctx.scratch.setdefault(self.state_key, []).append(
                {"predictor": predictor_value, "target": target_value, "loss": loss}
            )
            self.update_fn(ctx, predictor_value, target_value, loss)

        kernel.learn_hook = learn
        return resource


__all__ = [
    "Capability",
    "CapabilityRegistry",
    "LLMCapability",
    "ContextCapability",
    "MemoryCapability",
    "SkillRegistryCapability",
    "ResourceCapability",
    "ToolCapability",
    "MessageBusCapability",
    "ReinforcementLearningCapability",
    "JEPALearningCapability",
]
