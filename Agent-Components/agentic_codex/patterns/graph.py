"""Graph and mesh agent patterns."""
from __future__ import annotations

from typing import List, Mapping, MutableMapping, Sequence

from ..core.agent import Agent, Context
from ..core.orchestration.coordinator.base import CoordinatorBase
from ..core.schemas import Message


class MeshCoordinator(CoordinatorBase):
    """Coordinate peer-to-peer message passing across a capability graph."""

    def __init__(
        self,
        peers: Mapping[str, Agent],
        adjacency: Mapping[str, Sequence[str]],
        *,
        rounds: int = 1,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        if not peers:
            raise ValueError("At least one peer agent is required")
        self.peers = dict(peers)
        self.adjacency = {node: list(neighbours) for node, neighbours in adjacency.items()}
        self.rounds = rounds

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        mailboxes: MutableMapping[str, List[str]] = context.scratch.setdefault("mesh_mailboxes", {})

        for round_idx in range(self.rounds):
            context.scratch["mesh_round"] = round_idx
            for name, agent in self.peers.items():
                inbox = mailboxes.get(name, [])
                context.scratch["mesh_inbox"] = list(inbox)
                with self.tracer.span(agent.name, role="peer", round=round_idx):
                    result = agent.run(context)
                    history.extend(result.out_messages)
                    context.push_message(result.out_messages[-1])
                    context.scratch.update(result.state_updates)
                    out_message = result.out_messages[-1].content if result.out_messages else ""
                    for neighbour in self.adjacency.get(name, []):
                        mailboxes.setdefault(neighbour, []).append(out_message)

        events.extend(self.tracer.events)
        return history


class MixtureOfExpertsCommittee(CoordinatorBase):
    """Route to top experts and aggregate their responses."""

    def __init__(
        self,
        router: Agent,
        experts: Mapping[str, Agent],
        chair: Agent,
        *,
        top_k: int = 3,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        if not experts:
            raise ValueError("At least one expert is required")
        self.router = router
        self.experts = dict(experts)
        self.chair = chair
        self.top_k = top_k

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        with self.tracer.span(self.router.name, role="router"):
            routing_result = self.router.run(context)
            history.extend(routing_result.out_messages)
            candidates = routing_result.state_updates.get("experts") or list(self.experts.keys())
            if isinstance(candidates, str):
                candidates = [candidates]
            selected = list(candidates)[: self.top_k]

        expert_summaries = []
        for expert_name in selected:
            agent = self.experts.get(expert_name)
            if agent is None:
                continue
            with self.tracer.span(agent.name, role="expert"):
                result = agent.run(context)
                history.extend(result.out_messages)
                context.push_message(result.out_messages[-1])
                context.scratch.update(result.state_updates)
                expert_summaries.append(result.out_messages[-1].content)

        context.scratch["committee_notes"] = expert_summaries
        with self.tracer.span(self.chair.name, role="chair"):
            chair_result = self.chair.run(context)
            history.extend(chair_result.out_messages)
            context.scratch.update(chair_result.state_updates)

        events.extend(self.tracer.events)
        return history


__all__ = ["MeshCoordinator", "MixtureOfExpertsCommittee"]
