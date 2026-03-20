"""Tests for enhanced orchestration primitives."""
from __future__ import annotations

import asyncio

from agentic_codex.core.orchestration.planner import build_plan
from agentic_codex.core.orchestration.router import PolicyRouter
from agentic_codex.core.orchestration.scheduler import run_serial, run_local, run_async


def test_build_plan_with_hints() -> None:
    plan = build_plan("alpha.beta", hints=["review docs"])
    roots = plan.roots()
    assert roots[0].description == "alpha"
    assert plan.children("task-1")[0].description == "beta"


def test_policy_router_matches_rules() -> None:
    router = PolicyRouter(
        rules=[
            ("urgent", lambda payload: "urgent" in payload.get("tags", []), "fast-lane"),
            ("default", lambda payload: True, "slow-lane"),
        ],
        default="fallback",
    )
    decision = router.route({"tags": ["urgent"]})
    assert decision.target == "fast-lane"
    fallback = router.route({"tags": []})
    assert fallback.target == "slow-lane"


def test_schedulers_run() -> None:
    order: list[str] = []

    def first() -> None:
        order.append("first")

    def second() -> None:
        order.append("second")

    run_serial([first, second])
    assert order == ["first", "second"]

    order.clear()
    run_local([first, second], max_workers=2)
    assert sorted(order) == ["first", "second"]

    async def async_job(name: str) -> None:
        order.append(name)

    asyncio.run(run_async([lambda: async_job("async")]))
    assert "async" in order
