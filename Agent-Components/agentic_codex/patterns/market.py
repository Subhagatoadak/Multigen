"""Market and game-theoretic coordination patterns."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Mapping, MutableMapping, Sequence

from ..core.agent import Agent, Context
from ..core.orchestration.coordinator.base import CoordinatorBase
from ..core.schemas import Message


@dataclass
class Bid:
    """Represents a bid from a supplier agent."""

    supplier: str
    score: float
    meta: Mapping[str, str] | None = None


class ContractNetCoordinator(CoordinatorBase):
    """Auction sub-tasks to agents and assign to the best bidder."""

    def __init__(
        self,
        auctioneer: Agent,
        bidders: Mapping[str, Agent],
        *,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        if not bidders:
            raise ValueError("At least one bidder agent is required")
        self.auctioneer = auctioneer
        self.bidders = dict(bidders)

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        with self.tracer.span(self.auctioneer.name, role="auctioneer"):
            auction = self.auctioneer.run(context)
            history.extend(auction.out_messages)
            tasks = auction.state_updates.get("tasks") or context.scratch.get("tasks") or []
            if isinstance(tasks, str):
                tasks = [tasks]

        assignments: Dict[str, Bid] = {}
        for task in tasks:
            best_bid: Bid | None = None
            for bidder_name, bidder in self.bidders.items():
                context.scratch["current_task"] = task
                with self.tracer.span(bidder.name, role="bidder", task=task):
                    bid_result = bidder.run(context)
                    history.extend(bid_result.out_messages)
                    state = bid_result.state_updates
                    score = float(state.get("score", 0.0))
                    bid = Bid(supplier=bidder_name, score=score, meta=state.get("meta"))
                    assignments.setdefault(task, bid)
                    if best_bid is None or score > best_bid.score:
                        best_bid = bid
            if best_bid is not None:
                context.scratch.setdefault("awarded", {})[task] = best_bid.supplier

        events.extend(self.tracer.events)
        return history


class IncentiveGameCoordinator(CoordinatorBase):
    """Run rounds where agents earn rewards and penalties."""

    def __init__(
        self,
        game_master: Agent,
        participants: Sequence[Agent],
        *,
        rounds: int = 3,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        if not participants:
            raise ValueError("At least one participant agent is required")
        self.game_master = game_master
        self.participants = list(participants)
        self.rounds = rounds

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        scoreboard: MutableMapping[str, float] = context.scratch.setdefault("scoreboard", {})

        for round_idx in range(self.rounds):
            context.scratch["game_round"] = round_idx
            for participant in self.participants:
                with self.tracer.span(participant.name, role="participant", round=round_idx):
                    result = participant.run(context)
                    history.extend(result.out_messages)
                    delta = float(result.state_updates.get("reward", 0.0))
                    scoreboard[participant.name] = scoreboard.get(participant.name, 0.0) + delta
                    context.scratch.update(result.state_updates)

            with self.tracer.span(self.game_master.name, role="game_master", round=round_idx):
                gm_result = self.game_master.run(context)
                history.extend(gm_result.out_messages)
                context.push_message(gm_result.out_messages[-1])
                adjustments = gm_result.state_updates.get("adjustments", {})
                for agent_name, adjustment in adjustments.items():
                    scoreboard[agent_name] = scoreboard.get(agent_name, 0.0) + float(adjustment)

        events.extend(self.tracer.events)
        return history


__all__ = ["Bid", "ContractNetCoordinator", "IncentiveGameCoordinator"]
