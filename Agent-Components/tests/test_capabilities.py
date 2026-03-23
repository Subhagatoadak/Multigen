"""Tests for the capability registry and agent builder."""
from __future__ import annotations

from agentic_codex import AgentBuilder, Context, FunctionAdapter
from agentic_codex.core.memory import SemanticMemory
from agentic_codex.core.schemas import AgentStep, Message
from agentic_codex.core.capabilities import ToolCapability, MessageBusCapability, JEPALearningCapability
from agentic_codex.core.tools import MathToolAdapter, ToolPermissions, BudgetGuard


def test_agent_builder_with_capabilities() -> None:
    llm_calls: list[str] = []

    def llm_fn(prompt: str) -> str:
        llm_calls.append(prompt)
        return f"llm:{prompt}"

    def agent_step(ctx: Context) -> AgentStep:
        context_pack = ctx.components["context"]
        lookup = ctx.components["kv_store"]
        memory = ctx.components["memory"]
        memory.put("question", ctx.goal)
        llm_output = ctx.llm.generate(ctx.goal) if ctx.llm else "no-llm"
        content = f"{context_pack['context_key']}-{lookup['key']}-{llm_output}"
        return AgentStep(out_messages=[Message(role="assistant", content=content)])

    builder = (
        AgentBuilder(name="lego", role="assistant")
        .with_llm(FunctionAdapter(llm_fn))
        .with_context({"context_key": "ctx"})
        .with_memory(SemanticMemory())
        .with_resource("kv_store", {"key": "value"})
        .with_step(agent_step)
    )

    agent = builder.build()
    context = Context(goal="tell me")

    result = agent.run(context)

    assert result.out_messages[-1].content == "ctx-value-llm:tell me"
    assert llm_calls == ["tell me"]
    assert isinstance(context.memory["default"], SemanticMemory)
    assert context.memory["default"].get("question") == "tell me"
    assert context.components["kv_store"]["key"] == "value"


def test_tool_capability_with_permissions_and_budget() -> None:
    def agent_step(ctx: Context) -> AgentStep:
        tool = ctx.get_tool("math")
        result = tool.invoke(expression="1+1")
        return AgentStep(out_messages=[Message(role="assistant", content=str(result["result"]))])

    tools = {"math": MathToolAdapter(name="math")}
    permissions = ToolPermissions({"lego": {"math"}})
    budget = BudgetGuard(max_calls=1)

    builder = (
        AgentBuilder(name="lego", role="assistant")
        .with_capability(ToolCapability(tools=tools, permissions=permissions, budget=budget))
        .with_step(agent_step)
    )

    agent = builder.build()
    context = Context(goal="compute")
    result = agent.run(context)
    assert result.out_messages[-1].content == "2.0"
    try:
        agent.run(context)
    except RuntimeError as exc:
        assert "Budget exceeded" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("Expected budget guard to raise")


def test_message_bus_capability_shares_bus() -> None:
    bus_capability = MessageBusCapability()

    def agent_step(ctx: Context) -> AgentStep:
        bus = ctx.get_message_bus()
        bus.publish(agent="lego", content="hello", iteration=1)
        return AgentStep(out_messages=[Message(role="assistant", content="ok")])

    builder = AgentBuilder(name="lego", role="assistant").with_capability(bus_capability).with_step(agent_step)
    agent = builder.build()
    context = Context(goal="ping")
    agent.run(context)
    bus = context.get_message_bus()
    record = bus.last(agent="lego")
    assert record is not None
    assert record.content == "hello"


def test_reinforcement_learning_capability_hooks_trainer() -> None:
    class DummyTrainer:
        def __init__(self) -> None:
            self.setup_calls = 0
            self.updates: list[tuple[str, dict[str, float]]] = []

        def setup(self, context: Context, *, environment: dict[str, float] | None = None) -> None:
            self.setup_calls += 1
            context.scratch["rl_setup_environment"] = environment

        def update(
            self,
            context: Context,
            step: AgentStep,
            *,
            environment: dict[str, float] | None = None,
        ) -> None:
            last_message = step.out_messages[-1].content if step.out_messages else ""
            self.updates.append((last_message, environment or {}))
            context.scratch.setdefault("rewards", []).append(environment.get("reward", 0.0) if environment else 0.0)

    environment = {"reward": 0.75}

    def agent_step(ctx: Context) -> AgentStep:
        return AgentStep(out_messages=[
            Message(role="assistant", content=f"step:{len(ctx.scratch.get('_last_steps', []))}")
        ])

    trainer = DummyTrainer()
    builder = AgentBuilder(name="rl-agent", role="learner").with_step(agent_step).with_reinforcement_learning(
        trainer,
        environment=environment,
    )

    agent = builder.build()
    context = Context(goal="optimize")

    result_first = agent.run(context)
    result_second = agent.run(context)

    assert result_first.out_messages[-1].content.startswith("step:")
    assert result_second.out_messages[-1].content.startswith("step:")
    assert context.components["reinforcement_learning"]["trainer"] is trainer
    assert context.components["reinforcement_learning"]["environment"] == environment
    assert trainer.setup_calls == 1
    assert len(trainer.updates) == 2
    assert trainer.updates[0][0].startswith("step:")
    assert trainer.updates[0][1] == environment
    assert context.scratch["rl_setup_environment"] == environment
    assert context.scratch["rewards"] == [0.75, 0.75]


def test_jepa_learning_capability_records_updates() -> None:
    def predictor(ctx: Context) -> float:
        return float(ctx.scratch.get("predictor_value", 0.0))

    def target(ctx: Context) -> float:
        return float(ctx.scratch.get("target_value", 0.0))

    def loss_fn(pred: float, tgt: float) -> float:
        return abs(pred - tgt)

    def update_fn(ctx: Context, pred: float, tgt: float, loss: float) -> None:
        ctx.scratch.setdefault("jepa_updates", []).append({"pred": pred, "target": tgt, "loss": loss})

    capability = JEPALearningCapability(
        predictor=predictor,
        target=target,
        loss_fn=loss_fn,
        update_fn=update_fn,
    )

    def agent_step(ctx: Context) -> AgentStep:
        ctx.scratch["predictor_value"] = ctx.scratch.get("predictor_value", 0.0) + 1.0
        return AgentStep(out_messages=[Message(role="assistant", content="ok")])

    builder = AgentBuilder(name="jepa", role="learner").with_capability(capability).with_step(agent_step)
    agent = builder.build()
    context = Context(goal="align")
    context.scratch["target_value"] = 2.0

    agent.run(context)
    agent.run(context)

    history = context.scratch.get("jepa_history", [])
    assert len(history) == 2
    assert context.scratch["jepa_updates"][0]["loss"] >= 0.0
