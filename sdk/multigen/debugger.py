"""Time-travel debugger for Multigen workflows."""
from __future__ import annotations

import copy
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .snapshot import InMemorySnapshotStore, Snapshot, SnapshotStore


class WorkflowDebugger:
    """
    Captures per-step snapshots during workflow execution and supports
    time-travel replay, diffing, and state inspection.

    Quick start::

        debugger = WorkflowDebugger()

        # Run a chain and capture every step
        results = await debugger.run_chain(
            [
                (agent_a, {"text": "hello"}),
                (agent_b, {}),
                (agent_c, {}),
            ],
            workflow_id="wf-demo",
        )

        # List all captured snapshots
        steps = await debugger.list_steps("wf-demo")
        for s in steps:
            print(s.step_index, s.node_id, s.status, s.duration_ms)

        # Inspect state at step 1 (inputs, output, accumulated context)
        snap = await debugger.get_snapshot("wf-demo", step=1)

        # Diff step 1 → step 2
        diff = await debugger.diff("wf-demo", step_a=1, step_b=2)

        # Re-run from step 1 with a patched input
        new_results = await debugger.replay_from(
            "wf-demo",
            from_step=1,
            agents={"AgentB": patched_agent_b},
            patch={"extra_hint": "retry with more context"},
        )

        # Export the full trace as JSON-serialisable dicts
        trace = await debugger.export_trace("wf-demo")
    """

    def __init__(self, store: Optional[SnapshotStore] = None) -> None:
        self.store = store or InMemorySnapshotStore()
        self._step_counters: Dict[str, int] = {}

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _next_step(self, workflow_id: str) -> int:
        n = self._step_counters.get(workflow_id, 0)
        self._step_counters[workflow_id] = n + 1
        return n

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _elapsed_ms(start: datetime) -> float:
        return (datetime.now(timezone.utc) - start).total_seconds() * 1000

    # ── Execution with capture ────────────────────────────────────────────────

    async def run_agent(
        self,
        agent: Any,
        params: Dict[str, Any],
        *,
        workflow_id: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        node_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run a single agent and capture one snapshot."""
        wf_id = workflow_id or str(uuid.uuid4())
        step = self._next_step(wf_id)
        ctx = copy.deepcopy(context or {})
        start = datetime.now(timezone.utc)
        status = "success"
        error: Optional[str] = None
        output: Dict[str, Any] = {}

        try:
            output = await agent.run(params)
        except Exception as exc:
            status = "failed"
            error = str(exc)
            raise
        finally:
            snap = Snapshot(
                workflow_id=wf_id,
                step_index=step,
                node_id=node_id or getattr(agent, "name", type(agent).__name__),
                agent=type(agent).__name__,
                input_params=copy.deepcopy(params),
                output=copy.deepcopy(output),
                context=ctx,
                timestamp=start.isoformat(),
                duration_ms=self._elapsed_ms(start),
                status=status,
                error=error,
            )
            await self.store.save(snap)

        return output

    async def run_chain(
        self,
        steps: Sequence[Tuple[Any, Dict[str, Any]]],
        *,
        workflow_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Run a sequence of ``(agent, params)`` pairs.

        Each step sees the accumulated outputs of all prior steps merged into
        its params (lower priority — explicit params take precedence).
        A snapshot is saved after each step regardless of success or failure.
        """
        wf_id = workflow_id or str(uuid.uuid4())
        accumulated: Dict[str, Any] = {}
        results: List[Dict[str, Any]] = []

        for i, (agent, params) in enumerate(steps):
            node_id = getattr(agent, "name", None) or f"step_{i}"
            step = self._next_step(wf_id)
            resolved = {**accumulated, **params}
            start = datetime.now(timezone.utc)
            status = "success"
            error: Optional[str] = None
            output: Dict[str, Any] = {}

            try:
                output = await agent.run(resolved)
            except Exception as exc:
                status = "failed"
                error = str(exc)

            accumulated[node_id] = output
            snap = Snapshot(
                workflow_id=wf_id,
                step_index=step,
                node_id=node_id,
                agent=type(agent).__name__,
                input_params=copy.deepcopy(resolved),
                output=copy.deepcopy(output),
                context=copy.deepcopy(accumulated),
                timestamp=start.isoformat(),
                duration_ms=self._elapsed_ms(start),
                status=status,
                error=error,
            )
            await self.store.save(snap)
            results.append(output)

        return results

    # ── Replay ────────────────────────────────────────────────────────────────

    async def replay_from(
        self,
        workflow_id: str,
        from_step: int,
        *,
        agents: Optional[Dict[str, Any]] = None,
        patch: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Replay a workflow starting from *from_step*.

        The accumulated context up to ``from_step - 1`` is restored from the
        snapshot store so downstream agents see the correct prior state.

        Parameters
        ----------
        workflow_id : str
        from_step   : int   Inclusive start step (0-based).
        agents      : dict  ``{agent_class_name: agent_instance}`` — replacements
                            to use during replay. Steps whose agent class is not
                            in this dict carry forward the original recorded output.
        patch       : dict  Key-value overrides merged into every replayed step's
                            ``input_params``.
        """
        all_snaps = await self.store.list_for_workflow(workflow_id)
        if not all_snaps:
            raise ValueError(f"No snapshots found for workflow {workflow_id!r}")

        # Restore context just before from_step
        restored_ctx: Dict[str, Any] = {}
        if from_step > 0:
            pred = await self.store.load_at_step(workflow_id, from_step - 1)
            if pred is not None:
                restored_ctx = copy.deepcopy(pred.context)

        replay_snaps = [s for s in all_snaps if s.step_index >= from_step]
        if not replay_snaps:
            raise ValueError(
                f"No steps at or after step {from_step} in workflow {workflow_id!r}"
            )

        replay_wf_id = f"{workflow_id}:replay:{from_step}:{uuid.uuid4().hex[:6]}"
        self._step_counters[replay_wf_id] = from_step
        results: List[Dict[str, Any]] = []

        for snap in replay_snaps:
            step = self._next_step(replay_wf_id)
            start = datetime.now(timezone.utc)

            params = copy.deepcopy(snap.input_params)
            if patch:
                params.update(patch)

            agent_instance = (agents or {}).get(snap.agent)
            if agent_instance is not None:
                status = "success"
                error: Optional[str] = None
                output: Dict[str, Any] = {}
                try:
                    output = await agent_instance.run(params)
                except Exception as exc:
                    status = "failed"
                    error = str(exc)
            else:
                # No replacement agent — carry forward original output unchanged
                output = copy.deepcopy(snap.output)
                status = snap.status
                error = snap.error

            restored_ctx[snap.node_id] = output
            new_snap = Snapshot(
                workflow_id=replay_wf_id,
                step_index=step,
                node_id=snap.node_id,
                agent=snap.agent,
                input_params=copy.deepcopy(params),
                output=copy.deepcopy(output),
                context=copy.deepcopy(restored_ctx),
                timestamp=start.isoformat(),
                duration_ms=self._elapsed_ms(start),
                status=status,
                error=error,
                metadata={
                    "replayed_from": workflow_id,
                    "original_step": snap.step_index,
                },
            )
            await self.store.save(new_snap)
            results.append(output)

        return results

    # ── Query helpers ─────────────────────────────────────────────────────────

    async def list_steps(self, workflow_id: str) -> List[Snapshot]:
        """All snapshots for *workflow_id* ordered by step index."""
        return await self.store.list_for_workflow(workflow_id)

    async def get_snapshot(self, workflow_id: str, step: int) -> Optional[Snapshot]:
        """Snapshot at a specific step index."""
        return await self.store.load_at_step(workflow_id, step)

    async def diff(
        self, workflow_id: str, step_a: int, step_b: int
    ) -> Dict[str, Any]:
        """
        Diff the captured state at *step_a* and *step_b*.

        Returns a dict of fields that changed: ``{field: {"before": ..., "after": ...}}``.
        """
        snap_a = await self.store.load_at_step(workflow_id, step_a)
        snap_b = await self.store.load_at_step(workflow_id, step_b)
        missing = [i for i, s in ((step_a, snap_a), (step_b, snap_b)) if s is None]
        if missing:
            raise ValueError(f"Steps not found: {missing}")
        return snap_a.diff(snap_b)

    async def export_trace(self, workflow_id: str) -> List[Dict[str, Any]]:
        """All snapshots as JSON-serialisable dicts (for logging, export, UI)."""
        return [s.to_dict() for s in await self.store.list_for_workflow(workflow_id)]

    def reset(self, workflow_id: Optional[str] = None) -> None:
        """Clear snapshots and step counters for one or all workflows."""
        if isinstance(self.store, InMemorySnapshotStore):
            self.store.clear()
        if workflow_id:
            self._step_counters.pop(workflow_id, None)
        else:
            self._step_counters.clear()


__all__ = ["WorkflowDebugger"]
