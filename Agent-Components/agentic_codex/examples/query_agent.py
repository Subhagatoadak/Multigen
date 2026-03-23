"""Question answering example using lego-style capabilities."""
from __future__ import annotations

from typing import List

from agentic_codex import AgentBuilder, Context, FunctionAdapter
from agentic_codex.core.memory import EpisodicMemory
from agentic_codex.core.schemas import AgentStep, Message

KNOWLEDGE_BASE = [
    {
        "topic": "orion",
        "answer": "Orion is a prominent constellation located on the celestial equator.",
    },
    {
        "topic": "andromeda galaxy",
        "answer": "The Andromeda Galaxy is the nearest major galaxy to the Milky Way.",
    },
]


def _search_context(question: str, context: List[dict[str, str]]) -> str:
    question_lower = question.lower()
    for record in context:
        if record["topic"] in question_lower:
            return record["answer"]
    return "I could not find that in the provided context."


def _llm_stub(prompt: str) -> str:
    """Return the context chunk embedded at the end of the prompt."""

    sentinel = "RAW_CONTEXT::"
    return prompt.split(sentinel, 1)[-1].strip() if sentinel in prompt else prompt


def query_step(ctx: Context) -> AgentStep:
    """Retrieve answers from the shared context and store conversational memory."""

    question = ctx.goal
    context_bundle = ctx.components["context"]
    knowledge = context_bundle.get("knowledge", KNOWLEDGE_BASE)
    base_answer = _search_context(question, knowledge)

    memory = ctx.components["memory"]
    memory.put("question", question)
    memory.put("candidate", base_answer)

    if ctx.llm is None:
        final_answer = base_answer
    else:
        prompt = f"Question: {question}\nRAW_CONTEXT::{base_answer}"
        final_answer = ctx.llm.generate(prompt)

    memory.put("final_answer", final_answer)
    return AgentStep(
        out_messages=[Message(role="assistant", content=final_answer)],
        state_updates={"base_answer": base_answer},
    )


def build_agent() -> AgentBuilder:
    """Create a reusable query agent builder with context, memory and LLM capabilities."""

    return (
        AgentBuilder(name="query", role="assistant")
        .with_llm(FunctionAdapter(_llm_stub))
        .with_context({"knowledge": KNOWLEDGE_BASE})
        .with_memory(EpisodicMemory())
        .with_step(query_step)
    )


def run_example() -> Message:
    """Execute the query agent over a built-in knowledge base."""

    agent = build_agent().build()
    context = Context(goal="What is the Andromeda Galaxy?")
    result = agent.run(context)
    return result.out_messages[-1]


if __name__ == "__main__":  # pragma: no cover - manual example
    final_message = run_example()
    print(final_message.content)
