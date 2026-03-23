"""
Reasoning strategies for GraphWorkflow.

Provides:
  extract_confidence    — pull a 0–1 confidence score from any node output
  should_reflect        — decide whether to trigger a reflection loop
  build_reflection_node — create an injected critic node definition
  select_consensus      — merge/select from parallel fan-out results
  compute_descendants   — BFS over edge list to find all reachable nodes
  prune_pending         — remove a branch's descendant nodes from pending deque
"""
from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, List


# ── Confidence extraction ──────────────────────────────────────────────────────

_CONFIDENCE_KEYS = ("confidence", "score", "quality", "certainty", "probability", "p")


def extract_confidence(output: Dict[str, Any]) -> float:
    """
    Return a [0.0, 1.0] confidence score from a node output dict.

    Checks common keys; falls back to 1.0 (fully confident) when absent
    so nodes without explicit scoring never trigger spurious reflections.
    """
    for key in _CONFIDENCE_KEYS:
        val = output.get(key)
        if val is not None:
            try:
                return max(0.0, min(1.0, float(val)))
            except (TypeError, ValueError):
                continue
    return 1.0


# ── Reflection ─────────────────────────────────────────────────────────────────

def should_reflect(
    output: Dict[str, Any],
    threshold: float,
    reflection_count: int,
    max_reflections: int,
) -> bool:
    """
    Return True when the output confidence is below threshold and
    the reflection budget has not been exhausted.

    threshold=0.0 disables reflection entirely (default for nodes that
    do not declare 'reflection_threshold').
    """
    if threshold <= 0.0 or reflection_count >= max_reflections:
        return False
    return extract_confidence(output) < threshold


def build_reflection_node(
    original_node_id: str,
    reflection_count: int,
    critic_agent: str,
    prior_output: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Build a new node definition that critiques the prior output.

    The node's params embed the prior output inline so the critic
    agent receives it as structured data without needing {{…}} refs
    (refs are resolved at activity dispatch, not at node-build time).

    The returned node ID encodes the reflection round so multiple
    reflection hops produce unique IDs.
    """
    ref_id = f"{original_node_id}__reflect_{reflection_count}"
    return {
        "id": ref_id,
        "agent": critic_agent,
        "params": {
            "instruction": (
                "You are a quality critic. Evaluate the subject output and return "
                "an improved version plus a 'confidence' score (0.0–1.0). "
                "Return JSON: {improved_output, confidence, critique}."
            ),
            "subject": prior_output,
            "reflection_round": reflection_count,
        },
        "retry": 2,
        "timeout": 45,
        "edges_to": [],
        "_meta": {"reflection_of": original_node_id, "round": reflection_count},
    }


# ── Fan-out consensus ──────────────────────────────────────────────────────────

def select_consensus(
    results: List[Dict[str, Any]],
    strategy: str = "highest_confidence",
) -> Dict[str, Any]:
    """
    Select or merge results from a parallel fan-out execution.

    Strategies
    ----------
    highest_confidence  Pick the single result with the best confidence score.
    aggregate           Merge all outputs into a list under fan_out_results.
    majority_vote       Return the output value that appears most often
                        (compares JSON-serialised output for equality).
    first_success       Return the first result that has no error key.
    """
    if not results:
        return {}

    clean = [r for r in results if r and "error" not in r]
    if not clean:
        return results[0] if results else {}

    if strategy == "highest_confidence":
        return max(clean, key=lambda r: extract_confidence(r.get("output", {})))

    if strategy == "aggregate":
        return {
            "fan_out_results": [r.get("output", {}) for r in clean],
            "consensus": "aggregate",
            "count": len(clean),
            "confidence": sum(extract_confidence(r.get("output", {})) for r in clean) / len(clean),
        }

    if strategy == "majority_vote":
        import json as _json
        freq: Dict[str, int] = {}
        for r in clean:
            key = _json.dumps(r.get("output", {}), sort_keys=True, default=str)
            freq[key] = freq.get(key, 0) + 1
        best_key = max(freq, key=freq.__getitem__)
        for r in clean:
            if _json.dumps(r.get("output", {}), sort_keys=True, default=str) == best_key:
                return r

    if strategy == "first_success":
        return clean[0]

    return clean[0]


# ── Branch graph traversal ─────────────────────────────────────────────────────

def compute_descendants(root_id: str, all_edges: List[Dict[str, Any]]) -> set:
    """
    BFS over the edge list to collect all reachable node IDs from root_id
    (not including root_id itself).  Used by prune_branch to determine
    which pending nodes to remove.
    """
    visited: set = set()
    queue: Deque[str] = deque([root_id])
    while queue:
        current = queue.popleft()
        for edge in all_edges:
            target = edge.get("target", "")
            if edge.get("source") == current and target not in visited:
                visited.add(target)
                queue.append(target)
    return visited


def prune_pending(
    pending: Deque[str],
    skip_nodes: set,
    root_id: str,
    all_edges: List[Dict[str, Any]],
) -> None:
    """
    Add root_id and all its reachable descendants to skip_nodes,
    and remove them from the pending deque in place.

    This implements "no-go branch" — a whole reasoning sub-tree is
    abandoned without executing any of its remaining nodes.
    """
    descendants = compute_descendants(root_id, all_edges)
    to_prune = descendants | {root_id}
    skip_nodes.update(to_prune)
    # Rebuild pending without pruned nodes
    remaining = [n for n in pending if n not in to_prune]
    pending.clear()
    pending.extend(remaining)
