"""
Workflow versioning helpers for Multigen.

Temporal guarantees durable execution: if a workflow is interrupted (worker
restart, deploy) it replays its full event history.  Any code path executed
during replay **must produce the same commands** as the original execution —
otherwise Temporal raises a non-determinism error.

The ``workflow.patched()`` API solves this: you mark a change with a named
``patch_id``.  Workers that have already passed that point ignore the patch;
new executions take the new code path.  This lets you ship code changes
without breaking in-flight workflows.

Pattern
-------
    from flow_engine.versioning import patched, deprecated_patch

    # In your workflow:
    if patched("use-parallel-bfs-v2"):
        # new code path — taken by workflows starting from this deploy
        result = await new_implementation()
    else:
        # old code path — replayed by workflows started before this deploy
        result = await old_implementation()

    # When you're confident no in-flight workflows use the old path:
    deprecated_patch("use-parallel-bfs-v2")   # documents intent; becomes no-op guard

Versioning Strategy
-------------------
We use **semantic patch IDs**:
    "<component>-<description>-v<N>"

Examples:
    "graph-engine-parallel-bfs-v2"
    "graph-engine-depends-on-v1"
    "sequence-retry-policy-v3"

Patch IDs are strings; they have no inherent ordering — only presence/absence
matters.  Keep a registry of active patch IDs in PATCH_REGISTRY below.

Reference
---------
Temporal Python SDK docs:
    https://docs.temporal.io/develop/python/versioning
"""
from __future__ import annotations

import logging
from typing import Dict, List

from temporalio import workflow

logger = logging.getLogger(__name__)


# ── Patch registry ────────────────────────────────────────────────────────────
# Active patches currently deployed.  Remove a patch ID here only AFTER you
# are certain that no in-flight workflows were started before that patch.

PATCH_REGISTRY: Dict[str, str] = {
    "graph-engine-parallel-bfs-v2": (
        "Parallel BFS execution with pending_set dedup guard. "
        "Deployed: 2025-06. Safe to remove after all pre-2025-06 workflows complete."
    ),
    "graph-engine-depends-on-v1": (
        "Explicit depends_on field support on NodeDef. "
        "Deployed: 2025-06."
    ),
    "graph-engine-sse-events-v1": (
        "SSE streaming via _completed_events list + get_completed_nodes query. "
        "Deployed: 2025-06."
    ),
    "graph-engine-partition-fanout-v1": (
        "Partition-aware fan-out round-robin across task_queues. "
        "Deployed: 2025-06."
    ),
    "sequence-timeout-policy-v2": (
        "Per-step timeout via node.get('timeout') passed to execute_activity. "
        "Deployed: 2025-06."
    ),
}

# Patches that have been deprecated — all in-flight workflows have completed.
# Listed here for documentation only; guards in workflow code should be
# removed at the same time.
DEPRECATED_PATCHES: List[str] = []


# ── Convenience wrappers ──────────────────────────────────────────────────────

def patched(patch_id: str) -> bool:
    """
    Temporal-safe version gate.

    Call this **inside a workflow** (not in an activity) to branch between
    old and new code paths.

    Returns True for new executions and for in-flight executions that have
    already passed this point.  Returns False only for replays of workflows
    started before the patch was deployed.

    Example
    -------
        if patched("graph-engine-parallel-bfs-v2"):
            await new_parallel_execution()
        else:
            await old_sequential_execution()
    """
    if patch_id in DEPRECATED_PATCHES:
        logger.warning(
            "patched('%s') called but this patch is deprecated — "
            "remove the old code path and this guard",
            patch_id,
        )
        return True  # deprecated = always take new path

    if patch_id not in PATCH_REGISTRY:
        logger.warning(
            "patched('%s') called but patch_id is not in PATCH_REGISTRY — "
            "add it to flow_engine.versioning.PATCH_REGISTRY",
            patch_id,
        )

    return workflow.patched(patch_id)


def deprecated_patch(patch_id: str) -> None:
    """
    Mark a patch as fully deployed — no in-flight workflows predate it.

    After calling this you should:
    1. Remove the old code path (the ``else`` branch) from the workflow.
    2. Remove the ``if patched(...)`` guard entirely (always take new path).
    3. Move the patch_id from PATCH_REGISTRY to DEPRECATED_PATCHES.

    This function is a no-op at runtime; it is documentation + a guard that
    warns if someone still calls ``patched()`` with the old id.
    """
    if patch_id in PATCH_REGISTRY:
        del PATCH_REGISTRY[patch_id]
    if patch_id not in DEPRECATED_PATCHES:
        DEPRECATED_PATCHES.append(patch_id)
    logger.info("Patch '%s' marked as deprecated — clean up old code paths", patch_id)


# ── Workflow version metadata ─────────────────────────────────────────────────

class WorkflowVersion:
    """
    Attach version metadata to a workflow class for observability.

    Usage
    -----
        @workflow.defn
        @WorkflowVersion.tag("graph-workflow", major=2, minor=3)
        class GraphWorkflow:
            ...

        # Inside run():
        meta = WorkflowVersion.current(GraphWorkflow)
        logger.info("Running %s v%d.%d", meta["name"], meta["major"], meta["minor"])
    """

    _versions: Dict[str, Dict[str, int]] = {}

    @classmethod
    def tag(cls, name: str, major: int = 1, minor: int = 0) -> "WorkflowVersion._Decorator":
        return cls._Decorator(name, major, minor, cls)

    class _Decorator:
        def __init__(
            self,
            name: str,
            major: int,
            minor: int,
            registry: "type[WorkflowVersion]",
        ) -> None:
            self._name = name
            self._major = major
            self._minor = minor
            self._reg = registry

        def __call__(self, cls_: type) -> type:
            self._reg._versions[cls_.__name__] = {
                "name": self._name,
                "major": self._major,
                "minor": self._minor,
            }
            return cls_

    @classmethod
    def current(cls, workflow_cls: type) -> Dict[str, int]:
        return cls._versions.get(workflow_cls.__name__, {"name": "unknown", "major": 0, "minor": 0})


# ── Safe upgrade helper ───────────────────────────────────────────────────────

def assert_safe_to_upgrade(workflow_cls_name: str, pending_workflows: int) -> None:
    """
    Utility for deployment scripts.  Raises RuntimeError if there are
    in-flight workflows that would be broken by removing an old code path.

    In production, wire this into your CI/CD pipeline:

        from flow_engine.versioning import assert_safe_to_upgrade
        assert_safe_to_upgrade("GraphWorkflow", pending_count)

    pending_workflows should come from:
        GET /workflows/count?status=running&type=GraphWorkflow
    """
    if pending_workflows > 0:
        raise RuntimeError(
            f"Cannot safely remove old code paths from '{workflow_cls_name}': "
            f"{pending_workflows} in-flight workflow(s) may replay the old path. "
            "Wait for them to complete or use workflow.patched() to keep both paths."
        )
    logger.info(
        "Safe to upgrade '%s': no in-flight workflows detected.", workflow_cls_name
    )
