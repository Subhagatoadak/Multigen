"""
Enhanced simulator primitives for Multigen.

Problems solved
---------------
- No dry-run mode (can't test graph structure without spending tokens)
- No node mock injection (can't simulate specific agent outputs)
- No load simulation (can't test fan-out behaviour at scale)
- No cost estimator (no pre-run token/cost forecast)
- No graph diff visualiser (can't visually compare two graph versions)

Classes
-------
- ``MockOutput``            — a scripted output to inject for a named node
- ``DryRunSimulator``       — runs a pipeline with all agents replaced by mocks
- ``MockInjector``          — selectively replaces specific node outputs
- ``LoadScenario``          — definition of a load simulation run
- ``LoadSimulator``         — runs concurrent requests and measures throughput
- ``CostModel``             — token → cost mapping per model
- ``CostEstimator``         — pre-run cost forecast from a workflow definition
- ``GraphDiff``             — diff between two workflow/graph definitions
- ``GraphDiffVisualiser``   — renders a diff in text/ASCII form

Usage::

    from multigen.simulator import DryRunSimulator, CostEstimator, GraphDiffVisualiser

    # Dry run
    sim = DryRunSimulator(default_output={"result": "MOCKED"})
    result = await sim.run(my_pipeline, ctx)
    print(result.node_calls)

    # Cost forecast
    estimator = CostEstimator()
    estimator.add_node("fetch",   model="gpt-4o",     estimated_tokens=800)
    estimator.add_node("analyse", model="gpt-4o",     estimated_tokens=2000)
    estimator.add_node("report",  model="gpt-4o-mini", estimated_tokens=500)
    forecast = estimator.forecast()
    print(f"Estimated cost: ${forecast.total_cost_usd:.4f}")
"""
from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple


# ── DryRunSimulator ───────────────────────────────────────────────────────────

@dataclass
class DryRunResult:
    """Result of a dry-run simulation."""
    node_calls: List[str]
    mocked_outputs: Dict[str, Any]
    total_nodes: int
    elapsed_ms: float
    ctx_trace: List[Dict[str, Any]]


class DryRunSimulator:
    """
    Runs a pipeline with all agent calls replaced by mock outputs.
    No LLM tokens are consumed.

    The *pipeline_fn* is called with a specially instrumented context.
    Each agent sees a ``_dry_run=True`` flag; the simulator intercepts
    agent calls and returns scripted outputs.

    Usage::

        sim = DryRunSimulator(default_output={"answer": "MOCKED"})
        sim.set_mock("analyse", {"risk": "low", "confidence": 0.9})
        result = await sim.run(my_workflow, {"query": "test"})
        print(result.node_calls)
    """

    def __init__(self, default_output: Optional[Dict[str, Any]] = None) -> None:
        self._default = default_output or {"_dry_run": True, "result": "MOCKED"}
        self._mocks: Dict[str, Any] = {}
        self._calls: List[str] = []
        self._trace: List[Dict[str, Any]] = []

    def set_mock(self, node_name: str, output: Any) -> None:
        self._mocks[node_name] = output

    def _mock_agent(self, node_name: str) -> Callable:
        async def agent(ctx: Dict[str, Any]) -> Dict[str, Any]:
            self._calls.append(node_name)
            out = self._mocks.get(node_name, self._default)
            self._trace.append({"node": node_name, "input": ctx, "output": out})
            return out if isinstance(out, dict) else {"result": out}
        return agent

    async def run(
        self,
        pipeline_fn: Callable,
        ctx: Dict[str, Any],
        node_names: Optional[List[str]] = None,
    ) -> DryRunResult:
        self._calls.clear()
        self._trace.clear()
        start = time.monotonic()

        patched_ctx = {
            **ctx,
            "_dry_run": True,
            "_mock_agent": self._mock_agent,
        }

        try:
            result = pipeline_fn(patched_ctx)
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as exc:
            result = {"error": str(exc)}

        return DryRunResult(
            node_calls=list(self._calls),
            mocked_outputs=dict(self._mocks),
            total_nodes=len(self._calls),
            elapsed_ms=(time.monotonic() - start) * 1000,
            ctx_trace=list(self._trace),
        )


# ── MockInjector ──────────────────────────────────────────────────────────────

class MockInjector:
    """
    Wraps an agent callable and injects a scripted output on specified calls.

    Useful for testing specific code paths without changing the pipeline.

    Usage::

        injector = MockInjector(real_agent)
        injector.inject_on(call_number=2, output={"data": "synthetic"})

        result = await injector(ctx)   # call 1 → real agent
        result = await injector(ctx)   # call 2 → injected output
        result = await injector(ctx)   # call 3 → real agent
    """

    def __init__(self, real_agent: Callable) -> None:
        self._agent = real_agent
        self._injections: Dict[int, Any] = {}
        self._call_count = 0

    def inject_on(self, call_number: int, output: Any) -> None:
        self._injections[call_number] = output

    async def __call__(self, ctx: Dict[str, Any]) -> Any:
        self._call_count += 1
        if self._call_count in self._injections:
            return self._injections[self._call_count]
        result = self._agent(ctx)
        if asyncio.iscoroutine(result):
            result = await result
        return result


# ── LoadSimulator ─────────────────────────────────────────────────────────────

@dataclass
class LoadScenario:
    """Definition of a load simulation run."""
    name: str
    concurrency: int = 10
    total_requests: int = 100
    ctx_factory: Optional[Callable] = None   # () → ctx dict


@dataclass
class LoadReport:
    scenario_name: str
    total_requests: int
    successful: int
    failed: int
    mean_latency_ms: float
    p50_latency_ms: float
    p95_latency_ms: float
    p99_latency_ms: float
    throughput_rps: float
    elapsed_s: float

    @property
    def success_rate(self) -> float:
        return self.successful / self.total_requests if self.total_requests else 0.0


class LoadSimulator:
    """
    Runs concurrent requests against a pipeline function to measure
    throughput and latency under load.

    Usage::

        sim = LoadSimulator()
        scenario = LoadScenario("fan-out-test", concurrency=20, total_requests=200)
        report = await sim.run(my_pipeline, scenario)
        print(f"P95 latency: {report.p95_latency_ms:.1f}ms")
    """

    async def run(
        self,
        pipeline_fn: Callable,
        scenario: LoadScenario,
    ) -> LoadReport:
        sem = asyncio.Semaphore(scenario.concurrency)
        latencies: List[float] = []
        errors = 0

        ctx_factory = scenario.ctx_factory or (lambda: {"_load_test": True})

        async def one_request() -> float:
            async with sem:
                start = time.monotonic()
                try:
                    ctx = ctx_factory()
                    result = pipeline_fn(ctx)
                    if asyncio.iscoroutine(result):
                        result = await result
                    return (time.monotonic() - start) * 1000
                except Exception:
                    return -1.0

        wall_start = time.monotonic()
        results = await asyncio.gather(
            *[one_request() for _ in range(scenario.total_requests)],
            return_exceptions=True,
        )
        elapsed = time.monotonic() - wall_start

        for r in results:
            if isinstance(r, float) and r >= 0:
                latencies.append(r)
            else:
                errors += 1

        sorted_lat = sorted(latencies)
        n = len(sorted_lat)

        def percentile(p: float) -> float:
            if not sorted_lat:
                return 0.0
            idx = int(n * p / 100)
            return sorted_lat[min(idx, n - 1)]

        return LoadReport(
            scenario_name=scenario.name,
            total_requests=scenario.total_requests,
            successful=len(latencies),
            failed=errors,
            mean_latency_ms=statistics.mean(latencies) if latencies else 0.0,
            p50_latency_ms=percentile(50),
            p95_latency_ms=percentile(95),
            p99_latency_ms=percentile(99),
            throughput_rps=len(latencies) / elapsed if elapsed > 0 else 0.0,
            elapsed_s=elapsed,
        )


# ── CostEstimator ─────────────────────────────────────────────────────────────

# Default cost per 1K tokens (input, output) in USD
DEFAULT_COST_PER_1K: Dict[str, Tuple[float, float]] = {
    "gpt-4o":              (0.005, 0.015),
    "gpt-4o-mini":         (0.00015, 0.0006),
    "gpt-4-turbo":         (0.010, 0.030),
    "claude-opus-4-6":     (0.015, 0.075),
    "claude-sonnet-4-6":   (0.003, 0.015),
    "claude-haiku-4-5":    (0.00025, 0.00125),
    "default":             (0.002, 0.008),
}


@dataclass
class NodeCostSpec:
    name: str
    model: str = "default"
    estimated_input_tokens: int = 500
    estimated_output_tokens: int = 200
    runs_per_workflow: int = 1       # for fan-out nodes
    parallel: bool = False


@dataclass
class CostForecast:
    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    per_node: Dict[str, float]

    def render(self) -> str:
        lines = ["Cost Forecast:"]
        for name, cost in sorted(self.per_node.items(), key=lambda x: -x[1]):
            lines.append(f"  {name:<30} ${cost:.5f}")
        lines.append(f"  {'TOTAL':<30} ${self.total_cost_usd:.5f}")
        lines.append(f"  Input tokens:  {self.total_input_tokens:,}")
        lines.append(f"  Output tokens: {self.total_output_tokens:,}")
        return "\n".join(lines)


class CostEstimator:
    """
    Pre-run token / cost forecast for a workflow definition.

    Usage::

        estimator = CostEstimator()
        estimator.add_node("fetch",   model="gpt-4o-mini", estimated_input_tokens=300)
        estimator.add_node("analyse", model="gpt-4o",      estimated_input_tokens=1500,
                           estimated_output_tokens=800)
        estimator.add_node("report",  model="gpt-4o",      estimated_input_tokens=2500,
                           runs_per_workflow=3)  # fan-out × 3

        forecast = estimator.forecast()
        print(forecast.render())
    """

    def __init__(self, custom_costs: Optional[Dict[str, Tuple[float, float]]] = None) -> None:
        self._costs = {**DEFAULT_COST_PER_1K, **(custom_costs or {})}
        self._nodes: List[NodeCostSpec] = []

    def add_node(self, name: str, **kwargs: Any) -> NodeCostSpec:
        spec = NodeCostSpec(name=name, **kwargs)
        self._nodes.append(spec)
        return spec

    def forecast(self) -> CostForecast:
        per_node: Dict[str, float] = {}
        total_in = total_out = 0
        total_cost = 0.0

        for node in self._nodes:
            in_cost, out_cost = self._costs.get(node.model, self._costs["default"])
            in_tokens = node.estimated_input_tokens * node.runs_per_workflow
            out_tokens = node.estimated_output_tokens * node.runs_per_workflow
            cost = (in_tokens / 1000 * in_cost) + (out_tokens / 1000 * out_cost)

            per_node[node.name] = cost
            total_in += in_tokens
            total_out += out_tokens
            total_cost += cost

        return CostForecast(
            total_cost_usd=total_cost,
            total_input_tokens=total_in,
            total_output_tokens=total_out,
            per_node=per_node,
        )


# ── GraphDiffVisualiser ───────────────────────────────────────────────────────

@dataclass
class GraphDiff:
    """Diff between two workflow graph definitions."""
    added_nodes: List[str]
    removed_nodes: List[str]
    added_edges: List[Tuple[str, str]]
    removed_edges: List[Tuple[str, str]]
    changed_nodes: List[str]           # same name, different config
    version_a: str = "v_a"
    version_b: str = "v_b"

    @property
    def has_changes(self) -> bool:
        return bool(
            self.added_nodes or self.removed_nodes or
            self.added_edges or self.removed_edges or
            self.changed_nodes
        )


class GraphDiffVisualiser:
    """
    Compares two graph definitions and renders an ASCII diff.

    A "graph definition" is a dict with ``"nodes"`` (list of names) and
    optionally ``"edges"`` (list of [src, dst] pairs) and
    ``"config"`` (dict of node_name → config dict).

    Usage::

        vis = GraphDiffVisualiser()
        graph_v1 = {
            "nodes": ["fetch", "analyse", "report"],
            "edges": [["fetch", "analyse"], ["analyse", "report"]],
        }
        graph_v2 = {
            "nodes": ["fetch", "analyse", "summarise", "report"],
            "edges": [["fetch", "analyse"], ["analyse", "summarise"],
                      ["summarise", "report"]],
        }
        diff = vis.diff(graph_v1, graph_v2, version_a="v1", version_b="v2")
        print(vis.render(diff))
    """

    def diff(
        self,
        graph_a: Dict[str, Any],
        graph_b: Dict[str, Any],
        version_a: str = "v_a",
        version_b: str = "v_b",
    ) -> GraphDiff:
        nodes_a = set(graph_a.get("nodes", []))
        nodes_b = set(graph_b.get("nodes", []))
        edges_a = {tuple(e) for e in graph_a.get("edges", [])}
        edges_b = {tuple(e) for e in graph_b.get("edges", [])}
        config_a = graph_a.get("config", {})
        config_b = graph_b.get("config", {})

        changed = [
            n for n in nodes_a & nodes_b
            if config_a.get(n) != config_b.get(n)
        ]

        return GraphDiff(
            added_nodes=sorted(nodes_b - nodes_a),
            removed_nodes=sorted(nodes_a - nodes_b),
            added_edges=sorted(edges_b - edges_a),  # type: ignore[arg-type]
            removed_edges=sorted(edges_a - edges_b),  # type: ignore[arg-type]
            changed_nodes=sorted(changed),
            version_a=version_a,
            version_b=version_b,
        )

    def render(self, diff: GraphDiff) -> str:
        lines = [
            f"Graph Diff: {diff.version_a} → {diff.version_b}",
            "─" * 50,
        ]
        if not diff.has_changes:
            lines.append("  (no changes)")
            return "\n".join(lines)

        for n in diff.added_nodes:
            lines.append(f"  + node  {n}")
        for n in diff.removed_nodes:
            lines.append(f"  - node  {n}")
        for n in diff.changed_nodes:
            lines.append(f"  ~ node  {n}  (config changed)")
        for e in diff.added_edges:
            lines.append(f"  + edge  {e[0]} → {e[1]}")
        for e in diff.removed_edges:
            lines.append(f"  - edge  {e[0]} → {e[1]}")

        lines.append("─" * 50)
        lines.append(
            f"  Summary: +{len(diff.added_nodes)} nodes  "
            f"-{len(diff.removed_nodes)} nodes  "
            f"~{len(diff.changed_nodes)} changed  "
            f"+{len(diff.added_edges)} edges  "
            f"-{len(diff.removed_edges)} edges"
        )
        return "\n".join(lines)


__all__ = [
    "DryRunResult",
    "DryRunSimulator",
    "MockInjector",
    "LoadScenario",
    "LoadReport",
    "LoadSimulator",
    "NodeCostSpec",
    "CostForecast",
    "CostEstimator",
    "GraphDiff",
    "GraphDiffVisualiser",
]
