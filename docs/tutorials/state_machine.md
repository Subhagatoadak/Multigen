# Tutorial — MCMC State Machines

## Overview

Multigen's state machine pattern enables **iterative, confidence-driven workflows**. Unlike a fixed pipeline that runs once and returns, a state machine loops until an exit condition is met.

This is inspired by **Markov Chain Monte Carlo (MCMC)** sampling: instead of running a pipeline once and hoping for the best, you run multiple chains, compute statistics over the outputs, and only commit when the distribution is stable.

```
    ┌──────────────────────────────────────────────────────────┐
    │                  MCMC State Machine                      │
    │                                                          │
    │   DRAFT ──────► REVIEW ──────► DECIDE ──────► DONE       │
    │     ▲               │                                    │
    │     └───────────────┘ (if confidence < threshold)        │
    │                                                          │
    └──────────────────────────────────────────────────────────┘
```

---

## 1. Basic State Machine Loop

```python
from enum import Enum
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.schemas import AgentStep, Message

class State(Enum):
    DRAFT    = "draft"
    CRITIQUE = "critique"
    REVISE   = "revise"
    DONE     = "done"

# Agent that produces a draft with improving quality each iteration
def draft_step(ctx: Context) -> AgentStep:
    iteration = ctx.scratch.get("iteration", 0) + 1
    quality   = min(0.95, 0.45 + iteration * 0.15)  # improves each pass
    content   = f"Draft v{iteration}: quality={quality:.2f}"
    return AgentStep(
        out_messages=[Message(role="assistant", content=content)],
        state_updates={"iteration": iteration, "quality": quality, "draft": content},
        stop=False,
    )

# Agent that critiques the draft and suggests improvements
def critique_step(ctx: Context) -> AgentStep:
    quality    = ctx.scratch.get("quality", 0)
    iteration  = ctx.scratch.get("iteration", 0)
    passed     = quality >= 0.80
    critique   = f"Critique iter={iteration}: quality={quality:.2f} → {'PASS' if passed else 'NEEDS_REVISION'}"
    suggestions = [] if passed else ["Add more specific examples", "Improve evidence coverage"]
    return AgentStep(
        out_messages=[Message(role="assistant", content=critique)],
        state_updates={"passed_review": passed, "suggestions": suggestions},
        stop=False,
    )

drafter  = AgentBuilder("drafter",  "writer").with_step(draft_step).build()
critic   = AgentBuilder("critic",   "critic").with_step(critique_step).build()

# ── State Machine loop ────────────────────────────────────────────────────────
QUALITY_THRESHOLD = 0.80
MAX_ITERATIONS    = 5

state  = State.DRAFT
ctx    = Context(goal="Write a comprehensive market analysis report")
history = []

while state != State.DONE:
    if state == State.DRAFT:
        drafter.run(ctx)
        state = State.CRITIQUE

    elif state == State.CRITIQUE:
        critic.run(ctx)
        passed    = ctx.scratch.get("passed_review", False)
        quality   = ctx.scratch.get("quality", 0)
        iteration = ctx.scratch.get("iteration", 0)

        history.append({
            "iteration": iteration,
            "quality": quality,
            "state": state.value,
            "passed": passed,
        })

        if passed or iteration >= MAX_ITERATIONS:
            state = State.DONE
        else:
            state = State.DRAFT  # loop back for revision
            print(f"  Iteration {iteration}: quality={quality:.2f} → REVISE")

print(f"\nFinal quality: {ctx.scratch.get('quality', 0):.2f}")
print(f"Iterations: {ctx.scratch.get('iteration', 0)}")
print(f"Final draft: {ctx.scratch.get('draft', 'N/A')}")
```

---

## 2. MCMC Ensemble — Multiple Chains

The core MCMC idea: run the same pipeline N times with slight variations (different random seeds, temperatures, or noise), then compute consensus over the results.

```python
import copy
import random
import statistics
from typing import Dict, Any, List

def run_single_chain(
    inputs: Dict[str, Any],
    chain_id: int,
    noise_scale: float = 0.05,
) -> Dict[str, Any]:
    """
    Run the pipeline once with perturbed inputs to simulate MCMC sampling.
    The perturbation models epistemic uncertainty in the pipeline.
    """
    ctx = Context(goal="Analyse risk")
    ctx.scratch.update(inputs)

    # Add noise to numeric inputs (simulates sampling from the uncertainty distribution)
    if "risk_score" in ctx.scratch:
        noise = random.gauss(0, noise_scale)
        ctx.scratch["risk_score"] = max(0, min(1, ctx.scratch["risk_score"] + noise))

    # Run the pipeline
    drafter.run(ctx)
    critic.run(ctx)

    return {
        "chain_id": chain_id,
        "quality": ctx.scratch.get("quality", 0),
        "decision": "APPROVE" if ctx.scratch.get("quality", 0) >= 0.80 else "REVIEW",
        "iterations": ctx.scratch.get("iteration", 0),
    }


def run_ensemble(
    inputs: Dict[str, Any],
    n_chains: int = 5,
    noise_scale: float = 0.05,
) -> Dict[str, Any]:
    """
    Run N chains and compute consensus statistics.

    Returns:
        Dict with per-chain results, mean/std quality, consensus decision,
        and agreement percentage.
    """
    random.seed(42)   # reproducible demo
    chains = []

    for i in range(n_chains):
        result = run_single_chain(copy.deepcopy(inputs), chain_id=i, noise_scale=noise_scale)
        chains.append(result)

    qualities  = [c["quality"] for c in chains]
    decisions  = [c["decision"] for c in chains]

    mean_q     = statistics.mean(qualities)
    std_q      = statistics.stdev(qualities) if n_chains > 1 else 0.0

    decision_counts = {}
    for d in decisions:
        decision_counts[d] = decision_counts.get(d, 0) + 1

    consensus  = max(decision_counts, key=decision_counts.get)
    agreement  = decision_counts[consensus] / n_chains

    return {
        "chains": chains,
        "mean_quality": round(mean_q, 4),
        "std_quality": round(std_q, 4),
        "consensus_decision": consensus,
        "agreement_pct": round(agreement, 4),
        "decision_counts": decision_counts,
        "high_confidence": agreement >= 0.80 and std_q < 0.10,
    }


# Run the ensemble
ensemble = run_ensemble({"risk_score": 0.35, "application_id": "APP-001"}, n_chains=5)

print("Ensemble Results:")
print(f"  Per-chain quality: {[f'{c[\"quality\"]:.2f}' for c in ensemble['chains']]}")
print(f"  Mean quality     : {ensemble['mean_quality']:.4f}")
print(f"  Std quality      : {ensemble['std_quality']:.4f}")
print(f"  Consensus        : {ensemble['consensus_decision']}")
print(f"  Agreement        : {ensemble['agreement_pct']:.0%}")
print(f"  High confidence  : {ensemble['high_confidence']}")
```

---

## 3. Temperature Sampling

In MCMC terms, "temperature" controls how much randomness is injected. High temperature = more exploration. Low temperature = more exploitation (convergence).

```python
def create_temperature_sampler(base_fn, temperature: float = 1.0):
    """
    Wrap a step function with temperature-controlled noise injection.

    temperature=0.0 → deterministic (no noise)
    temperature=1.0 → standard noise
    temperature=2.0 → high exploration
    """
    import functools

    @functools.wraps(base_fn)
    def sampled_step(ctx: Context) -> AgentStep:
        # Inject temperature noise into numeric scratch values
        for key, value in list(ctx.scratch.items()):
            if isinstance(value, (int, float)) and not key.startswith("_"):
                noise = random.gauss(0, temperature * 0.03)
                ctx.scratch[key] = float(value) + noise

        return base_fn(ctx)

    return sampled_step


# Create agents with different temperatures
low_temp_agent  = AgentBuilder("low_temp",  "cautious").with_step(create_temperature_sampler(draft_step, temperature=0.2)).build()
high_temp_agent = AgentBuilder("high_temp", "creative").with_step(create_temperature_sampler(draft_step, temperature=2.0)).build()
```

---

## 4. Convergence Detection

Know when to stop sampling by detecting convergence:

```python
def has_converged(
    chain_history: List[float],
    window: int = 5,
    tolerance: float = 0.02,
) -> bool:
    """
    Check if the last `window` values have converged (std < tolerance).
    Modelled after the Gelman-Rubin convergence diagnostic.
    """
    if len(chain_history) < window:
        return False
    recent = chain_history[-window:]
    return statistics.stdev(recent) < tolerance


# Example: Run until convergence
ctx = Context(goal="Iterative refinement with convergence check")
quality_history = []
max_iter = 20

for i in range(max_iter):
    drafter.run(ctx)
    quality = ctx.scratch.get("quality", 0)
    quality_history.append(quality)

    if has_converged(quality_history, window=3, tolerance=0.05):
        print(f"Converged at iteration {i+1} with quality={quality:.4f}")
        break
    print(f"  iter={i+1} quality={quality:.4f} {'CONVERGING' if len(quality_history) >= 3 else ''}")
else:
    print(f"Max iterations reached. Final quality={quality_history[-1]:.4f}")
```

---

## 5. Multi-Chain State Machine (Full MCMC)

Combine state machine + ensemble + convergence into a full MCMC workflow:

```python
class MCMCWorkflow:
    """
    Full MCMC workflow with state machine + multi-chain ensemble + convergence.

    Attributes:
        confidence_threshold: minimum confidence to commit a decision
        n_chains: number of parallel MCMC chains
        max_iterations: maximum refinement iterations per chain
        convergence_tolerance: std threshold for convergence detection
    """

    def __init__(
        self,
        confidence_threshold: float = 0.80,
        n_chains: int = 5,
        max_iterations: int = 10,
        convergence_tolerance: float = 0.03,
    ):
        self.confidence_threshold   = confidence_threshold
        self.n_chains               = n_chains
        self.max_iterations         = max_iterations
        self.convergence_tolerance  = convergence_tolerance

    def run(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """Run MCMC sampling and return consensus result."""
        all_chain_histories = []

        for chain_id in range(self.n_chains):
            ctx = Context(goal="MCMC chain")
            ctx.scratch.update(copy.deepcopy(inputs))
            chain_history = []

            for step in range(self.max_iterations):
                drafter.run(ctx)
                quality = ctx.scratch.get("quality", 0)
                chain_history.append(quality)

                if has_converged(chain_history, window=3, tolerance=self.convergence_tolerance):
                    break

            all_chain_histories.append({
                "chain_id": chain_id,
                "final_quality": chain_history[-1] if chain_history else 0,
                "steps_to_convergence": len(chain_history),
                "converged": has_converged(chain_history, window=3, tolerance=self.convergence_tolerance),
            })

        # Compute ensemble statistics
        final_qualities = [c["final_quality"] for c in all_chain_histories]
        decisions       = ["APPROVE" if q >= self.confidence_threshold else "REVIEW" for q in final_qualities]

        mean_q          = statistics.mean(final_qualities)
        std_q           = statistics.stdev(final_qualities) if self.n_chains > 1 else 0.0
        consensus       = max(set(decisions), key=decisions.count)
        agreement       = decisions.count(consensus) / self.n_chains

        return {
            "chains": all_chain_histories,
            "mean_quality": round(mean_q, 4),
            "std_quality": round(std_q, 4),
            "consensus": consensus,
            "agreement": round(agreement, 4),
            "committed": agreement >= 0.80,
        }


# Run the full MCMC workflow
mcmc = MCMCWorkflow(confidence_threshold=0.80, n_chains=5, max_iterations=10)
result = mcmc.run({"initial_quality": 0.40})

print("\nFull MCMC Workflow Result")
print(f"Mean quality  : {result['mean_quality']:.4f}")
print(f"Std quality   : {result['std_quality']:.4f}")
print(f"Consensus     : {result['consensus']}")
print(f"Agreement     : {result['agreement']:.0%}")
print(f"Committed     : {result['committed']}")
print("\nChain details:")
for c in result["chains"]:
    print(f"  Chain {c['chain_id']}: final_q={c['final_quality']:.3f} steps={c['steps_to_convergence']} converged={c['converged']}")
```

---

## When to Use MCMC State Machines

| Use case | Key property | Recommended settings |
|----------|-------------|---------------------|
| Credit risk | Binary decision under uncertainty | chains=5, threshold=0.75 |
| Report generation | Quality gate | chains=3, threshold=0.80 |
| Medical triage | Safety-critical | chains=7, threshold=0.90 |
| Fast real-time | Low latency | chains=2, threshold=0.65 |
| Research synthesis | Epistemic confidence | chains=5, threshold=0.75 |

---

## Related Notebooks

- `notebooks/05_reflection_loops.ipynb` — basic reflection loops
- `notebooks/06_fan_out_consensus.ipynb` — ensemble voting
- `notebooks/use_cases/uc_01_credit_risk_assessment.ipynb` — MCMC for credit risk
- `notebooks/use_cases/uc_05_legal_document_analysis.ipynb` — state machine for legal review
