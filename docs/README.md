# Multigen Framework: Technical Overview for Architects & Researchers

> This document provides a conceptual, in-depth understanding of the Multigen framework—its motivations, architectural patterns, component responsibilities, and evolutionary roadmap. It is targeted at system architects, engineering researchers, and decision-makers seeking to evaluate or adopt a cutting-edge multi-agent orchestration platform.

---

## 1. Vision & Motivation

Modern enterprises require platforms that can:

- **Automate end-to-end AI workflows** across diverse domains (Customer Support, Finance, Healthcare, E-Commerce, Legal, Scientific Research, Education).
- **Enforce fine-grained governance** (cost controls, data privacy, ethical constraints) at every step.
- **Continuously learn and adapt** through reinforcement learning and human feedback loops.
- **Scale seamlessly** from a developer's local environment to multi-region Kubernetes deployments.
- **Extend dynamically** by discovering gaps in capabilities and auto-generating new agents or tool adapters.
- **Run entirely locally** with zero external dependencies via the Python SDK's `LocalRuntime`.

**Multigen** addresses these demands by fusing declarative orchestration, policy enforcement, self-healing codegen, and closed-loop learning into a unified architecture. The v0.6 SDK provides a comprehensive in-process runtime covering memory, caching, step composition, hierarchical structures, session management, reactive state, polymorphic agents, performance optimization, advanced messaging, multi-model routing, tool registry, planning (ToT/GoT/MCTS), workflow versioning, scheduling, continuous learning, evaluation, persistence, A/B testing, safety, resilience, streaming, and online optimization — all with no external services required.

---

## 2. High-Level Architecture

```text
                          +------------------+
                          |      User        |
                          +--------+---------+
                                   |
              +------------+       |      +----------------+
              |  Web UI /  |<------+----->|   CLI / SDK     |
              | External   |       |      | (DSL-as-Code)   |
              +------------+       |      +----------------+
                                   |
                          +--------v---------+
                          |   API Gateway    |
                          | (Traefik / Kong)  |
                          +--------+---------+
                                   |
                          +--------v---------+       +----------------+
                          |   Orchestrator   |<----->|  RL Policy     |
                          +--------+---------+       +----------------+
                                   |
                         +---------v----------+
                         |  Guardrails Engine |
                         |     (OPA)          |
                         +---------+----------+
                                   |
                          +--------v---------+
                          |    Flow Engine   |
                          | (Temporal / Argo) |
                          +----+------+------+
                               |      |
               +---------------+      +----------------+
               |                                     |
       +-------v-------+                     +-------v-------+
       | Message Bus   |                     | Agents &      |
       | (Kafka/MSK)   |                     | ToolAdapters  |
       +-------+-------+                     +---------------+
               |                                     /
               | Feedback Collector                /
               +------------------------------>  /
                                                  /
                                     +----------v---------+    +----------------+
                                     | Feedback Store     |<--> | Learning Loops  |
                                     | (PostgreSQL)       |    | (RLHF, PPO, ...)|
                                     +--------------------+    +----------------+

Observability & Autoscaling Modules (Prometheus, Grafana, Chaos Mesh, HPA)
```

**Key Layers:**
1. **Interface Layer:** Provides unified entry via API Gateway, decoupling clients from internal services.
2. **Control Plane:** Orchestrator applies learned policies, discovers capabilities, and coordinates workflows.
3. **Governance Layer:** Guardrails Engine enforces OPA-based policies for compliance, cost, and privacy.
4. **Execution Engine:** Flow Engine executes declarative DSL workflows reliably and at scale.
5. **Messaging Fabric:** Kafka ensures durable, partitioned, and decoupled communication.
6. **Agent Ecosystem:** Modular agents and adapters implement domain logic, LLM chains, and dynamic spawning.
7. **Feedback & Learning:** Structured feedback fuels RL policy updates, prompt/flow optimizers, and model fine-tuning.
8. **Observability & Resilience:** Metrics, tracing, chaos engineering, and autoscaling provide robustness.
9. **Local SDK Runtime:** Full in-process execution with zero external dependencies — memory, caching, messaging, session management, performance optimization, safety, resilience, streaming, and online optimization all included.
10. **Safety & Resilience:** Injection detection, PII redaction, output sanitization, deadline management, and exponential-backoff retry at the workflow level.
11. **Advanced Planning:** MCTS search, hierarchical task decomposition, AutoGPT-style task queues, and hierarchical summarization.
12. **Online Optimization:** Prompt bandits, few-shot libraries, agent specialization, and EpisodicMemory → prompt feedback loops.

---

## 3. Component Breakdown & Rationale

### 3.1 API Gateway
- **What:** Traefik or Kong as the single ingress point.
- **Why:** Centralizes authentication (mTLS, JWT), authorization, rate-limiting, and schema validation.
- **How:** Routes requests to the Orchestrator or static UIs, enforces cross-cutting security policies.

### 3.2 Orchestrator & RL Policy
- **What:** Core service (FastAPI/Go) coordinating workflow initiation.
- **Why:** Encapsulates decision logic; applies policy-learned routing based on cost, correctness, latency.
- **How:**
  1. Validates incoming requests.
  2. Calls `policy.selectAction(state)` to choose the next agent/tool.
  3. Ensures capability availability via Capability Directory or auto-generation.
  4. Dispatches workflow to Flow Engine.

### 3.3 Guardrails Engine (OPA)
- **What:** Policy enforcement service leveraging Open Policy Agent.
- **Why:** Enforces enterprise policies (cost budgets, PII detection, ethics) as code.
- **How:** Pre-invoke checks (`allow/deny/pause`), in-flight response validation, and policy updates via Rule Miner.

### 3.4 Flow Engine (Temporal/Argo)
- **What:** Stateful execution engine for declarative workflows.
- **Why:** Supports robust, long-running, parallelizable, and conditional business logic.
- **How:** Parses DSL files, manages state persistence, retries, joins, and external service callbacks.

### 3.5 Message Bus (Kafka + AdvancedMessageBus)
- **What:** Two-tier messaging — distributed Kafka for production, in-process `AdvancedMessageBus` for local/test.
- **Why:** Decouples producers and consumers, ensures high-throughput, partitioned scalability, and replayability.
- **How (Kafka):** Topics for `invocations`, `responses`, `errors`, and `approvals`; manages consumer offsets and DLQs.
- **How (AdvancedMessageBus):** Priority pub/sub with wildcard routing (`*`, `**`), request/reply correlation, scatter-gather, dead-letter queue, middleware pipeline, and content-based `MessageRouter`. Zero external dependencies.

### 3.6 Agents & ToolAdapters
- **What:** Containerized executors (Python/Node.js/Go) implementing `BaseAgent` or wrapping OpenAPI tools.
- **Why:** Encapsulate domain-specific logic, RAG chains (LangChainAgent), or vector retrieval (LlamaIndexAgent, Milvus).
- **How:** Self-register on startup, subscribe to invocation topics, and publish responses.

#### 3.6.1 Dynamic Sub-Tree Spawning
- **What:** Spawner agent emits sub-workflow steps dynamically based on runtime data.
- **Why:** Handles variable fan-out or complex branching without bloating DSL.
- **How:** Returns invocation IDs to Flow Engine, which then joins results.

#### 3.6.2 LangChain & LlamaIndex Agents
- **What:** Agents integrating LangChain pipelines or LlamaIndex vector queries.
- **Why:** Provides advanced RAG, memory, and semantic search capabilities seamlessly in workflows.
- **How:** Wrap the respective SDKs; expose a consistent invocation/response schema.

#### 3.6.3 Polymorphic & Shape-Shifting Agents
- **What:** Agents that dynamically select their execution shape at runtime via `PolymorphicAgent` and `ShapeRegistry`.
- **Why:** A single agent entry point can morph into fast/thorough/streaming modes based on context, eliminating routing boilerplate.
- **How:** `morph_fn(ctx) -> shape_name` selects the registered `AgentShape`; `force_shape()` context manager locks to a shape for testing.

#### 3.6.4 Inheritable Agents with Traits
- **What:** `InheritableAgent` base class with cooperative hook overrides (`pre_run`, `run`, `post_run`, `on_error`) and composable `Trait` mixins.
- **Why:** Avoids code duplication across agents; cross-cutting concerns (logging, retry, caching, validation) are isolated in traits.
- **How:** `build_agent(MyAgent, LoggingTrait, RetryTrait(max_retries=3))` constructs a new class via MRO composition. `@mixin(...)` decorator applies traits to existing classes.

### 3.7 Capability Directory (MongoDB & Neo4j)
- **What:** Metadata store of registered agents, adapters, schemas, and graph relationships.
- **Why:** Enables fast lookup, versioning, rollback, and lineage analysis.
- **How:** MongoDB collections store metadata; optional Neo4j graph enhances complex relationship queries.

### 3.8 Context & Memory Store

#### 3.8.1 Infrastructure Tier (Redis & Milvus)
- **What:** Stores conversation snapshots, vector embeddings, and summaries.
- **Why:** Supports RAG, long-term context, and PII masking.
- **How:** Redis caches LLM summaries; Milvus manages large-scale vector indices.

#### 3.8.2 SDK Memory System (in-process, zero dependencies)
The SDK ships a four-tier memory hierarchy managed by `MemoryManager`:

| Tier | Class | Capacity | TTL | Primary Use |
|------|-------|----------|-----|-------------|
| **Short-term** | `ShortTermMemory` | Configurable LRU | Per-entry TTL | Current task context, scratchpad |
| **Episodic** | `EpisodicMemory` | Rolling deque | N/A | Interaction history, audit trail |
| **Working** | `WorkingMemory` | Sliding window | N/A | LLM context injection, recent tools |
| **Semantic** | `SemanticMemory` | Unlimited | N/A | Facts, embeddings, long-term knowledge |

`MemoryManager` provides a unified facade: `remember()`, `recall()`, `search()`, `record_episode()`, `inject_context()`, and `consolidate()` (promotes short-term → semantic via forgetting-curve simulation).

#### 3.8.3 Advanced Memory (VectorMemory, ForgettingCurve, ContextualMemory)
- `VectorMemory` — pure-Python cosine similarity store; pluggable embedding function with keyword fallback.
- `ForgettingCurve` — Ebbinghaus retention model `R = e^(-t/S)` with `review()` strengthening and `due_for_review()` scheduling.
- `MemoryIndex` — TF-IDF inverted index for fast tag/keyword lookup.
- `ContextualMemory` — auto-selects relevant items from VectorMemory + MemoryIndex given a context dict.
- `PersistentMemory` — JSON file-backed store with `auto_save`.
- `AdvancedMemoryManager` — unified facade over all advanced tiers with consolidation and pruning.

### 3.9 Caching System
The SDK provides a complete multi-tier async caching stack:

| Class | Algorithm | Async | Use case |
|-------|-----------|-------|----------|
| `LRUCache` | LRU + optional TTL | No | Sync hot-path caching |
| `TTLCache` | TTL-only, lazy+eager eviction | No | Time-bounded results |
| `AsyncCache` | LRU + stampede protection | Yes | Agent call deduplication |
| `MultiTierCache` | L1→L2 write-through | Yes | Tiered latency optimization |
| `CacheManager` | Named registry | Yes | Cross-agent cache sharing |

`@cached` decorator wraps any async function with an exposed `.cache` and `.cache_clear()`. `make_cache_key()` produces stable 16-char hex keys from arbitrary argument types.

### 3.10 Session Management
`SessionManager` provides full agent session lifecycle:
- `create_session(agent_id, ttl=3600)` → `SessionContext` with `ACTIVE/IDLE/EXPIRED/TERMINATED` states.
- `with_session(agent_id)` async context manager (create on enter, terminate on exit).
- `touch_session()` refreshes idle timer; `cleanup()` removes expired sessions.
- `add_hook(event_type, fn)` fires async callbacks on `"create"` / `"terminate"` events.
- `SessionMiddleware` wraps agent callables, auto-injecting session context into `ctx["_session"]`.

### 3.11 Reactive State & State Initialization
- `StateSchema` — declare field types and defaults `{key: (type, default)}`.
- `StateInitializer` — produce validated, fully-populated state dicts from schema + overrides.
- `StateValidator` — composable rules: `require()`, `type_check()`, `range()`, `custom(predicate, msg)`.
- `ReactiveState` — observable dict; watchers (sync or async) fire on key mutation.
- `ComputedState` — derived properties that auto-recompute lazily when declared dependencies change.
- `StateTransition` — typed `(from_state → to_state)` with `guard`, `on_enter`, `on_exit` hooks.
- `StateMachineState` — named node with outgoing transitions for graph/state-machine backends.

### 3.12 Step Composition
Operator overloading enables pipeline construction without boilerplate:

```python
pipeline = (fetch >> clean) | (validate >> enrich) & summarize
result = await pipeline.run(ctx)
```

| Operator | Class | Semantics |
|----------|-------|-----------|
| `>>` | `StepSequence` | Sequential — output of left fed to right |
| `\|` | `ParallelStep` | Concurrent — both run, outputs collected |
| `&` | `FanInStep` | Fan-in — both run, outputs merged into one dict |

`BranchStep` adds condition-based routing; `LoopStep` iterates until a predicate or `max_iterations`. `Compose` factory provides named constructors (`sequence()`, `parallel()`, `fan_in()`).

### 3.13 Hierarchical Agent Structures
- `AgentRole` enum: `COORDINATOR`, `WORKER`, `CRITIC`, `PLANNER`, `MONITOR`, `GATEWAY`.
- `AgentSpec` — typed agent descriptor with role, capabilities, version.
- `AgentGroup` — collection of `AgentSpec` with role-based lookup.
- `AgentHierarchy` — multi-level tree with `visualize()` (ASCII), `to_dict()`, `find_agent()`, `agents_by_role()`.
- `TypedStep` — pipeline step with JSON schema input/output validation via `validate_input()`.
- `HierarchicalPipeline` — nestable pipeline with `flatten()`, `describe()`, and `embed()` sub-pipeline.

### 3.14 Performance Optimization

| Component | Purpose |
|-----------|---------|
| `AgentProfiler` | Per-agent timing: `avg_ms`, `p95_ms`, ASCII `report()` table |
| `BatchExecutor` | Groups async calls into batches; auto-flush on `batch_size` or `flush_interval` |
| `ConnectionPool` | Generic async connection pool with `acquire()` context manager, `min/max_size` |
| `LazyValue` | Deferred computation — evaluates once on first `await get()`, then cached |
| `RateLimiter` | Token-bucket rate limiter — `acquire(tokens)` async blocking, `try_acquire()` non-blocking |
| `ExecutionOptimizer` | Composes profiler + rate_limiter + batch_executor into a single `optimize(fn)` wrapper |

### 3.15 Method Overloading & Multi-Dispatch
- `@overload(*types)` — register multiple implementations of a function keyed by argument types.
- `MultiMethod` — n-ary dispatch: select handler by the full tuple of argument types, with base-class fallback.
- `implements(*protocols)` — class decorator that asserts all protocol methods are present at definition time.
- `cooperative_init()` — helper for correct `super().__init__()` chaining in diamond inheritance.

### 3.16 Time-Travel Debugging
- `WorkflowDebugger` — captures a `Snapshot` per step (input, output, context, timing, status).
- `list_steps(workflow_id)`, `get_snapshot(workflow_id, step=N)` — inspect full pipeline state.
- `diff(workflow_id, step_a, step_b)` — show what changed between any two steps.
- `replay_from(workflow_id, from_step, agents={}, patch={})` — replay from step N with patched agents or inputs; upstream steps are frozen (not re-run).
- `export_trace(workflow_id)` — JSON-serialisable list of all snapshots for observability backends.
- `InMemorySnapshotStore` (ephemeral) and `SQLiteSnapshotStore` (cross-session persistence).

### 3.17 Feedback Pipeline & Learning Loops
- **What:** End-to-end feedback capture and processing pipeline.
- **Why:** Powers continuous improvement via RL reward shaping, RLHF fine-tuning, prompt/flow DSL optimization, and guardrail evolution.
- **How:**
  1. **Capture**: FeedbackCollector ingests human corrections and low-confidence flags.
  2. **Store**: Postgres centralizes feedback records.
  3. **Process**: Modules (FeedbackRewarder, RLHFTrainer, PromptOptimizer, FlowOptimizer, RuleMiner, TestCaseGenerator) consume and act on feedback.

### 3.18 Observability & Resilience
- **What:** Metrics, tracing, chaos, and autoscaling subsystems.
- **Why:** Ensures SLA adherence, root-cause analysis, and self-healing under failure modes.
- **How:** Prometheus & Grafana for metrics, Jaeger for tracing, Chaos Mesh for fault injection, Kubernetes HPA for scaling.

### 3.19 AgentFactory & ToolFactory
- **What:** LLM-driven code generation services.
- **Why:** Reduces manual scaffolding and accelerates onboarding of new capabilities.
- **How:** On missing capability detection, generate code via LLM prompts, commit to repo, build image, deploy locally.

### 3.20 CI/CD & Local Docker Deployment
- **What:** Full-stack local deployment via Docker Compose.
- **Why:** Facilitates rapid prototyping and research without cloud dependencies.
- **How:** Includes Traefik, Kafka, databases (MongoDB, Redis, Postgres, Milvus), core services, agents, and observability stack.

---

## 4. SDK Public API Reference (v0.3)

The `multigen` Python package exposes **152 public symbols** via `from multigen import ...`. All modules run in-process with zero external dependencies.

### 4.1 Execution Primitives

| Import | Description |
|--------|-------------|
| `Chain`, `Pipeline` | Sequential agent execution with optional middleware |
| `Parallel`, `FanOut`, `MapReduce`, `Race`, `Batch` | Concurrent execution patterns |
| `Graph`, `GraphResult` | DAG workflow with cycle detection and conditional edges |
| `StateMachine`, `Sampler`, `EnsembleResult` | Probabilistic MCMC state machine |
| `Runtime`, `get_runtime` | Unified orchestrator; optional Temporal/Kafka backends |

### 4.2 Agent Types

| Import | Description |
|--------|-------------|
| `BaseAgent`, `@agent` | Abstract base and decorator |
| `FunctionAgent` | Wraps any Python callable |
| `LLMAgent` | Prompt-templated LLM call |
| `RouterAgent` | Routes to sub-agents by predicate |
| `AggregatorAgent` | Merges multiple agent outputs |
| `CircuitBreakerAgent` | Trip/recover on failure threshold |
| `RetryAgent` | Configurable retry with backoff |
| `MemoryAgent` | Persists context across calls |
| `HumanInLoopAgent` | Pauses for human approval signal |

### 4.3 Memory System

| Import | Description |
|--------|-------------|
| `ShortTermMemory` | LRU + TTL session-scoped store |
| `EpisodicMemory` | Ordered interaction episode log |
| `WorkingMemory` | Bounded sliding window |
| `SemanticMemory` | Embedding or keyword fact store |
| `MemoryManager` | Unified four-tier facade |
| `VectorMemory` | Cosine-similarity vector store |
| `ForgettingCurve` | Ebbinghaus retention model |
| `MemoryIndex` | TF-IDF inverted index |
| `ContextualMemory` | Context-aware auto-retrieval |
| `PersistentMemory` | JSON file-backed persistence |
| `AdvancedMemoryManager` | All advanced tiers unified |

### 4.4 Caching

| Import | Description |
|--------|-------------|
| `LRUCache` | Synchronous LRU + optional TTL |
| `TTLCache` | TTL-only with eager eviction |
| `AsyncCache` | Async LRU with stampede protection |
| `MultiTierCache` | L1→L2 write-through |
| `CacheManager` | Named registry of AsyncCaches |
| `@cached` | Async function caching decorator |
| `make_cache_key` | Stable hex key from args |

### 4.5 Messaging

| Import | Description |
|--------|-------------|
| `InMemoryBus`, `Message` | Basic pub/sub event bus |
| `AdvancedMessageBus` | Priority pub/sub, request/reply, scatter-gather |
| `AdvancedMessage` | Priority envelope with correlation ID |
| `MessageFilter` | Composable predicate (`&`, `\|`, `~`) |
| `MessagePipeline` | Async middleware chain |
| `MessageRouter` | Content-based routing |
| `DeadLetterQueue` | Undeliverable message capture |
| `PriorityMessageQueue` | Asyncio min-heap queue |

### 4.6 Step Composition

| Import | Description |
|--------|-------------|
| `Step` | Single composable unit with `>>`, `\|`, `&` |
| `StepSequence` | `a >> b` — sequential pipeline |
| `ParallelStep` | `a \| b` — concurrent execution |
| `FanInStep` | `a & b` — run both, merge outputs |
| `BranchStep` | Condition-based routing |
| `LoopStep` | Iterate until predicate or max |
| `Compose` | Factory: `sequence()`, `parallel()`, `fan_in()` |

### 4.7 Hierarchical Structures

| Import | Description |
|--------|-------------|
| `AgentRole` | Enum: COORDINATOR, WORKER, CRITIC, PLANNER, MONITOR, GATEWAY |
| `StepKind` | Enum: TRANSFORM, FILTER, AGGREGATE, BRANCH, LOOP |
| `AgentSpec` | Typed agent descriptor |
| `AgentGroup` | Collection with role-based lookup |
| `AgentHierarchy` | Multi-level tree with `visualize()` |
| `TypedStep` | Schema-validated pipeline step |
| `HierarchicalPipeline` | Nestable pipeline with `flatten()` |

### 4.8 Session Management & State

| Import | Description |
|--------|-------------|
| `SessionManager` | Session lifecycle facade |
| `SessionContext` | Session data with TTL, state, hooks |
| `SessionState` | Enum: ACTIVE, IDLE, EXPIRED, TERMINATED |
| `SessionMiddleware` | Auto-inject session into agent ctx |
| `StateSchema` | Declare field types and defaults |
| `StateInitializer` | Build validated state dicts |
| `StateValidator` | Composable validation rules |
| `ReactiveState` | Observable dict with watchers |
| `ComputedState` | Lazy derived properties |
| `StateTransition` | Typed transition with guard/hooks |
| `StateMachineState` | Named node for state machines |

### 4.9 Inheritance, Polymorphism & Overloading

| Import | Description |
|--------|-------------|
| `InheritableAgent` | Hooks: `pre_run`, `run`, `post_run`, `on_error` |
| `Trait` | Base for composable behaviours |
| `LoggingTrait`, `RetryTrait`, `CachingTrait`, `TimingTrait`, `ValidatingTrait` | Built-in traits |
| `build_agent(base, *traits)` | Class factory via MRO composition |
| `@mixin(*traits)` | Apply traits without subclassing |
| `@overload(*types)` | Type-dispatched function overloading |
| `MultiMethod` | N-ary type dispatch |
| `implements(*protocols)` | Protocol conformance check at class definition |
| `PolymorphicAgent` | Runtime shape selection via `morph_fn` |
| `ShapeRegistry`, `AgentShape` | Named shape registry |
| `TypeAdapter` | Type conversion registry |
| `DynamicAgent` | Runtime class factory |

### 4.10 Performance

| Import | Description |
|--------|-------------|
| `AgentProfiler` | Per-agent timing with `p95_ms`, ASCII `report()` |
| `BatchExecutor` | Auto-flush batched async calls |
| `ConnectionPool` | Generic async pool with `acquire()` context manager |
| `LazyValue`, `@lazy` | Deferred single-evaluation computation |
| `RateLimiter` | Token-bucket rate limiter |
| `ExecutionOptimizer` | Profiler + rate limiter + batch composer |

### 4.11 Debugging

| Import | Description |
|--------|-------------|
| `WorkflowDebugger` | Time-travel: snapshot, replay, diff, export |
| `Snapshot`, `SnapshotStore` | Snapshot dataclass and ABC |
| `InMemorySnapshotStore` | Ephemeral in-process store |
| `SQLiteSnapshotStore` | Cross-session file-backed store |

---

## 5. Gaps & Future Directions

| Domain | Current State | Potential Enhancement |
|--------|--------------|----------------------|
| **DSL Expressivity** | `dynamic_parallel`, `loop`, `conditional` in local runner | Temporal-native fan-out DSL; generator-style streaming steps |
| **Security** | Local secrets in `.env` | HashiCorp Vault / Kubernetes Secrets integration |
| **Testing** | Unit + integration tests for core paths | Full contract testing, sandboxed simulation environment |
| **Edge Deployments** | No offline/air-gapped support | Edge sync agents with local queues and eventual consistency |
| **Community Market** | No marketplace for shared agents | Launch registry and rating system for community assets |
| **Visualization** | Static DAG views + `AgentHierarchy.visualize()` ASCII | Real-time collaborative flow editor; interactive trace explorer |
| **Distributed Memory** | In-process tiers only | Distributed `VectorMemory` backend (Qdrant, Weaviate, pgvector) |
| **Persistent Sessions** | `InMemorySessionStore` only | Redis/Postgres `SessionStore` backends |
| **Typed Schemas** | `TypedStep` with JSON schema dict | Pydantic v2 / msgspec integration for zero-boilerplate schemas |
| **Observability SDK** | `export_trace()` JSON export | OpenTelemetry spans from WorkflowDebugger; OTLP exporter |

---

## 6. Evolution Roadmap

1. **v0.3 (current):** Full local SDK — memory, caching, messaging, session, state, composition, hierarchy, polymorphism, performance, time-travel debugging. 152 public exports, zero external dependencies.
2. **v0.4:** Distributed memory backends (Redis session store, Qdrant vector memory); OpenTelemetry trace export from WorkflowDebugger; Pydantic schema integration for TypedStep.
3. **v1.0:** Core orchestration, DSL, guardrails, local docker prototype.
4. **v1.1:** DynamicSpawning, LangChain/LlamaIndex agents, initial feedback loops.
5. **v1.2:** RL policy integration, Prompt/Flow optimizers, AgentFactory/ToolFactory.
6. **v2.0:** Graph-based lineage (Neo4j), feature flags, canary rollouts.
7. **v3.0:** Marketplace, edge/offline support, collaborative visual design.

---

## 7. End-to-End Example Workflow

Below is a step-by-step narrative showing how a **fact-checking** use case flows through Multigen, illustrating each component in action:

1. **User Request**
   ```json
   {
     "workflow": "fact_check",
     "input": { "statement": "The Eiffel Tower is in Berlin." }
   }
   ```

2. **API Gateway** — Receives the HTTP POST to `/workflows/run`, validates JWT, routes to the Orchestrator.

3. **Orchestrator & RL Policy** — Calls `RLPolicy.selectAction(state)` → selects `FactExtractionAgent`; looks up Capability Directory.

4. **Guardrails Engine (Pre-Invoke)** — Verifies cost and PII rules; returns `allow`.

5. **Flow Engine Execution**
   ```yaml
   - name: extract_facts
     agent: FactExtractionAgent
   - name: retrieve_evidence
     parallel:
       - agent: WikiSearchAgent
       - agent: DBSearchAgent
   - name: verify_sources
     agent: SourceVerifierAgent
   - name: judge_factuality
     agent: FactCheckerAgent
   - name: escalate_if_needed
     conditional:
       - condition: "{{ judge_factuality.output.confidence < 0.8 }}"
         then: agent: ApprovalEngine
       - else: agent: ReportGeneratorAgent
   ```

6. **Message Bus (Kafka)** — Publishes `invocation` events; agents subscribe, process, publish `response` events.

7. **Agents & ToolAdapters**
   - `FactExtractionAgent` extracts keywords.
   - `WikiSearchAgent` and `DBSearchAgent` run in parallel.
   - `SourceVerifierAgent` filters credible sources.
   - `FactCheckerAgent` (LLM) returns `{ valid: false, confidence: 0.95 }`.

8. **Guardrails Engine (Post-Invoke)** — Validates response, checks for disallowed content.

9. **Conditional Escalation** — `confidence >= 0.8` → routes to `ReportGeneratorAgent`.

10. **Report Generation** — Returns: *"Your statement is incorrect. The Eiffel Tower is in Paris."*

11. **Feedback Collection & Learning** — User corrects phrasing; PromptOptimizer updates system prompt; FeedbackRewarder generates RL reward signal.

12. **Observability & Autoscaling** — Prometheus records latency; HPA scales on queue lag; Chaos Mesh validates recovery paths.

---

## 7.1 Local SDK Usage (Zero Dependencies)

The same fact-checking pipeline can run entirely in-process using the v0.3 SDK:

```python
import asyncio
import sys
sys.path.insert(0, 'sdk')

from multigen import (
    FunctionAgent, Chain, Runtime,
    MemoryManager, AdvancedMessageBus, AdvancedMessage,
    SessionManager, ReactiveState, StateSchema, StateInitializer,
    AgentProfiler, WorkflowDebugger,
    InheritableAgent, LoggingTrait, RetryTrait, build_agent,
    Step, Compose,
)

# ── Memory ────────────────────────────────────────────────────────────────────
memory = MemoryManager()
memory.remember("domain", "fact-checking", tier="semantic", description="task domain")

# ── Session ───────────────────────────────────────────────────────────────────
session_mgr = SessionManager(default_ttl=3600)

# ── Reactive state ────────────────────────────────────────────────────────────
schema = StateSchema({"statement": str, "confidence": (float, 0.0), "verdict": (str, "unknown")})
state  = StateInitializer(schema).create({"statement": "The Eiffel Tower is in Berlin."})
rs     = ReactiveState(state)
rs.watch("verdict", lambda old, new: print(f"Verdict changed: {old} → {new}"))

# ── Agents with traits ────────────────────────────────────────────────────────
class ExtractAgent(InheritableAgent):
    async def run(self, ctx):
        return {"keywords": ctx["statement"].lower().split()}

class CheckAgent(InheritableAgent):
    async def run(self, ctx):
        kw = ctx.get("keywords", [])
        verdict  = "incorrect" if "berlin" in kw else "correct"
        confidence = 0.95
        rs["verdict"]    = verdict
        rs["confidence"] = confidence
        return {"verdict": verdict, "confidence": confidence}

ExtractAgentCls = build_agent(ExtractAgent, LoggingTrait)
CheckAgentCls   = build_agent(CheckAgent,   LoggingTrait, RetryTrait(max_retries=2))

# ── Step composition ──────────────────────────────────────────────────────────
extract_step = Step("extract", ExtractAgentCls().run)
check_step   = Step("check",   CheckAgentCls().run)
pipeline     = extract_step >> check_step

# ── Profiler + debugger ───────────────────────────────────────────────────────
profiler = AgentProfiler()
debugger = WorkflowDebugger()

# ── Run ───────────────────────────────────────────────────────────────────────
async def main():
    async with session_mgr.with_session("fact-checker") as session:
        ctx = {"statement": "The Eiffel Tower is in Berlin.", "_session": session}
        result = await pipeline.run(ctx)
        print("Result:", result)

    print(profiler.report())
    trace = await debugger.export_trace("fact-checker")
    print(f"Trace has {len(trace)} steps")

asyncio.run(main())
```

---

## 7.2 Reasoning Example: Chain-of-Thought & Consensus

To illustrate Multigen's internal reasoning capabilities, consider a **chain-of-thought** scenario in the `FactCheckerAgent` step:

1. **Invocation:**
   ```json
   { "step": "judge_factuality", "params": { "fact": "Eiffel Tower is in Berlin", "evidence": ["Paris article", "Berlin source"] } }
   ```

2. **Agent Prompt:**
   ```text
   You are a fact-checking assistant. Step-by-step, analyze the fact:
   1. Identify keywords in the statement.
   2. Review each piece of evidence.
   3. Weigh credibility by source.
   4. Conclude whether the fact is correct.
   Provide your internal reasoning and final verdict.
   ```

3. **Chain-of-Thought Output:**
   ```json
   {
     "reasoning": [
       "Keyword identified: 'Eiffel Tower', 'Berlin'",
       "Source1: Paris article says Eiffel Tower is in Paris",
       "Source2: Berlin site makes no mention of Eiffel Tower",
       "Source1 is a credible encyclopedia; high weight assigned",
       "No credible evidence supports Berlin location",
       "Conclusion: Statement is incorrect"
     ],
     "verdict": "incorrect",
     "confidence": 0.97
   }
   ```

4. **Consensus Mechanism:**
   ```yaml
   - name: judge_factuality
     parallel:
       - agent: FactCheckerAgent-v1
       - agent: FactCheckerAgent-v2
       - agent: FactCheckerAgent-v3
   - name: aggregate_verdicts
     agent: ConsensusAgent
     params:
       results: "{{ judge_factuality.output }}"
       method: "majority_vote"
   ```
   `ConsensusAgent` collects multiple agent reasonings and performs majority voting or weighted averaging to produce a final verdict.

5. **Guardrails Validation:** Before accepting the reasoning output, the Guardrails Engine checks for disallowed content (bias, toxicity) and verifies PII compliance.

---

## 8. Notebook Index

| # | Title | Key Features |
|---|-------|-------------|
| 01 | Quickstart | Basic workflow, API client |
| 02 | Graph Workflow | DAG, conditional edges |
| 03 | Signals & Control | Runtime signals, pause/resume |
| 04 | Circuit Breakers | Trip/recover patterns |
| 05 | Reflection Loops | Confidence-based re-evaluation |
| 06 | Fan-Out & Consensus | Dynamic spawning, voting |
| 07 | Dynamic Agents | Runtime agent creation |
| 08 | Observability | Prometheus, traces, events |
| 09 | MMM Autopilot | Marketing mix modeling |
| 10 | Autonomy & Transparency | Epistemic envelopes |
| 11 | Parallel BFS & Streaming A2A | Breadth-first agent search |
| 12 | Local SDK Agents | Zero-server local execution |
| 13 | Sequential Chains | Chain, Pipeline, middleware |
| 14 | Parallel Execution | FanOut, MapReduce, Race, Batch |
| 15 | Graph DAG (Local) | Local graph executor |
| 16 | MCMC State Machine | Probabilistic transitions |
| 17 | Event Bus & Messaging | InMemoryBus pub/sub |
| 18 | Runtime & Simulator | Unified runtime + UI events |
| 19 | Resilience Patterns | Retry, CircuitBreaker, MemoryAgent |
| 20 | Time-Travel Debugging | Snapshots, replay, diff, export |
| **21** | **Memory System** | ShortTerm, Episodic, Working, Semantic, MemoryManager |
| **22** | **Caching** | LRU, TTL, AsyncCache, MultiTier, @cached |
| **23** | **Step Composition** | `>>`, `\|`, `&`, Branch, Loop, Compose |
| **24** | **Hierarchical Structures** | AgentHierarchy, TypedStep, HierarchicalPipeline |
| **25** | **Advanced Messaging** | Priority pub/sub, request/reply, scatter-gather, DLQ |
| **26** | **Session & State** | SessionManager, ReactiveState, ComputedState, StateValidator |
| **27** | **Inheritance & Polymorphism** | Traits, build_agent, overload, MultiMethod, PolymorphicAgent |
| **28** | **Performance & Advanced Memory** | Profiler, BatchExecutor, VectorMemory, ForgettingCurve |
| **29** | **Persistence** | SQLiteCheckpointStore, DurableQueue, CheckpointedRuntime |
| **30** | **Eval & Measurement** | EvalSuite, Benchmark, LLMJudge, F1Score, Latency |
| **31** | **Multi-Model Routing** | ModelPool, CostRouter, QualityRouter, AdaptiveRouter, FallbackRouter |
| **32** | **Tool Registry & Sandboxing** | ToolRegistry, Sandbox, PermissionedRegistry |
| **33** | **Planning (ToT/GoT/ReAct)** | TreeOfThoughts, GraphOfThoughts, ReActPlanner, PlanAndExecute |
| **34** | **Workflow Versioning** | VersionedWorkflow, WorkflowDiff, ChangeLog, SQLiteVersionStore |
| **35** | **Scheduling & Triggers** | Scheduler, CronSchedule, IntervalSchedule, Trigger |
| **36** | **Continuous Learning** | AdaptivePrompt, OnlineLearner, FewShotSelector, ContinuousLearner |
| **37** | **A/B Testing & Safety** | ABTest, CanaryRollout, RollbackManager, SafetyGuard, PIIRedactor |
| **38** | **Resilience** | RetryPolicy, Deadline, DeadlineGuard, WorkflowRetry |
| **39** | **Advanced Planning** | HierarchicalDecomposer, MCTSPlanner, AutoGPTQueue, HierarchicalSummariser |
| **40** | **Streaming** | StreamingAgent, ParallelStreamer, PartialResultBus |
| **41** | **Online Optimization** | PromptBandit, FewShotLibrary, AgentSpecialisation, OptimizationManager |
| **42** | **WorkingMemory Wiring** | LLMAgent + WorkingMemory integration, template injection |

---

## 9. Phase Overview

| Phase | SDK Version | Key Additions |
| --- | --- | --- |
| Phase 1 | v0.1 | Agents, Chain, Parallel, Graph, StateMachine, Bus, Runtime |
| Phase 2 | v0.2 | Memory, Cache, Compose, Hierarchy, Messaging, Advanced Memory |
| Phase 3 | v0.3 | Polymorphism, Performance, Session, StateInit, Inheritance |
| Phase 3b | v0.4 | Persistence, Eval, Routing, Tools, Planning, Versioning, Scheduler, Learning |
| Phase 4 | v0.5 | DSL Versioning+A/B, Safety, Resilience |
| Phase 4b | **v0.6** | Advanced Planning (MCTS/AutoGPT/HierDecomp), Streaming, Online Optimization, WorkingMemory wiring |

---

*This document equips architects and researchers with a holistic understanding of Multigen's design principles, structural components, public API, and strategic evolution. For implementation details see the inline module docstrings and the notebook series above.*
