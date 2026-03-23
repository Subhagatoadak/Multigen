"""Smoke tests for the newly added coordination patterns."""
from __future__ import annotations

from typing import Dict, List

from agentic_codex.core.agent import Agent, Context
from agentic_codex.core.schemas import AgentStep, Message
from agentic_codex.core.observability.tracer import Tracer
from agentic_codex.patterns import (
    BlackboardCoordinator,
    ContractNetCoordinator,
    GuardrailSandwich,
    GuildRouter,
    HubAndSpokeCoordinator,
    MapReduceCoordinator,
    MeshCoordinator,
    MinistryOfExperts,
    PolicyLatticeCoordinator,
    StreamingActorSystem,
    TwoPersonRuleCoordinator,
)


def _planner_step(ctx: Context) -> AgentStep:
    return AgentStep(
        out_messages=[Message(role="assistant", content="plan:alpha,beta")],
        state_updates={"tasks": ["alpha", "beta"]},
    )


def _expert_step(name: str):
    def step(ctx: Context) -> AgentStep:
        task = ctx.scratch.get("current_task", "unknown")
        handled: List[str] = ctx.scratch.setdefault("handled", [])
        handled.append(f"{name}:{task}")
        return AgentStep(out_messages=[Message(role="assistant", content=f"{name} handled {task}")])

    return step


def _cabinet_step(ctx: Context) -> AgentStep:
    handled = ", ".join(ctx.scratch.get("handled", []))
    return AgentStep(out_messages=[Message(role="assistant", content=f"cabinet reviewed {handled}")])


def _scribe_step(label: str):
    def step(ctx: Context) -> AgentStep:
        facts: Dict[str, str] = ctx.scratch.setdefault("blackboard", {})
        facts[label] = f"fact-{label}"
        stable = len(facts) >= 2
        return AgentStep(
            out_messages=[Message(role="assistant", content=f"{label} wrote")],
            state_updates={"facts": {label: facts[label]}, "stable": stable},
        )

    return step


def _bidder_step(score: float):
    def step(ctx: Context) -> AgentStep:
        return AgentStep(
            out_messages=[Message(role="assistant", content=f"bid:{score}")],
            state_updates={"score": score},
        )

    return step


def _mapper_step(ctx: Context) -> AgentStep:
    shard = ctx.scratch.get("current_shard", "")
    return AgentStep(out_messages=[Message(role="assistant", content=f"map:{shard.upper()}")])


def _reducer_step(ctx: Context) -> AgentStep:
    maps = ctx.scratch.get("mapper_outputs", [])
    return AgentStep(out_messages=[Message(role="assistant", content="reduce:" + "|".join(maps))])


def _mesh_peer(name: str):
    def step(ctx: Context) -> AgentStep:
        inbox = ",".join(ctx.scratch.get("mesh_inbox", []))
        return AgentStep(out_messages=[Message(role="assistant", content=f"{name}:{inbox} -> ping")])

    return step


def _prefilter_step(ctx: Context) -> AgentStep:
    ctx.scratch["prefiltered"] = True
    return AgentStep(out_messages=[Message(role="assistant", content="prefilter ok")])


def _primary_step(ctx: Context) -> AgentStep:
    return AgentStep(
        out_messages=[Message(role="assistant", content="primary output")],
        state_updates={"approved": True},
    )


def _postfilter_step(ctx: Context) -> AgentStep:
    if ctx.scratch.get("prefiltered"):
        ctx.scratch["post_validated"] = True
    return AgentStep(out_messages=[Message(role="assistant", content="postfilter ok")])


def _hub_step(name: str):
    def step(ctx: Context) -> AgentStep:
        return AgentStep(out_messages=[Message(role="assistant", content=f"{name} handled")])

    return step


def _router_step(ctx: Context) -> AgentStep:
    return AgentStep(
        out_messages=[Message(role="assistant", content="route:guild")],
        state_updates={"guild": "retrieval"},
    )


def _guild_member_step(name: str):
    def step(ctx: Context) -> AgentStep:
        return AgentStep(out_messages=[Message(role="assistant", content=f"{name} served")])

    return step


def _event_actor_step(ctx: Context) -> AgentStep:
    payload = ctx.scratch.get("current_event", {})
    return AgentStep(out_messages=[Message(role="assistant", content=f"actor:{payload.get('id')}")])


def _policy_engine_step(ctx: Context) -> AgentStep:
    return AgentStep(out_messages=[Message(role="assistant", content="policy ok")], state_updates={"components": {}})


def _dispatch_step(ctx: Context) -> AgentStep:
    return AgentStep(out_messages=[Message(role="assistant", content="dispatch executed")])


def test_ministry_of_experts_flow() -> None:
    coordinator = MinistryOfExperts(
        planner=Agent(_planner_step, name="planner", role="planner"),
        experts=[
            Agent(_expert_step("exp1"), name="exp1", role="expert"),
            Agent(_expert_step("exp2"), name="exp2", role="expert"),
        ],
        cabinet=Agent(_cabinet_step, name="cabinet", role="review"),
    )
    result = coordinator.run(goal="Deliver plan", inputs={})
    assert "cabinet reviewed exp1:alpha" in [msg.content for msg in result.messages if "cabinet" in msg.content][0]


def test_blackboard_converges() -> None:
    coordinator = BlackboardCoordinator(
        scribes=[
            Agent(_scribe_step("a"), name="scribe_a", role="scribe"),
            Agent(_scribe_step("b"), name="scribe_b", role="scribe"),
        ]
    )
    result = coordinator.run(goal="Collect facts", inputs={})
    contents = [msg.content for msg in result.messages]
    assert "a wrote" in contents and "b wrote" in contents


def test_contract_net_awards_highest_bid() -> None:
    auctioneer = Agent(_planner_step, name="auctioneer", role="auctioneer")
    bidders = {
        "fast": Agent(_bidder_step(0.9), name="fast", role="bidder"),
        "cheap": Agent(_bidder_step(0.5), name="cheap", role="bidder"),
    }
    coordinator = ContractNetCoordinator(auctioneer=auctioneer, bidders=bidders)
    result = coordinator.run(goal="Award", inputs={})
    assert any("bid:0.9" in msg.content for msg in result.messages)


def test_map_reduce_coordinator() -> None:
    coordinator = MapReduceCoordinator(
        mappers=[Agent(_mapper_step, name="mapper", role="mapper")],
        reducer=Agent(_reducer_step, name="reducer", role="reducer"),
    )
    run = coordinator.run(goal="process", inputs={"shards": ["one", "two"]})
    assert any("reduce:map:ONE|map:TWO" in msg.content for msg in run.messages)


def test_mesh_coordinator_message_flow() -> None:
    peers = {
        "a": Agent(_mesh_peer("a"), name="a", role="peer"),
        "b": Agent(_mesh_peer("b"), name="b", role="peer"),
    }
    adjacency = {"a": ["b"], "b": ["a"]}
    coordinator = MeshCoordinator(peers=peers, adjacency=adjacency, rounds=2)
    result = coordinator.run(goal="mesh", inputs={})
    assert len(result.messages) >= 4  # both peers spoke in two rounds


def test_guardrail_sandwich_sequence() -> None:
    coordinator = GuardrailSandwich(
        prefilter=Agent(_prefilter_step, name="prefilter", role="safety"),
        primary=Agent(_primary_step, name="primary", role="agent"),
        postfilter=Agent(_postfilter_step, name="postfilter", role="safety"),
    )
    run = coordinator.run(goal="safety", inputs={})
    contents = [msg.content for msg in run.messages]
    assert contents == ["prefilter ok", "primary output", "postfilter ok"]


def test_federation_and_guild_routing() -> None:
    federation = HubAndSpokeCoordinator(
        federation_broker=Agent(_planner_step, name="broker", role="broker"),
        hubs={"hub-a": Agent(_hub_step("hub-a"), name="hub-a", role="hub")},
    )
    fed_run = federation.run(goal="federated", inputs={})
    assert any("hub-a handled" in msg.content for msg in fed_run.messages)

    guild_router = GuildRouter(
        router=Agent(_router_step, name="router", role="router"),
        guilds={
            "retrieval": [Agent(_guild_member_step("retriever"), name="retriever", role="guild")],
        },
    )
    guild_run = guild_router.run(goal="guild task", inputs={})
    assert any("retriever served" in msg.content for msg in guild_run.messages)


def test_streaming_actor_processes_events() -> None:
    system = StreamingActorSystem(
        actor=Agent(_event_actor_step, name="actor", role="actor"),
    )
    run = system.run(goal="stream", inputs={"events": [{"id": "e1"}, {"id": "e2"}]})
    counts = [msg.content for msg in run.messages if msg.content.startswith("actor")]
    assert counts == ["actor:e1", "actor:e2"]


def test_two_person_rule_and_policy_lattice() -> None:
    approvals = TwoPersonRuleCoordinator(
        initiator=Agent(_primary_step, name="initiator", role="initiator"),
        co_signer=Agent(_primary_step, name="cosigner", role="cosigner"),
        executor=Agent(_postfilter_step, name="executor", role="executor"),
    )
    run = approvals.run(goal="approve", inputs={})
    assert any("postfilter ok" in msg.content for msg in run.messages)

    lattice = PolicyLatticeCoordinator(
        policy_engine=Agent(_policy_engine_step, name="policy", role="policy"),
        dispatcher=Agent(_dispatch_step, name="dispatcher", role="router"),
    )
    lattice_run = lattice.run(goal="policy", inputs={})
    assert any("dispatch executed" in msg.content for msg in lattice_run.messages)


def test_tracer_metrics() -> None:
    tracer = Tracer()
    with tracer.span("demo"):
        tracer.metric("tokens", 42.0)
    assert any(event.kind == "metric" for event in tracer.events)
