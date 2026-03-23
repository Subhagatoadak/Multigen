"""Assembly line coordinator."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence

from ...core.agent import Context
from ...core.schemas import Message
from ...core.orchestration.coordinator.base import CoordinatorBase, Stage


@dataclass
class StageResult:
    name: str
    messages: List[Message]


class AssemblyCoordinator(CoordinatorBase):
    def __init__(self, stages: Sequence[Stage], *, guards: Sequence = ()):  # type: ignore[type-arg]
        super().__init__(guards=guards)
        self.stages = list(stages)

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        for idx, stage in enumerate(self.stages, start=1):
            with self.tracer.span(stage.name, stage=idx):
                result = stage.agent.run(context)
                history.extend(result.out_messages)
                context.push_message(result.out_messages[-1])
                context.scratch.update(result.state_updates)
                context.scratch["last_stage"] = stage.name
                self._check_guards({"messages": history, "hops": idx})
        events.extend(self.tracer.events)
        return history


__all__ = ["AssemblyCoordinator", "Stage", "StageResult"]
