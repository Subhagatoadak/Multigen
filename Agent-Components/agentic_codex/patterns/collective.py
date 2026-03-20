"""Collective intelligence coordination patterns."""
from __future__ import annotations

from typing import List, MutableMapping, Sequence

from ..core.agent import Agent, Context
from ..core.orchestration.coordinator.base import CoordinatorBase
from ..core.schemas import Message
from .swarm.coordinator import SwarmCoordinator


class BlackboardCoordinator(CoordinatorBase):
    """Shared fact board updated until convergence criteria is met."""

    def __init__(
        self,
        scribes: Sequence[Agent],
        *,
        max_rounds: int = 5,
        convergence_key: str = "stable",
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        if not scribes:
            raise ValueError("At least one scribe agent is required")
        self.scribes = list(scribes)
        self.max_rounds = max_rounds
        self.convergence_key = convergence_key

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        board: MutableMapping[str, str] = context.scratch.setdefault("blackboard", {})

        for round_idx in range(self.max_rounds):
            context.scratch["blackboard_round"] = round_idx
            for scribe in self.scribes:
                with self.tracer.span(scribe.name, role="scribe", round=round_idx):
                    result = scribe.run(context)
                    history.extend(result.out_messages)
                    context.push_message(result.out_messages[-1])
                    board.update(result.state_updates.get("facts", {}))
                    context.scratch.update(result.state_updates)
                    if result.state_updates.get(self.convergence_key):
                        events.extend(self.tracer.events)
                        return history
        events.extend(self.tracer.events)
        return history


class DebateCoordinator(CoordinatorBase):
    """Coordinate a pro/con debate with a judging agent."""

    def __init__(
        self,
        proponent: Agent,
        opponent: Agent,
        judge: Agent,
        *,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        self.proponent = proponent
        self.opponent = opponent
        self.judge = judge

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []

        with self.tracer.span(self.proponent.name, role="proponent"):
            pro_result = self.proponent.run(context)
            history.extend(pro_result.out_messages)
            context.push_message(pro_result.out_messages[-1])
            context.scratch.update(pro_result.state_updates)

        with self.tracer.span(self.opponent.name, role="opponent"):
            opp_result = self.opponent.run(context)
            history.extend(opp_result.out_messages)
            context.push_message(opp_result.out_messages[-1])
            context.scratch.update(opp_result.state_updates)

        context.scratch["debate_history"] = [message.content for message in history]
        with self.tracer.span(self.judge.name, role="judge"):
            judge_result = self.judge.run(context)
            history.extend(judge_result.out_messages)
            context.push_message(judge_result.out_messages[-1])
            context.scratch.update(judge_result.state_updates)

        events.extend(self.tracer.events)
        return history


__all__ = ["SwarmCoordinator", "BlackboardCoordinator", "DebateCoordinator"]
