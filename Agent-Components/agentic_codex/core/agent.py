"""Core Agent and Context definitions."""
from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Callable, Iterable, List, Mapping, MutableMapping, Optional

from .capabilities import Capability, CapabilityRegistry
from .interfaces import LLMAdapter
from .kernel import AgentKernel, kernel_from_step
from .schemas import AgentStep, Message
from .observability.metrics import record_counter, record_latency
from .observability.logger import StructuredLogger
from .cancel import CancelToken


@dataclass
class Context:
    """Mutable state passed to agents at each step."""

    goal: str
    scratch: MutableMapping[str, Any] = field(default_factory=dict)
    registry: Any | None = None
    inbox: List[Message] = field(default_factory=list)
    memory: MutableMapping[str, Any] = field(default_factory=dict)
    stores: MutableMapping[str, Any] = field(default_factory=dict)
    policies: MutableMapping[str, Any] = field(default_factory=dict)
    components: MutableMapping[str, Any] = field(default_factory=dict)
    llm: Optional[LLMAdapter] = None
    tools: MutableMapping[str, Any] = field(default_factory=dict)

    def add_component(self, name: str, value: Any) -> None:
        self.components[name] = value

    def get_component(self, name: str) -> Any:
        return self.components[name]

    def add_tool(self, name: str, tool: Any) -> None:
        self.tools[name] = tool

    def get_tool(self, name: str) -> Any:
        return self.tools[name]

    def get_message_bus(self) -> Any:
        if "message_bus" not in self.components:
            raise KeyError("message_bus not attached to context")
        return self.components["message_bus"]

    def push_message(self, message: Message) -> None:
        self.inbox.append(message)


StepFn = Callable[[Context], AgentStep]


@dataclass
class Agent:
    """Simple functional agent wrapper."""

    step: StepFn
    name: str
    role: str
    cfg: Mapping[str, Any] | None = None
    llm: Optional[LLMAdapter] = None
    registry: Any | None = None
    capabilities: CapabilityRegistry = field(default_factory=CapabilityRegistry)
    kernel: AgentKernel | None = None
    _runtime_kernel: AgentKernel | None = field(default=None, init=False, repr=False)

    def add_capability(self, capability: Capability, *, replace: bool = False) -> None:
        self.capabilities.register(capability, replace=replace)

    def add_capabilities(self, capabilities: Iterable[Capability], *, replace: bool = False) -> None:
        for capability in capabilities:
            self.capabilities.register(capability, replace=replace)

    def get_capability_resource(self, name: str) -> Any:
        return self.capabilities.get_resource(name)

    def _ensure_kernel(self) -> AgentKernel:
        if self._runtime_kernel is None:
            if self.kernel is not None:
                self._runtime_kernel = self.kernel
            else:
                metadata = {"name": self.name, "role": self.role, "cfg": self.cfg}
                self._runtime_kernel = kernel_from_step(self.step, metadata=metadata)
        return self._runtime_kernel

    def run(self, context: Context) -> AgentStep:
        """Invoke the underlying step function with error handling."""

        if self.registry and context.registry is None:
            context.registry = self.registry

        if self.llm and context.llm is None:
            context.llm = self.llm
            context.components.setdefault("llm", self.llm)

        bound = self.capabilities.bind_all(agent=self, context=context)
        if bound:
            context.components.update(bound)

        kernel = self._ensure_kernel()
        logger: StructuredLogger = (
            context.components.get("logger") if isinstance(context.components, Mapping) and "logger" in context.components else StructuredLogger(name="agentic")
        )
        if isinstance(context.scratch, Mapping):
            run_id = context.scratch.get("run_id")
            if run_id:
                logger.set_run_id(run_id)
        token = context.components.get("cancel_token") if isinstance(context.components, Mapping) else None
        if isinstance(token, CancelToken) and token.cancelled:
            return AgentStep(out_messages=[], state_updates={}, stop=True)
        start = time.perf_counter()
        try:
            logger.log("agent.start", agent=self.name, role=self.role)
            kernel.ensure_init(context)
            if isinstance(token, CancelToken) and token.cancelled:
                return AgentStep(out_messages=[], state_updates={}, stop=True)
            kernel.perceive(context)
            if isinstance(token, CancelToken) and token.cancelled:
                return AgentStep(out_messages=[], state_updates={}, stop=True)
            result = kernel.decide(context)
            kernel.act(context)
            if not isinstance(result, AgentStep):  # pragma: no cover - defensive
                raise TypeError("Agent step must return AgentStep")
            kernel.learn(context, result)
            logger.log("agent.finish", agent=self.name, role=self.role, stop=result.stop)
        except Exception:
            record_counter(context.scratch, "agent.error", agent=self.name, role=self.role)
            logger.log("agent.error", agent=self.name, role=self.role)
            raise
        finally:
            elapsed = time.perf_counter() - start
            record_latency(context.scratch, "agent.latency", elapsed, agent=self.name, role=self.role)
        return result
