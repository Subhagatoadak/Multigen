"""
CodifiedAgent — bridge between agentic_codex and Multigen.

Wraps any agentic_codex AgentBuilder-composed agent as a Multigen
BaseAgent so it can be registered via @register_agent and executed
as a Temporal activity inside any workflow DSL step.

Usage — single agent:
    from agents.codex_adapter import make_codified_agent
    from agentic_codex import AgentBuilder, EnvOpenAIAdapter
    from orchestrator.services.agent_registry import register_agent

    builder = (
        AgentBuilder(name="analyst", role="analyst")
        .with_llm(EnvOpenAIAdapter(model="gpt-4o-mini"))
        .with_step(my_step_fn)
    )

    @register_agent("AnalystAgent")
    class AnalystAgent(make_codified_agent(builder)):
        pass

Usage — coordinator pattern:
    from agents.codex_adapter import make_coordinator_agent
    from agentic_codex import MinistryOfExperts
    from orchestrator.services.agent_registry import register_agent

    @register_agent("MinistryAgent")
    class MinistryAgent(make_coordinator_agent(MinistryOfExperts, planner=..., experts=[...], cabinet=...)):
        pass
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Type

from agents.base_agent import BaseAgent
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.agent import Agent
from agentic_codex.core.schemas import AgentStep


class CodifiedAgent(BaseAgent):
    """
    Base class for any agentic_codex agent running inside Multigen.

    Translates Multigen's flat params dict into an agentic_codex Context,
    runs the full AgentKernel lifecycle (init → perceive → decide → act → learn),
    and returns a Multigen-compatible output dict.

    Subclasses must set _codex_agent at the class level (done automatically
    by make_codified_agent) or override run() directly.
    """

    _codex_agent: Optional[Agent] = None

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        agent = self._codex_agent
        if agent is None:
            raise RuntimeError(
                f"{self.__class__.__name__}._codex_agent is not set. "
                "Use make_codified_agent(builder) to create a proper subclass."
            )

        goal = str(params.get("goal", "execute"))

        ctx = Context(goal=goal)
        # Surface all params as scratch so step functions can read them
        ctx.scratch.update(params)
        # Also expose each param as a named context component
        for key, val in params.items():
            ctx.components[key] = val

        result: AgentStep = agent.run(ctx)

        output: Dict[str, Any] = {}

        if result.out_messages:
            output["content"] = result.out_messages[-1].content
            output["messages"] = [m.content for m in result.out_messages]
        else:
            output["content"] = ""
            output["messages"] = []

        if result.state_updates:
            output.update(result.state_updates)

        output["stop"] = result.stop
        output["metrics"] = ctx.scratch.get("_metrics", {})

        self.logger.info(
            "CodifiedAgent %s completed — stop=%s messages=%d",
            self.__class__.__name__,
            result.stop,
            len(output["messages"]),
        )
        return output


def make_codified_agent(builder: AgentBuilder) -> Type[CodifiedAgent]:
    """
    Factory: takes an AgentBuilder, builds the agent, and returns a
    CodifiedAgent subclass with that agent pre-wired.

    The returned class can be used directly as a base class:

        @register_agent("MyAgent")
        class MyAgent(make_codified_agent(builder)):
            pass
    """
    built = builder.build()

    class _CodifiedAgent(CodifiedAgent):
        _codex_agent = built

    _CodifiedAgent.__name__ = f"Codified_{builder.name}"
    _CodifiedAgent.__qualname__ = f"Codified_{builder.name}"
    return _CodifiedAgent


class CoordinatorAgent(BaseAgent):
    """
    Base class for agentic_codex coordinator patterns running inside Multigen.

    Coordinators (MinistryOfExperts, SwarmCoordinator, etc.) orchestrate
    multiple agentic_codex agents internally.  This wrapper runs the coordinator
    and collects its output messages as a Multigen-compatible dict.

    Subclasses must set _coordinator at the class level.
    """

    _coordinator: Any = None  # CoordinatorBase instance

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        coordinator = self._coordinator
        if coordinator is None:
            raise RuntimeError(
                f"{self.__class__.__name__}._coordinator is not set. "
                "Use make_coordinator_agent() to create a proper subclass."
            )

        goal = str(params.get("goal", "coordinate"))
        ctx = Context(goal=goal)
        ctx.scratch.update(params)
        for key, val in params.items():
            ctx.components[key] = val

        run_result = coordinator.run(goal=goal, inputs=params)

        contents: List[str] = [m.content for m in run_result.messages if hasattr(m, "content")]
        output: Dict[str, Any] = {
            "content": contents[-1] if contents else "",
            "messages": contents,
            "message_count": len(contents),
        }

        self.logger.info(
            "CoordinatorAgent %s completed — %d messages",
            self.__class__.__name__,
            len(contents),
        )
        return output


def make_coordinator_agent(coordinator_cls: type, **coordinator_kwargs: Any) -> Type[CoordinatorAgent]:
    """
    Factory: instantiates a coordinator pattern and returns a CoordinatorAgent
    subclass with it pre-wired.

    Example:
        from agentic_codex import MinistryOfExperts

        MinistryBase = make_coordinator_agent(
            MinistryOfExperts,
            planner=planner_agent,
            experts=[expert1, expert2],
            cabinet=cabinet_agent,
        )

        @register_agent("MinistryAgent")
        class MinistryAgent(MinistryBase):
            pass
    """
    instance = coordinator_cls(**coordinator_kwargs)

    class _CoordinatorAgent(CoordinatorAgent):
        _coordinator = instance

    _CoordinatorAgent.__name__ = f"Coordinator_{coordinator_cls.__name__}"
    _CoordinatorAgent.__qualname__ = f"Coordinator_{coordinator_cls.__name__}"
    return _CoordinatorAgent
