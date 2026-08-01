# Multigen

<div class="hero" markdown>

**A governed execution and control plane for complex multi-agent systems.**

[Get Started](getting-started/installation.md){ .md-button .md-button--primary }
[View on GitHub](https://github.com/Subhagatoadak/Multigen){ .md-button }

</div>

---

## What is Multigen?

Multigen is a Python-native framework for composing, executing, observing, and governing multi-agent workflows. It brings orchestration, runtime control, epistemic transparency, evaluation, resilience, and human review into one system.

The framework is intended for complex workflows where reliability, auditability, and controlled autonomy are important.

## Core capabilities

### Composable orchestration

Build sequential, parallel, graph, DAG, fan-out, MapReduce, race, batch, and state-machine workflows from reusable primitives.

### Governed execution

Use approval gates, permissions, safety controls, deadlines, retries, fallbacks, and circuit breakers to bound agent behaviour.

### Epistemic transparency

Carry confidence, assumptions, limitations, evidence quality, known unknowns, and propagated uncertainty with workflow outputs.

### Runtime control and recovery

Design workflows that can be paused, resumed, replayed, rerouted, recovered, and inspected through snapshots and execution history.

### Evaluation and observability

Measure quality, latency, cost, tokens, regressions, and operational behaviour through evaluators, traces, metrics, profiling, and reports.

### Optional infrastructure integrations

Basic SDK use is in-process. Temporal, Kafka, MongoDB, OpenTelemetry, Prometheus, and model-provider integrations are optional and should be enabled according to workload requirements.

---

## Capability maturity

Multigen is under active development. Not every module has the same maturity level.

| Status | Meaning |
| --- | --- |
| **Core** | Stable framework primitive intended for regular use |
| **Integration** | Requires an optional provider or infrastructure service |
| **Experimental** | Implemented for exploration; APIs may change |
| **Planned** | Architectural direction that is not yet a guaranteed capability |

Production adoption should be based on the tests, examples, integration validation, and performance evidence for the exact modules being used.

---

## Install the SDK

```bash
cd sdk
python -m pip install -e .
```

Install optional integrations as needed:

```bash
python -m pip install -e ".[openai]"
python -m pip install -e ".[temporal]"
python -m pip install -e ".[kafka]"
python -m pip install -e ".[all]"
```

---

## Explore the documentation

<div class="grid cards" markdown>

-   :material-rocket-launch: **Getting Started**

    Install the SDK and run your first workflow.

    [Installation →](getting-started/installation.md)

-   :material-school: **Tutorials**

    Learn the orchestration, memory, reasoning, safety, and evaluation primitives.

    [Tutorials →](tutorials/agents.md)

-   :material-briefcase: **Use Cases**

    Review applied workflow examples and domain demonstrations.

    [Use Cases →](use_cases/credit_risk.md)

-   :material-code-tags: **API Reference**

    Inspect classes, methods, inputs, outputs, and configuration.

    [API Reference →](api/agents.md)

</div>
