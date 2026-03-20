from __future__ import annotations

from agentic_codex import Agent, Context, Stage, AssemblyCoordinator, SwarmCoordinator
from agentic_codex.core.schemas import AgentStep, Message


def writer_step(_: Context) -> AgentStep:
    return AgentStep(out_messages=[Message(role="assistant", content="draft")])


def editor_step(ctx: Context) -> AgentStep:
    draft = ctx.inbox[-1].content if ctx.inbox else "draft"
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"edited {draft}")],
        state_updates={"edited": True},
    )


def test_assembly_coordinator_runs():
    writer = Agent(writer_step, name="writer", role="writer")
    editor = Agent(editor_step, name="editor", role="editor")
    coordinator = AssemblyCoordinator([Stage("write", writer), Stage("edit", editor)])
    result = coordinator.run(goal="spec", inputs={"domain": "travel"})
    assert result.messages[-1].content == "edited draft"


def make_explorer(name: str):
    def _step(_: Context) -> AgentStep:
        return AgentStep(out_messages=[Message(role="assistant", content=f"idea {name}")])

    return Agent(_step, name=name, role="explorer")


def test_swarm_coordinator_aggregates():
    swarm = SwarmCoordinator([make_explorer("a"), make_explorer("b")])
    result = swarm.run(goal="brainstorm", inputs={})
    assert result.messages[-1].role == "aggregator"
    assert "idea a" in result.messages[-1].content
