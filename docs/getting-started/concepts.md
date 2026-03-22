# Core Concepts

This page explains the mental model behind Multigen. Understanding these concepts will help you choose the right pattern for any problem and debug issues when things go wrong.

---

## The Three Layers

```
┌─────────────────────────────────────────────────────────────────┐
│  LAYER 3 — Coordination Patterns                                │
│  AssemblyCoordinator, MapReduceCoordinator, SwarmCoordinator,  │
│  GraphRunner, StateMachine loops, GuardrailSandwich ...         │
├─────────────────────────────────────────────────────────────────┤
│  LAYER 2 — Agent Primitives                                     │
│  Agent, Context, AgentStep, AgentBuilder                        │
│  Capabilities: LLM, Memory, Tools, Skills, MessageBus           │
├─────────────────────────────────────────────────────────────────┤
│  LAYER 1 — Runtime & Infrastructure                             │
│  Temporal workflows, OpenTelemetry, Prometheus, Agentic         │
│  Simulator, message buses (in-memory / Kafka)                   │
└─────────────────────────────────────────────────────────────────┘
```

You can operate at any layer. Most users start at Layer 2 and add Layer 3 patterns as workflows grow more complex.

---

## Agent

The **atom** of Multigen. An agent has:

- A `name` (string identifier)
- A `role` (semantic label — "analyst", "summariser", "validator", etc.)
- A `step` function that takes a `Context` and returns an `AgentStep`
- Optional `Capabilities` (LLM adapter, memory store, tool set, skill registry)

```
Agent
  ├── name: str
  ├── role: str
  ├── step: (Context) → AgentStep
  └── capabilities: CapabilityRegistry
        ├── LLMCapability       (optional)
        ├── MemoryCapability    (optional)
        ├── ToolCapability      (optional)
        └── ResourceCapability (optional)
```

Agents are **stateless by design**. All mutable state lives in `Context`. This makes agents safe to reuse across pipeline runs and easy to test in isolation.

### Building Agents

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.schemas import AgentStep, Message

def my_step(ctx: Context) -> AgentStep:
    # Read from context
    data = ctx.scratch.get("data", "")
    # Optionally use LLM (if capability attached)
    # response = ctx.llm.complete([Message(role="user", content=data)])
    # Write to context
    return AgentStep(
        out_messages=[Message(role="assistant", content=f"processed: {data}")],
        state_updates={"processed": True},
        stop=False,
    )

agent = (
    AgentBuilder("my-agent", "processor")
    .with_step(my_step)
    # .with_llm(EnvOpenAIAdapter(model="gpt-4o"))    # add when ready
    # .with_memory(EpisodicMemory())                  # add for stateful agents
    .build()
)
```

---

## Context

The `Context` object is the **shared state bus** passed to every agent at every step. Think of it as the agent's working memory for one pipeline run.

```
Context
  ├── goal: str                  — human-readable description of the task
  ├── scratch: dict              — arbitrary key-value pairs (primary data channel)
  ├── memory: dict               — accumulated long-term observations
  ├── inbox: List[Message]       — messages received from other agents via bus
  ├── tools: dict                — tool adapters registered for this run
  ├── stores: dict               — data stores (vector DBs, SQLite, etc.)
  ├── policies: dict             — rate limits, safety policies
  ├── llm: LLMAdapter | None     — LLM injected by capability
  └── components: dict           — custom capabilities (message bus, etc.)
```

**Convention**: put your pipeline data in `ctx.scratch`. Put long-lived facts in `ctx.memory`. The framework reserves the other fields for system use.

```python
ctx = Context(goal="Analyse quarterly report")
ctx.scratch["quarter"] = "Q4-2025"
ctx.scratch["documents"] = [...]

# After running agent:
result = ctx.scratch.get("analysis_result", {})
```

---

## AgentStep

The **return value** from every agent step function.

```python
from agentic_codex.core.schemas import AgentStep, Message

return AgentStep(
    out_messages=[
        Message(role="assistant", content="analysis complete"),
        Message(role="tool",      content='{"confidence": 0.87}'),
    ],
    state_updates={
        "analysis_done": True,
        "confidence_score": 0.87,
    },
    stop=False,   # True = signal to coordinator that this agent is done
)
```

- `out_messages`: all messages emitted in this step (collected by coordinators)
- `state_updates`: dict merged into `ctx.scratch` by the framework
- `stop`: if `True`, the agent's kernel will not call the step function again

---

## Coordination Patterns

Patterns are **composable wrappers** around one or more agents. They all implement:

```python
coordinator.run(goal: str, inputs: dict) → CoordinatorResult
```

### AssemblyCoordinator (Chain)

Executes agents sequentially in stages. Each stage passes its output to the next.

```
Stage1 → Stage2 → Stage3 → ... → Result
```

```python
from agentic_codex.patterns import AssemblyCoordinator, Stage

pipeline = AssemblyCoordinator(stages=[
    Stage(agent=agent_a, name="extract"),
    Stage(agent=agent_b, name="transform"),
    Stage(agent=agent_c, name="load"),
])
```

### MapReduceCoordinator

Runs N mapper agents against a set of shards, then the reducer combines the results.

```
         ┌─ mapper_0(shard_0) ─┐
inputs → ┼─ mapper_1(shard_1) ─┼─ reducer → result
         └─ mapper_2(shard_2) ─┘
```

```python
from agentic_codex.patterns import MapReduceCoordinator

pipeline = MapReduceCoordinator(
    mappers=[extractor, extractor, extractor],   # can reuse same agent
    reducer=aggregator,
)
result = pipeline.run(goal="Process", inputs={"shards": ["a", "b", "c"]})
```

### GraphRunner (DAG)

Executes coordinators as nodes in a directed acyclic graph. Nodes with satisfied dependencies run in parallel.

```
   A ──┬──► C
   B ──┘    │
            ▼
            D
```

```python
from agentic_codex.patterns import GraphRunner, GraphNodeSpec

graph = GraphRunner(nodes=[
    GraphNodeSpec(id="A", coordinator=coord_a, deps=[]),
    GraphNodeSpec(id="B", coordinator=coord_b, deps=[]),
    GraphNodeSpec(id="C", coordinator=coord_c, deps=["A", "B"]),
    GraphNodeSpec(id="D", coordinator=coord_d, deps=["C"]),
])
```

### SwarmCoordinator

Runs N explorer agents in parallel, then aggregates their outputs.

```
         ┌─ explorer_0 ─┐
context → ┼─ explorer_1 ─┼─ aggregator → consensus
         └─ explorer_2 ─┘
```

### GuardrailSandwich

Wraps any agent with a pre-filter (input validation) and post-filter (output validation). Acts as a circuit breaker for external services.

```
prefilter → primary_agent → postfilter
  (if prefilter stops → short-circuit, skip primary)
```

### Other Patterns

| Pattern | Description |
|---------|-------------|
| `DebateCoordinator` | Proponent + opponent argue, judge decides |
| `MinistryOfExperts` | Planner → domain experts → cabinet chair |
| `GuildRouter` | Route to specialised sub-agents by category |
| `HubAndSpokeCoordinator` | Central hub coordinates peripheral spoke agents |
| `TwoPersonRuleCoordinator` | Require two independent approvals |
| `PolicyLatticeCoordinator` | Hierarchical policy enforcement |
| `MeshCoordinator` | All-to-all peer communication |
| `ContractNetCoordinator` | Auction-based task allocation |

---

## StateMachine

Multigen's MCMC-inspired state machine is a **custom iteration pattern** — it's not a built-in class but a composable loop that you implement using agents, contexts, and Python control flow:

```python
from enum import Enum

class State(Enum):
    ASSESS = "assess"
    REVIEW = "review"
    DONE   = "done"

state = State.ASSESS
max_iter = 5
for i in range(max_iter):
    agent.run(ctx)
    confidence = ctx.scratch.get("confidence", 0)
    if confidence >= 0.85:
        state = State.DONE
        break
    state = State.REVIEW
```

For **ensemble / MCMC sampling**, run the same pipeline N times with slight perturbations and compute consensus:

```python
chains = [run_pipeline(inputs) for _ in range(5)]
decisions = [c["decision"] for c in chains]
consensus = max(set(decisions), key=decisions.count)
agreement = decisions.count(consensus) / len(chains)
```

---

## Message Bus

The `MessageBus` enables **asynchronous inter-agent communication** — agents publish to topics and subscribe to receive messages without direct coupling.

```
AgentA.publish("analysis.done", {"score": 0.87})
                      │
              MessageBus (in-memory or Kafka)
                      │
AgentB.subscribe("analysis.done") → callback triggered
```

```python
from agentic_codex import MessageBus, MessageRecord

bus = MessageBus()

# Subscribe
def on_analysis(record: MessageRecord):
    print(f"Received: {record.payload}")

bus.subscribe("analysis.done", on_analysis)

# Publish (from another agent or coordinator)
bus.publish("analysis.done", {"score": 0.87, "from": "AnalysisAgent"})
```

---

## Memory Systems

Multigen provides four memory types, each suited to different use cases:

```
┌──────────────────────────────────────────────────────────────────┐
│  EpisodicMemory     — ordered episode log (what happened)        │
│  SemanticMemory     — vector similarity search (what it means)   │
│  GraphMemory        — entity relationship graph (how things link) │
│  PersistentMemory   — SQLite-backed durable storage              │
└──────────────────────────────────────────────────────────────────┘
```

```python
from agentic_codex import EpisodicMemory, MemoryCapability

memory = EpisodicMemory()
agent  = (
    AgentBuilder("agent", "analyst")
    .with_memory(memory)
    .with_step(step_fn)
    .build()
)
# Inside step_fn:
# ctx.components["memory"].add({"event": "analysis_completed", "score": 0.87})
```

---

## Runtime

The **Runtime** connects your agent pipelines to the Temporal workflow engine and the Agentic Simulator. It provides:

- Durable execution — workflows survive process restarts
- Event streaming — push events to the simulator in real time
- Activity timeouts and retry policies
- Workflow state inspection

See the [Runtime tutorial](../tutorials/runtime.md) for setup instructions.

---

## Design Principles

1. **Context as first-class citizen** — state lives in `Context`, not in agents. Agents are pure functions over context.
2. **Composability over configuration** — combine patterns programmatically rather than configuring mega-agents.
3. **Test without LLMs** — use `FunctionAdapter` for all coordination logic tests. Wire real LLMs at integration test time.
4. **Graceful degradation** — circuit breakers and confidence scores let the pipeline handle partial failures without crashing.
5. **Observability by default** — every coordinator emits structured events. The simulator can replay any execution.
