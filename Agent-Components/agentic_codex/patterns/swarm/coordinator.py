"""Swarm coordinator with parallel explorers."""
from __future__ import annotations

from typing import Callable, List, Sequence

from ...core.agent import Agent, Context
from ...core.schemas import Message
from ...core.orchestration.coordinator.base import CoordinatorBase


Aggregator = Callable[[Sequence[Message]], Message]


def default_aggregator(messages: Sequence[Message]) -> Message:
    content = "\n".join(message.content for message in messages)
    return Message(role="aggregator", content=content)


class SwarmCoordinator(CoordinatorBase):
    def __init__(self, explorers: Sequence[Agent], *, aggregator: Aggregator | None = None, guards: Sequence = ()):  # type: ignore[type-arg]
        super().__init__(guards=guards)
        self.explorers = list(explorers)
        self.aggregator = aggregator or default_aggregator

    def _run(self, context: Context, events) -> List[Message]:
        outputs: List[Message] = []
        for idx, agent in enumerate(self.explorers, start=1):
            with self.tracer.span(agent.name, explorer=idx):
                step = agent.run(context)
                outputs.extend(step.out_messages)
                self._check_guards({"messages": outputs, "votes": len(outputs)})
        final = self.aggregator(outputs)
        outputs.append(final)
        events.extend(self.tracer.events)
        return outputs


__all__ = ["SwarmCoordinator", "Aggregator", "default_aggregator"]
