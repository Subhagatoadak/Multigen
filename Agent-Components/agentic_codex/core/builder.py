"""Agent builder for lego-style composition."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, Mapping, MutableMapping, Optional

from .agent import Agent, Context, StepFn
from .capabilities import (
    Capability,
    ContextCapability,
    LLMCapability,
    MemoryCapability,
    ResourceCapability,
    SkillRegistryCapability,
    ReinforcementLearningCapability,
    JEPALearningCapability,
)
from .interfaces import LLMAdapter, Memory, ReinforcementLearner
from .kernel import AgentKernel
from .skills import SkillRegistry
from .schemas import AgentStep
from .orchestration import StepSpec, run_steps


@dataclass
class AgentBuilder:
    """Fluent builder for composing agents from reusable capabilities."""

    name: str
    role: str
    _cfg: Mapping[str, Any] | None = None
    _step: Optional[StepFn] = None
    _capabilities: Dict[str, Capability] = field(default_factory=dict)
    _with_steps: Optional[Dict[str, Any]] = None

    def with_step(self, step: StepFn) -> "AgentBuilder":
        self._step = step
        return self

    def with_steps(self, steps: Iterable[StepSpec], *, max_parallel: int = 4) -> "AgentBuilder":
        """Attach a sequence of StepSpec entries to be executed inside the agent step.

        The agent will run the provided `steps` via `run_steps`, merge state updates,
        and emit all messages produced by the inner steps.
        """

        steps_list = list(steps)
        if not steps_list:
            raise ValueError("with_steps requires at least one StepSpec")
        self._with_steps = {"steps": steps_list, "max_parallel": max_parallel}

        def _composite_step(ctx: Context) -> AgentStep:
            results = run_steps(steps_list, ctx, max_parallel=max_parallel)
            state_updates: Dict[str, Any] = {}
            out_messages = []
            stop_flag = False
            for res in results:
                out_messages.extend(res.out_messages)
                state_updates.update(res.state_updates)
                stop_flag = stop_flag or res.stop
            return AgentStep(out_messages=out_messages, state_updates=state_updates, stop=stop_flag)

        self._step = _composite_step
        return self

    def with_config(self, cfg: Mapping[str, Any]) -> "AgentBuilder":
        self._cfg = cfg
        return self

    def with_capability(self, capability: Capability, *, replace: bool = False) -> "AgentBuilder":
        if capability.name in self._capabilities and not replace:
            raise ValueError(f"Capability {capability.name!r} already registered on builder")
        self._capabilities[capability.name] = capability
        return self

    def with_capabilities(self, capabilities: Iterable[Capability], *, replace: bool = False) -> "AgentBuilder":
        for capability in capabilities:
            self.with_capability(capability, replace=replace)
        return self

    def with_llm(self, adapter: LLMAdapter, *, name: str = "llm", replace: bool = False) -> "AgentBuilder":
        capability = LLMCapability(adapter=adapter, name=name)
        return self.with_capability(capability, replace=replace)

    def with_context(
        self,
        data: Mapping[str, Any],
        *,
        key: str = "context",
        name: Optional[str] = None,
        merge: bool = True,
        replace: bool = False,
    ) -> "AgentBuilder":
        capability = ContextCapability(data=data, key=key, name=name, merge=merge)
        return self.with_capability(capability, replace=replace)

    def with_memory(
        self,
        store: Memory,
        *,
        slot: str = "default",
        name: Optional[str] = None,
        replace: bool = False,
    ) -> "AgentBuilder":
        capability = MemoryCapability(store=store, slot=slot, name=name)
        return self.with_capability(capability, replace=replace)

    def with_skill_registry(
        self,
        registry: SkillRegistry,
        *,
        name: str = "skills",
        attach_to_agent: bool = True,
        replace: bool = False,
    ) -> "AgentBuilder":
        capability = SkillRegistryCapability(registry=registry, name=name, attach_to_agent=attach_to_agent)
        return self.with_capability(capability, replace=replace)

    def with_resource(
        self,
        name: str,
        resource: Any,
        *,
        target: str = "components",
        key: Optional[str] = None,
        replace: bool = False,
    ) -> "AgentBuilder":
        capability = ResourceCapability(name=name, resource=resource, target=target, key=key)
        return self.with_capability(capability, replace=replace)

    def with_reinforcement_learning(
        self,
        trainer: ReinforcementLearner,
        *,
        environment: Any | None = None,
        name: str = "reinforcement_learning",
        auto_setup: bool = True,
        replace: bool = False,
    ) -> "AgentBuilder":
        capability = ReinforcementLearningCapability(
            trainer=trainer,
            environment=environment,
            name=name,
            auto_setup=auto_setup,
        )
        return self.with_capability(capability, replace=replace)

    def with_jepa_learning(
        self,
        predictor: Callable[[Context], Any],
        target: Callable[[Context], Any],
        loss_fn: Callable[[Any, Any], float],
        update_fn: Callable[[Context, Any, Any, float], None],
        *,
        state_key: str = "jepa_history",
        name: str = "jepa_learning",
        replace: bool = False,
    ) -> "AgentBuilder":
        capability = JEPALearningCapability(
            predictor=predictor,
            target=target,
            loss_fn=loss_fn,
            update_fn=update_fn,
            name=name,
            state_key=state_key,
        )
        return self.with_capability(capability, replace=replace)

    def build(self, *, step: Optional[StepFn] = None, kernel: Optional[AgentKernel] = None) -> Agent:
        action = step or self._step
        if action is None:
            raise ValueError("Agent step function must be provided via with_step or build(step=...)")
        agent = Agent(step=action, name=self.name, role=self.role, cfg=self._cfg, kernel=kernel)
        for capability in self._capabilities.values():
            agent.add_capability(capability)
        return agent

    def prime_context(
        self,
        goal: str,
        *,
        scratch: Optional[MutableMapping[str, Any]] = None,
        context: Optional[Context] = None,
    ) -> Context:
        """Create or enrich a context with the registered capabilities without running the agent."""

        def _noop_step(_: Context) -> AgentStep:
            return AgentStep(out_messages=[])

        ctx = context or Context(goal=goal, scratch=scratch or {})
        preview_agent = Agent(
            step=self._step or _noop_step,
            name=self.name,
            role=self.role,
            cfg=self._cfg,
        )
        for capability in self._capabilities.values():
            preview_agent.add_capability(capability)
        preview_agent.capabilities.bind_all(agent=preview_agent, context=ctx)
        return ctx


__all__ = ["AgentBuilder"]
