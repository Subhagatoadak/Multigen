"""
Planning, Tree-of-Thoughts (ToT), and Graph-of-Thoughts (GoT) for Multigen.

Implements structured reasoning strategies that go beyond a single LLM call:

- ``ChainOfThought``   — sequential thought steps (standard CoT)
- ``TreeOfThoughts``   — BFS/DFS/beam search over a branching thought tree
- ``GraphOfThoughts``  — DAG-based reasoning where thoughts can merge and fork
- ``StepBackPlanner``  — abstract before solving (step-back prompting)
- ``ReActPlanner``     — Reason + Act loop (observe → think → act)
- ``PlanAndExecute``   — upfront plan decomposition then step-by-step execution
- ``Planner``          — base class / ABC

All planners are *model-agnostic*: they take a ``thinker_fn`` callable that
represents any LLM call, keeping the framework decoupled from providers.

Usage::

    from multigen.planning import (
        TreeOfThoughts, GraphOfThoughts, PlanAndExecute,
        ReActPlanner, ThoughtNode, ExecutionPlan,
    )

    # thinker: async (prompt: str) -> str
    async def gpt(prompt: str) -> str:
        ...  # call your LLM here

    # Tree of Thoughts — beam search
    tot = TreeOfThoughts(
        thinker_fn=gpt,
        branching_factor=3,
        max_depth=4,
        search="beam",
        beam_width=2,
    )
    best_thought = await tot.think("How do I optimise a slow database query?")
    print(best_thought.content)

    # Plan-and-Execute
    planner = PlanAndExecute(thinker_fn=gpt)
    plan = await planner.plan("Write a market analysis report on AI chips")
    result = await planner.execute(plan)
"""
from __future__ import annotations

import asyncio
import math
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── ThoughtNode ────────────────────────────────────────────────────────────────

@dataclass
class ThoughtNode:
    """
    A single node in a reasoning tree or graph.

    Attributes
    ----------
    content     The thought text produced by the LLM.
    score       Evaluation score (0–1); higher is better.
    depth       Depth in the tree (root = 0).
    parent_id   ID of the parent node (None for root).
    children    IDs of child nodes.
    metadata    Arbitrary extra data.
    """

    content: str
    score: float = 0.0
    depth: int = 0
    node_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    parent_id: Optional[str] = None
    children: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_terminal: bool = False

    def __repr__(self) -> str:
        snippet = self.content[:60].replace("\n", " ")
        return f"ThoughtNode(id={self.node_id}, depth={self.depth}, score={self.score:.2f}, '{snippet}')"


# ── PlanStep / ExecutionPlan ───────────────────────────────────────────────────

@dataclass
class PlanStep:
    """A single step in an ``ExecutionPlan``."""

    description: str
    step_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    depends_on: List[str] = field(default_factory=list)
    result: Optional[Any] = None
    error: Optional[str] = None
    completed: bool = False


@dataclass
class ExecutionPlan:
    """
    An ordered sequence of ``PlanStep`` objects produced by a planner.

    Usage::

        plan = ExecutionPlan(goal="write report", steps=[...])
        for step in plan.pending_steps():
            ...
    """

    goal: str
    steps: List[PlanStep] = field(default_factory=list)
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    metadata: Dict[str, Any] = field(default_factory=dict)

    def pending_steps(self) -> List[PlanStep]:
        return [s for s in self.steps if not s.completed and not s.error]

    def completed_steps(self) -> List[PlanStep]:
        return [s for s in self.steps if s.completed]

    def is_done(self) -> bool:
        return all(s.completed for s in self.steps)

    def summary(self) -> str:
        done = len(self.completed_steps())
        total = len(self.steps)
        return f"Plan '{self.goal}': {done}/{total} steps done"


# ── Planner base ───────────────────────────────────────────────────────────────

class Planner:
    """
    Base class for all planning strategies.

    Subclasses implement ``think(problem, ctx)`` which returns a ``ThoughtNode``
    or ``ExecutionPlan``.
    """

    def __init__(self, thinker_fn: Callable) -> None:
        """
        Parameters
        ----------
        thinker_fn  ``async (prompt: str) -> str`` — any LLM or mock callable.
        """
        self._think = thinker_fn

    async def _call(self, prompt: str) -> str:
        result = self._think(prompt)
        if asyncio.iscoroutine(result):
            result = await result
        return str(result)


# ── ChainOfThought ─────────────────────────────────────────────────────────────

class ChainOfThought(Planner):
    """
    Standard sequential chain-of-thought reasoning.

    Generates *n_steps* thoughts in sequence, each conditioned on the previous,
    then produces a final answer.

    Usage::

        cot = ChainOfThought(thinker_fn=gpt, n_steps=3)
        thought = await cot.think("What is the fastest sorting algorithm?")
        print(thought.content)
    """

    def __init__(self, thinker_fn: Callable, n_steps: int = 3) -> None:
        super().__init__(thinker_fn)
        self.n_steps = n_steps

    async def think(self, problem: str, ctx: Optional[Dict[str, Any]] = None) -> ThoughtNode:
        thoughts: List[str] = []
        current = problem

        for i in range(self.n_steps):
            prompt = (
                f"Problem: {problem}\n"
                + (f"Previous thoughts:\n" + "\n".join(f"Step {j+1}: {t}" for j, t in enumerate(thoughts)) + "\n" if thoughts else "")
                + f"Step {i+1}: Think carefully and reason one step further."
            )
            step_thought = await self._call(prompt)
            thoughts.append(step_thought)
            current = step_thought

        # Final synthesis
        final_prompt = (
            f"Problem: {problem}\n"
            f"Reasoning steps:\n"
            + "\n".join(f"Step {i+1}: {t}" for i, t in enumerate(thoughts))
            + "\nFinal answer:"
        )
        final = await self._call(final_prompt)

        root = ThoughtNode(content=problem, depth=0, score=1.0)
        node = ThoughtNode(
            content=final,
            depth=self.n_steps,
            score=1.0,
            parent_id=root.node_id,
            is_terminal=True,
            metadata={"steps": thoughts},
        )
        return node


# ── TreeOfThoughts ─────────────────────────────────────────────────────────────

class TreeOfThoughts(Planner):
    """
    Tree-of-Thoughts reasoning with configurable search strategy.

    Generates *branching_factor* candidate thoughts at each depth, scores them,
    and explores the most promising branches.

    Search strategies
    -----------------
    ``"bfs"``    — explore all nodes at current depth before going deeper
    ``"dfs"``    — explore each branch to its leaf before backtracking
    ``"beam"``   — keep only the top *beam_width* nodes at each depth level

    Usage::

        tot = TreeOfThoughts(
            thinker_fn=gpt,
            evaluator_fn=gpt,     # optional; defaults to thinker_fn
            branching_factor=3,
            max_depth=4,
            search="beam",
            beam_width=2,
        )
        best = await tot.think("Design a REST API for a blog platform")
        print(best.content)
    """

    def __init__(
        self,
        thinker_fn: Callable,
        evaluator_fn: Optional[Callable] = None,
        branching_factor: int = 3,
        max_depth: int = 3,
        search: str = "beam",
        beam_width: int = 2,
    ) -> None:
        super().__init__(thinker_fn)
        self._evaluate = evaluator_fn or thinker_fn
        self.branching_factor = branching_factor
        self.max_depth = max_depth
        self.search = search
        self.beam_width = beam_width
        self._nodes: Dict[str, ThoughtNode] = {}

    async def _generate_children(
        self, parent: ThoughtNode, problem: str
    ) -> List[ThoughtNode]:
        children: List[ThoughtNode] = []
        for _ in range(self.branching_factor):
            prompt = (
                f"Problem: {problem}\n"
                f"Current thought (depth {parent.depth}): {parent.content}\n"
                f"Generate the next reasoning step — be creative and diverge from previous attempts:"
            )
            content = await self._call(prompt)
            child = ThoughtNode(
                content=content,
                depth=parent.depth + 1,
                parent_id=parent.node_id,
                is_terminal=(parent.depth + 1 >= self.max_depth),
            )
            children.append(child)
        return children

    async def _score_node(self, node: ThoughtNode, problem: str) -> float:
        prompt = (
            f"Problem: {problem}\n"
            f"Reasoning step: {node.content}\n"
            f"Rate the quality of this reasoning step from 0.0 to 1.0 (respond with only the number):"
        )
        raw = await self._call(prompt)
        try:
            score = float(raw.strip().split()[0])
            return max(0.0, min(1.0, score))
        except (ValueError, IndexError):
            return 0.5  # default if LLM doesn't return a clean number

    async def think(self, problem: str, ctx: Optional[Dict[str, Any]] = None) -> ThoughtNode:
        root = ThoughtNode(content=problem, depth=0, score=1.0)
        self._nodes[root.node_id] = root

        if self.search == "bfs":
            return await self._bfs(root, problem)
        elif self.search == "dfs":
            best = [root]
            await self._dfs(root, problem, best)
            return best[0]
        else:  # beam
            return await self._beam(root, problem)

    async def _bfs(self, root: ThoughtNode, problem: str) -> ThoughtNode:
        frontier = [root]
        best = root
        for _ in range(self.max_depth):
            next_frontier: List[ThoughtNode] = []
            for node in frontier:
                if node.is_terminal:
                    continue
                children = await self._generate_children(node, problem)
                for child in children:
                    child.score = await self._score_node(child, problem)
                    self._nodes[child.node_id] = child
                    node.children.append(child.node_id)
                    next_frontier.append(child)
                    if child.score > best.score:
                        best = child
            frontier = next_frontier
            if not frontier:
                break
        return best

    async def _dfs(
        self, node: ThoughtNode, problem: str, best: List[ThoughtNode]
    ) -> None:
        if node.is_terminal or node.depth >= self.max_depth:
            if node.score > best[0].score:
                best[0] = node
            return
        children = await self._generate_children(node, problem)
        for child in children:
            child.score = await self._score_node(child, problem)
            self._nodes[child.node_id] = child
            node.children.append(child.node_id)
        children.sort(key=lambda c: c.score, reverse=True)
        for child in children:
            await self._dfs(child, problem, best)

    async def _beam(self, root: ThoughtNode, problem: str) -> ThoughtNode:
        beam: List[ThoughtNode] = [root]
        best = root
        for _ in range(self.max_depth):
            candidates: List[ThoughtNode] = []
            for node in beam:
                if node.is_terminal:
                    candidates.append(node)
                    continue
                children = await self._generate_children(node, problem)
                for child in children:
                    child.score = await self._score_node(child, problem)
                    self._nodes[child.node_id] = child
                    node.children.append(child.node_id)
                    candidates.append(child)
            if not candidates:
                break
            candidates.sort(key=lambda c: c.score, reverse=True)
            beam = candidates[: self.beam_width]
            if beam[0].score > best.score:
                best = beam[0]
        return best

    def all_nodes(self) -> List[ThoughtNode]:
        return list(self._nodes.values())


# ── GraphOfThoughts ────────────────────────────────────────────────────────────

class GraphOfThoughts(Planner):
    """
    Graph-of-Thoughts reasoning — thoughts can merge (aggregate) and fork.

    Unlike a tree, GoT allows multiple parents per node, enabling synthesis
    of parallel reasoning paths before continuing.

    Usage::

        got = GraphOfThoughts(thinker_fn=gpt, n_rounds=3, n_thoughts_per_round=2)
        result = await got.think("Compare microservices vs monolith for a startup")
        print(result.content)
    """

    def __init__(
        self,
        thinker_fn: Callable,
        n_rounds: int = 3,
        n_thoughts_per_round: int = 2,
    ) -> None:
        super().__init__(thinker_fn)
        self.n_rounds = n_rounds
        self.n_thoughts_per_round = n_thoughts_per_round
        self._nodes: Dict[str, ThoughtNode] = {}

    async def think(self, problem: str, ctx: Optional[Dict[str, Any]] = None) -> ThoughtNode:
        # Round 0: seed thoughts (parallel)
        seed_prompts = [
            f"Problem: {problem}\nInitial thought perspective {i+1}:"
            for i in range(self.n_thoughts_per_round)
        ]
        seed_contents = await asyncio.gather(*[self._call(p) for p in seed_prompts])
        current_layer: List[ThoughtNode] = []
        for content in seed_contents:
            node = ThoughtNode(content=content, depth=0)
            self._nodes[node.node_id] = node
            current_layer.append(node)

        # Rounds: aggregate then fork
        for round_i in range(1, self.n_rounds):
            # Aggregate: merge current layer into a synthesis
            aggregate_prompt = (
                f"Problem: {problem}\n"
                f"Multiple reasoning perspectives:\n"
                + "\n".join(f"- {n.content}" for n in current_layer)
                + "\nSynthesize the key insights from these perspectives:"
            )
            synthesis = await self._call(aggregate_prompt)
            agg_node = ThoughtNode(
                content=synthesis,
                depth=round_i,
                parent_id=",".join(n.node_id for n in current_layer),
            )
            self._nodes[agg_node.node_id] = agg_node

            # Fork: generate new parallel thoughts from the synthesis
            fork_prompts = [
                f"Problem: {problem}\nSynthesis: {synthesis}\nExplore angle {i+1} further:"
                for i in range(self.n_thoughts_per_round)
            ]
            fork_contents = await asyncio.gather(*[self._call(p) for p in fork_prompts])
            new_layer: List[ThoughtNode] = []
            for content in fork_contents:
                node = ThoughtNode(
                    content=content,
                    depth=round_i,
                    parent_id=agg_node.node_id,
                )
                self._nodes[node.node_id] = node
                new_layer.append(node)
            current_layer = new_layer

        # Final answer
        final_prompt = (
            f"Problem: {problem}\n"
            f"Final reasoning threads:\n"
            + "\n".join(f"- {n.content}" for n in current_layer)
            + "\nProvide the final, conclusive answer:"
        )
        final_content = await self._call(final_prompt)
        final_node = ThoughtNode(
            content=final_content,
            depth=self.n_rounds,
            parent_id=",".join(n.node_id for n in current_layer),
            is_terminal=True,
            score=1.0,
        )
        self._nodes[final_node.node_id] = final_node
        return final_node

    def all_nodes(self) -> List[ThoughtNode]:
        return list(self._nodes.values())


# ── StepBackPlanner ────────────────────────────────────────────────────────────

class StepBackPlanner(Planner):
    """
    Step-back prompting: abstract the problem before solving it.

    1. Ask the LLM to identify the abstract principle behind the problem.
    2. Reason about the abstract principle.
    3. Apply that reasoning to the concrete problem.

    Usage::

        sbp = StepBackPlanner(thinker_fn=gpt)
        result = await sbp.think("Why is my React app re-rendering too often?")
    """

    async def think(self, problem: str, ctx: Optional[Dict[str, Any]] = None) -> ThoughtNode:
        # Step 1: abstract
        abstract_prompt = (
            f"Given this specific question: {problem}\n"
            f"What is the more general, abstract principle or concept this question is about?"
        )
        abstract = await self._call(abstract_prompt)

        # Step 2: reason about the abstract
        principle_prompt = (
            f"Abstract principle: {abstract}\n"
            f"What are the key facts and reasoning about this principle?"
        )
        principle_reasoning = await self._call(principle_prompt)

        # Step 3: apply to concrete
        apply_prompt = (
            f"Concrete problem: {problem}\n"
            f"Abstract principle: {abstract}\n"
            f"Principle reasoning: {principle_reasoning}\n"
            f"Now solve the concrete problem using this reasoning:"
        )
        answer = await self._call(apply_prompt)

        root = ThoughtNode(content=problem, depth=0)
        result = ThoughtNode(
            content=answer,
            depth=3,
            parent_id=root.node_id,
            is_terminal=True,
            score=1.0,
            metadata={
                "abstract": abstract,
                "principle_reasoning": principle_reasoning,
            },
        )
        return result


# ── ReActPlanner ──────────────────────────────────────────────────────────────

class ReActPlanner(Planner):
    """
    ReAct (Reason + Act) loop: observe → think → act → observe → …

    Each iteration the planner emits a ``Thought``, an ``Action``, and then
    receives an ``Observation`` (from ``action_fn``).  The loop terminates when
    the planner outputs ``Action: Finish[answer]``.

    Parameters
    ----------
    thinker_fn      LLM callable.
    action_fn       ``async (action: str) -> str`` — tool dispatcher.
    max_iterations  Safety limit (default 10).

    Usage::

        async def dispatch(action: str) -> str:
            if action.startswith("Search["):
                query = action[7:-1]
                return search_web(query)
            return "Unknown action"

        react = ReActPlanner(thinker_fn=gpt, action_fn=dispatch)
        result = await react.think("What is the population of Paris?")
    """

    def __init__(
        self,
        thinker_fn: Callable,
        action_fn: Optional[Callable] = None,
        max_iterations: int = 10,
    ) -> None:
        super().__init__(thinker_fn)
        self._action_fn = action_fn
        self.max_iterations = max_iterations

    async def think(self, problem: str, ctx: Optional[Dict[str, Any]] = None) -> ThoughtNode:
        trajectory: List[str] = []
        answer: Optional[str] = None

        for i in range(self.max_iterations):
            prompt = (
                f"Question: {problem}\n"
                + ("\n".join(trajectory) + "\n" if trajectory else "")
                + f"Thought {i+1}:"
            )
            thought = await self._call(prompt)
            trajectory.append(f"Thought {i+1}: {thought}")

            # Ask for action
            action_prompt = (
                f"Question: {problem}\n"
                + "\n".join(trajectory)
                + "\nAction (use 'Finish[answer]' to end):"
            )
            action = await self._call(action_prompt)
            trajectory.append(f"Action {i+1}: {action}")

            if action.strip().lower().startswith("finish["):
                answer = action.strip()[7:-1] if action.strip().endswith("]") else action
                break

            # Execute action
            if self._action_fn is not None:
                obs_result = self._action_fn(action)
                if asyncio.iscoroutine(obs_result):
                    obs_result = await obs_result
                observation = str(obs_result)
            else:
                observation = "(no action_fn — observation skipped)"
            trajectory.append(f"Observation {i+1}: {observation}")

        final_answer = answer or trajectory[-1]
        return ThoughtNode(
            content=final_answer,
            depth=len(trajectory),
            is_terminal=True,
            score=1.0,
            metadata={"trajectory": trajectory},
        )


# ── PlanAndExecute ─────────────────────────────────────────────────────────────

class PlanAndExecute(Planner):
    """
    Two-phase planner: decompose the goal into steps, then execute each step.

    Phase 1 — Plan: ask the LLM to decompose the goal into numbered steps.
    Phase 2 — Execute: run each step with a fresh LLM call, passing prior results.

    Usage::

        planner = PlanAndExecute(thinker_fn=gpt, executor_fn=gpt)
        plan   = await planner.plan("Write a market analysis on AI chips")
        result = await planner.execute(plan)
        print(result)
    """

    def __init__(
        self,
        thinker_fn: Callable,
        executor_fn: Optional[Callable] = None,
        max_steps: int = 10,
    ) -> None:
        super().__init__(thinker_fn)
        self._execute_fn = executor_fn or thinker_fn
        self.max_steps = max_steps

    async def plan(self, goal: str, ctx: Optional[Dict[str, Any]] = None) -> ExecutionPlan:
        prompt = (
            f"Goal: {goal}\n"
            f"Decompose this goal into at most {self.max_steps} concrete, "
            f"sequential steps.  Number each step on its own line like: "
            f"1. Do X\n2. Do Y\n..."
        )
        raw = await self._call(prompt)
        steps: List[PlanStep] = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            # Strip leading "1." "1)" "Step 1:" etc.
            import re
            cleaned = re.sub(r"^(\d+[.):\-]\s*|Step\s*\d+[.):\-]\s*)", "", line, flags=re.IGNORECASE)
            if cleaned:
                steps.append(PlanStep(description=cleaned))

        return ExecutionPlan(goal=goal, steps=steps[:self.max_steps])

    async def execute(self, plan: ExecutionPlan) -> str:
        completed_results: List[str] = []
        for step in plan.steps:
            prior = (
                "\n".join(
                    f"Step {i+1} result: {r}"
                    for i, r in enumerate(completed_results)
                )
                if completed_results
                else ""
            )
            prompt = (
                f"Goal: {plan.goal}\n"
                + (f"Prior results:\n{prior}\n" if prior else "")
                + f"Current step: {step.description}\n"
                + "Execute this step and provide the result:"
            )
            result_fn = self._execute_fn
            result = result_fn(prompt)
            if asyncio.iscoroutine(result):
                result = await result
            step.result = str(result)
            step.completed = True
            completed_results.append(step.result)

        # Final synthesis
        synthesis_prompt = (
            f"Goal: {plan.goal}\n"
            f"Step results:\n"
            + "\n".join(f"Step {i+1}: {r}" for i, r in enumerate(completed_results))
            + "\nSynthesize a final, complete answer:"
        )
        final_fn = self._execute_fn
        final = final_fn(synthesis_prompt)
        if asyncio.iscoroutine(final):
            final = await final
        return str(final)

    async def think(self, problem: str, ctx: Optional[Dict[str, Any]] = None) -> ThoughtNode:
        plan = await self.plan(problem, ctx)
        result = await self.execute(plan)
        return ThoughtNode(
            content=result,
            depth=len(plan.steps) + 1,
            is_terminal=True,
            score=1.0,
            metadata={"plan": plan},
        )


__all__ = [
    "ChainOfThought",
    "ExecutionPlan",
    "GraphOfThoughts",
    "PlanAndExecute",
    "PlanStep",
    "Planner",
    "ReActPlanner",
    "StepBackPlanner",
    "ThoughtNode",
    "TreeOfThoughts",
]
