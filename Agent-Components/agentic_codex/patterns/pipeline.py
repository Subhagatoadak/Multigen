"""Pipeline and dataflow coordination patterns."""
from __future__ import annotations

from typing import Iterable, List, Mapping, Sequence

from ..core.agent import Agent, Context
from ..core.orchestration.coordinator.base import CoordinatorBase
from ..core.schemas import Message
from .assembly_line.coordinator import AssemblyCoordinator, Stage


class MapReduceCoordinator(CoordinatorBase):
    """Shard work across mapper agents and consolidate with a reducer."""

    def __init__(
        self,
        mappers: Sequence[Agent],
        reducer: Agent,
        *,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        if not mappers:
            raise ValueError("At least one mapper agent is required")
        self.mappers = list(mappers)
        self.reducer = reducer

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        shards = context.scratch.get("shards") or []
        if not shards:
            shards = [context.goal]
        mapper_outputs: List[Message] = []
        for idx, shard in enumerate(shards):
            mapper = self.mappers[idx % len(self.mappers)]
            with self.tracer.span(mapper.name, role="mapper", shard=idx):
                context.scratch["current_shard"] = shard
                result = mapper.run(context)
                history.extend(result.out_messages)
                mapper_outputs.extend(result.out_messages)
                context.push_message(result.out_messages[-1])
                context.scratch.update(result.state_updates)
        context.scratch["mapper_outputs"] = [message.content for message in mapper_outputs]
        with self.tracer.span(self.reducer.name, role="reducer"):
            reducer_result = self.reducer.run(context)
            history.extend(reducer_result.out_messages)
            context.scratch.update(reducer_result.state_updates)
        events.extend(self.tracer.events)
        return history


class StreamingActorSystem(CoordinatorBase):
    """Drive long-lived actors from an input event stream."""

    def __init__(
        self,
        actor: Agent,
        controller: Agent | None = None,
        *,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        self.actor = actor
        self.controller = controller

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        event_stream: Iterable[Mapping[str, str]] = context.scratch.get("events", [])
        for event_idx, payload in enumerate(event_stream):
            context.scratch["current_event"] = payload
            with self.tracer.span(self.actor.name, role="actor", event=event_idx):
                result = self.actor.run(context)
                history.extend(result.out_messages)
                context.push_message(result.out_messages[-1])
                context.scratch.update(result.state_updates)
            if self.controller:
                with self.tracer.span(self.controller.name, role="controller"):
                    controller_result = self.controller.run(context)
                    history.extend(controller_result.out_messages)
                    context.scratch.update(controller_result.state_updates)
        events.extend(self.tracer.events)
        return history


__all__ = ["AssemblyCoordinator", "Stage", "MapReduceCoordinator", "StreamingActorSystem"]
