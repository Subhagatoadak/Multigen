"""
Semantic / reasoning-level observability for Multigen.

Problems solved
---------------
- No causal attribution (can't trace which upstream node caused a bad output)
- No epistemic debt dashboard (no visualisation of accumulated uncertainty)
- No human-readable decision audit trail (OTel spans are for machines)
- No counterfactual replay ("what if node X had returned Y?")
- No cross-workflow pattern mining (no analysis across workflow runs)

Classes
-------
- ``NodeRecord``            — captured input/output/confidence for one node
- ``CausalGraph``           — directed graph of node-to-node causality
- ``CausalAttributor``      — propagates blame backward from a bad output
- ``EpistemicDebt``         — accumulated uncertainty across a workflow run
- ``AuditEvent``            — human-readable decision audit entry
- ``DecisionAuditTrail``    — ordered log of human-readable audit events
- ``CounterfactualReplayer`` — replay a workflow with a patched node output
- ``PatternMiner``          — mine recurring patterns across workflow runs

Usage::

    from multigen.observability import (
        CausalAttributor, DecisionAuditTrail, CounterfactualReplayer
    )

    trail = DecisionAuditTrail(run_id="run-42")
    trail.log("fetch", input=ctx, output=result, confidence=0.9)
    trail.log("analyse", input=result, output=analysis, confidence=0.6)
    print(trail.render())

    attributor = CausalAttributor()
    attributor.record("fetch", confidence=0.9, output="Paris")
    attributor.record("analyse", confidence=0.6, output="wrong answer",
                       upstream=["fetch"])
    blame = attributor.blame("analyse")
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── NodeRecord ────────────────────────────────────────────────────────────────

@dataclass
class NodeRecord:
    """Captured snapshot of a single node execution."""
    name: str
    input: Any
    output: Any
    confidence: float = 1.0
    latency_ms: float = 0.0
    upstream: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    metadata: Dict[str, Any] = field(default_factory=dict)


# ── CausalAttributor ──────────────────────────────────────────────────────────

@dataclass
class BlameResult:
    """Causal attribution result for a target node."""
    target: str
    contributors: List[Tuple[str, float]]   # (node_name, blame_score)
    total_uncertainty: float


class CausalAttributor:
    """
    Propagates "blame" backward from a low-confidence or incorrect output
    to the upstream nodes that most contributed to it.

    Blame score formula:
    ``blame(upstream) = (1 - upstream.confidence) * edge_weight``

    Usage::

        attributor = CausalAttributor()
        attributor.record("fetch",   confidence=0.95, output="data")
        attributor.record("analyse", confidence=0.60, output="wrong",
                           upstream=["fetch"])
        attributor.record("report",  confidence=0.40, output="bad output",
                           upstream=["analyse"])

        blame = attributor.blame("report")
        print(blame.contributors)
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, NodeRecord] = {}

    def record(
        self,
        name: str,
        confidence: float,
        output: Any,
        input: Any = None,
        upstream: Optional[List[str]] = None,
        **metadata: Any,
    ) -> NodeRecord:
        rec = NodeRecord(
            name=name,
            input=input,
            output=output,
            confidence=confidence,
            upstream=upstream or [],
            metadata=metadata,
        )
        self._nodes[name] = rec
        return rec

    def blame(self, target: str, depth: int = 4) -> BlameResult:
        contributors: Dict[str, float] = {}
        self._propagate(target, blame=1.0, contributors=contributors, depth=depth)
        # Remove self
        contributors.pop(target, None)
        sorted_c = sorted(contributors.items(), key=lambda x: -x[1])
        total = sum(contributors.values())
        return BlameResult(
            target=target,
            contributors=sorted_c,
            total_uncertainty=total,
        )

    def _propagate(
        self,
        node_name: str,
        blame: float,
        contributors: Dict[str, float],
        depth: int,
    ) -> None:
        if depth <= 0 or node_name not in self._nodes:
            return
        rec = self._nodes[node_name]
        contributors[node_name] = contributors.get(node_name, 0.0) + blame

        for upstream in rec.upstream:
            up_rec = self._nodes.get(upstream)
            if up_rec:
                up_blame = blame * (1.0 - up_rec.confidence)
                self._propagate(upstream, up_blame, contributors, depth - 1)


# ── EpistemicDebt ─────────────────────────────────────────────────────────────

@dataclass
class EpistemicDebtReport:
    """Summarises accumulated uncertainty across a workflow."""
    run_id: str
    total_nodes: int
    mean_confidence: float
    min_confidence: float
    max_confidence: float
    low_confidence_nodes: List[str]    # nodes below threshold
    propagated_uncertainty: float      # product of (1-confidence) across chain
    debt_score: float                  # 1 - mean_confidence (0=perfect, 1=useless)


class EpistemicDebt:
    """
    Tracks accumulated uncertainty (epistemic debt) across a workflow run.

    Each node adds its confidence score.  The final report shows which
    nodes are dragging down overall quality.

    Usage::

        debt = EpistemicDebt(run_id="run-42", low_threshold=0.7)
        debt.add("fetch",   confidence=0.95)
        debt.add("analyse", confidence=0.60)
        debt.add("report",  confidence=0.45)
        report = debt.report()
    """

    def __init__(self, run_id: str, low_threshold: float = 0.7) -> None:
        self.run_id = run_id
        self.low_threshold = low_threshold
        self._nodes: List[Tuple[str, float]] = []

    def add(self, node_name: str, confidence: float) -> None:
        self._nodes.append((node_name, confidence))

    def report(self) -> EpistemicDebtReport:
        if not self._nodes:
            return EpistemicDebtReport(
                run_id=self.run_id,
                total_nodes=0,
                mean_confidence=1.0,
                min_confidence=1.0,
                max_confidence=1.0,
                low_confidence_nodes=[],
                propagated_uncertainty=0.0,
                debt_score=0.0,
            )
        scores = [c for _, c in self._nodes]
        low = [n for n, c in self._nodes if c < self.low_threshold]
        propagated = 1.0
        for s in scores:
            propagated *= 1.0 - s

        return EpistemicDebtReport(
            run_id=self.run_id,
            total_nodes=len(self._nodes),
            mean_confidence=sum(scores) / len(scores),
            min_confidence=min(scores),
            max_confidence=max(scores),
            low_confidence_nodes=low,
            propagated_uncertainty=propagated,
            debt_score=1.0 - (sum(scores) / len(scores)),
        )


# ── DecisionAuditTrail ────────────────────────────────────────────────────────

@dataclass
class AuditEvent:
    """Human-readable record of a single decision in a workflow."""
    run_id: str
    node: str
    action: str
    input_summary: str
    output_summary: str
    confidence: float = 1.0
    reasoning: str = ""
    flags: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def render(self) -> str:
        flags = f"  ⚠ {', '.join(self.flags)}" if self.flags else ""
        return (
            f"[{self.node}] {self.action}\n"
            f"  Input:  {self.input_summary}\n"
            f"  Output: {self.output_summary}\n"
            f"  Confidence: {self.confidence:.0%}{flags}\n"
            + (f"  Reasoning: {self.reasoning}\n" if self.reasoning else "")
        )


class DecisionAuditTrail:
    """
    Human-readable chronological audit log for a workflow run.

    Designed for business reviewers — not OTel spans.

    Usage::

        trail = DecisionAuditTrail(run_id="run-42")
        trail.log("fetch", action="Searched database",
                  input_summary="query: Q3 revenue",
                  output_summary="Found 12 records",
                  confidence=0.95)
        print(trail.render())
    """

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._events: List[AuditEvent] = []

    def log(
        self,
        node: str,
        action: str,
        input_summary: str = "",
        output_summary: str = "",
        confidence: float = 1.0,
        reasoning: str = "",
        flags: Optional[List[str]] = None,
    ) -> AuditEvent:
        ev = AuditEvent(
            run_id=self.run_id,
            node=node,
            action=action,
            input_summary=input_summary,
            output_summary=output_summary,
            confidence=confidence,
            reasoning=reasoning,
            flags=flags or [],
        )
        self._events.append(ev)
        return ev

    def events(self) -> List[AuditEvent]:
        return list(self._events)

    def render(self) -> str:
        header = f"=== Audit Trail: {self.run_id} ===\n"
        return header + "\n".join(ev.render() for ev in self._events)

    def flagged(self) -> List[AuditEvent]:
        return [ev for ev in self._events if ev.flags]

    def low_confidence(self, threshold: float = 0.7) -> List[AuditEvent]:
        return [ev for ev in self._events if ev.confidence < threshold]

    def export_dict(self) -> List[Dict[str, Any]]:
        from dataclasses import asdict
        return [asdict(ev) for ev in self._events]


# ── CounterfactualReplayer ────────────────────────────────────────────────────

@dataclass
class CounterfactualResult:
    """Result of a counterfactual replay with a patched node."""
    original_output: Any
    counterfactual_output: Any
    patched_node: str
    patched_value: Any
    changed: bool


class CounterfactualReplayer:
    """
    Replays a workflow with a specific node's output patched to a
    hypothetical value.  Answers "what would have happened if node X
    had returned Y instead?"

    Usage::

        replayer = CounterfactualReplayer()
        result = await replayer.replay(
            workflow_fn=my_pipeline,
            original_ctx=ctx,
            patch_node="fetch",
            patch_output={"data": "alternative data"},
        )
        print("Changed:", result.changed)
    """

    async def replay(
        self,
        workflow_fn: Callable,
        original_ctx: Dict[str, Any],
        patch_node: str,
        patch_output: Any,
    ) -> CounterfactualResult:
        """Run workflow with patched node, compare to original."""
        import asyncio

        # Run original
        original_result = workflow_fn(dict(original_ctx))
        if asyncio.iscoroutine(original_result):
            original_result = await original_result

        # Inject patch
        patched_ctx = dict(original_ctx)
        patched_ctx[f"_patch_{patch_node}"] = patch_output

        cf_result = workflow_fn(patched_ctx)
        if asyncio.iscoroutine(cf_result):
            cf_result = await cf_result

        return CounterfactualResult(
            original_output=original_result,
            counterfactual_output=cf_result,
            patched_node=patch_node,
            patched_value=patch_output,
            changed=original_result != cf_result,
        )


# ── PatternMiner ──────────────────────────────────────────────────────────────

@dataclass
class WorkflowRunSummary:
    """Summary of a completed workflow run for pattern mining."""
    run_id: str
    nodes: List[str]
    mean_confidence: float
    final_outcome: Optional[float] = None
    tags: Dict[str, str] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


@dataclass
class Pattern:
    """A recurring node sequence found across runs."""
    sequence: Tuple[str, ...]
    count: int
    mean_outcome: float
    mean_confidence: float


class PatternMiner:
    """
    Mines recurring patterns across workflow runs.

    Identifies common node sequences and correlates them with outcomes
    to surface which execution paths tend to succeed or fail.

    Usage::

        miner = PatternMiner(min_support=3)
        miner.add_run(WorkflowRunSummary(
            run_id="r1", nodes=["fetch","analyse","report"],
            mean_confidence=0.85, final_outcome=1.0
        ))
        patterns = miner.mine(seq_length=2)
    """

    def __init__(self, min_support: int = 2) -> None:
        self.min_support = min_support
        self._runs: List[WorkflowRunSummary] = []

    def add_run(self, run: WorkflowRunSummary) -> None:
        self._runs.append(run)

    def mine(self, seq_length: int = 2) -> List[Pattern]:
        from collections import defaultdict
        counts: Dict[Tuple[str, ...], List[WorkflowRunSummary]] = defaultdict(list)

        for run in self._runs:
            nodes = run.nodes
            for i in range(len(nodes) - seq_length + 1):
                seq = tuple(nodes[i: i + seq_length])
                counts[seq].append(run)

        patterns: List[Pattern] = []
        for seq, runs in counts.items():
            if len(runs) < self.min_support:
                continue
            outcomes = [r.final_outcome for r in runs if r.final_outcome is not None]
            mean_outcome = sum(outcomes) / len(outcomes) if outcomes else 0.0
            mean_conf = sum(r.mean_confidence for r in runs) / len(runs)
            patterns.append(Pattern(
                sequence=seq,
                count=len(runs),
                mean_outcome=mean_outcome,
                mean_confidence=mean_conf,
            ))

        return sorted(patterns, key=lambda p: -p.count)


__all__ = [
    "NodeRecord",
    "BlameResult",
    "CausalAttributor",
    "EpistemicDebtReport",
    "EpistemicDebt",
    "AuditEvent",
    "DecisionAuditTrail",
    "CounterfactualResult",
    "CounterfactualReplayer",
    "WorkflowRunSummary",
    "Pattern",
    "PatternMiner",
]
