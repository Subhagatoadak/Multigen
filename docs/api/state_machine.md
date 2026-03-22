# API Reference — State Machine & MCMC

Multigen's MCMC state machine is a **composable pattern** built from standard Python constructs and Multigen agents, rather than a single class. This page documents the canonical patterns and helper utilities.

## MCMC Workflow Pattern

### Core Loop

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.schemas import AgentStep, Message
from enum import Enum

class WorkflowState(Enum):
    DRAFT    = "draft"
    CRITIQUE = "critique"
    DECIDE   = "decide"
    DONE     = "done"

# Configuration
CONFIDENCE_THRESHOLD = 0.80
MAX_ITERATIONS       = 10

state     = WorkflowState.DRAFT
ctx       = Context(goal="Your workflow goal")
ctx.scratch.update(your_inputs)
iteration = 0

while state != WorkflowState.DONE and iteration < MAX_ITERATIONS:
    iteration += 1

    if state == WorkflowState.DRAFT:
        draft_agent.run(ctx)
        state = WorkflowState.CRITIQUE

    elif state == WorkflowState.CRITIQUE:
        critique_agent.run(ctx)
        confidence = ctx.scratch.get("confidence", 0.0)
        state = WorkflowState.DECIDE if confidence >= CONFIDENCE_THRESHOLD else WorkflowState.DRAFT

    elif state == WorkflowState.DECIDE:
        decision_agent.run(ctx)
        state = WorkflowState.DONE
```

---

## Ensemble Functions

### `run_ensemble`

Run a pipeline function N times and compute consensus statistics.

```python
import copy
import statistics
from typing import Any, Callable, Dict, List, Optional

def run_ensemble(
    pipeline_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
    inputs: Dict[str, Any],
    *,
    n_chains: int = 5,
    noise_scale: float = 0.02,
    noise_keys: Optional[List[str]] = None,
    random_seed: int = 42,
) -> Dict[str, Any]:
    """
    Run pipeline_fn n_chains times with Gaussian noise injected into
    numeric inputs. Returns ensemble statistics and consensus decision.

    Args:
        pipeline_fn:  Function (inputs) → {"decision": str, "confidence": float, ...}
        inputs:       Base inputs dict.
        n_chains:     Number of MCMC chains.
        noise_scale:  Std-dev of Gaussian noise (fraction of value).
        noise_keys:   Keys to perturb. If None, perturbs all numeric values.
        random_seed:  Random seed for reproducibility.

    Returns:
        Dict with keys: chains, mean_confidence, std_confidence,
        consensus_decision, agreement_pct, decision_counts, high_confidence.
    """
    import random
    random.seed(random_seed)
    chains = []

    for i in range(n_chains):
        perturbed = copy.deepcopy(inputs)

        # Inject noise into numeric inputs
        for key, val in list(perturbed.items()):
            if (noise_keys is None or key in noise_keys) and isinstance(val, (int, float)):
                noise = random.gauss(0, noise_scale)
                perturbed[key] = val * (1.0 + noise)

        result = pipeline_fn(perturbed)
        chains.append({
            "chain_id": i,
            "decision": result.get("decision", "UNKNOWN"),
            "confidence": result.get("confidence", 0.5),
            "raw": result,
        })

    decisions   = [c["decision"]   for c in chains]
    confidences = [c["confidence"] for c in chains]

    mean_conf   = statistics.mean(confidences)
    std_conf    = statistics.stdev(confidences) if n_chains > 1 else 0.0

    decision_counts = {}
    for d in decisions:
        decision_counts[d] = decision_counts.get(d, 0) + 1

    consensus  = max(decision_counts, key=decision_counts.get)
    agreement  = decision_counts[consensus] / n_chains

    return {
        "chains": chains,
        "mean_confidence": round(mean_conf, 4),
        "std_confidence": round(std_conf, 4),
        "consensus_decision": consensus,
        "agreement_pct": round(agreement, 4),
        "decision_counts": decision_counts,
        "high_confidence": agreement >= 0.80 and std_conf < 0.10,
    }
```

### `has_converged`

Check whether a sequence of values has converged.

```python
def has_converged(
    history: List[float],
    *,
    window: int = 5,
    tolerance: float = 0.02,
    require_minimum_steps: int = 3,
) -> bool:
    """
    Returns True if the last `window` values have converged (std < tolerance).

    Args:
        history:              List of values (e.g., confidence scores per iteration).
        window:               Number of recent values to check.
        tolerance:            Std-dev threshold (lower = stricter convergence).
        require_minimum_steps: Minimum history length before checking.

    Returns:
        True if converged, False otherwise.
    """
    if len(history) < max(window, require_minimum_steps):
        return False
    recent = history[-window:]
    return statistics.stdev(recent) < tolerance
```

---

## Temperature Sampler

```python
import functools
import random

def temperature_sampler(
    step_fn,
    *,
    temperature: float = 1.0,
    numeric_keys: Optional[List[str]] = None,
):
    """
    Wrap a step function with temperature-controlled noise injection.

    Higher temperature → more exploration (sampling noise).
    Lower temperature → more exploitation (deterministic).

    temperature=0.0 → no noise (deterministic)
    temperature=1.0 → standard noise (scale=0.03 per numeric value)
    temperature=2.0 → high exploration (scale=0.06)

    Args:
        step_fn:      The step function to wrap.
        temperature:  Controls noise scale. 1.0 = standard.
        numeric_keys: Specific scratch keys to perturb. None = all numerics.
    """
    @functools.wraps(step_fn)
    def sampled(ctx):
        if temperature > 0:
            scale = temperature * 0.03
            for key, val in list(ctx.scratch.items()):
                if (numeric_keys is None or key in numeric_keys) and isinstance(val, (int, float)):
                    ctx.scratch[key] = val + random.gauss(0, scale)
        return step_fn(ctx)
    return sampled
```

---

## `EnsembleResult` (return type reference)

The return type from `run_ensemble`. Not a formal class — this is the documented dict structure.

| Key | Type | Description |
|-----|------|-------------|
| `chains` | `List[dict]` | Per-chain results with `chain_id`, `decision`, `confidence`, `raw` |
| `mean_confidence` | `float` | Mean confidence across all chains |
| `std_confidence` | `float` | Standard deviation of confidence (lower = more stable) |
| `consensus_decision` | `str` | Most common decision across chains |
| `agreement_pct` | `float` | Fraction of chains agreeing with consensus (0–1) |
| `decision_counts` | `dict` | Count of each decision label |
| `high_confidence` | `bool` | `True` if `agreement_pct >= 0.80` and `std_confidence < 0.10` |

---

## State Machine Configuration Reference

| Setting | Recommended Value | When to Adjust |
|---------|------------------|----------------|
| `n_chains` | 5 | Increase for higher-stakes decisions |
| `noise_scale` | 0.02 | Increase if pipeline outputs are very sensitive |
| `confidence_threshold` | 0.75–0.85 | Higher for safety-critical applications |
| `max_iterations` | 5–10 | Increase if quality converges slowly |
| `convergence_tolerance` | 0.02–0.05 | Tighten for precise applications |
| `window` | 3–5 | Window for convergence check |

---

## Pattern Variants

### Conservative (high-stakes)

```python
ensemble = run_ensemble(
    pipeline_fn,
    inputs,
    n_chains=9,               # more chains for stability
    noise_scale=0.01,         # less perturbation
)
threshold = 0.90              # require 90% agreement
```

### Exploratory (research/draft)

```python
ensemble = run_ensemble(
    pipeline_fn,
    inputs,
    n_chains=3,               # fewer chains for speed
    noise_scale=0.05,         # more perturbation to explore
)
threshold = 0.65              # lower bar for draft quality
```
