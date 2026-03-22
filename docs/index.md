# Multigen — Advanced Agentic Framework

<div class="hero" markdown>

**Build production-grade multi-agent systems with composable primitives, parallel execution, MCMC state machines, and built-in resilience.**

[Get Started](getting-started/installation.md){ .md-button .md-button--primary }
[View on GitHub](https://github.com/Subhagatoadak/Multigen){ .md-button }

</div>

---

## What is Multigen?

Multigen is a **Python-native multi-agent orchestration framework** built around the `agentic_codex` component library. It provides everything you need to build reliable, observable, and scalable agentic pipelines — from a single LLM call to enterprise workflows spanning dozens of specialised agents.

Unlike low-level SDKs that force you to manage agent lifecycles manually, Multigen gives you:

- **High-level coordination patterns** (Assembly, MapReduce, Swarm, Graph) that compose like building blocks
- **A stateful runtime** that connects your agents to the Temporal workflow engine, event buses, and the visual Agentic Simulator
- **First-class observability** via OpenTelemetry tracing, Prometheus metrics, and structured logging
- **Safety primitives** baked in — circuit breakers, guardrails, two-person rules, and human-in-the-loop gates

---

## 6 Key Differentiators

=== "1. Composable by Design"

    Every primitive — `Agent`, `Chain`, `Parallel`, `Graph`, `StateMachine` — implements the same `run(goal, inputs)` interface. You can nest them arbitrarily: a `Graph` node can be a `MapReduce`, which itself contains `SwarmCoordinator` workers.

    ```python
    from agentic_codex import AgentBuilder, Context
    from agentic_codex.patterns import MapReduceCoordinator, GraphRunner, GraphNodeSpec

    # A graph whose nodes are themselves coordinators
    pipeline = GraphRunner(nodes=[
        GraphNodeSpec(id="analyse", coordinator=MapReduceCoordinator(mappers=[...], reducer=...)),
        GraphNodeSpec(id="report",  coordinator=assembly_pipeline),
    ])
    ```

=== "2. MCMC State Machines"

    Unique to Multigen: iterative refinement loops modelled as Markov chains. Your workflow can **sample multiple execution paths**, compute consensus, and only commit when confidence crosses your threshold.

    ```python
    # Run 5 independent chains, compute ensemble agreement
    results = [run_pipeline(inputs) for _ in range(5)]
    consensus = max(set(r['decision'] for r in results), key=lambda d: sum(1 for r in results if r['decision']==d))
    agreement = sum(1 for r in results if r['decision']==consensus) / 5
    ```

=== "3. 20+ Coordination Patterns"

    A curated catalogue of battle-tested multi-agent patterns:
    `AssemblyCoordinator`, `SwarmCoordinator`, `MapReduceCoordinator`, `DebateCoordinator`, `MinistryOfExperts`, `GuardrailSandwich`, `TwoPersonRuleCoordinator`, `GuildRouter`, `HubAndSpokeCoordinator`, and more.

=== "4. Resilience First"

    `GuardrailSandwich` wraps any agent with pre/post validation. Circuit breakers auto-open on failure, degrade confidence scores gracefully, and close when the dependency recovers. Retry policies with exponential backoff are a one-liner.

=== "5. Visual Simulator"

    The bundled `agentic-simulator` provides a real-time dashboard: visualise agent networks, replay execution traces, run stress tests, and compare coordination strategies — all without touching production.

=== "6. Zero Lock-in LLM Layer"

    `FunctionAdapter` lets you build and test complete pipelines without any API key. When ready, swap to `EnvOpenAIAdapter`, your own adapter, or any `LLMAdapter` implementation. The coordination code is identical.

---

## Quick Install

```bash
pip install agentic-codex
# For all optional extras:
pip install "agentic-codex[openai,temporal,kafka]"
```

## 60-Second Example

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.schemas import AgentStep, Message
from agentic_codex.patterns import MapReduceCoordinator

# Define a simple summariser agent (no LLM needed for this example)
def summarise(ctx: Context) -> AgentStep:
    text = ctx.scratch.get("current_shard", "")
    summary = f"Summary of: {text[:50]}..."
    return AgentStep(out_messages=[Message(role="assistant", content=summary)], state_updates={}, stop=False)

def combine(ctx: Context) -> AgentStep:
    parts = ctx.scratch.get("mapper_outputs", [])
    combined = " | ".join(parts)
    return AgentStep(out_messages=[Message(role="assistant", content=combined)], state_updates={"result": combined}, stop=True)

summariser = AgentBuilder("Summariser", "summariser").with_step(summarise).build()
combiner   = AgentBuilder("Combiner",   "combiner").with_step(combine).build()

pipeline = MapReduceCoordinator(mappers=[summariser, summariser], reducer=combiner)

result = pipeline.run(
    goal="Summarise these documents",
    inputs={"shards": ["Document A about ML", "Document B about Python"]}
)
print(result.messages[-1].content)
```

---

## Framework Comparison

| Feature | Multigen | LangGraph | AutoGen | CrewAI | smolagents |
|---------|:--------:|:---------:|:-------:|:------:|:----------:|
| No-LLM testing | ✅ | ⚠️ | ⚠️ | ⚠️ | ✅ |
| MCMC/ensemble patterns | ✅ | ❌ | ❌ | ❌ | ❌ |
| 20+ coord patterns | ✅ | ❌ | ⚠️ | ⚠️ | ❌ |
| Visual simulator | ✅ | ❌ | ❌ | ❌ | ❌ |
| Temporal integration | ✅ | ❌ | ❌ | ❌ | ❌ |
| Circuit breakers | ✅ | ❌ | ❌ | ❌ | ❌ |
| MapReduce pattern | ✅ | ❌ | ⚠️ | ❌ | ❌ |
| Pure Python API | ✅ | ✅ | ✅ | ✅ | ✅ |

> See the [full comparison](comparison.md) for a detailed breakdown.

---

## Explore the Documentation

<div class="grid cards" markdown>

-   :material-rocket-launch: **Getting Started**

    Install, configure, and run your first multi-agent workflow in under 5 minutes.

    [Installation →](getting-started/installation.md)

-   :material-school: **Tutorials**

    Step-by-step guides for every major framework concept, from basic chains to MCMC state machines.

    [Agents →](tutorials/agents.md)

-   :material-briefcase: **Use Cases**

    Complete production-ready examples: credit risk, research pipelines, AIOps, clinical decision support, and legal analysis.

    [Credit Risk →](use_cases/credit_risk.md)

-   :material-code-tags: **API Reference**

    Full API documentation for every class, method, and parameter.

    [Agent API →](api/agents.md)

</div>
