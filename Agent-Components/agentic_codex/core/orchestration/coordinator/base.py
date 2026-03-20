"""Base coordinator implementation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4
from typing import Any, List, Mapping, Sequence

from ...agent import Agent, Context
from ...interfaces import Guard
from ...observability.tracer import Tracer
from ...observability.logger import StructuredLogger
from ...schemas import Message, RunEvent, RunResult


@dataclass
class Stage:
    name: str
    agent: Agent


class CoordinatorBase:
    def __init__(
        self,
        *,
        guards: Sequence[Guard] | None = None,
        run_id: str | None = None,
        run_store: Any | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        self.guards = list(guards or [])
        self.run_id = run_id
        self.tracer = Tracer(run_id=run_id)
        self.run_store = run_store
        self.logger = logger or StructuredLogger(name="agentic")

    def _emit(self, events: List[RunEvent], kind: str, payload: Mapping[str, Any]) -> None:
        events.append(RunEvent(ts=datetime.utcnow(), kind=kind, payload=dict(payload)))
        if self.logger:
            self.logger.log(kind, **dict(payload))

    def _check_guards(self, state: Mapping[str, Any]) -> None:
        for guard in self.guards:
            result = guard.check(state)
            if not result.get("ok", False):
                raise RuntimeError(f"Guard {guard.name} failed: {result.get('reason', '')}")

    def run(self, goal: str, inputs: Mapping[str, Any]) -> RunResult:
        run_id = self.run_id or uuid4().hex
        context = Context(goal=goal, scratch=dict(inputs))
        context.scratch.setdefault("run_id", run_id)
        events: List[RunEvent] = []
        self.tracer.set_run_id(run_id)
        self._emit(events, "run.started", {"run_id": run_id, "goal": goal})
        messages = self._run(context, events)
        self._emit(events, "run.finished", {"run_id": run_id, "goal": goal})
        result = RunResult(messages=messages, meta={**dict(inputs), "run_id": run_id}, events=events, run_id=run_id)
        if self.run_store:
            self.run_store.save(result)
        return result

    def _run(self, context: Context, events: List[RunEvent]) -> List[Message]:  # pragma: no cover - abstract
        raise NotImplementedError
