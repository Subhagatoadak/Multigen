"""
Pattern Agents — Multigen wrappers for agentic_codex coordinator patterns.

Each agent here is a self-contained Multigen BaseAgent that internally runs
one of the agentic_codex coordination patterns (Swarm, Ministry of Experts,
Debate, Guardrail Sandwich, etc.).

They are registered via @register_agent so they can be used directly in any
workflow DSL step:

    {"name": "reason", "agent": "MinistryOfExpertsAgent", "params": {"goal": "..."}}
    {"name": "explore", "agent": "SwarmAgent",            "params": {"goal": "..."}}
    {"name": "argue",   "agent": "DebateAgent",           "params": {"goal": "..."}}
    {"name": "safe",    "agent": "GuardrailAgent",        "params": {"goal": "..."}}

All patterns use a lightweight echo step by default so they work without an
LLM API key.  Replace the step functions and wire real LLM adapters to get
full cognitive behaviour.
"""
from __future__ import annotations

from typing import Any, Dict

from agentic_codex import (
    AgentBuilder,
    Context,
    MinistryOfExperts,
    SwarmCoordinator,
    DebateCoordinator,
    GuardrailSandwich,
    BlackboardCoordinator,
    MapReduceCoordinator,
)
from agentic_codex.core.schemas import AgentStep, Message

from agents.base_agent import BaseAgent
from orchestrator.services.agent_registry import register_agent


# ── Shared echo step (no LLM required) ────────────────────────────────────────

def _echo_step(ctx: Context) -> AgentStep:
    """Minimal step: echoes the goal back as output."""
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"[echo] {ctx.goal}")],
        state_updates={"tasks": [ctx.goal]},
        stop=False,
    )


def _build_echo_agent(name: str, role: str = "assistant"):
    return AgentBuilder(name=name, role=role).with_step(_echo_step).build()


# ── 1. Ministry of Experts ─────────────────────────────────────────────────────

@register_agent("MinistryOfExpertsAgent")
class MinistryOfExpertsAgent(BaseAgent):
    """
    Hierarchical coordinator: planner decomposes the goal into tasks,
    domain experts handle each task, cabinet chair synthesises the result.

    params:
        goal (str)          — the high-level objective
        task_count (int)    — how many expert slots to use (default 2)
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        goal = str(params.get("goal", "analyse"))
        task_count = int(params.get("task_count", 2))

        planner = _build_echo_agent("planner", "planner")
        experts = [_build_echo_agent(f"expert_{i}", "expert") for i in range(task_count)]
        cabinet = _build_echo_agent("cabinet", "synthesiser")

        coordinator = MinistryOfExperts(planner=planner, experts=experts, cabinet=cabinet)
        ctx = Context(goal=goal)
        ctx.scratch.update(params)

        run_result = coordinator.run(goal=goal, inputs=params)
        contents = [m.content for m in run_result.messages if hasattr(m, "content")]

        self.logger.info("MinistryOfExpertsAgent: %d messages produced", len(contents))
        return {
            "content": contents[-1] if contents else "",
            "messages": contents,
            "pattern": "ministry_of_experts",
        }


# ── 2. Swarm ───────────────────────────────────────────────────────────────────

@register_agent("SwarmAgent")
class SwarmAgent(BaseAgent):
    """
    Collective intelligence: N explorer agents attack the problem in parallel,
    outputs are aggregated into a single synthesised answer.

    params:
        goal (str)          — the problem to explore
        swarm_size (int)    — number of explorers (default 3)
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        goal = str(params.get("goal", "explore"))
        swarm_size = int(params.get("swarm_size", 3))

        explorers = [_build_echo_agent(f"explorer_{i}", "explorer") for i in range(swarm_size)]
        coordinator = SwarmCoordinator(explorers=explorers)
        ctx = Context(goal=goal)
        ctx.scratch.update(params)

        run_result = coordinator.run(goal=goal, inputs=params)
        contents = [m.content for m in run_result.messages if hasattr(m, "content")]

        self.logger.info("SwarmAgent: %d explorer messages + 1 aggregation", swarm_size)
        return {
            "content": contents[-1] if contents else "",
            "messages": contents,
            "pattern": "swarm",
            "swarm_size": swarm_size,
        }


# ── 3. Debate ──────────────────────────────────────────────────────────────────

@register_agent("DebateAgent")
class DebateAgent(BaseAgent):
    """
    Collective intelligence: proponent and opponent argue the goal,
    a judge produces a final ruling.

    params:
        goal (str)  — the proposition to debate
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        goal = str(params.get("goal", "debate"))

        proponent = _build_echo_agent("proponent", "proponent")
        opponent = _build_echo_agent("opponent", "opponent")
        judge = _build_echo_agent("judge", "judge")

        coordinator = DebateCoordinator(
            proponent=proponent,
            opponent=opponent,
            judge=judge,
        )
        ctx = Context(goal=goal)
        ctx.scratch.update(params)

        run_result = coordinator.run(goal=goal, inputs=params)
        contents = [m.content for m in run_result.messages if hasattr(m, "content")]

        self.logger.info("DebateAgent: %d debate messages", len(contents))
        return {
            "content": contents[-1] if contents else "",
            "messages": contents,
            "pattern": "debate",
        }


# ── 4. Guardrail Sandwich ──────────────────────────────────────────────────────

@register_agent("GuardrailAgent")
class GuardrailAgent(BaseAgent):
    """
    Safety overlay: pre-filter validates input, primary agent executes,
    post-validator checks the output before returning.

    params:
        goal (str)  — the task to execute safely
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        goal = str(params.get("goal", "execute safely"))

        pre_filter = _build_echo_agent("pre_filter", "validator")
        primary = _build_echo_agent("primary", "executor")
        post_validator = _build_echo_agent("post_validator", "validator")

        coordinator = GuardrailSandwich(
            prefilter=pre_filter,
            primary=primary,
            postfilter=post_validator,
        )
        ctx = Context(goal=goal)
        ctx.scratch.update(params)

        run_result = coordinator.run(goal=goal, inputs=params)
        contents = [m.content for m in run_result.messages if hasattr(m, "content")]

        self.logger.info("GuardrailAgent: safety pattern completed, %d messages", len(contents))
        return {
            "content": contents[-1] if contents else "",
            "messages": contents,
            "pattern": "guardrail_sandwich",
        }


# ── 5. Blackboard ──────────────────────────────────────────────────────────────

@register_agent("BlackboardAgent")
class BlackboardAgent(BaseAgent):
    """
    Stigmergy / shared memory: researchers write findings to a shared
    blackboard; an arbiter synthesises the final answer.

    params:
        goal (str)              — the research question
        researcher_count (int)  — number of researchers (default 2)
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        goal = str(params.get("goal", "research"))
        count = int(params.get("researcher_count", 2))

        scribes = [_build_echo_agent(f"scribe_{i}", "scribe") for i in range(count)]

        coordinator = BlackboardCoordinator(scribes=scribes)
        ctx = Context(goal=goal)
        ctx.scratch.update(params)

        run_result = coordinator.run(goal=goal, inputs=params)
        contents = [m.content for m in run_result.messages if hasattr(m, "content")]

        self.logger.info("BlackboardAgent: %d messages", len(contents))
        return {
            "content": contents[-1] if contents else "",
            "messages": contents,
            "pattern": "blackboard",
        }


# ── 6. Map-Reduce ──────────────────────────────────────────────────────────────

@register_agent("MapReduceAgent")
class MapReduceAgent(BaseAgent):
    """
    Pipeline / dataflow: shard planner splits the problem, mappers process
    each shard in parallel, reducer combines results.

    params:
        goal (str)          — the task to map-reduce
        mapper_count (int)  — number of mappers (default 3)
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        goal = str(params.get("goal", "process"))
        count = int(params.get("mapper_count", 3))

        mappers = [_build_echo_agent(f"mapper_{i}", "mapper") for i in range(count)]
        reducer = _build_echo_agent("reducer", "reducer")

        coordinator = MapReduceCoordinator(mappers=mappers, reducer=reducer)
        ctx = Context(goal=goal)
        ctx.scratch.update(params)

        run_result = coordinator.run(goal=goal, inputs=params)
        contents = [m.content for m in run_result.messages if hasattr(m, "content")]

        self.logger.info("MapReduceAgent: %d mapper messages reduced", count)
        return {
            "content": contents[-1] if contents else "",
            "messages": contents,
            "pattern": "map_reduce",
            "mapper_count": count,
        }
