"""
Evaluation and measurement framework for Multigen agents and pipelines.

The eval layer answers: "Is my agent/workflow actually doing a good job?"
It provides:

- ``EvalCase``        — a single (input, expected_output, metadata) test case
- ``EvalDataset``     — a collection of ``EvalCase`` objects (load from list/JSONL)
- ``Metric``          — base class for a scoring function
- Built-in metrics    — ``ExactMatch``, ``ContainsMatch``, ``RegexMatch``,
                        ``JSONFieldMatch``, ``LLMJudge``, ``Latency``,
                        ``TokenCount``, ``CostEstimate``
- ``EvalResult``      — per-case score record
- ``EvalReport``      — aggregated statistics across all cases
- ``Evaluator``       — orchestrates running an agent against a dataset and
                        collecting ``EvalReport``
- ``EvalSuite``       — groups multiple evaluators for batch runs
- ``EvalRegistry``    — register named datasets and metrics for reuse
- ``Benchmark``       — thin wrapper for head-to-head comparison of two agents

Usage
-----
    from multigen.eval import (
        EvalCase, EvalDataset, Evaluator, EvalSuite,
        ExactMatch, ContainsMatch, LLMJudge, Latency, CostEstimate,
    )

    # 1. Build a dataset
    dataset = EvalDataset([
        EvalCase(input={"question": "What is 2+2?"}, expected="4"),
        EvalCase(input={"question": "Capital of France?"}, expected="Paris"),
    ])

    # 2. Choose metrics
    metrics = [ExactMatch(), ContainsMatch(), Latency()]

    # 3. Run evaluation
    evaluator = Evaluator(agent=my_agent, dataset=dataset, metrics=metrics)
    report = await evaluator.run()

    print(report.summary())        # pass_rate, mean_latency_ms, ...
    report.save_jsonl("results.jsonl")

    # 4. Head-to-head benchmark
    from multigen.eval import Benchmark
    bench = Benchmark(agent_a, agent_b, dataset, metrics)
    comparison = await bench.run()
    print(comparison)
"""
from __future__ import annotations

import asyncio
import json
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple, Union


# ── EvalCase ───────────────────────────────────────────────────────────────────

@dataclass
class EvalCase:
    """
    A single evaluation case: *input* fed to the agent plus the *expected*
    output used for scoring.

    Parameters
    ----------
    input       Context dict (or any value) passed to ``agent(input)``.
    expected    Reference output — interpretation depends on the metric.
    id          Optional stable identifier (auto-generated if omitted).
    metadata    Arbitrary key-value annotations (domain, difficulty, etc.).
    tags        Shorthand labels for filtering.
    """

    input: Any
    expected: Any = None
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    metadata: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "input": self.input,
            "expected": self.expected,
            "metadata": self.metadata,
            "tags": self.tags,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "EvalCase":
        return cls(
            input=d["input"],
            expected=d.get("expected"),
            id=d.get("id", str(uuid.uuid4())[:8]),
            metadata=d.get("metadata", {}),
            tags=d.get("tags", []),
        )


# ── EvalDataset ────────────────────────────────────────────────────────────────

class EvalDataset:
    """
    A collection of ``EvalCase`` objects.

    Supports filtering by tag, slicing, and serialisation to/from JSONL.

    Usage::

        ds = EvalDataset([
            EvalCase({"q": "hello"}, expected="hi"),
        ])
        ds.add(EvalCase({"q": "bye"}, expected="goodbye", tags=["farewell"]))
        farewell_ds = ds.filter_by_tag("farewell")
        ds.save_jsonl("cases.jsonl")
        ds2 = EvalDataset.load_jsonl("cases.jsonl")
    """

    def __init__(self, cases: Optional[List[EvalCase]] = None) -> None:
        self._cases: List[EvalCase] = list(cases or [])

    def add(self, case: EvalCase) -> None:
        self._cases.append(case)

    def filter_by_tag(self, tag: str) -> "EvalDataset":
        return EvalDataset([c for c in self._cases if tag in c.tags])

    def filter(self, predicate: Callable[[EvalCase], bool]) -> "EvalDataset":
        return EvalDataset([c for c in self._cases if predicate(c)])

    def sample(self, n: int, seed: Optional[int] = None) -> "EvalDataset":
        import random
        rng = random.Random(seed)
        cases = list(self._cases)
        rng.shuffle(cases)
        return EvalDataset(cases[:n])

    def save_jsonl(self, path: Union[str, Path]) -> None:
        with open(path, "w") as f:
            for case in self._cases:
                f.write(json.dumps(case.to_dict()) + "\n")

    @classmethod
    def load_jsonl(cls, path: Union[str, Path]) -> "EvalDataset":
        cases: List[EvalCase] = []
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    cases.append(EvalCase.from_dict(json.loads(line)))
        return cls(cases)

    def __len__(self) -> int:
        return len(self._cases)

    def __iter__(self):
        return iter(self._cases)

    def __getitem__(self, idx):
        return self._cases[idx]

    def __repr__(self) -> str:
        return f"EvalDataset({len(self._cases)} cases)"


# ── Metric base ────────────────────────────────────────────────────────────────

class Metric:
    """
    Base class for evaluation metrics.

    Subclass and implement ``score(case, output, latency_ms, **kwargs)``.
    The method must return a ``float`` in ``[0.0, 1.0]`` (or any numeric scale
    you document — convention is 1.0 = perfect).

    ``name``  identifies the metric in reports.
    ``higher_is_better`` determines how deltas are reported.
    """

    name: str = "metric"
    higher_is_better: bool = True

    def score(
        self,
        case: EvalCase,
        output: Any,
        latency_ms: float = 0.0,
    ) -> float:
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{type(self).__name__}()"


# ── Built-in metrics ───────────────────────────────────────────────────────────

class ExactMatch(Metric):
    """
    Binary score: 1.0 if ``str(output).strip() == str(expected).strip()``.

    If *case_sensitive* is False, both sides are lower-cased before comparison.
    """

    name = "exact_match"

    def __init__(self, case_sensitive: bool = False) -> None:
        self.case_sensitive = case_sensitive

    def score(self, case: EvalCase, output: Any, latency_ms: float = 0.0) -> float:
        a = str(output).strip()
        b = str(case.expected).strip()
        if not self.case_sensitive:
            a, b = a.lower(), b.lower()
        return 1.0 if a == b else 0.0


class ContainsMatch(Metric):
    """
    Binary score: 1.0 if the string representation of *expected* is contained
    within the string representation of *output*.
    """

    name = "contains_match"

    def __init__(self, case_sensitive: bool = False) -> None:
        self.case_sensitive = case_sensitive

    def score(self, case: EvalCase, output: Any, latency_ms: float = 0.0) -> float:
        a = str(output)
        b = str(case.expected)
        if not self.case_sensitive:
            a, b = a.lower(), b.lower()
        return 1.0 if b in a else 0.0


class RegexMatch(Metric):
    """
    Checks whether the output matches a regex pattern stored in ``case.expected``
    (or a fixed pattern supplied at construction time).
    """

    name = "regex_match"

    def __init__(self, pattern: Optional[str] = None, flags: int = re.IGNORECASE) -> None:
        self._pattern = pattern
        self._flags = flags

    def score(self, case: EvalCase, output: Any, latency_ms: float = 0.0) -> float:
        pat = self._pattern if self._pattern is not None else str(case.expected)
        try:
            return 1.0 if re.search(pat, str(output), self._flags) else 0.0
        except re.error:
            return 0.0


class JSONFieldMatch(Metric):
    """
    Checks that specific keys in the JSON output match expected values.

    ``expected`` in the ``EvalCase`` should be a dict like ``{"key": value}``.
    Score = fraction of matched keys.

    Usage::

        metric = JSONFieldMatch(fields=["answer", "confidence"])
    """

    name = "json_field_match"

    def __init__(
        self,
        fields: Optional[List[str]] = None,
        case_sensitive: bool = False,
    ) -> None:
        self._fields = fields
        self._case_sensitive = case_sensitive

    def score(self, case: EvalCase, output: Any, latency_ms: float = 0.0) -> float:
        if isinstance(output, str):
            try:
                output = json.loads(output)
            except (json.JSONDecodeError, ValueError):
                return 0.0
        if not isinstance(output, dict):
            return 0.0
        expected = case.expected
        if not isinstance(expected, dict):
            return 0.0
        keys = self._fields if self._fields else list(expected.keys())
        if not keys:
            return 1.0
        matches = 0
        for k in keys:
            got = output.get(k)
            exp = expected.get(k)
            if not self._case_sensitive:
                got = str(got).lower() if got is not None else None
                exp = str(exp).lower() if exp is not None else None
            if got == exp:
                matches += 1
        return matches / len(keys)


class F1Score(Metric):
    """
    Token-level F1 score between *output* and *expected* (common in QA tasks).

    Tokenises by whitespace after normalisation.
    """

    name = "f1_score"

    @staticmethod
    def _tokens(text: str) -> List[str]:
        text = re.sub(r"[^a-z0-9 ]", " ", text.lower())
        return [t for t in text.split() if t]

    def score(self, case: EvalCase, output: Any, latency_ms: float = 0.0) -> float:
        pred = self._tokens(str(output))
        gold = self._tokens(str(case.expected))
        if not pred or not gold:
            return 1.0 if not pred and not gold else 0.0
        common = set(pred) & set(gold)
        if not common:
            return 0.0
        precision = len(common) / len(pred)
        recall = len(common) / len(gold)
        return 2 * precision * recall / (precision + recall)


class Latency(Metric):
    """
    Returns normalised latency score in ``[0.0, 1.0]``.

    ``score = max(0, 1 - latency_ms / budget_ms)``

    A response that completes in zero time scores 1.0; at or beyond *budget_ms*
    it scores 0.0.  Set *higher_is_better = False* if you want raw milliseconds
    in the report instead.
    """

    name = "latency"
    higher_is_better = False   # report raw ms in summary

    def __init__(self, budget_ms: float = 5000.0) -> None:
        self.budget_ms = budget_ms

    def score(self, case: EvalCase, output: Any, latency_ms: float = 0.0) -> float:
        # Return raw ms so the report can compute p50/p95
        return latency_ms


class TokenCount(Metric):
    """
    Estimates output token count (whitespace tokenisation).
    Useful for tracking verbosity / cost proxies.
    """

    name = "token_count"
    higher_is_better = False

    def score(self, case: EvalCase, output: Any, latency_ms: float = 0.0) -> float:
        return float(len(str(output).split()))


class CostEstimate(Metric):
    """
    Rough cost estimate in USD based on estimated token counts.

    Uses a *cost_per_1k_tokens* rate (default: $0.002 — GPT-3.5-class).
    Input tokens are estimated from ``case.input``; output tokens from ``output``.
    """

    name = "cost_usd"
    higher_is_better = False

    def __init__(self, cost_per_1k_tokens: float = 0.002) -> None:
        self.rate = cost_per_1k_tokens / 1000.0

    def score(self, case: EvalCase, output: Any, latency_ms: float = 0.0) -> float:
        in_tokens = len(str(case.input).split())
        out_tokens = len(str(output).split())
        return (in_tokens + out_tokens) * self.rate


class LLMJudge(Metric):
    """
    Uses an LLM-as-judge to score agent output on a 0–1 scale.

    The judge is any callable ``judge_fn(case, output) -> float``.  This
    decouples the metric from any specific LLM provider.

    Usage::

        async def my_judge(case, output):
            # call your LLM here
            return 0.9  # 0.0 – 1.0

        metric = LLMJudge(judge_fn=my_judge, name="coherence")

    If ``judge_fn`` is a coroutine function it is awaited; otherwise called
    synchronously.
    """

    higher_is_better = True

    def __init__(
        self,
        judge_fn: Callable,
        name: str = "llm_judge",
    ) -> None:
        self.name = name
        self._judge = judge_fn

    def score(self, case: EvalCase, output: Any, latency_ms: float = 0.0) -> float:
        # Synchronous wrapper — async variant handled by Evaluator directly
        raise RuntimeError("LLMJudge must be called via _async_score")

    async def async_score(
        self,
        case: EvalCase,
        output: Any,
        latency_ms: float = 0.0,
    ) -> float:
        result = self._judge(case, output)
        if asyncio.iscoroutine(result):
            result = await result
        return float(result)


class CustomMetric(Metric):
    """Wrap any ``fn(case, output, latency_ms) -> float`` as a named metric."""

    def __init__(self, fn: Callable, name: str = "custom") -> None:
        self.name = name
        self._fn = fn

    def score(self, case: EvalCase, output: Any, latency_ms: float = 0.0) -> float:
        return float(self._fn(case, output, latency_ms))


# ── EvalResult ────────────────────────────────────────────────────────────────

@dataclass
class EvalResult:
    """Scores for a single (case, output) pair across all metrics."""

    case_id: str
    input: Any
    expected: Any
    output: Any
    latency_ms: float
    scores: Dict[str, float] = field(default_factory=dict)
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "case_id": self.case_id,
            "input": self.input,
            "expected": self.expected,
            "output": self.output,
            "latency_ms": self.latency_ms,
            "scores": self.scores,
            "error": self.error,
            "metadata": self.metadata,
        }


# ── EvalReport ────────────────────────────────────────────────────────────────

class EvalReport:
    """
    Aggregated statistics across all ``EvalResult`` objects.

    Usage::

        report = EvalReport(results, agent_name="MyAgent")
        print(report.summary())
        report.save_jsonl("report.jsonl")
        df = report.to_dataframe()    # requires pandas
    """

    def __init__(
        self,
        results: List[EvalResult],
        agent_name: str = "agent",
        dataset_name: str = "dataset",
    ) -> None:
        self.results = results
        self.agent_name = agent_name
        self.dataset_name = dataset_name
        self._stats: Optional[Dict[str, Any]] = None

    def _compute(self) -> Dict[str, Any]:
        if self._stats is not None:
            return self._stats

        total = len(self.results)
        errored = sum(1 for r in self.results if r.error is not None)
        successful = total - errored

        # Per-metric aggregation
        metric_scores: Dict[str, List[float]] = {}
        for r in self.results:
            for metric_name, score in r.scores.items():
                metric_scores.setdefault(metric_name, []).append(score)

        agg: Dict[str, Dict[str, float]] = {}
        for metric_name, scores in metric_scores.items():
            if not scores:
                continue
            sorted_s = sorted(scores)
            n = len(sorted_s)
            agg[metric_name] = {
                "mean": sum(sorted_s) / n,
                "min": sorted_s[0],
                "max": sorted_s[-1],
                "p50": sorted_s[int(n * 0.50)],
                "p95": sorted_s[min(int(n * 0.95), n - 1)],
                "count": n,
            }

        latencies = [r.latency_ms for r in self.results if r.error is None]
        latency_agg: Dict[str, float] = {}
        if latencies:
            sl = sorted(latencies)
            nl = len(sl)
            latency_agg = {
                "mean_ms": sum(sl) / nl,
                "p50_ms": sl[int(nl * 0.50)],
                "p95_ms": sl[min(int(nl * 0.95), nl - 1)],
                "max_ms": sl[-1],
            }

        self._stats = {
            "agent": self.agent_name,
            "dataset": self.dataset_name,
            "total": total,
            "successful": successful,
            "errored": errored,
            "error_rate": errored / total if total else 0.0,
            "metrics": agg,
            "latency": latency_agg,
        }
        return self._stats

    def summary(self) -> str:
        s = self._compute()
        lines = [
            f"EvalReport — agent={s['agent']}  dataset={s['dataset']}",
            f"  cases: {s['total']} total  |  {s['successful']} ok  |  {s['errored']} errors  "
            f"({s['error_rate']*100:.1f}% error rate)",
        ]
        if s.get("latency"):
            lat = s["latency"]
            lines.append(
                f"  latency: mean={lat['mean_ms']:.0f}ms  "
                f"p50={lat['p50_ms']:.0f}ms  p95={lat['p95_ms']:.0f}ms"
            )
        for metric_name, agg in sorted(s["metrics"].items()):
            lines.append(
                f"  {metric_name}: mean={agg['mean']:.3f}  "
                f"min={agg['min']:.3f}  max={agg['max']:.3f}  p95={agg['p95']:.3f}"
            )
        return "\n".join(lines)

    def stats(self) -> Dict[str, Any]:
        return self._compute()

    def save_jsonl(self, path: Union[str, Path]) -> None:
        with open(path, "w") as f:
            # First line: aggregate stats
            f.write(json.dumps({"_type": "report_summary", **self._compute()}) + "\n")
            for r in self.results:
                f.write(json.dumps({"_type": "result", **r.to_dict()}) + "\n")

    @classmethod
    def load_jsonl(cls, path: Union[str, Path]) -> "EvalReport":
        results: List[EvalResult] = []
        agent_name = "agent"
        dataset_name = "dataset"
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                d = json.loads(line)
                if d.get("_type") == "report_summary":
                    agent_name = d.get("agent", agent_name)
                    dataset_name = d.get("dataset", dataset_name)
                elif d.get("_type") == "result":
                    results.append(EvalResult(
                        case_id=d["case_id"],
                        input=d["input"],
                        expected=d["expected"],
                        output=d["output"],
                        latency_ms=d["latency_ms"],
                        scores=d["scores"],
                        error=d.get("error"),
                        metadata=d.get("metadata", {}),
                    ))
        return cls(results, agent_name=agent_name, dataset_name=dataset_name)

    def to_dataframe(self):
        """Convert results to a pandas DataFrame (requires pandas)."""
        try:
            import pandas as pd  # type: ignore
        except ImportError as e:
            raise ImportError("pandas is required for to_dataframe()") from e
        rows = []
        for r in self.results:
            row = {
                "case_id": r.case_id,
                "latency_ms": r.latency_ms,
                "error": r.error,
            }
            row.update(r.scores)
            rows.append(row)
        return pd.DataFrame(rows)

    def __repr__(self) -> str:
        s = self._compute()
        return (
            f"EvalReport(agent={s['agent']!r}, cases={s['total']}, "
            f"errors={s['errored']})"
        )


# ── Evaluator ─────────────────────────────────────────────────────────────────

class Evaluator:
    """
    Runs an agent against a dataset and scores each output with the given metrics.

    Parameters
    ----------
    agent           Any callable ``agent(input) -> output`` (sync or async).
    dataset         ``EvalDataset`` to evaluate against.
    metrics         List of ``Metric`` instances.
    agent_name      Label used in the ``EvalReport``.
    concurrency     Max number of cases to run in parallel (default: 4).
    timeout_s       Per-case timeout in seconds (default: 30).
    output_key      If the agent returns a dict, extract this key as the output
                    for metric scoring.  ``None`` → use the full return value.
    on_result       Optional callback ``fn(EvalResult) -> None`` fired after each case.

    Usage::

        ev = Evaluator(
            agent=my_agent,
            dataset=dataset,
            metrics=[ExactMatch(), Latency()],
            concurrency=8,
        )
        report = await ev.run()
        print(report.summary())
    """

    def __init__(
        self,
        agent: Callable,
        dataset: EvalDataset,
        metrics: Optional[List[Metric]] = None,
        agent_name: str = "agent",
        dataset_name: str = "dataset",
        concurrency: int = 4,
        timeout_s: float = 30.0,
        output_key: Optional[str] = None,
        on_result: Optional[Callable[[EvalResult], None]] = None,
    ) -> None:
        self._agent = agent
        self._dataset = dataset
        self._metrics = metrics or [ExactMatch()]
        self._agent_name = agent_name
        self._dataset_name = dataset_name
        self._concurrency = concurrency
        self._timeout = timeout_s
        self._output_key = output_key
        self._on_result = on_result

    async def _run_case(
        self,
        case: EvalCase,
        sem: asyncio.Semaphore,
    ) -> EvalResult:
        async with sem:
            t0 = time.perf_counter()
            output = None
            error: Optional[str] = None
            try:
                if asyncio.iscoroutinefunction(self._agent):
                    output = await asyncio.wait_for(
                        self._agent(case.input), timeout=self._timeout
                    )
                else:
                    loop = asyncio.get_event_loop()
                    output = await asyncio.wait_for(
                        loop.run_in_executor(None, self._agent, case.input),
                        timeout=self._timeout,
                    )
                if self._output_key and isinstance(output, dict):
                    output = output.get(self._output_key, output)
            except asyncio.TimeoutError:
                error = f"TimeoutError: exceeded {self._timeout}s"
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            latency_ms = (time.perf_counter() - t0) * 1000

            scores: Dict[str, float] = {}
            for metric in self._metrics:
                if error is not None:
                    scores[metric.name] = 0.0
                    continue
                try:
                    if isinstance(metric, LLMJudge):
                        scores[metric.name] = await metric.async_score(
                            case, output, latency_ms
                        )
                    else:
                        scores[metric.name] = metric.score(case, output, latency_ms)
                except Exception as exc:
                    scores[metric.name] = 0.0

            result = EvalResult(
                case_id=case.id,
                input=case.input,
                expected=case.expected,
                output=output,
                latency_ms=latency_ms,
                scores=scores,
                error=error,
                metadata=dict(case.metadata),
            )
            if self._on_result is not None:
                self._on_result(result)
            return result

    async def run(self) -> EvalReport:
        """Evaluate all cases and return an ``EvalReport``."""
        sem = asyncio.Semaphore(self._concurrency)
        tasks = [self._run_case(case, sem) for case in self._dataset]
        results = await asyncio.gather(*tasks)
        return EvalReport(
            list(results),
            agent_name=self._agent_name,
            dataset_name=self._dataset_name,
        )

    async def run_streaming(self):
        """
        Async generator that yields each ``EvalResult`` as it completes.

        Usage::

            async for result in evaluator.run_streaming():
                print(result.case_id, result.scores)
        """
        sem = asyncio.Semaphore(self._concurrency)
        queue: asyncio.Queue[EvalResult] = asyncio.Queue()

        async def worker(case: EvalCase) -> None:
            r = await self._run_case(case, sem)
            await queue.put(r)

        tasks = [asyncio.create_task(worker(c)) for c in self._dataset]
        done = 0
        total = len(tasks)
        while done < total:
            yield await queue.get()
            done += 1
        await asyncio.gather(*tasks, return_exceptions=True)


# ── EvalSuite ─────────────────────────────────────────────────────────────────

class EvalSuite:
    """
    Groups multiple ``Evaluator`` instances for a batch run.

    Usage::

        suite = EvalSuite(name="nightly")
        suite.add("fast_model", Evaluator(fast_agent, ds, metrics))
        suite.add("slow_model", Evaluator(slow_agent, ds, metrics))
        reports = await suite.run()
        suite.print_comparison(reports)
    """

    def __init__(self, name: str = "suite") -> None:
        self.name = name
        self._evaluators: List[Tuple[str, Evaluator]] = []

    def add(self, label: str, evaluator: Evaluator) -> "EvalSuite":
        self._evaluators.append((label, evaluator))
        return self

    async def run(self) -> Dict[str, EvalReport]:
        """Run all evaluators concurrently and return a label → report mapping."""
        async def _run(label: str, ev: Evaluator) -> Tuple[str, EvalReport]:
            report = await ev.run()
            return label, report

        pairs = await asyncio.gather(*[_run(lbl, ev) for lbl, ev in self._evaluators])
        return dict(pairs)

    def print_comparison(self, reports: Dict[str, EvalReport]) -> None:
        """Print a side-by-side summary of all reports."""
        print(f"\n{'─'*60}")
        print(f"  EvalSuite: {self.name}")
        print(f"{'─'*60}")
        for label, report in reports.items():
            print(f"\n[{label}]")
            print(report.summary())
        print(f"{'─'*60}")


# ── EvalRegistry ──────────────────────────────────────────────────────────────

class EvalRegistry:
    """
    Central registry for named datasets and metrics.

    Usage::

        registry = EvalRegistry()
        registry.register_dataset("qa_basic", dataset)
        registry.register_metric("em", ExactMatch())

        ds = registry.get_dataset("qa_basic")
        m  = registry.get_metric("em")
    """

    def __init__(self) -> None:
        self._datasets: Dict[str, EvalDataset] = {}
        self._metrics: Dict[str, Metric] = {}

    def register_dataset(self, name: str, dataset: EvalDataset) -> None:
        self._datasets[name] = dataset

    def register_metric(self, name: str, metric: Metric) -> None:
        self._metrics[name] = metric

    def get_dataset(self, name: str) -> EvalDataset:
        if name not in self._datasets:
            raise KeyError(f"Dataset '{name}' not registered")
        return self._datasets[name]

    def get_metric(self, name: str) -> Metric:
        if name not in self._metrics:
            raise KeyError(f"Metric '{name}' not registered")
        return self._metrics[name]

    def list_datasets(self) -> List[str]:
        return list(self._datasets.keys())

    def list_metrics(self) -> List[str]:
        return list(self._metrics.keys())


# ── Benchmark ─────────────────────────────────────────────────────────────────

@dataclass
class BenchmarkResult:
    """Head-to-head comparison of two agents on the same dataset."""

    agent_a: str
    agent_b: str
    report_a: EvalReport
    report_b: EvalReport

    def winner(self, metric_name: str) -> str:
        """Return the label of the better-performing agent on *metric_name*."""
        stats_a = self.report_a.stats()["metrics"].get(metric_name, {})
        stats_b = self.report_b.stats()["metrics"].get(metric_name, {})
        mean_a = stats_a.get("mean", 0.0)
        mean_b = stats_b.get("mean", 0.0)
        if mean_a > mean_b:
            return self.agent_a
        if mean_b > mean_a:
            return self.agent_b
        return "tie"

    def delta(self, metric_name: str) -> float:
        """Return mean(A) - mean(B) for *metric_name* (positive = A wins)."""
        stats_a = self.report_a.stats()["metrics"].get(metric_name, {})
        stats_b = self.report_b.stats()["metrics"].get(metric_name, {})
        return stats_a.get("mean", 0.0) - stats_b.get("mean", 0.0)

    def summary(self) -> str:
        all_metrics = set(self.report_a.stats()["metrics"]) | set(
            self.report_b.stats()["metrics"]
        )
        lines = [
            f"Benchmark: {self.agent_a} vs {self.agent_b}",
            f"{'Metric':<25} {'Agent A':>10} {'Agent B':>10} {'Winner':>12}",
            "─" * 60,
        ]
        for m in sorted(all_metrics):
            sa = self.report_a.stats()["metrics"].get(m, {}).get("mean", float("nan"))
            sb = self.report_b.stats()["metrics"].get(m, {}).get("mean", float("nan"))
            w = self.winner(m)
            lines.append(f"{m:<25} {sa:>10.4f} {sb:>10.4f} {w:>12}")
        return "\n".join(lines)


class Benchmark:
    """
    Head-to-head comparison of two agents on the same dataset.

    Usage::

        bench = Benchmark(
            agent_a=gpt4_agent, label_a="gpt4",
            agent_b=claude_agent, label_b="claude",
            dataset=dataset,
            metrics=[ExactMatch(), Latency(), CostEstimate()],
        )
        result = await bench.run()
        print(result.summary())
        print("Winner (exact_match):", result.winner("exact_match"))
    """

    def __init__(
        self,
        agent_a: Callable,
        agent_b: Callable,
        dataset: EvalDataset,
        metrics: Optional[List[Metric]] = None,
        label_a: str = "agent_a",
        label_b: str = "agent_b",
        concurrency: int = 4,
    ) -> None:
        self._ev_a = Evaluator(
            agent_a, dataset, metrics,
            agent_name=label_a, concurrency=concurrency,
        )
        self._ev_b = Evaluator(
            agent_b, dataset, metrics,
            agent_name=label_b, concurrency=concurrency,
        )
        self._label_a = label_a
        self._label_b = label_b

    async def run(self) -> BenchmarkResult:
        report_a, report_b = await asyncio.gather(
            self._ev_a.run(), self._ev_b.run()
        )
        return BenchmarkResult(
            agent_a=self._label_a,
            agent_b=self._label_b,
            report_a=report_a,
            report_b=report_b,
        )


# ── Module-level default registry ─────────────────────────────────────────────

_default_registry = EvalRegistry()

def get_eval_registry() -> EvalRegistry:
    """Return the module-level default ``EvalRegistry``."""
    return _default_registry


__all__ = [
    # Data model
    "EvalCase",
    "EvalDataset",
    "EvalResult",
    "EvalReport",
    # Metrics
    "Metric",
    "ExactMatch",
    "ContainsMatch",
    "RegexMatch",
    "JSONFieldMatch",
    "F1Score",
    "Latency",
    "TokenCount",
    "CostEstimate",
    "LLMJudge",
    "CustomMetric",
    # Orchestration
    "Evaluator",
    "EvalSuite",
    "EvalRegistry",
    "get_eval_registry",
    # Benchmark
    "Benchmark",
    "BenchmarkResult",
]
