"""
Epistemic Transparency Engine.

Epistemic transparency is a core pillar of Multigen:
  - Every agent output carries structured epistemic metadata.
  - Uncertainty propagates through the graph (upstream gaps widen downstream gaps).
  - The system generates a full transparency report on demand.
  - Epistemic debt (accumulated unknowns) is surfaced to humans before high-stakes nodes.

Epistemic metadata format (every agent output should include this):
────────────────────────────────────────────────────────────────────
{
  "epistemic": {
    "confidence":          0.82,          # 0.0–1.0
    "reasoning":           "string",      # why this conclusion was reached
    "uncertainty_sources": ["list"],      # inputs that were unclear / missing
    "assumptions":         ["list"],      # what was assumed when data was absent
    "known_limitations":   ["list"],      # what this agent structurally cannot assess
    "known_unknowns":      ["list"],      # identified gaps in knowledge
    "evidence_quality":    "high|medium|low|none",
    "data_completeness":   0.0–1.0,       # fraction of expected data that was present
    "propagated_uncertainty": 0.0–1.0,   # uncertainty inherited from upstream nodes
    "flags":               ["list"]       # e.g. ["needs_human_review", "extrapolated"]
  }
}
────────────────────────────────────────────────────────────────────

Agents that don't return this format get a default epistemic envelope
computed from their raw confidence score.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_EVIDENCE_QUALITY_WEIGHT = {"high": 1.0, "medium": 0.75, "low": 0.5, "none": 0.2}


# ── Per-node epistemic extraction ─────────────────────────────────────────────

def extract_epistemic(output: Dict[str, Any]) -> Dict[str, Any]:
    """
    Pull or construct the epistemic envelope from a node output.
    Guarantees all standard keys are present.
    """
    inner = output.get("output")
    inner_dict: Dict[str, Any] = inner if isinstance(inner, dict) else {}
    raw = output.get("epistemic") or inner_dict.get("epistemic") or {}
    if not isinstance(raw, dict):
        raw = {}

    confidence = _safe_float(
        raw.get("confidence")
        or output.get("confidence")
        or inner_dict.get("confidence"),
        default=0.5,
    )

    return {
        "confidence":             confidence,
        "reasoning":              raw.get("reasoning", "No reasoning provided."),
        "uncertainty_sources":    _ensure_list(raw.get("uncertainty_sources")),
        "assumptions":            _ensure_list(raw.get("assumptions")),
        "known_limitations":      _ensure_list(raw.get("known_limitations")),
        "known_unknowns":         _ensure_list(raw.get("known_unknowns")),
        "evidence_quality":       raw.get("evidence_quality", "medium"),
        "data_completeness":      _safe_float(raw.get("data_completeness"), default=0.8),
        "propagated_uncertainty": _safe_float(raw.get("propagated_uncertainty"), default=0.0),
        "flags":                  _ensure_list(raw.get("flags")),
    }


def _safe_float(val: Any, default: float = 0.5) -> float:
    try:
        f = float(val)
        return max(0.0, min(1.0, f))
    except (TypeError, ValueError):
        return default


def _ensure_list(val: Any) -> List[str]:
    if isinstance(val, list):
        return [str(v) for v in val]
    if isinstance(val, str) and val:
        return [val]
    return []


# ── Uncertainty propagation ────────────────────────────────────────────────────

def propagate_uncertainty(
    node_epistemic: Dict[str, Any],
    upstream_epistemics: List[Dict[str, Any]],
    propagation_weight: float = 0.3,
) -> Dict[str, Any]:
    """
    Combine a node's own epistemic state with uncertainty inherited from
    upstream nodes.

    propagation_weight: how much upstream uncertainty bleeds into this node.
      0.0 = fully independent  |  1.0 = fully determined by upstream
    """
    if not upstream_epistemics:
        return node_epistemic

    # Aggregate upstream confidence (geometric mean → penalises weak links)
    upstream_confidences = [
        e.get("confidence", 0.5) for e in upstream_epistemics
    ]
    geo_mean = 1.0
    for c in upstream_confidences:
        geo_mean *= max(c, 0.01)
    geo_mean = geo_mean ** (1.0 / len(upstream_confidences))

    # Upstream uncertainty is 1 - geo_mean
    inherited_uncertainty = (1.0 - geo_mean) * propagation_weight

    # Pool all upstream known_unknowns and flags
    pooled_unknowns: List[str] = []
    pooled_flags: List[str] = []
    for e in upstream_epistemics:
        pooled_unknowns.extend(e.get("known_unknowns", []))
        pooled_flags.extend(e.get("flags", []))

    updated = dict(node_epistemic)
    updated["propagated_uncertainty"] = round(inherited_uncertainty, 4)

    # Effective confidence is reduced by inherited uncertainty
    raw_conf = node_epistemic.get("confidence", 0.5)
    updated["confidence"] = round(
        max(0.0, raw_conf * (1.0 - inherited_uncertainty)), 4
    )

    # Merge known unknowns from upstream
    combined_unknowns = list(set(
        node_epistemic.get("known_unknowns", []) + pooled_unknowns
    ))
    updated["known_unknowns"] = combined_unknowns

    # Propagate critical flags
    if "needs_human_review" in pooled_flags and "needs_human_review" not in updated["flags"]:
        updated["flags"] = updated["flags"] + ["needs_human_review_upstream"]

    return updated


# ── Graph-level epistemic state ────────────────────────────────────────────────

class EpistemicStateTracker:
    """
    Maintains the epistemic state of the entire graph during execution.

    Tracks:
      - Per-node epistemic envelopes
      - Cumulative epistemic debt (sum of known unknowns × severity)
      - Uncertainty propagation through edges
      - Flags requiring human attention
    """

    def __init__(self, workflow_id: str) -> None:
        self.workflow_id = workflow_id
        self._node_states: Dict[str, Dict[str, Any]] = {}
        self._epistemic_debt: List[Dict[str, Any]] = []
        self._human_review_flags: List[Dict[str, Any]] = []

    def record_node(
        self,
        node_id: str,
        agent_name: str,
        output: Dict[str, Any],
        upstream_node_ids: List[str],
    ) -> Dict[str, Any]:
        """
        Extract, propagate, and store epistemic state for a completed node.
        Returns the final epistemic envelope.
        """
        raw_epistemic = extract_epistemic(output)

        upstream_states = [
            self._node_states[nid]
            for nid in upstream_node_ids
            if nid in self._node_states
        ]

        final_epistemic = propagate_uncertainty(raw_epistemic, upstream_states)
        final_epistemic["node_id"] = node_id
        final_epistemic["agent"] = agent_name

        self._node_states[node_id] = final_epistemic

        # Register epistemic debt items
        for unknown in final_epistemic.get("known_unknowns", []):
            self._epistemic_debt.append({
                "node_id": node_id,
                "unknown": unknown,
                "severity": _severity_from_confidence(final_epistemic["confidence"]),
            })

        # Flag for human review if confidence is too low or flagged explicitly
        if (
            final_epistemic["confidence"] < 0.6
            or "needs_human_review" in final_epistemic.get("flags", [])
            or "needs_human_review_upstream" in final_epistemic.get("flags", [])
        ):
            self._human_review_flags.append({
                "node_id": node_id,
                "agent": agent_name,
                "confidence": final_epistemic["confidence"],
                "reason": (
                    "low_confidence" if final_epistemic["confidence"] < 0.6
                    else "explicit_flag"
                ),
                "known_unknowns": final_epistemic.get("known_unknowns", []),
            })

        logger.debug(
            "Epistemic recorded [%s] agent=%s confidence=%.2f propagated_uncertainty=%.2f",
            node_id, agent_name,
            final_epistemic["confidence"],
            final_epistemic["propagated_uncertainty"],
        )
        return final_epistemic

    def get_transparency_report(self) -> Dict[str, Any]:
        """
        Generate a full epistemic transparency report for the current graph run.
        Intended for human review before high-stakes decisions.
        """
        if not self._node_states:
            return {"workflow_id": self.workflow_id, "status": "no_nodes_completed"}

        confidences = [s["confidence"] for s in self._node_states.values()]
        avg_confidence = sum(confidences) / len(confidences)
        min_confidence = min(confidences)
        propagated = [s["propagated_uncertainty"] for s in self._node_states.values()]
        avg_propagation = sum(propagated) / len(propagated)

        all_unknowns = []
        for state in self._node_states.values():
            all_unknowns.extend(state.get("known_unknowns", []))
        all_unknowns = list(set(all_unknowns))

        evidence_qualities = [
            s.get("evidence_quality", "medium") for s in self._node_states.values()
        ]
        weakest_evidence = min(
            evidence_qualities,
            key=lambda q: _EVIDENCE_QUALITY_WEIGHT.get(q, 0.5),
        )

        return {
            "workflow_id": self.workflow_id,
            "summary": {
                "nodes_assessed":      len(self._node_states),
                "avg_confidence":      round(avg_confidence, 3),
                "min_confidence":      round(min_confidence, 3),
                "avg_propagated_uncertainty": round(avg_propagation, 3),
                "epistemic_debt_items": len(self._epistemic_debt),
                "nodes_flagged_for_human_review": len(self._human_review_flags),
                "weakest_evidence_quality": weakest_evidence,
                "total_known_unknowns": len(all_unknowns),
            },
            "overall_trustworthiness": _trustworthiness_label(avg_confidence, len(all_unknowns)),
            "node_states":            self._node_states,
            "epistemic_debt":         self._epistemic_debt,
            "human_review_flags":     self._human_review_flags,
            "known_unknowns_pool":    all_unknowns,
            "recommendation": _generate_recommendation(
                avg_confidence, len(all_unknowns), len(self._human_review_flags)
            ),
        }

    def get_node_epistemic(self, node_id: str) -> Optional[Dict[str, Any]]:
        return self._node_states.get(node_id)

    def has_critical_flags(self) -> bool:
        """True if any node has confidence < 0.5 or explicit needs_human_review flag."""
        return any(
            f["confidence"] < 0.5 or f["reason"] == "explicit_flag"
            for f in self._human_review_flags
        )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _severity_from_confidence(confidence: float) -> str:
    if confidence < 0.4:
        return "critical"
    if confidence < 0.6:
        return "high"
    if confidence < 0.75:
        return "medium"
    return "low"


def _trustworthiness_label(avg_confidence: float, n_unknowns: int) -> str:
    if avg_confidence >= 0.85 and n_unknowns <= 2:
        return "HIGH — results are well-supported and uncertainty is low"
    if avg_confidence >= 0.70 and n_unknowns <= 5:
        return "MEDIUM — results are usable but verify flagged unknowns"
    if avg_confidence >= 0.55:
        return "LOW — significant uncertainty; human review strongly recommended"
    return "CRITICAL — confidence is too low to act on without expert review"


def _generate_recommendation(
    avg_confidence: float,
    n_unknowns: int,
    n_flagged: int,
) -> str:
    parts = []
    if avg_confidence < 0.65:
        parts.append(
            f"Average confidence ({avg_confidence:.0%}) is below the recommended "
            f"threshold (65%). Consider re-running with richer input data."
        )
    if n_unknowns > 5:
        parts.append(
            f"{n_unknowns} known unknowns accumulated across the graph. "
            f"Resolve the highest-severity items before making decisions."
        )
    if n_flagged > 0:
        parts.append(
            f"{n_flagged} node(s) have been flagged for human review. "
            f"Inspect the 'human_review_flags' section."
        )
    if not parts:
        return "All nodes passed epistemic quality thresholds. Proceed with normal review."
    return " | ".join(parts)


# ── Default epistemic envelope for agents that don't report one ───────────────

def default_epistemic(confidence: float, agent_name: str) -> Dict[str, Any]:
    """Minimal epistemic envelope for agents that return no epistemic field."""
    return {
        "confidence":             confidence,
        "reasoning":              f"No reasoning provided by {agent_name}.",
        "uncertainty_sources":    ["agent did not report uncertainty sources"],
        "assumptions":            ["agent did not report assumptions"],
        "known_limitations":      ["agent did not report known limitations"],
        "known_unknowns":         [],
        "evidence_quality":       "medium",
        "data_completeness":      0.7,
        "propagated_uncertainty": 0.0,
        "flags":                  ["no_epistemic_metadata"],
    }
