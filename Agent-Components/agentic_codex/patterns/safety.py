"""Safety and governance overlays."""
from __future__ import annotations

from typing import List, Sequence

from ..core.agent import Agent, Context
from ..core.orchestration.coordinator.base import CoordinatorBase
from ..core.schemas import Message


class GuardrailSandwich(CoordinatorBase):
    """Apply pre-filter, main agent, and post-validation in sequence."""

    def __init__(
        self,
        prefilter: Agent,
        primary: Agent,
        postfilter: Agent,
        *,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        self.prefilter = prefilter
        self.primary = primary
        self.postfilter = postfilter

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        with self.tracer.span(self.prefilter.name, role="prefilter"):
            pre = self.prefilter.run(context)
            history.extend(pre.out_messages)
            context.scratch.update(pre.state_updates)
        with self.tracer.span(self.primary.name, role="primary"):
            main = self.primary.run(context)
            history.extend(main.out_messages)
            context.push_message(main.out_messages[-1])
            context.scratch.update(main.state_updates)
        with self.tracer.span(self.postfilter.name, role="postfilter"):
            post = self.postfilter.run(context)
            history.extend(post.out_messages)
            context.push_message(post.out_messages[-1])
            context.scratch.update(post.state_updates)
        events.extend(self.tracer.events)
        return history


class TwoPersonRuleCoordinator(CoordinatorBase):
    """Require two approvals before committing the final output."""

    def __init__(
        self,
        initiator: Agent,
        co_signer: Agent,
        executor: Agent | None = None,
        *,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        self.initiator = initiator
        self.co_signer = co_signer
        self.executor = executor

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        approvals: dict[str, bool] = {}

        with self.tracer.span(self.initiator.name, role="initiator"):
            init = self.initiator.run(context)
            history.extend(init.out_messages)
            approvals["initiator"] = bool(init.state_updates.get("approved", True))
            context.scratch.update(init.state_updates)

        with self.tracer.span(self.co_signer.name, role="co_signer"):
            co = self.co_signer.run(context)
            history.extend(co.out_messages)
            approvals["co_signer"] = bool(co.state_updates.get("approved", True))
            context.scratch.update(co.state_updates)

        if all(approvals.values()) and self.executor is not None:
            with self.tracer.span(self.executor.name, role="executor"):
                result = self.executor.run(context)
                history.extend(result.out_messages)
                context.scratch.update(result.state_updates)

        context.scratch["approvals"] = approvals
        events.extend(self.tracer.events)
        return history


class PolicyLatticeCoordinator(CoordinatorBase):
    """Apply policy-as-code to determine routing, models, and permissions."""

    def __init__(
        self,
        policy_engine: Agent,
        dispatcher: Agent,
        *,
        guards: Sequence = (),  # type: ignore[type-arg]
    ) -> None:
        super().__init__(guards=guards)
        self.policy_engine = policy_engine
        self.dispatcher = dispatcher

    def _run(self, context: Context, events) -> List[Message]:
        history: List[Message] = []
        with self.tracer.span(self.policy_engine.name, role="policy_engine"):
            policy_result = self.policy_engine.run(context)
            history.extend(policy_result.out_messages)
            context.scratch.update(policy_result.state_updates)
            context.components.update(policy_result.state_updates.get("components", {}))
        with self.tracer.span(self.dispatcher.name, role="dispatcher"):
            dispatch_result = self.dispatcher.run(context)
            history.extend(dispatch_result.out_messages)
            context.push_message(dispatch_result.out_messages[-1])
            context.scratch.update(dispatch_result.state_updates)
        events.extend(self.tracer.events)
        return history


__all__ = ["GuardrailSandwich", "TwoPersonRuleCoordinator", "PolicyLatticeCoordinator"]
