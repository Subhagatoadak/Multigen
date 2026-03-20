"""Federation and tenancy patterns."""
from __future__ import annotations

from typing import List, Mapping, Sequence

from ..core.agent import Agent, Context
from ..core.orchestration.coordinator.base import CoordinatorBase
from ..core.schemas import Message


class HubAndSpokeCoordinator(CoordinatorBase):
    """Coordinate work across tenant hubs with a federation broker."""

    def __init__(
        self,
        federation_broker: Agent,
        hubs: Mapping[str, Agent],
        *,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        if not hubs:
            raise ValueError("At least one hub agent is required")
        self.broker = federation_broker
        self.hubs = dict(hubs)

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        with self.tracer.span(self.broker.name, role="federation_broker"):
            broker_result = self.broker.run(context)
            history.extend(broker_result.out_messages)
            selected = broker_result.state_updates.get("target_hubs", list(self.hubs.keys()))
            if isinstance(selected, str):
                selected = [selected]

        for hub_name in selected:
            hub_agent = self.hubs.get(hub_name)
            if hub_agent is None:
                continue
            with self.tracer.span(hub_agent.name, role="hub", hub=hub_name):
                local_result = hub_agent.run(context)
                history.extend(local_result.out_messages)
                context.push_message(local_result.out_messages[-1])
                context.scratch.setdefault("hub_results", {})[hub_name] = local_result.state_updates

        events.extend(self.tracer.events)
        return history


class GuildRouter(CoordinatorBase):
    """Dispatch requests to capability-aligned guild pools."""

    def __init__(
        self,
        router: Agent,
        guilds: Mapping[str, Sequence[Agent]],
        *,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        if not guilds:
            raise ValueError("At least one guild is required")
        self.router = router
        self.guilds = {name: list(members) for name, members in guilds.items()}

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        with self.tracer.span(self.router.name, role="router"):
            routing = self.router.run(context)
            history.extend(routing.out_messages)
            guild_name = routing.state_updates.get("guild")
            member_idx = int(routing.state_updates.get("member", 0))

        candidates = self.guilds.get(guild_name, [])
        if not candidates:
            candidates = [agent for members in self.guilds.values() for agent in members]
        agent = candidates[member_idx % len(candidates)]
        with self.tracer.span(agent.name, role="guild_member", guild=guild_name):
            result = agent.run(context)
            history.extend(result.out_messages)
            context.push_message(result.out_messages[-1])
            context.scratch.setdefault("guild_history", []).append(
                {"guild": guild_name, "member": agent.name, "state": result.state_updates}
            )

        events.extend(self.tracer.events)
        return history


__all__ = ["HubAndSpokeCoordinator", "GuildRouter"]
