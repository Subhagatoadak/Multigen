# Multigen

**A governed execution and control plane for complex multi-agent systems.**

[![CI](https://github.com/Subhagatoadak/Multigen/actions/workflows/ci.yml/badge.svg)](https://github.com/Subhagatoadak/Multigen/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![SDK](https://img.shields.io/badge/SDK-0.3.0-orange)](sdk/pyproject.toml)

Multigen is a Python-native framework for building, operating, and governing multi-agent workflows. It combines composable orchestration primitives with durable execution, runtime intervention, evaluation, observability, safety controls, and human approval.

The project is designed for systems where reliability, auditability, and controlled autonomy matter more than unconstrained agent-to-agent conversation.

## Core ideas

- **Composable orchestration** — chains, parallel execution, graphs, state machines, fan-out, MapReduce, race, and batch patterns.
- **Governed autonomy** — approval gates, permissions, safety checks, circuit breakers, and controlled dynamic agent creation.
- **Epistemic transparency** — confidence, assumptions, evidence quality, uncertainty sources, limitations, and propagated uncertainty can travel with node outputs.
- **Durable and interruptible execution** — workflows can support persistence, retries, replay, pausing, resuming, rerouting, and recovery.
- **Evaluation and learning** — evaluation suites, regression datasets, A/B testing, canary rollout, feedback ingestion, and adaptive prompt mechanisms.
- **Enterprise observability** — structured events, tracing, metrics, profiling, snapshots, replay, SLA monitoring, and decision audit trails.

## Project status

Multigen is under active development. The repository contains a broad set of implemented components, integrations, notebooks, and experimental modules. Capability maturity varies across the project.

| Status | Meaning |
| --- | --- |
| **Core** | Stable framework primitive intended for regular use |
| **Integration** | Requires an optional service, provider, or infrastructure component |
| **Experimental** | Implemented for exploration and validation; APIs may change |
| **Planned** | Documented direction, not yet guaranteed as a production capability |

Before production adoption, validate the specific modules you intend to use, review their tests and examples, and perform workload-specific security, reliability, and performance testing.

## Capability map

### Orchestration and execution

- Declarative and Python-native workflow composition
- Sequential, parallel, graph, DAG, fan-out, MapReduce, race, and batch execution
- Explicit node dependencies and dependency-aware scheduling
- State-machine and probabilistic workflow experiments
- Runtime controls such as pause, resume, skip, reroute, prune, and inject
- Optional durable execution integrations

### Memory, knowledge, and state

- Short-term, episodic, working, semantic, and vector-memory components
- Session and state-management primitives
- Persistent checkpoints and snapshots
- Retrieval, chunking, hybrid search, citations, and feedback experiments
- Knowledge-graph, provenance, contradiction, confidence, and expiry components

### Planning and reasoning

- ReAct, plan-and-execute, hierarchical decomposition, and task queues
- Tree-of-Thoughts, Graph-of-Thoughts, and MCTS experiments
- Reflection, critique, consensus, and ensemble patterns
- Structured epistemic metadata and uncertainty propagation

### Safety, resilience, and governance

- Prompt-injection detection and output sanitisation
- PII detection and redaction
- Tool permissions, sandboxing, and network policies
- Retry policies, deadlines, fallbacks, and circuit breakers
- Human approval gates and review workflows
- API-key, JWT, data-classification, and compliance-oriented components

### Evaluation and operations

- Evaluators, benchmarks, LLM judges, latency, token, and quality metrics
- Regression suites and golden datasets
- A/B testing, canary rollout, rollback, and compatibility checks
- OpenTelemetry and Prometheus integrations
- Profiling, workflow snapshots, replay, and execution reports
- Streaming, partial results, scheduling, and event routing

## Installation

The installable SDK is located in `sdk/`.

```bash
cd sdk
python -m pip install -e .
```

Optional integrations can be installed with extras:

```bash
python -m pip install -e ".[openai]"
python -m pip install -e ".[temporal]"
python -m pip install -e ".[kafka]"
python -m pip install -e ".[all]"
```

The in-process SDK does not require Kafka, Temporal, MongoDB, or Docker for basic local use. Those systems are optional integrations for workloads that need distributed messaging, durable orchestration, or external persistence.

## Minimal example

```python
from multigen.agent import FunctionAgent
from multigen.chain import Chain

extract = FunctionAgent(
    name="extract",
    fn=lambda payload: {"text": payload["text"].strip()},
)

summarise = FunctionAgent(
    name="summarise",
    fn=lambda payload: {"summary": payload["text"][:120]},
)

workflow = Chain([extract, summarise])
result = workflow.run({"text": "Multigen coordinates governed agent workflows."})
print(result)
```

APIs may differ between modules as the project is consolidated. Refer to the examples, notebooks, module docstrings, and tests for the exact interface of the component you are using.

## Repository structure

| Path | Purpose |
| --- | --- |
| `sdk/multigen/` | In-process Python SDK and framework components |
| `flow_engine/` | Workflow-engine and execution components |
| `docs/` | Documentation and design material |
| `notebooks/` | Examples, experiments, and demonstrations |
| `tests/` | Automated validation where available |

## Design principles

1. **Control before autonomy.** Agent freedom should be bounded by contracts, permissions, and observable execution paths.
2. **Uncertainty should remain visible.** Confidence and evidence limitations should not disappear as outputs move through a workflow.
3. **Human review should be structural.** Approval is part of execution, not an informal process outside the system.
4. **Recovery should be designed in.** Retries, checkpoints, replay, and fallback behaviour belong in the runtime.
5. **Claims should be verifiable.** Production readiness depends on tests, examples, integration validation, and workload-specific evidence.

## Documentation

The documentation source is in [`docs/`](docs/), with MkDocs configuration in [`mkdocs.yml`](mkdocs.yml).

## Contributing

Issues and pull requests are welcome. For major architectural changes, open an issue describing the intended capability, maturity level, compatibility impact, and validation strategy.

## License

Multigen is released under the [MIT License](LICENSE).
