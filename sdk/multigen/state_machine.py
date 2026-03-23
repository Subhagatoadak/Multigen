"""
multigen.state_machine
======================
Probabilistic agent orchestration via Markov Chain automata with
optional MCMC sampling for intelligent state exploration.

Concepts
--------
State           — a named step in the workflow, executed by an agent
Transition      — directed edge with a probability weight
StateMachine    — runs agents turn-by-turn, sampling transitions
MCMCSampler     — Metropolis-Hastings / Gibbs sampler for transition selection

When to use this instead of a Graph
-------------------------------------
Use Graph when your workflow is deterministic and DAG-shaped.
Use StateMachine when:
  - Transitions depend on agent output (quality scores, confidence)
  - You want iterative refinement loops with convergence criteria
  - You need non-deterministic exploration (research, ideation)
  - You're modelling a process with known state transition probabilities
  - You want to learn transition probabilities from execution data

Usage
-----
    from multigen.state_machine import StateMachine, Transition
    from multigen.agent import LLMAgent

    sm = StateMachine(name="research_loop")

    # Define states (state_id → agent)
    sm.state("search",   LLMAgent("searcher",  prompt="Search for: {query}"))
    sm.state("evaluate", LLMAgent("evaluator", prompt="Evaluate quality of: {search.response}"))
    sm.state("refine",   LLMAgent("refiner",   prompt="Refine the result: {evaluate.response}"))
    sm.state("report",   LLMAgent("reporter",  prompt="Write final report: {refine.response}"))

    # Transitions with base probabilities
    sm.transition("search",   "evaluate", prob=1.0)
    sm.transition("evaluate", "report",   prob=0.6)  # confident → done
    sm.transition("evaluate", "refine",   prob=0.3)  # uncertain → refine
    sm.transition("evaluate", "search",   prob=0.1)  # poor → re-search
    sm.transition("refine",   "evaluate", prob=0.8)  # re-evaluate after refine
    sm.transition("refine",   "report",   prob=0.2)  # direct to report

    # Adaptive transitions: probability adjusted by agent output
    sm.adaptive_transition(
        "evaluate", "report",
        score_fn=lambda ctx: float(ctx.get("evaluate", {}).get("confidence", 0.5)),
        threshold=0.8,
    )

    sm.start_state = "search"
    sm.terminal_states = {"report"}
    sm.max_steps = 20

    result = await sm.run({"query": "GPT-5 capabilities"})
    print(result.path)           # ["search", "evaluate", "refine", "evaluate", "report"]
    print(result.final_output)   # output of the "report" state

MCMC Exploration Mode
---------------------
    sm.enable_mcmc(
        temperature=0.8,        # higher → more random exploration
        sampler="metropolis",   # or "gibbs", "greedy"
        burn_in=2,
        target_distribution="uniform",  # or callable that returns log-prob
    )

    # Run multiple chains for ensemble output
    ensemble = await sm.run_ensemble(ctx, chains=5, steps=30)
    print(ensemble.best_path)
    print(ensemble.consensus_output)
"""

from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .agent import BaseAgent, AgentOutput

logger = logging.getLogger(__name__)
Context = Dict[str, Any]


# ─── Data structures ─────────────────────────────────────────────────────────

@dataclass
class Transition:
    source:    str
    target:    str
    prob:      float = 1.0       # base probability weight
    condition: Optional[Callable[[Context], bool]] = None
    score_fn:  Optional[Callable[[Context], float]] = None   # dynamic prob modifier


@dataclass
class StateResult:
    state_id:   str
    output:     AgentOutput
    latency_ms: float
    step:       int


@dataclass
class SMResult:
    status:         str            # completed | max_steps | failed
    path:           List[str]      # ordered list of visited states
    state_results:  Dict[str, StateResult]
    context:        Context
    total_latency_ms: float
    error:          Optional[str] = None

    @property
    def final_output(self) -> AgentOutput:
        if self.path:
            return self.state_results.get(self.path[-1], StateResult("", {}, 0, 0)).output
        return {}

    @property
    def steps(self) -> int:
        return len(self.path)


@dataclass
class EnsembleResult:
    chains:           List[SMResult]
    best_path:        List[str]
    consensus_output: AgentOutput
    agreement_score:  float            # 0-1: how often chains agreed on terminal state


# ─── MCMC Samplers ───────────────────────────────────────────────────────────

class Sampler:
    """Select the next state given a probability distribution over candidates."""

    def __init__(self, algorithm: str = "weighted", temperature: float = 1.0, burn_in: int = 0) -> None:
        self.algorithm = algorithm
        self.temperature = temperature
        self.burn_in = burn_in
        self._step = 0

    def sample(self, candidates: List[Tuple[str, float]]) -> str:
        """Return one state_id sampled from the (state, weight) distribution."""
        self._step += 1
        if not candidates:
            raise ValueError("No candidate transitions")

        if len(candidates) == 1:
            return candidates[0][0]

        if self.algorithm == "greedy":
            return max(candidates, key=lambda x: x[1])[0]

        if self.algorithm == "metropolis":
            return self._metropolis(candidates)

        if self.algorithm == "gibbs":
            return self._gibbs(candidates)

        # default: weighted random (softmax with temperature)
        return self._weighted(candidates)

    def _weighted(self, candidates: List[Tuple[str, float]]) -> str:
        states, weights = zip(*candidates)
        # Apply temperature via softmax
        if self.temperature != 1.0:
            log_weights = [w / self.temperature for w in weights]
            max_lw = max(log_weights)
            exp_w = [math.exp(lw - max_lw) for lw in log_weights]
        else:
            exp_w = list(weights)
        total = sum(exp_w)
        if total == 0:
            return states[0]
        r = random.random() * total
        cumulative = 0.0
        for state, w in zip(states, exp_w):
            cumulative += w
            if r <= cumulative:
                return state
        return states[-1]

    def _metropolis(self, candidates: List[Tuple[str, float]]) -> str:
        """Metropolis-Hastings: accept a proposal with probability min(1, p_new/p_current)."""
        states, weights = zip(*candidates)
        total = sum(weights)
        probs = [w / total for w in weights]
        # Propose from uniform, accept/reject by MH criterion
        proposal_idx = random.randint(0, len(states) - 1)
        if not hasattr(self, "_current_state"):
            self._current_state = proposal_idx
        current_prob = probs[self._current_state]
        proposal_prob = probs[proposal_idx]
        accept_prob = min(1.0, proposal_prob / current_prob) if current_prob > 0 else 1.0
        if random.random() < accept_prob:
            self._current_state = proposal_idx
        return states[self._current_state]

    def _gibbs(self, candidates: List[Tuple[str, float]]) -> str:
        """Gibbs: sample each dimension conditioned on all others (degenerates to weighted here)."""
        return self._weighted(candidates)


# ─── StateMachine ────────────────────────────────────────────────────────────

class StateMachine:
    """Probabilistic agent orchestrator — Markov Chain automata.

    Parameters
    ----------
    name:
        Identifier for logging.
    start_state:
        Initial state.
    terminal_states:
        Set of state IDs where execution stops.
    max_steps:
        Hard cap on total state visits (prevents infinite loops).
    """

    def __init__(
        self,
        name: str = "state_machine",
        *,
        start_state: Optional[str] = None,
        terminal_states: Optional[Set[str]] = None,
        max_steps: int = 50,
    ) -> None:
        self.name = name
        self.start_state = start_state
        self.terminal_states: Set[str] = terminal_states or set()
        self.max_steps = max_steps

        self._states:      Dict[str, BaseAgent] = {}
        self._transitions: List[Transition] = []
        self._on_enter:    Dict[str, Callable[[Context], None]] = {}
        self._on_exit:     Dict[str, Callable[[Context], None]] = {}

        # MCMC config
        self._mcmc_enabled = False
        self._sampler: Sampler = Sampler(algorithm="weighted")
        self._convergence_fn: Optional[Callable[[Context], bool]] = None

    # ── Build API ────────────────────────────────────────────────────────

    def state(
        self,
        state_id: str,
        agent: BaseAgent,
        *,
        on_enter: Optional[Callable[[Context], None]] = None,
        on_exit:  Optional[Callable[[Context], None]] = None,
    ) -> "StateMachine":
        self._states[state_id] = agent
        if on_enter:
            self._on_enter[state_id] = on_enter
        if on_exit:
            self._on_exit[state_id] = on_exit
        return self

    def transition(
        self,
        source: str,
        target: str,
        *,
        prob: float = 1.0,
        condition: Optional[Callable[[Context], bool]] = None,
    ) -> "StateMachine":
        """Add a transition with a static probability weight."""
        self._transitions.append(Transition(source=source, target=target, prob=prob, condition=condition))
        return self

    def adaptive_transition(
        self,
        source: str,
        target: str,
        *,
        score_fn: Callable[[Context], float],
        threshold: float = 0.5,
    ) -> "StateMachine":
        """Add a transition whose probability is dynamically computed from agent output.

        ``score_fn(ctx)`` returns a float in [0, 1].  The transition probability
        is multiplied by ``score_fn(ctx) / threshold`` (clamped to [0, 2]).
        """
        def dynamic_condition(ctx: Context) -> bool:
            return True   # always eligible; prob is adjusted dynamically

        def dynamic_score(ctx: Context) -> float:
            score = score_fn(ctx)
            return min(2.0, score / threshold)

        self._transitions.append(Transition(
            source=source, target=target,
            prob=1.0, condition=dynamic_condition, score_fn=dynamic_score,
        ))
        return self

    def enable_mcmc(
        self,
        *,
        temperature: float = 1.0,
        sampler: str = "weighted",   # weighted | greedy | metropolis | gibbs
        burn_in: int = 0,
        convergence_fn: Optional[Callable[[Context], bool]] = None,
    ) -> "StateMachine":
        """Enable MCMC-based transition sampling."""
        self._mcmc_enabled = True
        self._sampler = Sampler(algorithm=sampler, temperature=temperature, burn_in=burn_in)
        self._convergence_fn = convergence_fn
        return self

    def set_convergence(self, fn: Callable[[Context], bool]) -> "StateMachine":
        """Set a convergence predicate: stops when fn(ctx) returns True."""
        self._convergence_fn = fn
        return self

    # ── Execution ────────────────────────────────────────────────────────

    async def run(self, ctx: Optional[Context] = None) -> SMResult:
        """Run the state machine until a terminal state or max_steps."""
        if not self.start_state:
            raise ValueError("StateMachine.start_state must be set before run()")

        context: Context = dict(ctx or {})
        path: List[str] = []
        state_results: Dict[str, StateResult] = {}
        current = self.start_state
        total_start = time.perf_counter()

        for step in range(self.max_steps):
            if current not in self._states:
                return SMResult(
                    status="failed", path=path, state_results=state_results, context=context,
                    total_latency_ms=round((time.perf_counter() - total_start) * 1000, 2),
                    error=f"State {current!r} not registered",
                )

            # on_enter hook
            if current in self._on_enter:
                try:
                    self._on_enter[current](context)
                except Exception as e:
                    logger.warning("on_enter %s raised: %s", current, e)

            # Execute agent for this state
            agent = self._states[current]
            step_start = time.perf_counter()
            try:
                output = await agent(context)
            except Exception as exc:
                return SMResult(
                    status="failed", path=path + [current], state_results=state_results,
                    context=context,
                    total_latency_ms=round((time.perf_counter() - total_start) * 1000, 2),
                    error=f"State {current!r} agent failed: {exc}",
                )

            latency = (time.perf_counter() - step_start) * 1000
            sr = StateResult(state_id=current, output=output, latency_ms=round(latency, 2), step=step)
            state_results[current] = sr
            path.append(current)

            # Store output in context under state name for downstream use
            context[current] = output
            context["_last_output"] = output
            context["_step"] = step

            # on_exit hook
            if current in self._on_exit:
                try:
                    self._on_exit[current](context)
                except Exception as e:
                    logger.warning("on_exit %s raised: %s", current, e)

            # Check terminal
            if current in self.terminal_states:
                return SMResult(
                    status="completed", path=path, state_results=state_results, context=context,
                    total_latency_ms=round((time.perf_counter() - total_start) * 1000, 2),
                )

            # Check convergence
            if self._convergence_fn and self._convergence_fn(context):
                return SMResult(
                    status="completed", path=path, state_results=state_results, context=context,
                    total_latency_ms=round((time.perf_counter() - total_start) * 1000, 2),
                )

            # Gather eligible transitions
            candidates = self._get_candidates(current, context)
            if not candidates:
                logger.info("StateMachine %s: no outgoing transitions from %s — halting", self.name, current)
                return SMResult(
                    status="completed", path=path, state_results=state_results, context=context,
                    total_latency_ms=round((time.perf_counter() - total_start) * 1000, 2),
                )

            # Sample next state
            next_state = self._sampler.sample(candidates)
            logger.debug("SM %s step %d: %s → %s (candidates=%s)", self.name, step, current, next_state, candidates)
            current = next_state

        return SMResult(
            status="max_steps", path=path, state_results=state_results, context=context,
            total_latency_ms=round((time.perf_counter() - total_start) * 1000, 2),
        )

    def _get_candidates(self, state_id: str, context: Context) -> List[Tuple[str, float]]:
        """Return list of (target_state_id, weight) for outgoing transitions."""
        candidates = []
        for t in self._transitions:
            if t.source != state_id:
                continue
            if t.target not in self._states:
                continue
            if t.condition and not t.condition(context):
                continue
            weight = t.prob
            if t.score_fn:
                try:
                    weight = t.prob * t.score_fn(context)
                except Exception:
                    pass
            if weight > 0:
                candidates.append((t.target, weight))
        return candidates

    # ── Ensemble (multiple independent chains) ────────────────────────────

    async def run_ensemble(
        self,
        ctx: Optional[Context] = None,
        *,
        chains: int = 5,
    ) -> EnsembleResult:
        """Run N independent chains concurrently and aggregate."""
        tasks = [self.run(dict(ctx or {})) for _ in range(chains)]
        results: List[SMResult] = await asyncio.gather(*tasks)

        # Find consensus terminal state
        terminal_counts: Dict[str, int] = {}
        for r in results:
            if r.path:
                ts = r.path[-1]
                terminal_counts[ts] = terminal_counts.get(ts, 0) + 1
        best_terminal = max(terminal_counts, key=terminal_counts.__getitem__) if terminal_counts else ""
        agreement = terminal_counts.get(best_terminal, 0) / chains if chains else 0

        # Best path = chain whose final state matches consensus and has fewest steps
        candidates = [r for r in results if r.path and r.path[-1] == best_terminal]
        best = min(candidates, key=lambda r: r.steps) if candidates else results[0]

        # Consensus output: merge all terminal outputs
        consensus: AgentOutput = {}
        for r in results:
            if r.status == "completed":
                consensus.update(r.final_output)

        return EnsembleResult(
            chains=results,
            best_path=best.path,
            consensus_output=consensus,
            agreement_score=round(agreement, 3),
        )

    # ── Transition matrix ────────────────────────────────────────────────

    def transition_matrix(self) -> Dict[str, Dict[str, float]]:
        """Return the normalised transition probability matrix."""
        matrix: Dict[str, Dict[str, float]] = {s: {} for s in self._states}
        for t in self._transitions:
            if t.source in matrix:
                matrix[t.source][t.target] = t.prob

        # Normalise rows
        for row in matrix.values():
            total = sum(row.values())
            if total > 0:
                for k in row:
                    row[k] = round(row[k] / total, 4)
        return matrix

    def __repr__(self) -> str:
        return (
            f"StateMachine(name={self.name!r}, "
            f"states={list(self._states)}, "
            f"start={self.start_state!r}, "
            f"terminals={self.terminal_states})"
        )
