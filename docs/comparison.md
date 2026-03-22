# Framework Comparison

## Overview

The agentic framework landscape is crowded. This page gives an honest, detailed comparison of Multigen against the most popular alternatives. We focus on **technical capabilities** rather than marketing claims.

---

## Feature Matrix

| Feature | Multigen | LangGraph | AutoGen | CrewAI | smolagents | OpenAI Agents SDK |
|---------|:--------:|:---------:|:-------:|:------:|:----------:|:-----------------:|
| **No-LLM testing** | ✅ | ⚠️ | ⚠️ | ⚠️ | ✅ | ❌ |
| **MCMC/ensemble patterns** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **MapReduce pattern** | ✅ | ❌ | ⚠️ | ❌ | ❌ | ❌ |
| **20+ coord patterns** | ✅ | ❌ | ⚠️ | ⚠️ | ❌ | ❌ |
| **Visual simulator** | ✅ | ⚠️ | ❌ | ❌ | ❌ | ❌ |
| **Circuit breakers** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Temporal integration** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Pure Python API** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **OpenAI support** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Multi-LLM support** | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| **Memory systems** | ✅ (4 types) | ⚠️ | ⚠️ | ⚠️ | ❌ | ❌ |
| **Tool adapters** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Structured output** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Streaming** | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **HITL support** | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| **Replay/debugging** | ✅ | ⚠️ | ❌ | ❌ | ❌ | ⚠️ |
| **Prometheus metrics** | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Graph (DAG) execution** | ✅ | ✅ | ⚠️ | ❌ | ❌ | ❌ |
| **Pattern catalogue** | ✅ (20+) | ❌ | ❌ | ❌ | ❌ | ❌ |

✅ = Full support | ⚠️ = Partial/manual | ❌ = Not supported

---

## Detailed Comparison

### vs LangGraph

**LangGraph** is a graph-based execution framework from LangChain. It's mature, well-documented, and tightly integrated with the LangChain ecosystem.

**Where Multigen wins:**
- No LangChain dependency — works with any LLM or none at all
- MCMC state machines with ensemble sampling (unique to Multigen)
- 20+ coordination patterns vs LangGraph's focus on graph primitives
- Built-in circuit breakers and confidence scoring
- Visual simulator with real-time event streaming
- Temporal integration for durable workflow execution
- Prometheus metrics out of the box

**Where LangGraph wins:**
- Larger community and more integrations (LangSmith, LangServe)
- Battle-tested at scale by many production users
- Richer LangChain ecosystem (hundreds of tool integrations)
- More documentation and tutorials

**When to choose Multigen:** You need MCMC ensemble patterns, don't want to lock into LangChain, or need durable workflows via Temporal.

**When to choose LangGraph:** Your team already uses LangChain, you need LangSmith observability, or you want the largest community support.

---

### vs AutoGen (Microsoft)

**AutoGen** focuses on conversational multi-agent systems where agents exchange messages in a conversation loop.

**Where Multigen wins:**
- Structured coordination patterns (MapReduce, Assembly, Swarm) vs AutoGen's conversational paradigm
- No-LLM-required testing via `FunctionAdapter`
- Better support for pipeline-style workflows (sequential stages, DAGs)
- Confidence scoring and uncertainty quantification built in
- Visual simulator for debugging

**Where AutoGen wins:**
- Natural fit for conversational/debate-style agent patterns
- Strong Microsoft backing and enterprise support
- `GroupChat` pattern is richer than Multigen's `DebateCoordinator`
- Better documentation for conversational use cases

**When to choose Multigen:** Your problem is a structured pipeline (ETL, risk assessment, research synthesis) rather than a conversational multi-agent system.

**When to choose AutoGen:** You're building conversational agents, coding assistants, or debate-style reasoning systems.

---

### vs CrewAI

**CrewAI** provides a high-level abstraction where you define "crews" of agents with roles, goals, and tasks.

**Where Multigen wins:**
- Lower-level control — you define exactly what each agent does
- MapReduce and parallel execution patterns (CrewAI is sequential by default)
- Circuit breakers and resilience patterns
- MCMC ensemble for uncertainty quantification
- Temporal integration for durability
- No mandatory role/goal ontology — use whatever names make sense

**Where CrewAI wins:**
- Simpler high-level API for quick prototyping
- Built-in human feedback and delegation mechanisms
- Growing community with many examples

**When to choose Multigen:** You need fine-grained control over execution, parallel processing, or resilience patterns.

**When to choose CrewAI:** You want the quickest path to a working multi-agent system and don't need parallel execution or durability.

---

### vs smolagents (HuggingFace)

**smolagents** is a lightweight, code-first agent framework from HuggingFace designed for simplicity.

**Where Multigen wins:**
- Coordination patterns (MapReduce, Assembly, Swarm, Graph)
- MCMC state machines
- Memory systems (4 types vs smolagents' minimal memory)
- Circuit breakers and resilience
- Visual simulator

**Where smolagents wins:**
- Extremely simple API — minimal boilerplate
- Strong HuggingFace model integration
- Excellent for code execution agents
- Very lightweight (minimal dependencies)

**When to choose Multigen:** You need structured multi-agent coordination with persistence and observability.

**When to choose smolagents:** You want the simplest possible agent wrapper around HuggingFace models.

---

### vs OpenAI Agents SDK

**OpenAI Agents SDK** (formerly Swarm) is OpenAI's official framework.

**Where Multigen wins:**
- LLM-agnostic (works with any LLM or none)
- MapReduce, Assembly, and 18+ other coordination patterns
- MCMC ensemble patterns
- Circuit breakers and resilience
- Memory systems
- Temporal integration
- Visual simulator

**Where OpenAI Agents SDK wins:**
- Native OpenAI integration (no adapter layer)
- Handoff mechanism is elegant for conversational routing
- Strong OpenAI ecosystem integration (Assistants API, function calling)

**When to choose Multigen:** You're not locked into OpenAI, need structured pipelines, or need durable execution.

**When to choose OpenAI Agents SDK:** You're building purely on OpenAI and want the simplest integration.

---

## Unique Multigen Capabilities

These features are not available (or not equivalent) in any other framework:

### 1. MCMC Ensemble Patterns

Run the same pipeline N times with slight perturbation and compute consensus. Measures epistemic uncertainty in your pipeline's outputs.

```python
# Run 5 chains, compute agreement
ensemble = run_ensemble(pipeline_fn, inputs, n_chains=5)
print(f"Agreement: {ensemble['agreement_pct']:.0%}")
print(f"Consensus: {ensemble['consensus_decision']}")
```

### 2. Agentic Simulator

A live visual dashboard that shows:
- Agent interaction network in real time
- Execution trace replay
- Stress testing with configurable load
- KPI dashboard for business metrics

### 3. Temporal Integration

Your workflows survive process restarts, network failures, and service outages. Every agent step is checkpointed. Failed workflows auto-retry from the last checkpoint.

### 4. GuardrailSandwich with Confidence Scoring

```python
# Every agent outputs a confidence score
# Pipeline proceeds only when confidence meets threshold
guarded = GuardrailSandwich(prefilter=validator, primary=agent, postfilter=checker)
# Automatically short-circuits when prefilter sets stop=True
```

### 5. Pattern Catalogue (20+ Patterns)

A curated, tested library of coordination patterns with documented trade-offs, covering everything from simple chains to market-based task allocation.
