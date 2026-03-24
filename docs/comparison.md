# Framework Comparison

## Overview

The agentic framework landscape is crowded. This page gives an honest, detailed comparison of Multigen against the most popular alternatives. We focus on **technical capabilities** rather than marketing claims.

---

> ✅ = Full support &nbsp;&nbsp; ⚠️ = Partial/manual &nbsp;&nbsp; ❌ = Not supported

## Feature Matrix

### Orchestration & Execution

| Feature | Multigen | LangGraph | AutoGen | CrewAI | smolagents | OpenAI SDK |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Declarative workflow DSL | ✅ | ⚠️ | ❌ | ❌ | ❌ | ❌ |
| Durable execution (Temporal) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Real-time runtime control signals | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Dynamic agent creation + HITL approval | ✅ | ❌ | ⚠️ | ❌ | ❌ | ❌ |
| Graph (DAG) execution | ✅ | ✅ | ⚠️ | ❌ | ❌ | ❌ |
| MCMC probabilistic state machine | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 20+ coordination patterns | ✅ | ❌ | ⚠️ | ⚠️ | ❌ | ❌ |
| FanOut / MapReduce / Race / Batch | ✅ | ❌ | ⚠️ | ❌ | ❌ | ❌ |
| Parallel BFS (dependency-aware) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Partition-aware fan-out (multi-queue) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| No-LLM testing | ✅ | ⚠️ | ⚠️ | ⚠️ | ✅ | ❌ |
| Pure Python API (zero deps) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Multi-LLM support | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |

### Memory, State & Caching

| Feature | Multigen | LangGraph | AutoGen | CrewAI | smolagents | OpenAI SDK |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Short-term / episodic / working memory | ✅ | ⚠️ | ⚠️ | ⚠️ | ❌ | ❌ |
| Semantic memory (namespace + tags) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Vector memory (embedding recall) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Ebbinghaus forgetting curve | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| WorkingMemory → LLM prompt wiring | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Session management + middleware | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Reactive / computed state | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Multi-tier async caching (LRU+TTL) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| SQLite-backed durable persistence | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Planning & Reasoning

| Feature | Multigen | LangGraph | AutoGen | CrewAI | smolagents | OpenAI SDK |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Chain-of-Thought | ✅ | ❌ | ⚠️ | ❌ | ❌ | ❌ |
| Tree of Thoughts (ToT) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Graph of Thoughts (GoT) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| ReAct (Reason + Act loop) | ✅ | ⚠️ | ⚠️ | ❌ | ❌ | ❌ |
| StepBack + PlanAndExecute | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| MCTS-based planning (UCB1) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Hierarchical task decomposition | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| AutoGPT-style task queue | ✅ | ❌ | ⚠️ | ❌ | ❌ | ❌ |
| Hierarchical summarisation | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Epistemic transparency per node | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Safety & Resilience

| Feature | Multigen | LangGraph | AutoGen | CrewAI | smolagents | OpenAI SDK |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Injection detection (14 patterns) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Output sanitization | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| PII redaction (9 types, 3 modes) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Deadline management | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Workflow-level retry + exp. backoff | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Per-node circuit breaker | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Human-in-the-loop gates | ✅ | ✅ | ✅ | ✅ | ⚠️ | ✅ |
| Tool sandboxing + permissions | ✅ | ❌ | ⚠️ | ❌ | ❌ | ❌ |

### Optimization & Learning

| Feature | Multigen | LangGraph | AutoGen | CrewAI | smolagents | OpenAI SDK |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| Online prompt optimisation (bandit) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Few-shot library (scored, retrieval) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Agent specialisation by performance | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| EpisodicMemory → prompt feedback | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Continuous learner (RLHF-style) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Experience replay | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Adaptive prompts (auto few-shot) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Multi-model routing (cost/quality/latency) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |

### Observability, DevEx & Infrastructure

| Feature | Multigen | LangGraph | AutoGen | CrewAI | smolagents | OpenAI SDK |
| --- | :---: | :---: | :---: | :---: | :---: | :---: |
| OpenTelemetry + Prometheus | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Time-travel debugging (snapshot/replay) | ✅ | ⚠️ | ❌ | ❌ | ❌ | ⚠️ |
| Agent profiler + execution reports | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Token streaming + partial result bus | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Eval/measurement framework | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| A/B testing + canary rollout | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Workflow versioning + rollback | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Scheduling (cron, interval, triggers) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| MCP server (Claude/Cursor/Windsurf) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Real-time SSE streaming (node events) | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Agent2Agent (A2A) protocol | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |
| Visual simulator dashboard | ✅ | ⚠️ | ❌ | ❌ | ❌ | ❌ |
| Kafka-based distributed messaging | ✅ | ❌ | ❌ | ❌ | ❌ | ❌ |

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
- Safety layer: injection detection, PII redaction, output sanitization
- Online prompt optimisation via multi-armed bandit
- Workflow A/B testing, canary rollout, and one-line rollback
- MCTS-based planning, hierarchical task decomposition, AutoGPT queue
- Deadline management + workflow-level retry with exponential backoff
- Token streaming + partial result bus across parallel branches

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

---

## New in v0.6 — Unique Capabilities (Not in Any Other Framework)

### 6. MCTS-Based Planning

Monte Carlo Tree Search (UCB1) over your agent's action space. No other framework offers this out of the box.

```python
from multigen import MCTSPlanner

planner = MCTSPlanner(
    actions_fn=lambda s: ["search", "analyse", "report"],
    transition_fn=apply_action,
    simulate_fn=score_state,
    n_simulations=200,
)
best_action = await planner.plan(initial_state)
```

### 7. Online Prompt Optimisation (Multi-Armed Bandit)

Automatically explores and exploits prompt variants using epsilon-greedy or UCB1. Reward scores are accumulated from real workflow outcomes.

```python
from multigen import PromptBandit

bandit = PromptBandit([
    "Answer concisely: {q}",
    "Think step by step: {q}",
    "Be an expert: {q}",
])
variant = bandit.select()
# ... run workflow, get score ...
bandit.record(variant.id, reward=0.92)
print(bandit.best_variant().template)
```

### 8. Safety Layer (Injection + PII + Sanitization)

End-to-end safety pipeline combining 14 injection patterns, 9 PII types, and output sanitization in a single `SafetyGuard` call.

```python
from multigen import SafetyGuard

guard = SafetyGuard()
clean_text, report = guard.full_pass(untrusted_tool_output)
if report.blocked:
    raise ValueError(f"Unsafe input: {report.reason}")
```

### 9. Workflow A/B Testing + Canary Rollout

Production traffic splitting with automatic promotion and one-line rollback.

```python
from multigen import CanaryRollout

rollout = CanaryRollout(stable_agent, new_agent, initial_pct=0.05, target_pct=1.0)
result, decision = await rollout.call(ctx)
rollout.record_outcome(decision.agent_label, success=True)
rollout.maybe_promote()   # auto-promotes when success rate is high enough
```

### 10. Deadline Management + Workflow-Level Retry

Budget-aware execution: every workflow run gets a time budget; exceeding it triggers escalation. Retry loops respect the remaining budget.

```python
from multigen import WorkflowRetry, RetryPolicy, Deadline

wr = WorkflowRetry(
    policy=RetryPolicy(max_retries=3, base_delay=2.0),
    deadline=Deadline(budget_s=120),
    on_breach=lambda: notify_oncall(),
)
result = await wr.run(my_workflow, ctx)
```

### 11. Token Streaming + Partial Result Bus

Real-time token-by-token output from parallel agent branches, with a pub/sub bus for live UI updates — all in pure Python.

```python
from multigen import ParallelStreamer, StreamingAgent, PartialResultBus

bus = PartialResultBus()
bus.subscribe("run-1", lambda pr: update_ui(pr.content, pr.progress))

streamer = ParallelStreamer([agent_a, agent_b, agent_c])
async for token in streamer.stream(ctx):
    print(f"[{token.source}] {token.text}", end="", flush=True)
```

### 12. Hierarchical Task Decomposition + AutoGPT Queue

Runtime goal → sub-goal decomposition with concurrent leaf execution, and an agent-driven self-directed task queue — no external orchestrator needed.

```python
from multigen import HierarchicalDecomposer, AutoGPTQueue

# Decompose
decomp = HierarchicalDecomposer(llm_decompose_fn, max_depth=3)
tree = await decomp.decompose("Build a competitor analysis report")

# Self-directed queue
queue = AutoGPTQueue(agent_fn, max_steps=25)
result = await queue.run("Research and summarise AI trends for Q1 2026")
```
