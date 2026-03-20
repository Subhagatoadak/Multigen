"""Agent kernel implementing lifecycle hooks."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, TYPE_CHECKING

from .schemas import AgentStep

if TYPE_CHECKING:  # pragma: no cover
    from .agent import Context


LifecycleHook = Callable[["Context"], None]
DecisionHook = Callable[["Context"], AgentStep]


@dataclass
class AgentKernel:
    """Implements the init → perceive → decide → act → learn loop.

    Each hook can be overridden; default implementations are no-ops except
    for :meth:`decide`, which delegates to the provided callable.
    """

    decide_hook: DecisionHook
    init_hook: LifecycleHook | None = None
    perceive_hook: LifecycleHook | None = None
    act_hook: LifecycleHook | None = None
    learn_hook: LifecycleHook | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    _initialized: bool = field(default=False, init=False, repr=False)

    def ensure_init(self, context: "Context") -> None:
        if not self._initialized:
            if self.init_hook:
                self.init_hook(context)
            self._initialized = True

    def perceive(self, context: "Context") -> None:
        if self.perceive_hook:
            self.perceive_hook(context)

    def decide(self, context: "Context") -> AgentStep:
        return self.decide_hook(context)

    def act(self, context: "Context") -> None:
        if self.act_hook:
            self.act_hook(context)

    def learn(self, context: "Context", step: AgentStep) -> None:
        if self.learn_hook:
            # expose last step to learning hook via scratch
            context.scratch.setdefault("_last_steps", []).append(step)
            self.learn_hook(context)


def kernel_from_step(step: DecisionHook, *, metadata: Optional[Mapping[str, Any]] = None) -> AgentKernel:
    """Create a kernel that only overrides the decision stage."""

    return AgentKernel(decide_hook=step, metadata=dict(metadata or {}))


__all__ = ["AgentKernel", "kernel_from_step"]
