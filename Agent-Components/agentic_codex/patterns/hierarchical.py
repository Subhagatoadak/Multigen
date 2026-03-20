"""Hierarchical coordination patterns."""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Mapping, MutableMapping, Sequence

from ..core.agent import Agent, Context
from ..core.orchestration.coordinator.base import CoordinatorBase
from ..core.schemas import Message


@dataclass
class MinistryContext:
    """Runtime data for the ministry pattern."""

    tasks: Sequence[str]
    metadata: MutableMapping[str, str] | None = None


class MinistryOfExperts(CoordinatorBase):
    """Lead planner assigns tasks to experts and a cabinet consolidates the outcome."""

    def __init__(
        self,
        planner: Agent,
        experts: Sequence[Agent],
        cabinet: Agent,
        *,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        if not experts:
            raise ValueError("At least one expert agent is required")
        self.planner = planner
        self.experts = list(experts)
        self.cabinet = cabinet

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []

        with self.tracer.span(self.planner.name, role="planner"):
            plan_result = self.planner.run(context)
            history.extend(plan_result.out_messages)
            context.scratch.update(plan_result.state_updates)
            tasks = plan_result.state_updates.get("tasks") or context.scratch.get("tasks") or []
            if isinstance(tasks, str):
                tasks = [tasks]
            task_list = list(tasks)

        if not task_list:
            task_list = [message.content for message in history] or [context.goal]

        for idx, task in enumerate(task_list):
            expert = self.experts[idx % len(self.experts)]
            with self.tracer.span(expert.name, role="expert", task=task):
                context.scratch["current_task"] = task
                result = expert.run(context)
                history.extend(result.out_messages)
                context.scratch.update(result.state_updates)
                context.push_message(result.out_messages[-1])
                self._check_guards({"messages": history, "task": task, "expert": expert.name})

        with self.tracer.span(self.cabinet.name, role="cabinet"):
            cabinet_result = self.cabinet.run(context)
            history.extend(cabinet_result.out_messages)
            context.scratch.update(cabinet_result.state_updates)

        events.extend(self.tracer.events)
        return history


class ManagerWorker(CoordinatorBase):
    """Supervisor assigns milestones to workers and aggregates their outputs."""

    def __init__(
        self,
        manager: Agent,
        workers: Mapping[str, Agent],
        *,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        if not workers:
            raise ValueError("At least one worker agent is required")
        self.manager = manager
        self.workers = dict(workers)

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        with self.tracer.span(self.manager.name, role="manager"):
            manager_result = self.manager.run(context)
            history.extend(manager_result.out_messages)
            assignments = manager_result.state_updates.get("assignments") or {}
            context.scratch.setdefault("assignments", assignments)

        for milestone, payload in assignments.items():
            worker_key = payload.get("assignee") if isinstance(payload, Mapping) else None
            if worker_key is None:
                worker_key = milestone
            worker = self.workers.get(worker_key)
            if worker is None:
                continue
            with self.tracer.span(worker.name, role="worker", milestone=milestone):
                context.scratch["current_milestone"] = milestone
                context.scratch["milestone_payload"] = payload
                result = worker.run(context)
                history.extend(result.out_messages)
                context.scratch.update(result.state_updates)
                context.push_message(result.out_messages[-1])

        events.extend(self.tracer.events)
        return history


class JudgeRefereeGate(CoordinatorBase):
    """Post-process output with a judge and optional referee validation."""

    def __init__(
        self,
        producer: Agent,
        judge: Agent,
        referee: Agent | None = None,
        *,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        self.producer = producer
        self.judge = judge
        self.referee = referee

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []

        with self.tracer.span(self.producer.name, role="producer"):
            prod_result = self.producer.run(context)
            history.extend(prod_result.out_messages)
            context.push_message(prod_result.out_messages[-1])
            context.scratch.update(prod_result.state_updates)

        with self.tracer.span(self.judge.name, role="judge"):
            judge_result = self.judge.run(context)
            history.extend(judge_result.out_messages)
            context.push_message(judge_result.out_messages[-1])
            context.scratch.update(judge_result.state_updates)
            verdict = judge_result.state_updates.get("verdict")

        if self.referee is not None:
            context.scratch["verdict"] = verdict
            with self.tracer.span(self.referee.name, role="referee"):
                ref_result = self.referee.run(context)
                history.extend(ref_result.out_messages)
                context.push_message(ref_result.out_messages[-1])
                context.scratch.update(ref_result.state_updates)

        events.extend(self.tracer.events)
        return history


__all__ = ["MinistryOfExperts", "ManagerWorker", "JudgeRefereeGate", "MinistryContext"]
