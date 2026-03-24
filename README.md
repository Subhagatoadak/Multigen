# Multigen

**Enterprise-grade autonomous multi-agent orchestration framework with durable execution, epistemic transparency, human-in-the-loop governance, and a full in-process Python SDK — zero external dependencies required.**

[![CI](https://github.com/Subhagatoadak/Multigen/actions/workflows/ci.yml/badge.svg)](https://github.com/Subhagatoadak/Multigen/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.6.0--dev-orange)](CHANGELOG.md)
[![Temporal](https://img.shields.io/badge/temporal-1.11-blueviolet)](https://temporal.io)
[![OpenTelemetry](https://img.shields.io/badge/otel-enabled-brightgreen)](https://opentelemetry.io)

---

## What is Multigen?

Multigen is a production-ready orchestration framework for building complex AI agent pipelines. It goes far beyond simple agent chaining — it provides **durable execution**, **real-time runtime control**, **epistemic transparency**, and **autonomous self-expansion** with human governance.

Built on top of [Temporal.io](https://temporal.io) for workflow durability, Apache Kafka for distributed messaging, and MongoDB for state persistence, Multigen is designed to run in enterprise environments where reliability, auditability, and human oversight are non-negotiable.

---

## Why Multigen?

| Capability | LangGraph | CrewAI | AutoGen | LlamaIndex Workflows | **Multigen** |
|---|---|---|---|---|---|
| Declarative workflow DSL | Partial | ✗ | ✗ | ✗ | ✅ |
| Durable execution (crash recovery) | ✗ | ✗ | ✗ | ✗ | ✅ Temporal |
| Real-time runtime control signals | ✗ | ✗ | ✗ | ✗ | ✅ |
| Dynamic agent creation + approval | ✗ | ✗ | Partial | ✗ | ✅ |
| Epistemic transparency per node | ✗ | ✗ | ✗ | ✗ | ✅ |
| Circuit breakers per node | ✗ | ✗ | ✗ | ✗ | ✅ |
| Reflection / self-critique loops | Partial | ✗ | Partial | ✗ | ✅ |
| Fan-out consensus reasoning | ✗ | ✗ | Partial | ✗ | ✅ |
| Human-in-the-loop approval gates | ✗ | ✗ | ✗ | ✗ | ✅ |
| Kafka-based distributed messaging | ✗ | ✗ | ✗ | ✗ | ✅ |
| OpenTelemetry + Prometheus | ✗ | ✗ | ✗ | ✗ | ✅ |
| MCP server (Claude/Cursor/Windsurf) | ✗ | ✗ | ✗ | ✗ | ✅ |
| Self-registering agent decorator | ✗ | ✗ | ✗ | ✗ | ✅ |
| Parallel BFS execution (dependency-aware) | ✗ | ✗ | ✗ | ✗ | ✅ |
| Explicit `depends_on` node dependencies | ✗ | ✗ | ✗ | ✗ | ✅ |
| Partition-aware fan-out (multi-queue) | ✗ | ✗ | ✗ | ✗ | ✅ |
| Real-time SSE streaming (node completion) | ✗ | ✗ | ✗ | ✗ | ✅ |
| Agent2Agent (A2A) protocol support | ✗ | ✗ | ✗ | ✗ | ✅ |
| Cross-worker agent hydration (MongoDB) | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Local runtime — zero external deps** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **4-tier memory system** | ✗ | ✗ | Partial | Partial | ✅ |
| **Multi-tier async caching** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Operator step composition (`>>` `\|` `&`)** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Hierarchical agent structures** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Priority pub/sub + scatter-gather bus** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Session management + reactive state** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Trait-based inheritance + overloading** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Agent profiler + rate limiter + batch exec** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Time-travel debugging (snapshot/replay)** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Vector memory + Ebbinghaus forgetting curve** | ✗ | ✗ | ✗ | Partial | ✅ |
| **SQLite-backed durable persistence** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Eval/measurement framework** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Multi-model routing (cost/quality/latency)** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Tool registry + sandboxing + permissions** | ✗ | ✗ | Partial | ✗ | ✅ |
| **Planning: ToT / GoT / MCTS / ReAct** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Workflow versioning + A/B testing + canary** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Scheduling: cron, interval, event triggers** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Continuous learning + few-shot library** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Injection detection + PII redaction** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Deadline management + workflow-level retry** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Token streaming + partial result bus** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Online prompt optimisation (bandit)** | ✗ | ✗ | ✗ | ✗ | ✅ |
| **Hierarchical task decomp + AutoGPT queue** | ✗ | ✗ | Partial | ✗ | ✅ |
| **WorkingMemory → LLM prompt wiring** | ✗ | ✗ | ✗ | ✗ | ✅ |

---

## Core Pillars

### 1. Durable Execution
Every workflow step is persisted in Temporal's event history. If the worker crashes mid-execution, the workflow resumes exactly where it left off — no lost work, no double execution, no manual recovery.

### 2. Real-Time Runtime Control
Running workflows can be interrupted, redirected, and extended without restarting:
- **Interrupt / Resume** — pause at any node boundary
- **Inject Node** — append a new agent node to a running workflow
- **Jump To** — reprioritise any node to the front of the execution queue
- **Skip Node** — silently drop a node from the pipeline
- **Reroute** — add a new edge between nodes at runtime
- **Prune Branch** — cancel a node and all its downstream descendants
- **Fan-Out** — spawn N parallel nodes and merge via consensus

### 3. Dynamic Agent Self-Healing
When a workflow encounters an agent that doesn't exist yet, it doesn't crash. Instead:
1. An LLM generates a detailed `AgentSpec` describing what the agent should do
2. The workflow **pauses** and presents the spec for human review
3. The human approves, edits, or rejects it
4. On approval, a `BlueprintAgent` is created and registered live
5. Execution resumes; the agent is deregistered when the workflow ends

### 4. Epistemic Transparency
Every node output carries a structured `epistemic` envelope:
```json
{
  "confidence": 0.82,
  "reasoning": "Analysis based on 3-year revenue history",
  "uncertainty_sources": ["no audited financials", "stale comparables"],
  "assumptions": ["8% revenue growth based on sector median"],
  "known_limitations": ["cannot assess off-balance-sheet liabilities"],
  "known_unknowns": ["regulatory filing status unknown"],
  "evidence_quality": "medium",
  "data_completeness": 0.72,
  "propagated_uncertainty": 0.15,
  "flags": ["needs_human_review"]
}
```
Uncertainty **propagates through the graph** — if upstream nodes have low confidence, downstream nodes inherit that uncertainty. The full transparency report is queryable at any time.

### 5. Reasoning Quality Mechanisms
- **Reflection loops** — automatically inject a critic agent when confidence falls below threshold
- **Fan-out consensus** — run N competing agents in parallel, select best via `highest_confidence`, `aggregate`, `majority_vote`, or `first_success`
- **Circuit breakers** — per-node failure tracking with automatic fallback routing and dead-letter capture

### 6. Local Python SDK — Zero Dependencies

The `multigen` Python package ships a complete in-process runtime. No Kafka, Temporal, MongoDB, or Docker required. **152 public exports** across 15 modules:

| Module | Key classes |
|--------|------------|
| `chain`, `parallel`, `graph`, `state_machine` | `Chain`, `Parallel`, `Graph`, `StateMachine` |
| `memory` | `ShortTermMemory`, `EpisodicMemory`, `WorkingMemory`, `SemanticMemory`, `MemoryManager` |
| `advanced_memory` | `VectorMemory`, `ForgettingCurve`, `MemoryIndex`, `ContextualMemory`, `PersistentMemory` |
| `cache` | `LRUCache`, `TTLCache`, `AsyncCache`, `MultiTierCache`, `@cached` |
| `messaging` | `AdvancedMessageBus`, priority pub/sub, request/reply, scatter-gather, DLQ |
| `compose` | `Step` with `>>`, `\|`, `&` operators; `BranchStep`, `LoopStep` |
| `hierarchy` | `AgentHierarchy`, `AgentGroup`, `TypedStep`, `HierarchicalPipeline` |
| `session` | `SessionManager`, `SessionContext`, `SessionMiddleware` |
| `state_init` | `StateSchema`, `ReactiveState`, `ComputedState`, `StateValidator` |
| `inheritance` | `InheritableAgent`, `Trait`, `build_agent`, `@overload`, `MultiMethod` |
| `polymorphic` | `PolymorphicAgent`, `ShapeRegistry`, `TypeAdapter`, `DynamicAgent` |
| `performance` | `AgentProfiler`, `BatchExecutor`, `ConnectionPool`, `RateLimiter` |
| `debugger` + `snapshot` | `WorkflowDebugger`, time-travel replay, `SQLiteSnapshotStore` |

```python
# Zero-config local pipeline — no server needed
import asyncio, sys
sys.path.insert(0, 'sdk')

from multigen import (
    FunctionAgent, Chain,
    MemoryManager, AdvancedMessageBus, AdvancedMessage,
    Step, build_agent, LoggingTrait, RetryTrait,
    SessionManager, ReactiveState, AgentProfiler,
)

async def main():
    memory  = MemoryManager()
    bus     = AdvancedMessageBus()
    session = SessionManager()
    profiler = AgentProfiler()

    @bus.subscribe("pipeline.**")
    async def on_event(msg): print(f"[event] {msg.topic}")

    @profiler.profile("fetch")
    async def fetch(ctx): return {**ctx, "data": [1, 2, 3]}

    @profiler.profile("process")
    async def process(ctx):
        memory.remember("result", sum(ctx["data"]), tier="short")
        return {"sum": sum(ctx["data"])}

    async with session.with_session("demo") as sess:
        pipeline = Step("fetch", fetch) >> Step("process", process)
        result   = await pipeline.run({"_session": sess})
        print(result)             # {'sum': 6}

    print(profiler.report())
    await bus.publish(AdvancedMessage(topic="pipeline.done", content=result))

asyncio.run(main())
```

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│              Client Layer (SDK / REST / MCP / CLI / EventSource)             │
└─────────────────────────────────┬────────────────────────────────────────────┘
                                  │
┌─────────────────────────────────▼────────────────────────────────────────────┐
│                        Orchestrator  :8000 (FastAPI)                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐ │
│  │  DSL Parser  │  │  LLM text→   │  │  Graph Signal│  │  A2A Server      │ │
│  │  Validator   │  │  DSL Service │  │  Controller  │  │  /.well-known/   │ │
│  └──────────────┘  └──────────────┘  └──────────────┘  │  /a2a endpoints  │ │
│                                                          └──────────────────┘ │
│  ┌──────────────────────────────────────────────────────────────────────────┐ │
│  │  SSE Streaming  GET /workflows/{id}/stream   (text/event-stream)         │ │
│  │  Polling        GET /workflows/{id}/events?since=N                       │ │
│  └──────────────────────────────────────────────────────────────────────────┘ │
└──────┬──────────────────────────────────────────────┬─────────────────────────┘
       │ validate                                      │ Temporal client
       ▼                                               ▼
┌─────────────────┐                 ┌──────────────────────────────────────────┐
│ Capability      │                 │              Temporal Server             │
│ Service  :8001  │                 │   ComplexSequenceWorkflow                │
│ (MongoDB        │                 │   GraphWorkflow                          │
│  agent_specs)   │                 │     • Parallel BFS (pending_set)         │
│                 │                 │     • depends_on resolution              │
│ A2A Client ─────┼──────────────── │     • Signal handlers                   │
│ (remote agents) │                 │     • Query handlers                     │
└─────────────────┘                 │     • Epistemic tracking                 │
                                    └───────────────────┬──────────────────────┘
       ┌───────────────────────────────────────────     │ activities
       │ Kafka                                           ▼
       ▼                                ┌───────────────────────────────────────┐
┌─────────────┐    partition-aware      │   Worker Pool A   (queue-a)           │
│  Apache     │    fan-out              │   Worker Pool B   (queue-b)           │
│  Kafka      ├───────────────────────► │   Worker Pool C   (queue-c)           │
│  flow-      │                         │                                       │
│  requests   │                         │  Each pool runs:                      │
└─────────────┘                         │  • agent_activity                     │
                                        │  • tool_activity                      │
                                        │  • create_agent_activity              │
                                        │  • generate_agent_spec_activity       │
                                        │  • deregister_agents_activity         │
                                        │  • persist_node_state_activity        │
                                        │  • sse_publish_activity               │
                                        └───────────────────┬───────────────────┘
                                                            │
                       ┌────────────────────────────────────▼──────────────────┐
                       │                   Agent Registry                      │
                       │  EchoAgent, LangChainAgent, LlamaIndexAgent,          │
                       │  SpawnerAgent, ScreeningAgents, PatternAgents         │
                       │  + Dynamic BlueprintAgents                            │
                       │  + Hydrated agents from MongoDB (agent_specs)         │
                       └───────────────────────────────────────────────────────┘
```

---

## Quickstart

**Requirements:** Docker, Docker Compose, Python 3.11+, OpenAI API key

```bash
git clone https://github.com/Subhagatoadak/Multigen.git
cd Multigen
git submodule update --init --recursive   # pulls Agent-Components

# Set environment variables
cp .env.example .env
# Edit .env: set OPENAI_API_KEY, and optionally MULTIGEN_BASE_URL

# Start full stack (Temporal, Kafka, MongoDB, Orchestrator, Capability Service)
docker-compose up -d --build

# Wait ~60s for Temporal to initialise
curl http://localhost:8000/health   # {"status":"ok"}
```

### Run your first graph workflow (Python SDK)

```python
from multigen import SyncMultigenClient, GraphBuilder

client = SyncMultigenClient("http://localhost:8000")

graph = (
    GraphBuilder()
    .node("research").agent("EchoAgent").params(task="research NovaSemi Technologies").timeout(30).done()
    .node("analysis").agent("EchoAgent").params(task="analyse findings").timeout(30).done()
    .edge("research", "analysis")
    .entry("research")
    .build()
)

resp = client.run_graph(graph_def=graph, payload={"company": "NovaSemi"})
print(f"Workflow: {resp.instance_id}")

# Poll state
state = client.get_state(resp.instance_id)
for node in state.nodes:
    print(f"  {node.node_id}: {node.output}")
```

### Run the M&A Due Diligence example

```bash
# Set your OpenAI key
export OPENAI_API_KEY=sk-...
export MULTIGEN_BASE_URL=http://localhost:8009   # if running orchestrator locally

# Start services (see DEV.md for local setup without Docker)
python -m workers.temporal_worker &        # Terminal 1
uvicorn orchestrator.main:app --port 8009  # Terminal 2

# Run the example
python examples/ma_due_diligence/run.py
```

### Open the Jupyter notebooks

```bash
pip install jupyter
jupyter notebook notebooks/
```

Start with `01_quickstart.ipynb`. For local SDK features start at `12_local_sdk_agents.ipynb`.

---

## Graph Workflow DSL

### Building a graph programmatically

```python
from multigen import GraphBuilder

graph = (
    GraphBuilder()
    # Simple agent node
    .node("ingest")
        .agent("DataIngestionAgent")
        .params(source="s3://bucket/data.csv")
        .timeout(60)
        .done()

    # Node with reflection (auto-critique if confidence < threshold)
    .node("analyse")
        .agent("AnalysisAgent")
        .reflect(threshold=0.80, max_rounds=2, critic="CritiqueAgent")
        .timeout(90)
        .done()

    # Fallback routing if circuit breaker trips
    .node("report")
        .agent("ReportAgent")
        .fallback("FallbackReportAgent")
        .timeout(60)
        .done()

    .edge("ingest", "analyse")
    .edge("analyse", "report")
    .entry("ingest")
    .max_cycles(5)
    .circuit_breaker(trip_threshold=3, recovery_executions=5)
    .build()
)
```

### Raw graph definition (dict)

```python
graph_def = {
    "nodes": [
        {
            "id": "think",
            "agent": "PlannerAgent",
            "params": {"objective": "draft a quarterly strategy"},
            "timeout": 60,
            "retry": 3,
            "reflection_threshold": 0.75,
            "max_reflections": 2,
            "critic_agent": "CritiqueAgent",
            "fallback_agent": "FallbackPlannerAgent"
        },
        {
            "id": "write",
            "blueprint": {
                "system_prompt": "You are a professional business writer...",
                "instruction": "Draft a 500-word executive summary"
            }
        }
    ],
    "edges": [
        {"source": "think", "target": "write"},
        {"source": "write", "target": "review", "condition": "confidence < 0.8"}
    ],
    "entry": "think",
    "max_cycles": 10,
    "circuit_breaker": {"trip_threshold": 3, "recovery_executions": 5}
}
```

---

## New Features (v0.3.0)

### depends_on: Explicit Node Dependencies

`depends_on` declares logical dependencies that don't carry data. A node only executes when ALL listed nodes are present in the result context — regardless of whether there is a direct data edge between them. This is useful when a node needs a resource or side-effect to be ready before it can proceed.

```python
graph_def = {
    "nodes": [
        {"id": "fetch_data", "agent": "DataAgent"},
        {"id": "load_model", "agent": "ModelAgent"},
        {
            "id": "run_inference",
            "agent": "InferenceAgent",
            "depends_on": ["fetch_data", "load_model"],  # waits for BOTH
        }
    ],
    "edges": [
        {"source": "fetch_data", "target": "run_inference"},
        # model loading is a resource dep, not a data dep — use depends_on
    ],
    "entry": "fetch_data"
}
```

### Partition-Aware Fan-Out

Nodes dispatched via fan-out can be distributed across named task queues, each backed by dedicated worker pods. This enables true multi-worker parallelism rather than concurrency within a single worker.

```python
# Signal a fan-out across 3 worker pools
client.fan_out(workflow_id, {
    "group_id": "expert_panel",
    "task_queues": ["queue-a", "queue-b", "queue-c"],  # round-robin
    "consensus": "highest_confidence",
    "nodes": [
        {"id": "financial_expert", "agent": "FinancialAgent"},
        {"id": "legal_expert", "agent": "LegalAgent"},
        {"id": "market_expert", "agent": "MarketAgent"},
    ]
})
# Nodes are distributed: financial→queue-a, legal→queue-b, market→queue-c
# Each queue has dedicated worker pods → true multi-worker parallelism
```

Configure available queues via environment variable:

```bash
TEMPORAL_TASK_QUEUES=queue-a,queue-b,queue-c
```

### SSE Streaming: Real-Time Node Completion

Subscribe to a live event stream that delivers a structured event for each node as it completes. No polling required.

```python
import requests, json

# Open SSE stream — events arrive as each node completes
with requests.get(
    f"http://localhost:8000/workflows/{workflow_id}/stream",
    stream=True,
    headers={"Accept": "text/event-stream"},
) as resp:
    for line in resp.iter_lines():
        if line.startswith(b"data:"):
            event = json.loads(line[5:])
            print(f"[{event['node_id']}] confidence={event['confidence']:.2f}")
            if event.get("done"):
                break

# Polling fallback (for clients that can't use SSE)
events = requests.get(
    f"http://localhost:8000/workflows/{workflow_id}/events?since=3"
).json()
```

Event format: `{index, node_id, agent, confidence, timestamp, output, done}`

Reconnect: `Last-Event-ID` header is automatically used by browser `EventSource` to resume from the last received event.

### Agent2Agent (A2A) Protocol

Multigen supports the Agent2Agent (A2A) protocol for both calling remote A2A-compatible agents from a graph node and exposing Multigen agents to external systems.

**a) Calling a remote A2A agent from a graph node:**

```python
{
    "id": "external_analyst",
    "a2a_endpoint": "https://partner-ai.example.com",
    "a2a_skill": "financial_analysis",
    "timeout": 60,
    "retry": 3,
}
```

**b) Exposing Multigen agents to external systems:**

```python
# Discover all Multigen agents
card = requests.get("http://localhost:8000/.well-known/agent.json").json()

# Call an agent via A2A JSON-RPC
result = requests.post("http://localhost:8000/a2a", json={
    "jsonrpc": "2.0", "id": 1,
    "method": "tasks/send",
    "params": {
        "id": "task-001",
        "message": {"role": "user", "parts": [{"type": "text", "text": "Analyse NovaSemi"}]},
        "metadata": {"skill_id": "EchoAgent"}
    }
}).json()
```

---

## Sequence Workflow DSL

```python
dsl = {
    "steps": [
        # Sequential step
        {"name": "parse", "agent": "ParserAgent", "params": {"doc": "..."}},

        # Parallel step
        {
            "name": "dual_review",
            "parallel": [
                {"name": "legal", "agent": "LegalReviewAgent"},
                {"name": "technical", "agent": "TechReviewAgent"}
            ]
        },

        # Conditional branch
        {
            "name": "escalate",
            "condition": "steps.dual_review.legal.output.risk > 0.7",
            "if_true": {"name": "escalate", "agent": "EscalationAgent"},
            "if_false": {"name": "approve",  "agent": "ApprovalAgent"}
        },

        # Dynamic subtree (spawner generates steps at runtime)
        {
            "name": "custom_pipeline",
            "dynamic_subtree": {"spawner": "PipelineSpawnerAgent", "params": {}}
        }
    ]
}
```

---

## Runtime Control Signals

```python
client = SyncMultigenClient("http://localhost:8000")
wf_id = "wf-abc123"

# Pause at the next node boundary
client.interrupt(wf_id)

# Inject a new node into the running workflow
from multigen.models import InjectNodeRequest
client.inject_node(wf_id, InjectNodeRequest(
    id="security_check",
    agent="SecurityAuditAgent",
    params={"scope": "full"},
    edges_to=["synthesis"]
))

# Prioritise a node
client.jump_to(wf_id, "risk_assessment")

# Add a conditional edge at runtime
client.reroute(wf_id, source="analysis", target="escalation", condition="confidence < 0.6")

# Run parallel hypotheses, pick best
from multigen.models import FanOutRequest, FanOutNodeDef
client.fan_out(wf_id, FanOutRequest(
    group_id="multi_view",
    consensus="highest_confidence",
    nodes=[
        FanOutNodeDef(id="optimist", agent="BullCaseAgent"),
        FanOutNodeDef(id="pessimist", agent="BearCaseAgent"),
        FanOutNodeDef(id="neutral",   agent="BaselineAgent"),
    ]
))

# Resume after human review
client.resume(wf_id)
```

---

## Human Approval Gate

```python
# Poll for pending approvals
approvals = client.get_pending_approvals(wf_id)

if approvals:
    spec = approvals[0]
    print(f"Agent requested: {spec['agent_name']}")
    print(f"Capability: {spec['capability_description']}")
    print(f"Known limitations: {spec['known_limitations']}")

    # Optionally edit the spec
    spec["system_prompt"] += "\n\nAdditional constraint: flag anything with >20% uncertainty."

    # Approve — workflow resumes, agent is created and runs
    client.approve_agent(wf_id, spec)

    # Or reject — node is skipped, workflow continues
    # client.reject_agent(wf_id, spec["agent_name"], reason="Use ValuationAgent instead")
```

---

## Epistemic Transparency

```python
report = client.get_epistemic_report(wf_id)

print(f"Average confidence:    {report['summary']['avg_confidence']:.1%}")
print(f"Nodes flagged:         {report['summary']['nodes_flagged_for_human_review']}")
print(f"Trustworthiness:       {report['overall_trustworthiness']}")
print(f"Recommendation:        {report['recommendation']}")

# Per-node breakdown
for node_id, state in report["node_states"].items():
    print(f"  {node_id}: confidence={state['confidence']:.1%}  "
          f"propagated_uncertainty={state['propagated_uncertainty']:.1%}")
```

---

## Writing an Agent

```python
from agents.base_agent import BaseAgent
from orchestrator.services.agent_registry import register_agent

@register_agent("FinancialAnalystAgent")
class FinancialAnalystAgent(BaseAgent):
    async def run(self, params: dict) -> dict:
        company = params.get("company", "Unknown")
        # ... your analysis logic ...
        return {
            "output": {
                "dcf_valuation": 1_200_000_000,
                "recommendation": "BUY",
                "confidence": 0.84,
                "epistemic": {
                    "confidence": 0.84,
                    "reasoning": "3-year DCF with sector comps",
                    "uncertainty_sources": ["no audited H2 data"],
                    "assumptions": ["8% revenue growth"],
                    "known_limitations": ["off-balance-sheet items excluded"],
                    "known_unknowns": ["cap table concentration"],
                    "evidence_quality": "medium",
                    "data_completeness": 0.78,
                    "flags": []
                }
            }
        }
```

The `@register_agent` decorator auto-registers the agent in the live registry. Import the module in `workers/temporal_worker.py` and the agent is immediately available to all workflows.

---

## Observability

### OpenTelemetry
Every node execution generates an OTel span with attributes:
- `graph.node_id`, `graph.agent`, `graph.iteration`
- `graph.circuit_state`, `graph.confidence`
- `graph.epistemic_debt` (count of known unknowns)
- `graph.duration_ms`

Configure your OTLP exporter endpoint in `.env`:
```
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4317
```

### Prometheus
Counters exposed at `/metrics`:
- `multigen_nodes_total` — total nodes executed
- `multigen_errors_total` — node failures
- `multigen_reflections_total` — reflection loops triggered
- `multigen_circuit_open_total` — circuit breaker trips

### Temporal UI
Access the workflow event history and execution timeline at `http://localhost:8080` (Temporal Web UI).

---

## MCP Server (Claude Desktop / Cursor / Windsurf)

Multigen ships with a Model Context Protocol server, letting you control workflows from any MCP-compatible AI assistant.

```bash
python -m mcp_server.server
```

Add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "multigen": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/path/to/Multigen"
    }
  }
}
```

Available MCP tools: `start_workflow`, `run_graph`, `interrupt`, `resume`, `inject_node`, `jump_to`, `fan_out`, `get_state`, `get_epistemic_report`, `approve_agent`, `reject_agent`.

---

## Project Structure

```
Multigen/
├── agents/                      # Agent implementations
│   ├── base_agent.py            # BaseAgent abstract class
│   ├── echo_agent/              # Demo EchoAgent
│   ├── langchain_agent/         # LangChain LCEL integration
│   ├── llamaindex_agent/        # LlamaIndex RAG integration
│   ├── spawner_agent/           # Dynamic subtree spawner
│   ├── screening_agents/        # Resume screening pipeline
│   └── pattern_agents/          # Ministry of Experts, Swarm, Debate
├── orchestrator/                # FastAPI orchestration server
│   ├── controller/              # REST endpoints
│   └── services/                # DSL parser, registry, LLM, Kafka, MongoDB
├── flow_engine/                 # Temporal workflow execution
│   ├── workflows/sequence.py    # ComplexSequenceWorkflow
│   └── graph/                   # GraphWorkflow + all sub-systems
│       ├── engine.py            # Main workflow, signals, queries, activities
│       ├── autonomy.py          # Dynamic agent lifecycle
│       ├── epistemic.py         # Epistemic transparency engine
│       ├── reasoning.py         # Reflection, fan-out consensus
│       ├── circuit_breaker.py   # Per-node circuit breaker
│       ├── agent_factory.py     # BlueprintAgent factory
│       └── telemetry.py         # OTel spans + Prometheus counters
├── workers/temporal_worker.py   # Temporal worker entrypoint
├── messaging/kafka_client.py    # Kafka producer/consumer
├── capability_service/          # Agent capability directory microservice
├── mcp_server/                  # MCP server for AI assistants
├── sdk/multigen/                # Python SDK (310+ public exports, zero deps)
│   ├── client.py                # Async HTTP client
│   ├── sync_client.py           # Sync wrapper (Jupyter-safe)
│   ├── dsl.py                   # GraphBuilder, WorkflowBuilder
│   ├── models.py                # Pydantic request/response models
│   ├── agent.py                 # BaseAgent, FunctionAgent, LLMAgent (+ WorkingMemory wiring), …
│   ├── chain.py                 # Chain, Pipeline, middleware
│   ├── parallel.py              # Parallel, FanOut, MapReduce, Race, Batch
│   ├── graph.py                 # Local Graph executor (DAG)
│   ├── state_machine.py         # MCMC StateMachine
│   ├── bus.py                   # InMemoryBus, Message
│   ├── runtime.py               # Unified Runtime (local / Temporal / Kafka)
│   ├── memory.py                # ShortTermMemory, EpisodicMemory, WorkingMemory,
│   │                            #   SemanticMemory, MemoryManager
│   ├── advanced_memory.py       # VectorMemory, ForgettingCurve, MemoryIndex,
│   │                            #   ContextualMemory, PersistentMemory
│   ├── cache.py                 # LRUCache, TTLCache, AsyncCache, MultiTierCache,
│   │                            #   CacheManager, @cached
│   ├── messaging.py             # AdvancedMessageBus, priority pub/sub,
│   │                            #   request/reply, scatter-gather, DLQ
│   ├── compose.py               # Step (>>, |, &), StepSequence, ParallelStep,
│   │                            #   FanInStep, BranchStep, LoopStep, Compose
│   ├── hierarchy.py             # AgentRole, AgentGroup, AgentHierarchy,
│   │                            #   TypedStep, HierarchicalPipeline
│   ├── session.py               # SessionManager, SessionContext, SessionMiddleware
│   ├── state_init.py            # StateSchema, StateInitializer, ReactiveState,
│   │                            #   ComputedState, StateValidator, StateTransition
│   ├── inheritance.py           # InheritableAgent, Trait, LoggingTrait, RetryTrait,
│   │                            #   build_agent, @mixin, @overload, MultiMethod
│   ├── polymorphic.py           # PolymorphicAgent, ShapeRegistry, TypeAdapter,
│   │                            #   MultiDispatch, DynamicAgent
│   ├── performance.py           # AgentProfiler, BatchExecutor, ConnectionPool,
│   │                            #   LazyValue, RateLimiter, ExecutionOptimizer
│   ├── snapshot.py              # Snapshot, InMemorySnapshotStore, SQLiteSnapshotStore
│   ├── debugger.py              # WorkflowDebugger (time-travel debugging)
│   ├── persistence.py           # SQLiteCheckpointStore, DurableQueue,
│   │                            #   CheckpointedRuntime, PersistentEpisodicMemory
│   ├── eval.py                  # Evaluator, EvalSuite, Benchmark, LLMJudge, metrics
│   ├── routing.py               # ModelPool, CostRouter, QualityRouter, AdaptiveRouter,
│   │                            #   FallbackRouter, ModelRouter
│   ├── tools.py                 # ToolRegistry, Sandbox, PermissionedRegistry,
│   │                            #   ToolSpec, ToolValidator
│   ├── planning.py              # TreeOfThoughts, GraphOfThoughts, ReActPlanner,
│   │                            #   ChainOfThought, PlanAndExecute
│   ├── versioning.py            # VersionedWorkflow, WorkflowDiff, ChangeLog,
│   │                            #   SQLiteVersionStore
│   ├── scheduler.py             # Scheduler, CronSchedule, IntervalSchedule, Trigger
│   ├── learning.py              # AdaptivePrompt, OnlineLearner, FewShotSelector,
│   │                            #   ContinuousLearner, ExperienceReplay
│   ├── workflow_ab.py           # ABTest, CanaryRollout, RollbackManager,
│   │                            #   CompatibilityChecker, TrafficSplit
│   ├── safety.py                # InjectionDetector, PIIRedactor, OutputSanitizer,
│   │                            #   SafetyGuard (14 injection patterns, 9 PII patterns)
│   ├── resilience.py            # RetryPolicy, Deadline, DeadlineGuard,
│   │                            #   DeadlineManager, WorkflowRetry
│   ├── planning_advanced.py     # HierarchicalDecomposer, MCTSPlanner,
│   │                            #   AutoGPTQueue, HierarchicalSummariser
│   ├── streaming.py             # StreamingAgent, ParallelStreamer, StreamAggregator,
│   │                            #   PartialResultBus, StreamToken
│   └── optimization.py         # PromptBandit, FewShotLibrary, AgentSpecialisation,
│                                #   EpisodicFeedbackLoop, OptimizationManager
├── examples/                    # End-to-end examples
│   ├── resume_screening.py      # Resume screening pipeline
│   └── ma_due_diligence/        # M&A due diligence graph workflow
├── notebooks/                   # Jupyter notebooks (01–42)
├── tests/                       # pytest test suite
├── docs/                        # Architecture diagrams, whitepapers
├── Agent-Components/            # agentic_codex submodule (tools, patterns)
└── docker-compose.yml           # Full local stack
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | Required for LLM-powered features (text→DSL, dynamic agent spec generation) |
| `TEMPORAL_SERVER_URL` | `localhost:7233` | Temporal gRPC endpoint |
| `TEMPORAL_TASK_QUEUE` | `multigen-queue` | Temporal task queue name |
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka broker addresses |
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `MULTIGEN_BASE_URL` | `http://localhost:8000` | Orchestrator base URL (used by SDK) |
| `LLM_MODEL` | `gpt-4o` | OpenAI model for agent spec generation |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://localhost:4317` | OTLP exporter for traces/metrics |

---

## Development Setup

```bash
# Create virtual environment
python -m venv .venv && source .venv/bin/activate

# Install all dependencies
pip install -r requirements.txt
pip install -e ./Agent-Components   # local agentic_codex
pip install -e ./sdk                # local multigen SDK

# Run tests
pytest tests/ -v

# Run linter
ruff check . && ruff format --check .

# Start local services (without Docker)
# Terminal 1: Temporal (requires temporal CLI)
temporal server start-dev

# Terminal 2: MongoDB
mongod --dbpath ./data/db

# Terminal 3: Orchestrator
uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 4: Temporal Worker
python -m workers.temporal_worker
```

See [docs/DEV.md](docs/DEV.md) for detailed development guide.

---

## Notebooks

**Server-side workflows (require Docker stack):**

| Notebook | What you'll learn |
|---|---|
| [01_quickstart](notebooks/01_quickstart.ipynb) | Connect to Multigen, run your first workflow |
| [02_graph_workflow](notebooks/02_graph_workflow.ipynb) | Build directed graph workflows with nodes, edges, conditions |
| [03_signals_control](notebooks/03_signals_control.ipynb) | Interrupt, inject, jump, skip, reroute at runtime |
| [04_circuit_breakers](notebooks/04_circuit_breakers.ipynb) | Fault tolerance: circuit breakers, fallback routing, dead letters |
| [05_reflection_loops](notebooks/05_reflection_loops.ipynb) | Self-improvement: auto-critique when confidence is low |
| [06_fan_out_consensus](notebooks/06_fan_out_consensus.ipynb) | Parallel hypotheses: run N agents, pick best via consensus |
| [07_dynamic_agents](notebooks/07_dynamic_agents.ipynb) | Create agents at runtime from LLM-generated blueprints |
| [08_observability](notebooks/08_observability.ipynb) | OTel tracing, Prometheus metrics, Temporal UI |
| [09_mmm_autopilot](notebooks/09_mmm_autopilot.ipynb) | Full autopilot: hierarchical MMM with synthetic data |
| [10_autonomy_transparency](notebooks/10_autonomy_transparency.ipynb) | Dynamic agents + human approval + epistemic transparency report |

**Local SDK (zero external dependencies):**

| Notebook | What you'll learn |
|---|---|
| [11_parallel_bfs_streaming_a2a](notebooks/11_parallel_bfs_streaming_a2a.ipynb) | Parallel BFS execution, SSE streaming, A2A protocol |
| [12_local_sdk_agents](notebooks/12_local_sdk_agents.ipynb) | Local runtime, no server required |
| [13_sequential_chains](notebooks/13_sequential_chains.ipynb) | Chain, Pipeline, logging/tracing middleware |
| [14_parallel_execution](notebooks/14_parallel_execution.ipynb) | FanOut, MapReduce, Race, Batch |
| [15_graph_dag_local](notebooks/15_graph_dag_local.ipynb) | Local Graph executor with conditional edges |
| [16_mcmc_state_machine](notebooks/16_mcmc_state_machine.ipynb) | Probabilistic MCMC state machine |
| [17_event_bus_messaging](notebooks/17_event_bus_messaging.ipynb) | InMemoryBus pub/sub |
| [18_runtime_simulator_integration](notebooks/18_runtime_simulator_integration.ipynb) | Unified Runtime + simulator event push |
| [19_resilience_patterns](notebooks/19_resilience_patterns.ipynb) | Retry, CircuitBreaker, MemoryAgent patterns |
| [20_time_travel_debugging](notebooks/20_time_travel_debugging.ipynb) | Snapshot capture, replay, diff, SQLite persistence |
| [21_memory_system](notebooks/21_memory_system.ipynb) | ShortTerm/Episodic/Working/Semantic memory, consolidation |
| [22_caching](notebooks/22_caching.ipynb) | LRU, TTL, AsyncCache, MultiTier, stampede protection, @cached |
| [23_step_composition](notebooks/23_step_composition.ipynb) | `>>`, `\|`, `&` operators, BranchStep, LoopStep, Compose |
| [24_hierarchical_structures](notebooks/24_hierarchical_structures.ipynb) | AgentHierarchy, TypedStep, HierarchicalPipeline |
| [25_advanced_messaging](notebooks/25_advanced_messaging.ipynb) | Priority pub/sub, request/reply, scatter-gather, DLQ, filters |
| [26_session_and_state](notebooks/26_session_and_state.ipynb) | SessionManager, ReactiveState, ComputedState, StateValidator |
| [27_inheritance_and_polymorphism](notebooks/27_inheritance_and_polymorphism.ipynb) | Traits, build_agent, @overload, MultiMethod, PolymorphicAgent |
| [28_performance_and_advanced_memory](notebooks/28_performance_and_advanced_memory.ipynb) | AgentProfiler, BatchExecutor, VectorMemory, ForgettingCurve |
| [29_persistence](notebooks/29_persistence.ipynb) | SQLiteCheckpointStore, DurableQueue, CheckpointedRuntime |
| [30_eval_and_measurement](notebooks/30_eval_and_measurement.ipynb) | EvalSuite, Benchmark, LLMJudge, F1Score, Latency |
| [31_multi_model_routing](notebooks/31_multi_model_routing.ipynb) | ModelPool, CostRouter, QualityRouter, AdaptiveRouter, FallbackRouter |
| [32_tool_registry](notebooks/32_tool_registry.ipynb) | ToolRegistry, Sandbox, PermissionedRegistry |
| [33_planning_tot_got](notebooks/33_planning_tot_got.ipynb) | TreeOfThoughts, GraphOfThoughts, ReActPlanner, PlanAndExecute |
| [34_workflow_versioning](notebooks/34_workflow_versioning.ipynb) | VersionedWorkflow, WorkflowDiff, ChangeLog, SQLiteVersionStore |
| [35_scheduling_triggers](notebooks/35_scheduling_triggers.ipynb) | Scheduler, CronSchedule, IntervalSchedule, Trigger |
| [36_continuous_learning](notebooks/36_continuous_learning.ipynb) | AdaptivePrompt, OnlineLearner, FewShotSelector, ContinuousLearner |
| [37_workflow_ab_safety](notebooks/37_workflow_ab_safety.ipynb) | ABTest, CanaryRollout, RollbackManager, SafetyGuard, PIIRedactor |
| [38_resilience](notebooks/38_resilience.ipynb) | RetryPolicy, Deadline, DeadlineGuard, WorkflowRetry |
| [39_advanced_planning](notebooks/39_advanced_planning.ipynb) | HierarchicalDecomposer, MCTSPlanner, AutoGPTQueue, HierarchicalSummariser |
| [40_streaming](notebooks/40_streaming.ipynb) | StreamingAgent, ParallelStreamer, PartialResultBus |
| [41_optimization](notebooks/41_optimization.ipynb) | PromptBandit, FewShotLibrary, AgentSpecialisation, OptimizationManager |
| [42_working_memory_wiring](notebooks/42_working_memory_wiring.ipynb) | LLMAgent + WorkingMemory integration, template injection |

---

## License

Apache License 2.0 — see [LICENSE](LICENSE).

---

## Citation

```bibtex
@software{multigen2026,
  title   = {Multigen: Enterprise Multi-Agent Orchestration Framework},
  author  = {Adak, Subhagato},
  year    = {2026},
  url     = {https://github.com/Subhagatoadak/Multigen},
  license = {Apache-2.0}
}
```
