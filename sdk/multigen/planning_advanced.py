"""
Advanced planning primitives: MCTS, hierarchical task decomposition,
AutoGPT-style task queue, and hierarchical summarisation.

Problems solved
---------------
- No runtime goal → sub-goal decomposition tree
- No MCTS-based planning (UCB1 tree search over thought space)
- No AutoGPT-style agent-driven task queue
- No hierarchical summarisation (summarise summaries recursively)

Classes
-------
- ``TaskNode``             — node in a hierarchical task tree
- ``HierarchicalDecomposer`` — recursively decomposes goals into sub-tasks
- ``MCTSNode``             — node in MCTS search tree
- ``MCTSPlanner``          — Monte Carlo Tree Search over action space
- ``TaskQueueItem``        — item in the AutoGPT task queue
- ``AutoGPTQueue``         — agent-driven self-directed task execution loop
- ``HierarchicalSummariser`` — recursive summarise-of-summaries

Usage::

    from multigen.planning_advanced import (
        HierarchicalDecomposer, MCTSPlanner, AutoGPTQueue,
        HierarchicalSummariser,
    )

    # Hierarchical decomposition
    decomposer = HierarchicalDecomposer(decompose_fn, max_depth=3)
    tree = await decomposer.decompose("Write a research paper on AI")

    # MCTS planning
    planner = MCTSPlanner(simulate_fn, n_simulations=50)
    best_action = await planner.plan(state)

    # AutoGPT-style queue
    queue = AutoGPTQueue(agent_fn, max_steps=20)
    result = await queue.run("Build a web scraper for news articles")

    # Hierarchical summarisation
    summariser = HierarchicalSummariser(summarise_fn, chunk_size=5)
    summary = await summariser.summarise(long_document_chunks)
"""
from __future__ import annotations

import asyncio
import logging
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── TaskNode / HierarchicalDecomposer ─────────────────────────────────────────

@dataclass
class TaskNode:
    """A node in the hierarchical task decomposition tree."""
    task: str
    depth: int = 0
    parent: Optional["TaskNode"] = field(default=None, repr=False)
    children: List["TaskNode"] = field(default_factory=list)
    result: Any = None
    status: str = "pending"   # pending | running | done | failed

    def is_leaf(self) -> bool:
        return len(self.children) == 0

    def all_leaves(self) -> List["TaskNode"]:
        if self.is_leaf():
            return [self]
        leaves: List[TaskNode] = []
        for c in self.children:
            leaves.extend(c.all_leaves())
        return leaves

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "depth": self.depth,
            "status": self.status,
            "result": self.result,
            "children": [c.to_dict() for c in self.children],
        }

    def __repr__(self) -> str:
        return f"TaskNode(task={self.task!r}, depth={self.depth}, children={len(self.children)})"


class HierarchicalDecomposer:
    """
    Recursively decomposes a goal into sub-tasks using a callable.

    The *decompose_fn* receives ``(task: str, depth: int, ctx: dict)`` and
    must return either:
    - A ``list[str]`` of sub-tasks (task is not a leaf)
    - An empty list ``[]`` (task is atomic / leaf)

    After decomposition, *execute_fn* (optional) is called on each leaf node
    to produce results, which are then aggregated bottom-up.

    Usage::

        async def decompose(task, depth, ctx):
            if depth >= 2 or "simple" in task:
                return []
            # ask LLM to break task into sub-tasks
            return ["sub-task A", "sub-task B"]

        decomposer = HierarchicalDecomposer(decompose, max_depth=3)
        tree = await decomposer.decompose("Build an app")
    """

    def __init__(
        self,
        decompose_fn: Callable,
        execute_fn: Optional[Callable] = None,
        max_depth: int = 3,
        max_children: int = 8,
    ) -> None:
        self._decompose = decompose_fn
        self._execute = execute_fn
        self.max_depth = max_depth
        self.max_children = max_children

    async def decompose(
        self,
        goal: str,
        ctx: Optional[Dict[str, Any]] = None,
    ) -> TaskNode:
        """Build and (optionally) execute a hierarchical task tree for *goal*."""
        ctx = ctx or {}
        root = TaskNode(task=goal, depth=0)
        await self._expand(root, ctx)
        if self._execute:
            await self._execute_tree(root, ctx)
        return root

    async def _expand(self, node: TaskNode, ctx: Dict[str, Any]) -> None:
        if node.depth >= self.max_depth:
            return
        try:
            result = self._decompose(node.task, node.depth, ctx)
            if asyncio.iscoroutine(result):
                result = await result
            sub_tasks: List[str] = (result or [])[: self.max_children]
        except Exception as exc:
            logger.warning("HierarchicalDecomposer: decompose failed for %r: %s", node.task, exc)
            sub_tasks = []

        for sub in sub_tasks:
            child = TaskNode(task=sub, depth=node.depth + 1, parent=node)
            node.children.append(child)
            await self._expand(child, ctx)

    async def _execute_tree(self, node: TaskNode, ctx: Dict[str, Any]) -> Any:
        if node.is_leaf():
            node.status = "running"
            try:
                result = self._execute(node.task, ctx)
                if asyncio.iscoroutine(result):
                    result = await result
                node.result = result
                node.status = "done"
            except Exception as exc:
                node.result = str(exc)
                node.status = "failed"
            return node.result

        # Execute children concurrently
        child_results = await asyncio.gather(
            *[self._execute_tree(c, ctx) for c in node.children],
            return_exceptions=True,
        )
        node.result = child_results
        node.status = "done"
        return child_results


# ── MCTS ──────────────────────────────────────────────────────────────────────

@dataclass
class MCTSNode:
    """A node in the Monte Carlo Tree Search tree."""
    state: Any
    parent: Optional["MCTSNode"] = field(default=None, repr=False)
    action: Any = None          # action that led to this state
    children: List["MCTSNode"] = field(default_factory=list)
    visits: int = 0
    value: float = 0.0
    untried_actions: List[Any] = field(default_factory=list)

    def is_fully_expanded(self) -> bool:
        return len(self.untried_actions) == 0

    def is_terminal(self) -> bool:
        return len(self.children) == 0 and self.is_fully_expanded()

    def ucb1(self, exploration: float = 1.414) -> float:
        if self.visits == 0:
            return float("inf")
        assert self.parent is not None
        return self.value / self.visits + exploration * math.sqrt(
            math.log(self.parent.visits) / self.visits
        )

    def best_child(self, exploration: float = 1.414) -> "MCTSNode":
        return max(self.children, key=lambda c: c.ucb1(exploration))

    def most_visited_child(self) -> "MCTSNode":
        return max(self.children, key=lambda c: c.visits)


class MCTSPlanner:
    """
    Monte Carlo Tree Search over an action/state space.

    Parameters
    ----------
    actions_fn      ``(state) → list[action]``   — returns legal actions
    transition_fn   ``(state, action) → state``   — applies an action
    simulate_fn     ``(state) → float``           — rollout reward (0..1)
    is_terminal_fn  ``(state) → bool``            — terminal state check
    n_simulations   Number of MCTS iterations per ``plan()`` call
    exploration     UCB1 exploration constant (default √2)

    Usage::

        planner = MCTSPlanner(
            actions_fn=lambda s: ["left", "right", "up", "down"],
            transition_fn=apply_move,
            simulate_fn=random_rollout,
            is_terminal_fn=lambda s: s["done"],
            n_simulations=200,
        )
        best_action = await planner.plan(initial_state)
    """

    def __init__(
        self,
        actions_fn: Callable,
        transition_fn: Callable,
        simulate_fn: Callable,
        is_terminal_fn: Optional[Callable] = None,
        n_simulations: int = 100,
        exploration: float = 1.414,
        max_rollout_depth: int = 20,
    ) -> None:
        self._actions = actions_fn
        self._transition = transition_fn
        self._simulate = simulate_fn
        self._is_terminal = is_terminal_fn or (lambda s: False)
        self.n_simulations = n_simulations
        self.exploration = exploration
        self.max_rollout_depth = max_rollout_depth

    async def plan(self, state: Any) -> Any:
        """Return the best action from *state* after MCTS search."""
        actions = await self._call(self._actions, state)
        root = MCTSNode(state=state, untried_actions=list(actions))

        for _ in range(self.n_simulations):
            node = await self._select(root)
            if not await self._call(self._is_terminal, node.state):
                node = await self._expand(node)
            reward = await self._call(self._simulate, node.state)
            self._backprop(node, reward)

        if not root.children:
            return actions[0] if actions else None
        best = root.most_visited_child()
        return best.action

    async def _select(self, node: MCTSNode) -> MCTSNode:
        while node.is_fully_expanded() and node.children:
            node = node.best_child(self.exploration)
        return node

    async def _expand(self, node: MCTSNode) -> MCTSNode:
        if not node.untried_actions:
            return node
        action = node.untried_actions.pop(0)
        new_state = await self._call(self._transition, node.state, action)
        new_actions = await self._call(self._actions, new_state)
        child = MCTSNode(
            state=new_state,
            parent=node,
            action=action,
            untried_actions=list(new_actions),
        )
        node.children.append(child)
        return child

    def _backprop(self, node: MCTSNode, reward: float) -> None:
        while node is not None:
            node.visits += 1
            node.value += reward
            node = node.parent  # type: ignore[assignment]

    async def _call(self, fn: Callable, *args: Any) -> Any:
        result = fn(*args)
        if asyncio.iscoroutine(result):
            result = await result
        return result


# ── AutoGPT Task Queue ─────────────────────────────────────────────────────────

@dataclass
class TaskQueueItem:
    """A pending task in the AutoGPT queue."""
    task: str
    priority: int = 0
    depends_on: List[str] = field(default_factory=list)
    result: Any = None
    status: str = "pending"   # pending | done | failed | skipped
    created_at: float = field(default_factory=time.monotonic)


@dataclass
class AutoGPTResult:
    """Final result of an AutoGPT run."""
    goal: str
    output: Any
    steps_taken: int
    completed_tasks: List[str]
    failed_tasks: List[str]
    elapsed_s: float

    @property
    def success(self) -> bool:
        return len(self.failed_tasks) == 0


class AutoGPTQueue:
    """
    Agent-driven self-directed task execution loop.

    The *agent_fn* is called with ``ctx`` on each step.  It should return a
    dict with at least one of:

    - ``"new_tasks": [str, ...]``   — additional tasks to enqueue
    - ``"done": True``              — signals the agent considers the goal met
    - ``"output": <any>``          — final result (used when done=True)
    - ``"error": str``              — non-fatal error, task marked failed

    Parameters
    ----------
    agent_fn    Async callable ``(ctx: dict) → dict``
    max_steps   Hard limit on iterations (prevents infinite loops)
    max_tasks   Max queue depth (prevents unbounded growth)

    Usage::

        async def agent(ctx):
            task = ctx["current_task"]
            if "search" in task:
                return {"new_tasks": ["analyse results", "write report"], "output": "..."}
            return {"done": True, "output": "finished"}

        queue = AutoGPTQueue(agent, max_steps=20)
        result = await queue.run("Research quantum computing")
    """

    def __init__(
        self,
        agent_fn: Callable,
        max_steps: int = 20,
        max_tasks: int = 100,
    ) -> None:
        self._agent = agent_fn
        self.max_steps = max_steps
        self.max_tasks = max_tasks

    async def run(
        self,
        goal: str,
        initial_ctx: Optional[Dict[str, Any]] = None,
    ) -> AutoGPTResult:
        start = time.monotonic()
        ctx = dict(initial_ctx or {})
        ctx["goal"] = goal

        queue: List[TaskQueueItem] = [TaskQueueItem(task=goal, priority=10)]
        completed: List[str] = []
        failed: List[str] = []
        final_output: Any = None
        steps = 0

        while queue and steps < self.max_steps:
            # Sort by priority (descending)
            queue.sort(key=lambda t: -t.priority)
            item = queue.pop(0)

            if item.status != "pending":
                continue

            # Check dependency satisfaction
            if not all(dep in completed for dep in item.depends_on):
                item.status = "skipped"
                continue

            item.status = "running"
            ctx["current_task"] = item.task
            ctx["completed_tasks"] = completed
            ctx["step"] = steps
            steps += 1

            try:
                response = self._agent(ctx)
                if asyncio.iscoroutine(response):
                    response = await response

                if not isinstance(response, dict):
                    response = {"output": response}

                item.result = response.get("output")
                item.status = "done"
                completed.append(item.task)

                if response.get("output") is not None:
                    final_output = response["output"]

                # Enqueue new tasks
                for new_task in response.get("new_tasks", []):
                    if len(queue) < self.max_tasks:
                        queue.append(TaskQueueItem(
                            task=new_task,
                            priority=item.priority - 1,
                        ))
                        logger.debug("AutoGPTQueue: enqueued %r", new_task)

                if response.get("done"):
                    break

            except Exception as exc:
                item.result = str(exc)
                item.status = "failed"
                failed.append(item.task)
                logger.warning("AutoGPTQueue: task %r failed: %s", item.task, exc)

        return AutoGPTResult(
            goal=goal,
            output=final_output,
            steps_taken=steps,
            completed_tasks=completed,
            failed_tasks=failed,
            elapsed_s=time.monotonic() - start,
        )


# ── HierarchicalSummariser ────────────────────────────────────────────────────

@dataclass
class SummaryNode:
    """A node in the hierarchical summary tree."""
    chunks: List[str]
    summary: Optional[str] = None
    children: List["SummaryNode"] = field(default_factory=list)
    level: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "level": self.level,
            "summary": self.summary,
            "n_chunks": len(self.chunks),
            "children": [c.to_dict() for c in self.children],
        }


class HierarchicalSummariser:
    """
    Recursively summarises large collections of text chunks.

    Splits *chunks* into groups of *chunk_size*, summarises each group,
    then recurses on the summaries until a single root summary is produced.

    Parameters
    ----------
    summarise_fn    Async or sync callable ``(chunks: list[str]) → str``
    chunk_size      Number of chunks to combine per summarisation call
    max_levels      Safety limit on recursion depth

    Usage::

        async def summarise(chunks):
            return \"Summary: \" + \" | \".join(chunks[:3])

        s = HierarchicalSummariser(summarise, chunk_size=4)
        result = await s.summarise(many_paragraphs)
        print(result.summary)
    """

    def __init__(
        self,
        summarise_fn: Callable,
        chunk_size: int = 5,
        max_levels: int = 8,
    ) -> None:
        self._summarise = summarise_fn
        self.chunk_size = chunk_size
        self.max_levels = max_levels

    async def summarise(
        self,
        chunks: List[str],
        level: int = 0,
    ) -> SummaryNode:
        """Recursively summarise *chunks* and return the root SummaryNode."""
        if not chunks:
            root = SummaryNode(chunks=[], level=level)
            root.summary = ""
            return root

        if len(chunks) <= self.chunk_size or level >= self.max_levels:
            # Base case: summarise directly
            node = SummaryNode(chunks=chunks, level=level)
            node.summary = await self._call(self._summarise, chunks)
            return node

        # Split into groups and summarise each group concurrently
        groups = [
            chunks[i: i + self.chunk_size]
            for i in range(0, len(chunks), self.chunk_size)
        ]
        children = await asyncio.gather(
            *[self.summarise(g, level + 1) for g in groups]
        )
        child_summaries = [c.summary for c in children if c.summary]

        # Summarise the child summaries
        root = SummaryNode(chunks=chunks, level=level)
        root.children = list(children)
        root.summary = await self._call(self._summarise, child_summaries)
        return root

    async def _call(self, fn: Callable, *args: Any) -> Any:
        result = fn(*args)
        if asyncio.iscoroutine(result):
            result = await result
        return result


__all__ = [
    # Hierarchical decomposition
    "TaskNode",
    "HierarchicalDecomposer",
    # MCTS
    "MCTSNode",
    "MCTSPlanner",
    # AutoGPT task queue
    "TaskQueueItem",
    "AutoGPTResult",
    "AutoGPTQueue",
    # Hierarchical summarisation
    "SummaryNode",
    "HierarchicalSummariser",
]
