"""Assembly workflow powered by the OpenAI adapter.

The script shows how to connect the :class:`~agentic_codex.core.llm.EnvOpenAIAdapter`
with a simple assembly line coordinator.  It wires in a lightweight
post-processing stage and only runs when an API key is available.
"""
from __future__ import annotations

import os

from typing import TYPE_CHECKING

from agentic_codex import Agent, AssemblyCoordinator, EnvOpenAIAdapter, Stage
from agentic_codex.core.schemas import AgentStep, Message

if TYPE_CHECKING:  # pragma: no cover - typing helpers
    from agentic_codex import Context


def make_writer(adapter: EnvOpenAIAdapter) -> Agent:
    def writer_step(_: "Context") -> AgentStep:  # type: ignore[name-defined]
        prompt = "Draft a concise onboarding guide for our remote engineers."
        completion = adapter.chat([Message(role="user", content=prompt)])
        if isinstance(completion, Message):
            content = completion.content
        else:  # pragma: no cover - streaming branch
            content = "".join(chunk.content for chunk in completion)
        return AgentStep(out_messages=[Message(role="assistant", content=content)])

    return Agent(writer_step, name="openai-writer", role="assistant", llm=adapter)


def reviewer_step(ctx: "Context") -> AgentStep:  # type: ignore[name-defined]
    draft = ctx.inbox[-1].content if ctx.inbox else ""
    comment = f"Quality check complete. Received draft of length {len(draft)}."
    return AgentStep(out_messages=[Message(role="assistant", content=comment)])


def run_with_openai(goal: str) -> Message:
    adapter = EnvOpenAIAdapter(model="gpt-4o-mini")
    if adapter.api_key is None:
        raise RuntimeError("OPENAI_API_KEY must be set to run this example")

    writer = make_writer(adapter)
    reviewer = Agent(reviewer_step, name="reviewer", role="reviewer")
    pipeline = AssemblyCoordinator([Stage("writer", writer), Stage("review", reviewer)])
    result = pipeline.run(goal=goal, inputs={})
    return result.messages[-1]


if __name__ == "__main__":  # pragma: no cover - manual example
    goal = os.getenv("CODEX_GOAL", "an onboarding guide for new hires")
    message = run_with_openai(goal)
    print(message.content)
