# Multigen: A Technical Whitepaper

## Enterprise-Grade Autonomous Multi-Agent Orchestration with Durable Execution, Epistemic Transparency, and Human Governance

Version 0.3 — March 2026

---

## Abstract

Modern AI systems face a fundamental tension: they must be autonomous enough to be useful, but controllable enough to be trusted. Existing multi-agent frameworks solve for autonomy at the expense of reliability, auditability, and human oversight. Multigen is an open-source orchestration framework that resolves this tension through six core architectural commitments: **durable execution** (workflows survive infrastructure failures), **real-time runtime control** (running workflows are steerable without restart), **dynamic self-expansion** (missing capabilities are generated and approved on demand), **epistemic transparency** (every node reports what it knows and doesn't know), **human governance** (humans approve, modify, or reject AI decisions at any point), and **scalable parallel execution** (graph nodes execute concurrently with real-time streaming visibility and cross-framework interoperability via the Agent2Agent protocol). This paper details the technical architecture, design decisions, and the engineering principles behind each pillar.

---

## Table of Contents

1. [Motivation and Problem Space](#1-motivation-and-problem-space)
2. [System Architecture](#2-system-architecture)
3. [Workflow Execution Engine](#3-workflow-execution-engine)
4. [Graph Execution and Runtime Control](#4-graph-execution-and-runtime-control)
5. [Dynamic Agent Lifecycle](#5-dynamic-agent-lifecycle)
6. [Epistemic Transparency Engine](#6-epistemic-transparency-engine)
7. [Reasoning Quality Mechanisms](#7-reasoning-quality-mechanisms)
8. [Observability Layer](#8-observability-layer)
9. [Distributed Messaging Architecture](#9-distributed-messaging-architecture)
10. [SDK and Developer Experience](#10-sdk-and-developer-experience)
11. [Security and Governance Model](#11-security-and-governance-model)

11b. [Agent2Agent (A2A) Protocol Integration](#11b-agent2agent-a2a-protocol-integration)

12. [Performance Characteristics](#12-performance-characteristics)
13. [Roadmap to Market Leadership](#13-roadmap-to-market-leadership)
14. [Parallel BFS Execution, Dependency Tracking, and Streaming](#14-parallel-bfs-execution-dependency-tracking-and-streaming)
15. [Developer Experience: CLI, Hot Reload, Type-Safe DSL, Local Execution, and Versioning](#15-developer-experience-cli-hot-reload-type-safe-dsl-local-execution-and-versioning)

---

## 1. Motivation and Problem Space

### 1.1 The State of Multi-Agent Frameworks

The rapid growth of LLM-based AI systems has produced a proliferation of agent orchestration frameworks — LangGraph, CrewAI, AutoGen, LlamaIndex Workflows, among others. Each solves a subset of the problem:

- **LangGraph** provides stateful graph execution but lacks durable execution semantics. A process crash loses all in-flight state.
- **CrewAI** provides role-based agent collaboration but offers no runtime control after workflow start.
- **AutoGen** supports conversational agent coordination but provides no production deployment infrastructure.
- **LlamaIndex Workflows** supports event-driven execution but has no built-in reliability or observability.

None of these frameworks address what we call the **enterprise triad**: reliability (crash recovery), transparency (what did the AI actually do and why?), and governance (can a human intervene?).

### 1.2 The Enterprise Requirements Gap

Deploying AI agent systems in enterprise contexts — financial analysis, legal due diligence, clinical decision support, strategic planning — requires properties that current frameworks do not provide:

| Requirement | Why it matters |
| --- | --- |
| **Crash recovery** | Workflows take hours; a single node failure should not require full restart |
| **Audit trail** | Regulators and risk teams need to know what every agent decided and why |
| **Human override** | High-stakes decisions need human approval before action |
| **Uncertainty quantification** | Agents must report what they don't know, not just what they do |
| **Runtime steering** | Business conditions change mid-workflow; operators need to redirect execution |
| **Scalable distribution** | Enterprise workflows span thousands of agents across data centers |

### 1.3 The Multigen Thesis

Multigen's thesis is simple: **an AI system's most important output is not its answer, but its uncertainty**. An answer without uncertainty metadata cannot be safely acted on, audited, or appealed. Every feature in Multigen flows from this principle — durable execution ensures the audit trail is never lost; epistemic transparency ensures uncertainty is always surfaced; human governance ensures humans can act on it.

---

## 2. System Architecture

### 2.1 High-Level Topology

```text
┌──────────────────────────────────────────────────────────────────────────┐
│                            CLIENT LAYER                                  │
│   Python SDK (sync/async)  │  REST API  │  MCP Server  │  Jupyter/CLI   │
└────────────────────────────────────┬─────────────────────────────────────┘
                                     │ HTTP / MCP
┌────────────────────────────────────▼─────────────────────────────────────┐
│                         ORCHESTRATION LAYER                              │
│                     FastAPI Orchestrator :8000                           │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────┐  ┌───────────────┐  │
│  │  DSL Parser │  │ LLM text→DSL │  │ Graph Signal│  │  OTel Tracer  │  │
│  │  Validator  │  │  (GPT-4o)    │  │ Controller  │  │  Prometheus   │  │
│  └──────┬──────┘  └──────────────┘  └──────┬──────┘  └───────────────┘  │
└─────────┼───────────────────────────────────┼────────────────────────────┘
          │ validate capability              │ Temporal client signals
          ▼                                  ▼
┌──────────────────┐          ┌──────────────────────────────────────────┐
│  Capability Svc  │          │              TEMPORAL SERVER             │
│  :8001 (MongoDB) │          │   ┌─────────────────────────────────┐   │
│  • register      │          │   │    ComplexSequenceWorkflow       │   │
│  • lookup        │          │   │    (sequential, parallel,        │   │
│  • version       │          │   │     conditional, dynamic)        │   │
└──────────────────┘          │   └─────────────────────────────────┘   │
                              │   ┌─────────────────────────────────┐   │
         ┌────────────────────│   │         GraphWorkflow            │   │
         │ Kafka publish      │   │    • BFS execution engine        │   │
         ▼                    │   │    • 10 signal handlers          │   │
┌─────────────────┐           │   │    • 6 query handlers            │   │
│  Apache Kafka   │           │   │    • Epistemic state tracker     │   │
│  flow-requests  ├──────────►│   │    • Circuit breaker registry    │   │
│  flow-responses │           │   └─────────────────────────────────┘   │
│  flow-dead-lttr │           └────────────────────┬─────────────────────┘
└─────────────────┘                                │ Temporal activity dispatch
                              ┌─────────────────────▼────────────────────┐
                              │           TEMPORAL WORKER                 │
                              │  ┌────────────────┐  ┌─────────────────┐ │
                              │  │  Agent Registry │  │  Tool Adapters  │ │
                              │  │  EchoAgent      │  │  HTTP, RAG,     │ │
                              │  │  LangChainAgent │  │  Math, Code, DB │ │
                              │  │  LlamaIndexAgent│  └─────────────────┘ │
                              │  │  ScreeningAgents│  ┌─────────────────┐ │
                              │  │  PatternAgents  │  │  BlueprintAgent │ │
                              │  │  + Dynamic      │  │  Factory        │ │
                              │  └────────────────┘  └─────────────────┘ │
                              └──────────────────────────────────────────┘
                                             │
                              ┌──────────────▼───────────────┐
                              │          PERSISTENCE          │
                              │  MongoDB: graph_state CQRS   │
                              │  Temporal: event history      │
                              └──────────────────────────────┘

```
### 2.2 Data Flow

**Sequence Workflow path:**

```text
Client → POST /workflows/run → DSL Parser → Capability Validator
       → Kafka publish(flow-requests) → FlowWorker consume
       → Temporal.start_workflow(ComplexSequenceWorkflow)
       → Worker executes agent_activity per step
       → Results stored in Temporal event history
       → Response via Temporal query or Kafka flow-responses

```
**Graph Workflow path:**

```text
Client → POST /workflows/run-graph → GraphRunRequest parse
       → Temporal.start_workflow(GraphWorkflow, graph_def)
       → BFS queue begins from entry node
       → Per node: circuit breaker check → tools → _ensure_agent
         → agent_activity → epistemic recording → edge evaluation
       → Real-time signals modify execution (interrupt/inject/fan-out/etc.)
       → Persist to MongoDB (non-fatal, CQRS read model)
       → Final epistemic report computed
       → Dynamic agents deregistered

```
### 2.3 Component Responsibilities

| Component | Responsibility | Technology |
| --- | --- | --- |
| Orchestrator | API gateway, DSL parsing, workflow dispatch, signal forwarding | FastAPI, Pydantic v2 |
| Capability Service | Agent capability registry (name, version, description, metadata) | FastAPI, MongoDB |
| Temporal Server | Workflow state machine, event history, retry, scheduling | Temporal.io |
| Temporal Worker | Activity execution host, agent registry, tool adapters | Python, temporalio SDK |
| Kafka | Async workflow submission, distributed fan-out, dead letter | Apache Kafka, confluent-kafka |
| MongoDB | CQRS read model (graph state), capability directory | Motor (async driver) |
| SDK | Client library for workflow submission and control | httpx, Pydantic |
| MCP Server | AI assistant integration protocol | Model Context Protocol |

---

## 3. Workflow Execution Engine

### 3.1 ComplexSequenceWorkflow

The `ComplexSequenceWorkflow` is a Temporal workflow that executes a tree-structured DSL. It supports four step types:

**Sequential**: Linear execution, output of each step feeds into the next via `_resolve_refs`.

```text
step_1 → step_2 → step_3

```
**Parallel**: `asyncio.gather()` executes all branches concurrently. Results are merged into the context under each branch name.

```text
          ┌── branch_A ──┐
step_1 → ─┤              ├─→ merge → step_3
          └── branch_B ──┘

```
**Conditional**: Safe AST-based condition evaluation (no `eval()`). Supports `==`, `!=`, `<`, `>`, `<=`, `>=`, `and`, `or`, `not`, subscript access, and attribute access. References step outputs via `steps.stepName.output.key`.

```text
step_1 → condition(steps.review.output.risk > 0.7)
              ├── if_true  → escalate_agent
              └── if_false → approve_agent

```
**Dynamic Subtree**: A spawner agent generates a list of step definitions at runtime. The workflow then executes this dynamically generated subtree before proceeding.

```text
spawner_agent → [generated_step_1, generated_step_2, ...] → continue

```
### 3.2 Reference Resolution

Step parameters can reference outputs from previous steps using the `{{steps.name.output.key}}` template syntax:

```python
{"company_data": "{{steps.ingest.output.company_profile}}"}

```
The `_resolve_refs()` function walks the parameter dict recursively, resolving any string matching the template pattern. This allows composing agent inputs from upstream agent outputs without coupling the DSL to specific data schemas.

### 3.3 Temporal Durability Guarantees

Temporal's event-sourced workflow model provides the following guarantees:

- **At-least-once activity execution**: Every `execute_activity` call is retried until it succeeds or hits maximum attempts.
- **Exactly-once workflow state transitions**: Workflow code is deterministic — on replay after a crash, the same decisions are made using the same recorded history.
- **Long-running workflows**: Workflows can span days or weeks; there is no timeout on the workflow itself.
- **Crash recovery**: If the worker process dies mid-execution, Temporal re-dispatches pending activities to a new worker. No state is lost.

The key constraint Temporal imposes is **workflow determinism**: workflow code must make the same decisions on every replay. This means:

- No direct calls to `time.time()` or `time.monotonic()` — use `workflow.now()` instead
- No direct I/O, registry mutation, or LLM calls from workflow code — use activities
- No randomness in workflow branching logic

Multigen handles this by routing all non-deterministic operations (LLM calls, registry mutation, database writes) through Temporal activities. The workflow code only manipulates in-memory state and dispatches activities.

---

## 4. Graph Execution and Runtime Control

### 4.1 GraphWorkflow Parallel BFS Execution Model

`GraphWorkflow` executes a directed graph using a **dependency-aware parallel Breadth-First Search**. In each iteration all nodes whose dependencies are satisfied execute simultaneously via `asyncio.gather()`. This is a deliberate design choice over sequential BFS or DFS because:

- Independent nodes (siblings in the DAG) run in the same wall-clock round
- A 5-node expert panel that took 150s sequentially now completes in ~30s (the slowest expert)
- Fan-out latency collapses from O(N × latency) to O(max(latency))
- Dependency tracking is exact — no node executes before its inputs are ready

```text
Execution state:
  pending     : deque([entry_node])       ← FIFO BFS queue
  pending_set : set({entry_node})         ← O(1) dedup guard (prevents double-execution)
  context     : {node_id → output}        ← grows as nodes complete
  executed    : {node_id → count}         ← cycle guard

Each iteration:

  1. Handle interrupt (wait if paused)
  2. Drain prune requests
  3. Drain jump_queue → prepend to pending (+ pending_set)
  4. Drain fan_out_specs → partition-aware parallel execution + consensus
  5. Drain injected_nodes → append to pending (+ pending_set)
  6. _collect_ready_batch():

       while pending:
         nid = pending.popleft(); pending_set.discard(nid)
         if skip/cycle/CB → handle and continue
         if not _deps_satisfied(nid) → deferred.append(nid)
         else → ready_batch.append(nid)
       pending.extend(deferred); pending_set.update(deferred)

  7. asyncio.gather(*[_execute_node_full(nodes[nid]) for nid in ready_batch])
  8. For each result: _process_node_result()

       → update context[node_id]
       → append to _completed_events (streaming log)
       → persist to MongoDB (non-fatal)
       → reflection check → inject critic if needed
       → enqueue successors (deduped via pending_set)

_deps_satisfied(nid):
  edge_deps    = {e.source for e in edges if e.target == nid}
  explicit_deps = set(nodes[nid].get("depends_on", []))
  all_deps     = edge_deps | explicit_deps
  return all(dep in context for dep in all_deps)

```
The `pending_set` is the critical correctness guarantee: when nodes A and B both complete in the same parallel wave and both have C as a successor, C is only enqueued once, preventing duplicate execution.

### 4.2 Signal Architecture

Temporal signals are the mechanism for external actors to modify a running workflow. They are:

- **Asynchronous**: callers do not block waiting for the signal to be processed
- **Durable**: signals are recorded in Temporal's event history; on replay, the same signal is re-applied
- **In-order**: signals are delivered in the order they were sent

Multigen implements 10 signal handlers:

| Signal | Handler Type | Effect |
| --- | --- | --- |
| `interrupt` | Pause | Sets `_interrupted=True`; workflow blocks at next node boundary |
| `resume` | Unpause | Clears `_interrupted`; `wait_condition` unblocks |
| `inject_node` | Structural | Appends new node to `_injected` queue; node is absorbed before next BFS iteration |
| `jump_to` | Priority | Prepends node_id to `_jump_queue`; node executes next regardless of queue position |
| `skip_node` | Structural | Adds node_id to `_skip_nodes` set; silently dropped whenever reached |
| `reroute` | Structural | Appends new edge to `_raw_edges`; affects all future edge evaluations |
| `fan_out` | Parallel | Adds fan-out spec to `_fan_out_specs`; N nodes execute in parallel next iteration |
| `prune_branch` | Structural | Removes node and all reachable descendants from pending queue |
| `approve_agent` | Approval | Moves agent from `_pending_approvals` to `_approved_agents`; unblocks `wait_condition` |
| `reject_agent` | Approval | Moves agent to `_rejected_agents`; node will raise and be dead-lettered |

### 4.3 Query Architecture

Temporal queries provide synchronous read access to workflow state without modifying it. They are:

- **Synchronous**: caller blocks until the query is answered
- **Non-mutating**: queries cannot modify workflow state
- **Real-time**: reflect the current in-memory state, not a database snapshot

| Query | Returns |
| --- | --- |
| `get_health` | Interrupt state, skip set, circuit breaker trips, last 20 errors, dead letters |
| `get_metrics` | Nodes executed, skipped, reflections triggered, fan-outs, CB trips, error count |
| `get_pending_count` | Current size of the BFS pending queue |
| `get_pending_approvals` | List of agent specs awaiting human approval |
| `get_epistemic_report` | Full epistemic transparency report (all node states, debt, flags, recommendation) |
| `get_dynamic_agents` | Created, pending, approved, rejected agent names |
| `get_completed_nodes` | Append-only event log `{index, node_id, agent, confidence, timestamp, output}` + `done` flag; polled by the SSE streaming endpoint every 500 ms |

### 4.4 Conditional Edge Evaluation

Edges can carry conditions that are evaluated against the current execution context:

```python
{"source": "analysis", "target": "escalation", "condition": "confidence < 0.6"}

```
The condition evaluator uses Python's `ast` module to parse and walk the expression tree, evaluating only a whitelist of safe operations. This prevents code injection while supporting rich conditional logic:

```text
Supported: ==, !=, <, >, <=, >=, and, or, not
           Name lookup (key in context), subscript (context["key"]), attribute (obj.field)
           Numeric and string literals, boolean literals
Not supported: function calls, imports, assignments, comprehensions

```
### 4.5 Fan-Out Consensus Strategies

Fan-out executes N nodes in parallel using `asyncio.gather()` and selects a winner according to the consensus strategy:

| Strategy | Algorithm |
| --- | --- |
| `highest_confidence` | Select node output with the highest `confidence` value |
| `aggregate` | Merge all outputs; concatenate string fields, average numeric fields |
| `majority_vote` | For categorical outputs, select the most common value |
| `first_success` | Select the first non-error result (useful for race conditions) |

---

## 5. Dynamic Agent Lifecycle

### 5.1 The Self-Healing Problem

Traditional agent frameworks require all agents to be registered before any workflow starts. This creates a **closed-world assumption**: the system can only do what it was explicitly programmed to do. In real deployments, this means:

- Workflows fail with `AgentNotFoundError` on unexpected inputs
- New capabilities require code changes, testing, and redeployment
- The system cannot adapt to novel tasks at runtime

Multigen's dynamic agent lifecycle replaces this closed-world assumption with an **open-world model**: when a capability is needed that doesn't exist, the system generates it, seeks approval, and creates it.

### 5.2 Lifecycle State Machine

```text
Unknown agent encountered in node
         │
         ▼
  generate_agent_spec_activity
  (LLM or structured template)
         │
         ▼
  spec added to _pending_approvals
  workflow.wait_condition(approved OR rejected)
         │
    ┌────┴────┐
    │         │
  approved  rejected
    │         │
    ▼         ▼
  create_  node added to
  agent_   dead_letters
  activity workflow continues
    │
    ▼
  execute agent_activity
    │
    ▼
  node completes normally
    │
    ▼ (at workflow end)
  deregister_agents_activity
  (agent removed from registry)

```
### 5.3 Spec Generation

The `generate_agent_spec_activity` calls `make_agent_spec()` which:

1. If `OPENAI_API_KEY` is set: calls GPT-4o with a structured prompt requesting a JSON spec containing:
   - `agent_name`: the requested name
   - `capability_description`: what it can and cannot do
   - `system_prompt`: 200-400 word prompt (the actual agent instruction)
   - `required_inputs`: list of parameter keys needed
   - `output_schema`: keys the agent promises to return
   - `uncertainty_floor`: minimum expected uncertainty (0.0-1.0)
   - `known_limitations`: explicit list of edge cases it cannot handle
   - `estimated_confidence_range`: [min, max] expected confidence
   - `rationale`: why this agent is appropriate for the task
   - `fallback_strategy`: what to do if the agent fails

2. If no API key: generates a structured template based on domain inference from the agent name

This spec is designed to be **inspectable by humans** — every field is intended to give the reviewing human enough information to make an informed approval decision.

### 5.4 BlueprintAgent Creation

On approval, `create_agent_from_blueprint()` instantiates a `BlueprintAgent` — a concrete `BaseAgent` subclass that wraps a system prompt and instructions:

```text
approved_spec → spec_to_blueprint() → blueprint dict
             → create_agent_from_blueprint(name, blueprint)
             → BlueprintAgent(system_prompt, instruction) registered as name

```
The `BlueprintAgent` calls the configured LLM (default: GPT-4o) with the approved system prompt and the node's task parameters. It is a general-purpose LLM wrapper, parameterised entirely by the approved spec.

### 5.5 Sandbox Isolation Consideration

Temporal's workflow sandbox re-imports modules in isolation to enforce determinism. The agent registry (`orchestrator.services.agent_registry`) must be declared as a **passthrough module** so the sandbox shares the worker process's populated registry dict rather than seeing an empty sandbox-local copy:

```python
SandboxedWorkflowRunner(
    restrictions=SandboxRestrictions.default.with_passthrough_modules(
        "orchestrator.services.agent_registry",   # share live registry
        "prometheus_client",                       # avoid duplicate metric errors
        "opentelemetry",                           # OTel context propagation
        "flow_engine.graph.telemetry",             # Prometheus counters
    )
)

```
Without this passthrough, `is_agent_registered("EchoAgent")` returns `False` inside the sandbox, causing every node — including nodes with known registered agents — to incorrectly trigger the approval gate.

---

## 6. Epistemic Transparency Engine

### 6.1 Philosophy

Most AI systems are optimised to produce answers. The implicit design pressure is toward confidence — a system that hedges too much feels useless. But this creates a failure mode: **silent overconfidence**. The system is wrong, but it sounds right.

Epistemic transparency inverts this default. Every agent is required to report what it doesn't know alongside what it does. The framework treats uncertainty as a **first-class data type** — tracked, propagated, aggregated, and surfaced.

### 6.2 Epistemic Envelope Schema

Every node output should include an `epistemic` sub-dict:

```text
epistemic: {
  confidence:          float [0,1]   // overall confidence in the output
  reasoning:           str           // explanation of how the conclusion was reached
  uncertainty_sources: list[str]     // inputs that were unclear, missing, or contradictory
  assumptions:         list[str]     // what was assumed when data was absent
  known_limitations:   list[str]     // structural things this agent cannot assess
  known_unknowns:      list[str]     // identified gaps — known to be unknown
  evidence_quality:    str           // "high" | "medium" | "low" | "none"
  data_completeness:   float [0,1]   // fraction of expected inputs that were present
  propagated_uncertainty: float [0,1]// uncertainty inherited from upstream nodes
  flags:               list[str]     // "needs_human_review", "extrapolated", etc.
}

```
Agents that don't return this format receive a `default_epistemic` envelope computed from any `confidence` field present in the raw output.

### 6.3 Uncertainty Propagation

Uncertainty propagates through graph edges using a weighted geometric mean:

```text
upstream_confidences = [c1, c2, ..., cn]  (from direct upstream nodes)
geo_mean = (c1 * c2 * ... * cn) ^ (1/n)   (geometric mean penalises weak links)
inherited_uncertainty = (1 - geo_mean) * propagation_weight

effective_confidence = raw_confidence * (1 - inherited_uncertainty)
propagated_uncertainty = inherited_uncertainty

```
The geometric mean is used rather than arithmetic mean because it **penalises weak links** — if even one upstream node has very low confidence (e.g. 0.1), it drags the geometric mean significantly lower, whereas the arithmetic mean would dilute its effect among many high-confidence nodes.

`propagation_weight` (default: 0.3) controls how much upstream uncertainty bleeds into the current node. At 0.0, nodes are fully independent. At 1.0, the node is fully determined by upstream quality.

Additionally:

- `known_unknowns` from all upstream nodes are pooled and carried forward
- `needs_human_review` flags in upstream nodes propagate as `needs_human_review_upstream` flags in downstream nodes

### 6.4 EpistemicStateTracker

The `EpistemicStateTracker` maintains the epistemic state of the entire graph during execution:

```text
For each completed node:

  1. extract_epistemic(output)          → normalised epistemic envelope
  2. propagate_uncertainty(ep, upstream) → adjusted confidence + inherited unknowns
  3. _node_states[node_id] = final_ep   → stored for report generation
  4. if final_ep.known_unknowns:         → add to _epistemic_debt with severity
  5. if confidence < 0.6 or flagged:     → add to _human_review_flags

```
Epistemic debt severity:

- `confidence < 0.4` → `critical`
- `confidence < 0.6` → `high`
- `confidence < 0.75` → `medium`
- `confidence >= 0.75` → `low`

### 6.5 Transparency Report

The `get_transparency_report()` method generates a structured report:

```json
{
  "workflow_id": "...",
  "summary": {
    "nodes_assessed": 5,
    "avg_confidence": 0.78,
    "min_confidence": 0.62,
    "avg_propagated_uncertainty": 0.12,
    "epistemic_debt_items": 7,
    "nodes_flagged_for_human_review": 2,
    "weakest_evidence_quality": "low",
    "total_known_unknowns": 4
  },
  "overall_trustworthiness": "MEDIUM — results are usable but verify flagged unknowns",
  "node_states": { ... per-node epistemic state ... },
  "epistemic_debt": [ { "node_id": "...", "unknown": "...", "severity": "high" }, ... ],
  "human_review_flags": [ { "node_id": "...", "reason": "low_confidence", "confidence": 0.62 }, ... ],
  "known_unknowns_pool": [ "regulatory filing status", "customer concentration" ],
  "recommendation": "2 node(s) have been flagged for human review. Inspect human_review_flags."
}

```
This report is:

- **Live-queryable** via `GET /workflows/{id}/epistemic` at any time during execution
- **Durable** — the final report is embedded in the Temporal workflow result
- **Incremental** — the cached `_epistemic_report` is updated after each node completes

---

## 7. Reasoning Quality Mechanisms

### 7.1 Reflection Loops

Reflection is triggered automatically when a node's output confidence falls below its configured `reflection_threshold`:

```python
# Node definition
{
  "id": "analysis",
  "agent": "AnalysisAgent",
  "reflection_threshold": 0.80,   # trigger if confidence < 0.80
  "max_reflections": 2,            # at most 2 rounds of reflection
  "critic_agent": "CritiqueAgent"  # which agent reviews the output
}

```
The critic node receives the original output and is instructed to identify weaknesses, factual errors, and logical gaps. The revised output from the critic replaces the original in the context. This can iterate up to `max_reflections` times.

Implementation detail: the reflection node is injected at the **front** of the BFS queue (`pending.appendleft`), ensuring it executes immediately after the node that triggered it. This makes reflection latency equal to one activity round-trip, not an entire BFS iteration.

### 7.2 Fan-Out Consensus

Fan-out enables **parallel hypothesis exploration** — running multiple competing agents simultaneously and selecting the best result:

```text
GraphWorkflow receives fan_out signal:
  ├── node_A (BullCaseAgent)  ─────────┐
  ├── node_B (BearCaseAgent)  ─────────┤ asyncio.gather()
  └── node_C (BaselineAgent)  ─────────┘
                                        │
                               select_consensus("highest_confidence")
                                        │
                               winner stored in context["group_id"]

```
This pattern is particularly powerful for decisions where different reasoning approaches may reach different conclusions (investment decisions, risk assessments, strategic options analysis). Rather than trusting a single agent, the system explores multiple perspectives and selects the most confident one.

### 7.3 Circuit Breaker Pattern

Each node has an independent circuit breaker that tracks failure rates:

```text
States:
  CLOSED    → normal operation; failures increment counter
  OPEN      → node is blocked; all requests routed to fallback_agent
  HALF_OPEN → after recovery_executions, allow one request through

Transitions:
  CLOSED  → OPEN      : failure_count >= trip_threshold
  OPEN    → HALF_OPEN : node_executions_since_trip >= recovery_executions
  HALF_OPEN → CLOSED  : next execution succeeds
  HALF_OPEN → OPEN    : next execution fails

```
When a node's circuit is OPEN, the workflow checks for a `fallback_agent` in the node definition. If present, a synthetic fallback node is created and pushed to the front of the BFS queue. If absent, the node is dead-lettered.

---

## 8. Observability Layer

### 8.1 OpenTelemetry Integration

Every node execution generates an OTel span with the following standard attributes:

```text
graph.node_id             string  "financial_model"
graph.agent               string  "QuantitativeMacroEconomistAgent"
graph.workflow_id         string  "wf-abc123"
graph.iteration           int     1
graph.circuit_state       string  "closed"
graph.confidence          float   0.84
graph.epistemic_debt      int     2          (count of known unknowns)
graph.duration_ms         float   1234.5
graph.status              string  "success" | "error"

```
Spans are exported via OTLP to any compatible backend (Jaeger, Grafana Tempo, Zipkin, Honeycomb, DataDog, etc.).

Fan-out groups generate a parent span with N child spans — one per parallel node. This produces a flame graph showing exactly which agents ran in parallel and their relative durations.

### 8.2 Prometheus Metrics

The following counters are emitted per execution:

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `multigen_nodes_total` | Counter | `workflow_id`, `agent`, `status` | Total node executions |
| `multigen_errors_total` | Counter | `workflow_id`, `agent`, `error_type` | Node failures |
| `multigen_reflections_total` | Counter | `workflow_id`, `node_id` | Reflection loops triggered |
| `multigen_circuit_open_total` | Counter | `workflow_id`, `node_id` | Circuit breaker trips |
| `multigen_fan_outs_total` | Counter | `workflow_id`, `group_id` | Fan-out executions |
| `multigen_dynamic_agents_total` | Counter | `workflow_id`, `status` | Dynamic agent creations |

### 8.3 Temporal Observability

Temporal's own UI (`http://localhost:8080` in local dev) provides:

- Workflow execution timeline (Gantt chart of all activities)
- Full event history (every signal, activity result, timer)
- Retry history per activity
- Dead workflow inspection

Temporal's event history is the **authoritative audit trail** for compliance purposes — it records exactly what happened, when, and in what order, with cryptographic continuity guarantees.

### 8.4 MongoDB CQRS Read Model

After each node execution, output is persisted to MongoDB in the `graph_state` collection:

```json
{
  "workflow_id": "wf-abc123",
  "node_id": "financial_model",
  "output": { ... agent output including epistemic envelope ... },
  "updated_at": "2026-03-20T14:23:11.000Z"
}

```
This is a **CQRS read model** — separate from Temporal's write-side event history. It enables fast queries via `GET /workflows/{id}/state` without replaying the full event log. The persist activity is explicitly non-fatal: if MongoDB is unavailable, the workflow continues and Temporal's event history remains authoritative.

---

## 9. Distributed Messaging Architecture

### 9.1 Kafka Integration

Apache Kafka provides the decoupling layer between workflow submission and execution. This has three benefits:

1. **Backpressure**: workflow submissions queue in Kafka if the Temporal server is overloaded
2. **Decoupling**: the orchestrator can be scaled independently of the workers
3. **Replay**: failed workflow dispatches can be replayed from the Kafka topic offset

Topic schema:

```text
flow-requests   → {"workflow_id": "...", "dsl": {...}, "payload": {...}}
flow-responses  → {"workflow_id": "...", "status": "completed", "result": {...}}
flow-dead-letter→ {"workflow_id": "...", "error": "...", "original": {...}}

```
The `FlowMessagingService` is a long-running async Kafka consumer that reads from `flow-requests`, starts the corresponding Temporal workflow, and publishes the result to `flow-responses`.

### 9.2 Scalability Model

```text
                    ┌─────────────────────────────────┐
HTTP clients ──────►│  Orchestrator (N replicas)      │
                    │  horizontal scale behind LB     │
                    └────────────────┬────────────────┘
                                     │ Kafka publish
                    ┌────────────────▼────────────────┐
                    │  Kafka (partitioned topics)     │
                    │  flow-requests: K partitions    │
                    └────────────────┬────────────────┘
                                     │ Kafka consume
                    ┌────────────────▼────────────────┐
                    │  FlowWorkers (N consumers)      │
                    │  each in same consumer group    │
                    └────────────────┬────────────────┘
                                     │ Temporal.start_workflow
                    ┌────────────────▼────────────────┐
                    │  Temporal Server cluster        │
                    │  sharded by workflow ID         │
                    └────────────────┬────────────────┘
                                     │ activity dispatch
                    ┌────────────────▼────────────────┐
                    │  Temporal Workers (M replicas)  │
                    │  agent registry replicated      │
                    └─────────────────────────────────┘

```
Horizontal scaling is achieved by:

- Increasing Kafka partitions to scale `flow-requests` throughput
- Adding FlowWorker consumer replicas (same consumer group = automatic partition assignment)
- Adding Temporal Worker replicas (Temporal load-balances across same task queue)
- Temporal Server itself is horizontally scalable (frontend + history + matching + worker services)

---

## 10. SDK and Developer Experience

### 10.1 Python SDK Design

The SDK provides both an async and a synchronous interface:

```python
# Async (native)
async with MultigenClient("http://localhost:8000") as client:
    resp = await client.run_graph(graph_def=graph)

# Sync (Jupyter-safe)
client = SyncMultigenClient("http://localhost:8000")
resp = client.run_graph(graph_def=graph)

```
The `SyncMultigenClient` uses a **dedicated background thread** with its own event loop, submitting coroutines via `asyncio.run_coroutine_threadsafe()`. This avoids the `RuntimeError: This event loop is already running` error that occurs when calling `loop.run_until_complete()` from within Jupyter's already-running event loop.

### 10.2 GraphBuilder DSL

The `GraphBuilder` provides a fluent interface for constructing graph definitions:

```python
graph = (
    GraphBuilder()
    .node("id")
        .agent("AgentName")
        .params(key="value")
        .timeout(60)
        .retry(3)
        .reflect(threshold=0.8, max_rounds=2, critic="CritiqueAgent")
        .fallback("FallbackAgent")
        .done()
    .edge("source", "target")
    .edge("source", "target2", condition="confidence < 0.6")
    .entry("id")
    .max_cycles(10)
    .circuit_breaker(trip_threshold=3, recovery_executions=5)
    .build()   # returns dict compatible with run_graph()
)

```
### 10.3 Error Mapping

The SDK maps HTTP status codes to typed exceptions:

| HTTP Status | Exception |
| --- | --- |
| 404 (agent in detail) | `AgentNotFoundError` |
| 404 (workflow in detail) | `WorkflowNotFoundError` |
| 400 | `DSLValidationError` |
| 4xx (other) | `MultigenHTTPError` |
| 5xx | `MultigenHTTPError` |
| Signal error | `GraphSignalError` |

---

## 11. Security and Governance Model

### 11.1 Current Security Posture

The current release is designed for trusted-network deployment (internal enterprise LAN or VPC). It does not yet implement:

- API key authentication
- Role-based access control (RBAC)
- Agent-level permission policies
- Request signing

### 11.2 Human Governance Architecture

The dynamic agent approval gate is the primary governance mechanism. It enforces a **human-in-the-loop requirement** for any capability that was not pre-approved:

1. Every dynamically generated agent spec includes `known_limitations` — the AI's own assessment of what it cannot do.
2. The human reviewer can **edit** the spec before approving — strengthening constraints, adding instructions, or restricting tool access.
3. Rejection is tracked in the workflow's dead letter with the reason, providing a complete governance audit trail.
4. All dynamic agents are deregistered at workflow end — no persistent side effects.

### 11.3 Epistemic Governance

The epistemic report functions as an **audit trail for AI reasoning**:

- What did each agent assume?
- What data was missing?
- What could the agent structurally not assess?
- How much should the output be trusted?

For regulated industries (financial services, healthcare, legal), this report can be attached to any AI-assisted decision as evidence that the system was transparent about its limitations.

---

## 12. Performance Characteristics

### 12.1 Latency Profile

| Operation | Typical Latency | Bottleneck |
| --- | --- | --- |
| Workflow submission (Kafka path) | 50-200ms | Kafka publish + Temporal start |
| Single agent node (GPT-4o) | 2-10s | LLM inference |
| Temporal activity overhead | 10-50ms | gRPC + event history write |
| MongoDB persist (non-fatal) | 10-100ms | Network + write concern |
| Signal delivery | 50-150ms | Temporal gRPC |
| Query response | 10-50ms | Temporal gRPC (in-memory, no DB) |

### 12.2 Throughput Characteristics

- **Sequential workflows**: bounded by sum of node latencies (LLM calls dominate)
- **Parallel fan-out**: bounded by the slowest branch (asyncio.gather)
- **Worker scaling**: Temporal distributes activities across all workers polling the same task queue; linear scaling up to Temporal server capacity
- **Kafka throughput**: millions of messages/second with appropriate partition count

### 12.3 Temporal Event History Limits

Temporal's default event history limit is 50,000 events per workflow execution. For very long-running workflows with hundreds of nodes and many retries, this limit can be approached. The `continue_as_new` pattern can be used to reset the history while preserving workflow state (not yet implemented in Multigen — see roadmap).

---

## 13. Roadmap to Market Leadership

This section outlines the technical and product investments required to make Multigen the leading enterprise multi-agent orchestration framework.

### Phase 1: Production Hardening (Q2 2026)

**Goal**: Make Multigen safe to run in a production enterprise environment.

- [ ] **API Key Authentication** — Bearer token validation on all endpoints; key scoping per workflow or agent
- [ ] **RBAC** — Role-based access: who can approve agents, who can interrupt workflows, who can view epistemic reports
- [ ] **TLS everywhere** — HTTPS for orchestrator, mTLS for worker-Temporal communication
- [ ] **Secrets management** — HashiCorp Vault or AWS Secrets Manager integration for API keys
- [ ] **Rate limiting** — Per-tenant rate limits on workflow submissions
- [ ] **Input validation hardening** — Strict Pydantic schemas for all endpoints; reject unknown fields
- [ ] **continue_as_new** — Handle long-running workflows exceeding Temporal's 50K event limit
- [ ] **Temporal namespace isolation** — Per-tenant namespaces for workflow isolation
- [ ] **Graceful worker shutdown** — Handle SIGTERM; complete in-flight activities before exit
- [ ] **Health check depth** — /health endpoint that validates Temporal, Kafka, MongoDB connectivity

### Phase 2: Parallel Execution ✅ Completed (March 2026)

**Goal**: True parallel node execution instead of sequential BFS.

- [x] **Async parallel BFS** — `_collect_ready_batch()` drains pending queue each iteration; `asyncio.gather()` executes all dependency-satisfied nodes simultaneously; `pending_set` prevents double-enqueue on diamond graphs
- [x] **Dependency tracking** — `depends_on` field in node definition; `_deps_satisfied()` unions edge-inferred upstream with explicit `depends_on` list; zero-dep nodes (entry) always ready
- [x] **Partition-aware fan-out** — `task_queues` list in fan-out spec; nodes assigned round-robin to queues; per-node `task_queue` field for static pinning; `TEMPORAL_TASK_QUEUES` env var configures the pool
- [x] **Streaming results** — `_completed_events` append-only log in workflow; `get_completed_nodes` query; FastAPI SSE endpoint (`GET /workflows/{id}/stream`) polls every 500 ms; `Last-Event-ID` reconnect; JSON polling fallback (`GET /workflows/{id}/events?since=N`)

See [Section 14](#14-parallel-bfs-execution-dependency-tracking-and-streaming) for full technical details.

### Phase 3: Developer Experience (Q3 2026)

**Goal**: Make Multigen the easiest framework to adopt and build with.

- [x] **CLI (`multigen` command)** — Full `click`-based CLI: `multigen run`, `multigen status`, `multigen logs`, `multigen signal` (interrupt/resume/skip-node/jump-to/inject-node/prune/approve-agent/reject-agent); `--local` flag for in-process execution; `--watch` for SSE streaming after submit; `--base-url` / `MULTIGEN_BASE_URL` env var
- [ ] **VS Code extension** — Graph workflow visual editor; drag-and-drop node/edge construction; live execution overlay
- [x] **`multigen init` scaffolding** — `multigen init my-project [--template basic|graph|sequence]` creates full project structure: `agents/my_agent.py` (epistemic envelope template), `workflows/my_workflow.yaml`, `tests/test_my_agent.py`, `.env.example`, `requirements.txt`, `README.md`
- [x] **Hot reload for agents** — `AgentHotReloader` in `workers/hot_reload.py`; watchdog `FileSystemEventHandler` watches agent directories; on `.py` change: clears stale registry entries for the module, calls `importlib.reload()`, re-fires `@register_agent` decorators; debounce guard prevents duplicate events; non-blocking background observer thread
- [x] **Type-safe DSL** — `sdk/multigen/types.py`; full `TypedDict` stubs: `NodeDef`, `EdgeDef`, `GraphDef`, `FanOutSpec`, `CircuitBreakerConfig`, `StepDef`, `WorkflowDSL`, `RunGraphRequest`, `RunWorkflowRequest`, `EpistemicEnvelope`, `AgentOutput`; compatible with mypy and pyright
- [x] **Local execution mode** — `LocalWorkflowRunner` in `sdk/multigen/local_runner.py`; in-process parallel BFS graph execution (no Temporal, no Kafka, no MongoDB); supports `depends_on`, edge conditions, `{{template}}` param resolution, parallel wave execution via `asyncio.gather`; also handles sequential DSL with parallel branches, conditionals, and loop steps
- [x] **Workflow versioning** — `flow_engine/versioning.py`; `patched(patch_id)` wraps `workflow.patched()` with registry validation and deprecation warnings; `PATCH_REGISTRY` dict documents all active patches with deployment dates; `deprecated_patch()` helper; `WorkflowVersion.tag()` decorator for version metadata; `assert_safe_to_upgrade()` for CI/CD deployment gates
- [ ] **Test harness** — `WorkflowTestRunner` that replays recorded workflow events with mock agents
- [ ] **Agent testing framework** — `AgentTestCase` base class for unit testing agents with fixture inputs

See [Section 15](#15-developer-experience-cli-hot-reload-type-safe-dsl-local-execution-and-versioning) for full technical details.

### Phase 4: Intelligence Layer (Q3-Q4 2026)

**Goal**: Workflows that learn and improve over time.

- [ ] **RL-based orchestration optimizer** — Learn which agent sequences produce highest-confidence outputs for each task type; PPO policy over routing decisions
- [ ] **Confidence calibration** — Compare predicted vs actual confidence on ground-truth tasks; auto-adjust agent confidence reporting
- [ ] **Automatic prompt optimizer** — Use epistemic reports to identify prompts that produce low-confidence outputs; suggest improvements
- [ ] **Agent performance registry** — Track per-agent confidence, latency, error rate, cost over time; inform routing decisions
- [ ] **Workflow template library** — Registry of proven workflow patterns (due diligence, screening, analysis) as reusable templates
- [ ] **LLM-router** — Route requests to the most cost-efficient model that meets the confidence threshold (GPT-4o for complex, GPT-4o-mini for simple)
- [ ] **Semantic agent matching** — When an unknown agent is requested, search the capability directory for semantically similar registered agents before triggering dynamic creation

### Phase 5: Enterprise Scale (Q4 2026)

**Goal**: Run Multigen at Fortune 500 scale.

- [ ] **Kubernetes Helm chart** — Production-ready K8s deployment with auto-scaling, pod disruption budgets, resource limits
- [ ] **Multi-tenancy** — Tenant isolation: separate Temporal namespaces, Kafka topics, MongoDB databases per tenant
- [ ] **Geo-distribution** — Multi-region deployment; route workflows to regional worker clusters for data residency compliance
- [ ] **Agent marketplace** — Registry of community-contributed agents; install with `multigen install FinancialAnalystAgent`
- [ ] **Federation** — Multiple Multigen deployments collaborating; cross-cluster workflow dispatch
- [ ] **Cost tracking** — Per-workflow, per-agent token usage and cost; budget alerts and automatic throttling
- [ ] **SLA monitoring** — Per-workflow SLA definition; automatic escalation when SLA is at risk
- [ ] **Compliance export** — One-click export of epistemic report + Temporal event history as PDF for regulatory audit

### Phase 6: Ecosystem (2027)

**Goal**: Build the Multigen ecosystem and community.

- [ ] **TypeScript SDK** — Full-featured client for Node.js and browser-based tooling
- [ ] **Go SDK** — High-performance client for infrastructure tooling
- [ ] **Terraform provider** — Manage Multigen resources (workflows, agents, capabilities) as infrastructure code
- [ ] **GitHub Actions integration** — `multigen/run-workflow` action for CI/CD pipelines
- [ ] **Agent certification program** — Testing and compliance standards for community agents
- [ ] **Hosted cloud offering** — Multigen Cloud: managed Temporal, Kafka, MongoDB; pay-per-execution pricing
- [ ] **Enterprise support tiers** — SLA-backed support, private registry, air-gapped deployment

---

## Competitive Differentiation Summary

Multigen's sustainable competitive advantages are:

1. **Temporal-backed durability** — No other open-source agent framework offers true durable execution with crash recovery. This is a significant structural advantage for enterprise adoption.

2. **Epistemic transparency as first-class citizen** — No other framework provides structured uncertainty propagation through the graph. This is the key differentiator for regulated industries.

3. **Human governance at the signal level** — The approval gate pattern (pause workflow → generate spec → human reviews → approve/reject → resume) is unique and directly addresses enterprise AI governance requirements.

4. **Runtime workflow steering** — 10 signal types for modifying running workflows without restart. This is operationally critical for long-running enterprise workflows.

5. **Full observability stack** — OTel + Prometheus + Temporal event history + MongoDB CQRS. No other framework provides this level of built-in observability.

---

## 11b. Agent2Agent (A2A) Protocol Integration

### Why Inter-Agent Communication Matters

Enterprise AI deployments do not operate in isolation. A Multigen workflow running a financial analysis pipeline may need to invoke a risk model owned by a different team (running CrewAI), a compliance check from a third-party vendor (running LangGraph), or a data enrichment agent deployed on Vertex AI. Without a standard inter-agent communication protocol, these integrations require bespoke adapters for every pairing.

The **Agent2Agent (A2A) protocol** — an open standard developed by Google and adopted by a growing ecosystem — solves this with a simple, discoverable, HTTP-based contract. Multigen implements A2A on both sides: as a **server** (so external systems can call Multigen agents) and as a **client** (so Multigen nodes can call remote A2A agents).

### A2A Protocol Overview

The A2A protocol defines three primitives:

| Primitive | Description |
| --- | --- |
| **Agent Card** | A JSON document at `/.well-known/agent.json` describing the agent's capabilities, skills, authentication, and I/O modes |
| **Task** | The unit of work: a message sent from client to server, producing artifacts as output |
| **Skill** | A named capability advertised in the Agent Card; clients address tasks to specific skills |

Communication uses JSON-RPC 2.0 over HTTP(S). Tasks can be synchronous (`tasks/send`) or streaming via Server-Sent Events (`tasks/sendSubscribe`).

### Multigen as A2A Server

Multigen exposes these A2A endpoints:

```text
GET  /.well-known/agent.json              Global Agent Card
GET  /a2a/agents/{agent_name}/card        Per-agent Agent Card
POST /a2a                                 JSON-RPC 2.0 task handler (sync)
POST /a2a/stream                          SSE streaming task handler

```
The **Agent Card** is auto-generated from the live agent registry — every registered agent becomes an A2A skill:

```json
{
  "name": "Multigen",
  "url": "https://multigen.example.com",
  "version": "0.2.0",
  "capabilities": { "streaming": true, "stateTransitionHistory": true },
  "skills": [
    {
      "id": "echoagent",
      "name": "EchoAgent",
      "description": "Demo echo agent",
      "tags": ["demo"],
      "input_modes": ["text", "data"],
      "output_modes": ["text", "data"]
    },
    {
      "id": "financialanalystagent",
      "name": "FinancialAnalystAgent",
      "description": "DCF valuation and sector analysis",
      "tags": ["finance"]
    }
  ]
}

```
A task sent to Multigen's A2A endpoint is routed to the matching agent via fuzzy skill resolution (exact name match → case-insensitive match → substring match). The agent executes normally, and the result is returned as an A2A Artifact.

### Multigen as A2A Client

A2A **node type** in GraphWorkflow allows any node to call a remote A2A agent:

```python
# Node definition with A2A endpoint
{
    "id": "remote_risk_model",
    "a2a_endpoint": "https://risk-platform.partner.com",
    "a2a_skill": "portfolio_risk_analysis",
    "params": {
        "company": "NovaSemi Technologies",
        "analysis_date": "2026-03-20"
    },
    "timeout": 90,
    "retry": 2
}

```
When the engine encounters a node with `a2a_endpoint`, it dispatches to `a2a_activity` instead of the normal `agent_activity`:

```text
Node with a2a_endpoint
        │
        ▼
  a2a_activity (Temporal activity)
        │
        ▼
  A2AClient.send_task(skill_id, data)
        │ HTTP POST /a2a (JSON-RPC 2.0)
        ▼
  Remote A2A Agent (any framework)
        │
        ▼
  A2A Task Response (artifacts)
        │
        ▼
  _extract_output() → standard Multigen output format
  {"output": {...}, "confidence": float}
        │
        ▼
  Epistemic envelope attached (propagated_uncertainty from remote)

```
The result is seamlessly merged into the workflow context, treated identically to a local agent output — including epistemic envelope attachment, reflection triggering, and circuit breaker tracking.

### Skill Resolution

```text
Request skill: "risk_analysis"

1. Exact match:      "risk_analysis" in registry?  → use it
2. Case-insensitive: "RiskAnalysis" in registry?    → use it
3. Substring:        "risk" in "RiskAnalysisAgent"? → use it
4. No match:         return 404 with available skills

```
### A2A + Dynamic Agent Lifecycle

A2A and dynamic agent creation are complementary. When the workflow encounters an unknown agent:

1. It first checks if a remote A2A endpoint is configured for that agent name (via `A2A_AGENT_REGISTRY` env var)
2. If found, routes to the remote A2A endpoint instead of triggering spec generation
3. If not found, proceeds with the normal approval gate

This enables a **federated agent directory** — teams register their agents as A2A endpoints, and Multigen discovers and calls them automatically without requiring co-deployment.

### A2A SDK Methods

```python
# Discover what agents a remote Multigen instance offers
card = await client.get_agent_card()
print(f"Remote skills: {[s['name'] for s in card['skills']]}")

# Test A2A directly (without going through workflow)
result = await client.a2a_send(
    skill_id="FinancialAnalystAgent",
    data={"company": "NovaSemi", "analysis_date": "2026-03-20"},
)
print(result["artifacts"])

# Using the standalone A2AClient for external systems
from a2a.client import A2AClient
async with A2AClient("https://other-framework.example.com") as a2a:
    card = await a2a.get_agent_card()
    result = await a2a.send_task(skill_id="risk_model", data={"scope": "full"})

```
### Interoperability Matrix

| External Framework | Calls Multigen via A2A | Multigen calls it via A2A |
| --- | --- | --- |
| LangGraph | ✅ (if LangGraph exposes A2A server) | ✅ via a2a_endpoint node |
| CrewAI | ✅ | ✅ via a2a_endpoint node |
| Vertex AI Agents | ✅ | ✅ via a2a_endpoint node |
| AutoGen | ✅ | ✅ via a2a_endpoint node |
| Another Multigen | ✅ | ✅ via a2a_endpoint node |
| Any A2A-compliant system | ✅ | ✅ via a2a_endpoint node |

---

## Conclusion

Multigen represents a new category of AI infrastructure: not just an agent framework, but an **enterprise-grade AI workflow operating system**. It combines the expressiveness of a declarative DSL with the reliability of event-sourced durable execution, the transparency of per-node epistemic reporting, and the governance of human-in-the-loop approval gates.

The framework's architecture is designed for the hard reality of enterprise AI deployment: agents fail, confidence should be reported honestly, humans need to remain in control, and workflows must survive infrastructure failures. Multigen treats these requirements not as afterthoughts but as core architectural pillars.

The path to market leadership runs through production hardening, parallel execution performance, developer experience, and ecosystem growth — in that order. Each phase builds on the previous, and the foundation (durable execution + epistemic transparency + human governance) is already in place.

---

---

## 14. Parallel BFS Execution, Dependency Tracking, and Streaming

### 14.1 The Sequential BFS Problem

The original `GraphWorkflow` executed nodes one at a time: pop a node from the BFS queue, run it, enqueue its successors, repeat. For a graph with five independent parallel branches each taking 30 seconds, sequential execution took 150 seconds. The bottleneck was not Python, not Temporal, not the network — it was the serialisation of inherently parallel work.

The correct mental model is: **the bottleneck in an LLM agent pipeline is always LLM inference latency (2–10 s per node), never Python scheduling overhead (<1 ms)**. Parallelism in Python's asyncio event loop is the right tool — not C++ thread pools, not subprocess spawning. `asyncio.gather()` issues all activity calls to Temporal in a single event-loop tick; Temporal dispatches them to any available worker. The wall-clock time for a batch of N independent nodes becomes `max(latency_i)` instead of `Σ(latency_i)`.

### 14.2 The Double-Enqueue Correctness Problem

Parallel execution introduces a subtle correctness bug that is easy to miss: when two nodes A and B complete in the same parallel wave and both have node C as a downstream successor, `_process_node_result` is called for both A and B. Both independently walk C's outgoing edges and append C to `pending`. In the next iteration, `_collect_ready_batch` dequeues C twice, both instances pass `_deps_satisfied`, and C executes twice.

The fix is a shadow `pending_set: Set[str]` that mirrors `pending` at all times:

```text
Invariant: nid ∈ pending  ⟺  nid ∈ pending_set

Enqueue:   if target not in pending_set:
               pending.append(target)
               pending_set.add(target)

Dequeue:   nid = pending.popleft()
           pending_set.discard(nid)

Deferred:  pending.extend(deferred)
           pending_set.update(deferred)

```
The `pending_set` check adds O(1) cost per enqueue and eliminates duplicate execution entirely. All six enqueue paths (successor edges, fallback agents, reflection nodes, `jump_to` signals, `inject_node` signals, circuit-breaker fallbacks) are guarded by this check.

### 14.3 Dependency Tracking: `depends_on`

Graph edges encode **data flow** — the output of node A is consumed by node B. But enterprise workflows have a second kind of dependency: **resource dependencies**. A model-serving node may depend on a model-loading node not because it receives its output, but because the model must be warm before inference can begin. A rate-limited external API call may depend on a quota-tracking node. These dependencies are real but invisible to the edge topology.

The `depends_on` field makes these explicit:

```python
{
    "id": "run_inference",
    "agent": "InferenceAgent",
    "depends_on": ["load_model", "fetch_data"],   # logical + data deps
}

```
`_deps_satisfied` unions both sources:

```python
edge_deps     = {e["source"] for e in edges if e["target"] == nid}
explicit_deps = set(node.get("depends_on") or [])
all_deps      = edge_deps | explicit_deps
return all(dep in context for dep in all_deps)

```
A node with no edges and no `depends_on` is an entry node — always immediately ready.

### 14.4 Partition-Aware Fan-Out

Standard fan-out dispatches all parallel activities to the single default Temporal task queue. Under high concurrency all activities queue behind each other on that queue's worker pool — defeating the purpose of parallelism.

Partition-aware fan-out distributes activities across multiple task queues, each backed by a dedicated worker pool:

```text
Fan-out spec:
  task_queues: ["queue-gpu", "queue-finance", "queue-legal"]
  nodes: [node_A, node_B, node_C, node_D, node_E]

Assignment (round-robin):
  node_A → queue-gpu      (idx 0 % 3 = 0)
  node_B → queue-finance  (idx 1 % 3 = 1)
  node_C → queue-legal    (idx 2 % 3 = 2)
  node_D → queue-gpu      (idx 3 % 3 = 0)
  node_E → queue-finance  (idx 4 % 3 = 1)

```
Each node inherits its `task_queue` field before `_execute_node_full` runs. `_run_agent` passes this to `workflow.execute_activity(..., task_queue=node_queue)`. Temporal routes each activity to a worker polling that queue.

Individual nodes can also declare their own queue statically:

```python
{"id": "gpu_heavy", "agent": "EmbeddingAgent", "task_queue": "gpu-workers"}

```
The operator configures the pool via `TEMPORAL_TASK_QUEUES=queue-a,queue-b,queue-c` and starts one or more `temporal_worker.py` processes per queue. Multigen does not manage worker lifecycle — this integrates naturally with Kubernetes HPA, each Deployment targeting one queue.

**Scaling model under partition-aware fan-out:**

```text
                    ┌──────────────────────────────────┐
                    │  GraphWorkflow (asyncio.gather)  │
                    │  dispatches N activities at once  │
                    └──────┬──────────┬──────────┬─────┘
                    queue-a│    queue-b│    queue-c│
              ┌────────────▼┐  ┌──────▼──────┐  ┌▼───────────────┐
              │  Worker Pool│  │ Worker Pool │  │ Worker Pool    │
              │  A (GPU)    │  │ B (Finance) │  │ C (Legal)      │
              │  M replicas │  │ M replicas  │  │ M replicas     │
              └─────────────┘  └─────────────┘  └────────────────┘
                    │                │                 │
              node_A, node_D   node_B, node_E      node_C
                    └────────────────┴─────────────────┘
                              asyncio.gather waits
                            for all N to complete

```
Wall-clock time = latency of the slowest node, regardless of how many workers are in each pool.

### 14.5 SSE Streaming: Real-Time Node Completion

#### The Problem with Polling

The historical approach to observing workflow progress was polling: `GET /workflows/{id}/state` every N seconds. This produces two failure modes: too infrequent (stale view, missed intermediate states) or too frequent (unnecessary load on Temporal's gRPC query interface).

SSE (Server-Sent Events) solves this with a persistent HTTP connection where the server pushes events as they occur. It is simpler than WebSockets (unidirectional, no framing protocol, works through HTTP/1.1 proxies) and directly supported by browser `EventSource` API and Python `requests` with `stream=True`.

#### Architecture

```text
Workflow side:
  After each _process_node_result():
    self._completed_events.append({
        "index":      len(self._completed_events),   <- monotonic
        "node_id":    node_id,
        "agent":      agent_name,
        "confidence": result["confidence"],
        "timestamp":  workflow.now().isoformat(),
        "output":     output,
    })
  At workflow end:
    self._workflow_done = True

  @workflow.query get_completed_nodes() -> {events, total, done}

SSE endpoint side (GET /workflows/{id}/stream):
  cursor = since (from ?since= or Last-Event-ID header)
  while True:
      payload = await handle.query("get_completed_nodes")
      for ev in payload["events"]:
          if ev["index"] > cursor:
              cursor = ev["index"]
              yield "id: {index}\ndata: {json}\n\n"
      if payload["done"]:
          yield "data: {done: true}\n\n"
          return
      await asyncio.sleep(0.5)

```
The `index` field is a monotonic integer starting at 0. Clients track the last received index. On reconnect, the browser's `EventSource` sends `Last-Event-ID: <last_index>`; the SSE endpoint resumes from that point. No events are missed across disconnections.

#### Event format

```json
{
  "index": 3,
  "node_id": "financial_model",
  "agent": "QuantitativeMacroEconomistAgent",
  "confidence": 0.87,
  "timestamp": "2026-03-21T10:04:22.113Z",
  "output": { "dcf_value": 4200000000, "recommendation": "BUY" }
}

```
Terminal sentinel (when `done=True` from workflow):

```json
{"done": true, "total": 7, "workflow_id": "wf-abc123"}

```
#### Polling Fallback

For environments that cannot use SSE (some mobile clients, internal tooling):

```text
GET /workflows/{id}/events?since=3
→ {"events": [...events with index > 3...], "total": 7, "done": false, "next_since": 6}

```
Clients increment `since` to `next_since` after each poll.

### 14.6 Performance Impact

| Metric | Sequential BFS | Parallel BFS |
| --- | --- | --- |
| 5-node independent graph (30s/node) | 150 s | ~30 s (5× improvement) |
| 10-node diamond graph | ~100 s | ~40 s (2.5× improvement) |
| Scheduling overhead (Python) | <1 ms | <1 ms |
| Memory overhead (pending_set) | O(pending) | O(pending) same |
| Temporal activity dispatch | Serial gRPC calls | Batched in single event-loop tick |
| First event visible to SSE client | After all nodes | After first node (~2–10 s) |

The dominant cost in every LLM agent pipeline is LLM inference latency, not orchestration overhead. Parallel BFS eliminates the unnecessary serialisation of independent work while adding zero meaningful overhead.

---

## 15. Developer Experience: CLI, Hot Reload, Type-Safe DSL, Local Execution, and Versioning

Phase 3 delivers the full developer-facing layer that makes Multigen the easiest multi-agent framework to adopt.

### 15.1 The `multigen` CLI

`cli/multigen_cli.py` implements a Click command group installable as the `multigen` entry point.

```text
multigen [--base-url URL]
  run    <workflow.yaml>  [--payload JSON] [--payload-file FILE]
                          [--workflow-id ID] [--local] [--watch]
  status <workflow-id>   [--json-out]
  logs   <workflow-id>   [--since N] [--poll]
  signal interrupt  <workflow-id>
  signal resume     <workflow-id>
  signal skip-node  <workflow-id> --node <id>
  signal jump-to    <workflow-id> --node <id>
  signal inject-node <workflow-id> --spec <JSON|@file>
  signal prune      <workflow-id> --node <id>
  signal approve-agent <workflow-id> --agent <name>
  signal reject-agent  <workflow-id> --agent <name> [--reason TEXT]
  init   <project_name>  [--template basic|graph|sequence]
```

Key design decisions:

- **HTTP abstraction**: prefers `httpx`, falls back to `requests`, raises a clear error if neither is installed.
- **`--local` flag**: bypasses the Temporal stack entirely; delegates to `LocalWorkflowRunner` for instant feedback during development.
- **`--watch` flag**: submits the workflow then immediately starts streaming SSE events via `multigen logs`.
- **`status` rendering**: prints a color-coded node table (green ≥ 0.75, yellow ≥ 0.5, red < 0.5) plus the epistemic summary.
- **Signal commands**: each maps to `POST /workflows/{id}/signal/{name}` with the appropriate JSON body, giving operators full runtime control from the terminal.

### 15.2 `multigen init` Scaffolding

`cli/scaffold.py` creates a self-contained starter project in one command:

```text
my-project/
├── agents/
│   ├── __init__.py
│   └── my_agent.py          # @register_agent("MyAgent") with epistemic envelope
├── workflows/
│   └── my_workflow.yaml     # basic (sequential) or graph (parallel with depends_on)
├── tests/
│   ├── __init__.py
│   └── test_my_agent.py     # pytest skeleton: echo, confidence, missing-param tests
├── .env.example             # OPENAI_API_KEY, TEMPORAL_SERVER_URL, MONGODB_URI
├── requirements.txt         # multigen, pyyaml, pytest, pytest-asyncio, watchdog
└── README.md
```

The graph template generates a three-node DAG (`research → analysis → report`) with `reflection_threshold`, `max_reflections`, and `depends_on` pre-wired to demonstrate all features.

### 15.3 Hot Reload for Agents

`workers/hot_reload.py` implements `AgentHotReloader` using the `watchdog` library.

**Problem**: Temporal workers are long-lived processes.  When a developer edits an agent file, the changes are not visible until the worker restarts — breaking the inner dev loop.

**Solution**:

```python
reloader = AgentHotReloader(watch_paths=["agents/"])
reloader.start()   # non-blocking background observer thread
```

On every `.py` file modification or creation:

1. `_path_to_module(path, search_roots)` converts the file path to a dotted module name.
2. `_clear_module_agents(module_name)` removes all `_registry` entries whose class `__module__` matches, bypassing the idempotent guard.
3. `importlib.reload(mod)` re-executes the module; `@register_agent` decorators fire again, inserting the fresh class objects.
4. An optional `on_reload` callback fires (useful for metrics or test assertions).

A threading lock provides debounce protection against editors that emit multiple events per save (write + rename + chmod).

### 15.4 Type-Safe DSL

`sdk/multigen/types.py` provides `TypedDict` definitions for every dict shape in the system:

| Type | Description |
| --- | --- |
| `NodeDef` | Graph node (id, agent, params, depends_on, reflection_threshold, fan_out, …) |
| `EdgeDef` | Directed edge with optional condition expression |
| `GraphDef` | Top-level graph: nodes, edges, entry, max_cycles, circuit_breaker |
| `FanOutSpec` | Dynamic fan-out: items_key, agent, params_template, task_queues |
| `StepDef` | Sequential step with nested parallel/conditional/loop/graph |
| `WorkflowDSL` | Sequential DSL: steps list |
| `RunGraphRequest` | POST /workflows/graph/run body |
| `EpistemicEnvelope` | confidence, reasoning, uncertainty_sources, flags, … |
| `AgentOutput` | Canonical agent return shape |

These are purely structural — no runtime overhead — and enable full IDE auto-complete and mypy/pyright checking when building DSL definitions programmatically.

### 15.5 Local Execution Mode

`sdk/multigen/local_runner.py` implements `LocalWorkflowRunner` — an in-process executor that runs the same graph logic without any infrastructure dependencies.

**Graph execution** mirrors the Temporal engine:

- Parallel BFS with `pending_set` dedup guard (identical correctness to the production engine)
- `depends_on` + edge-inferred dependency resolution
- Edge condition evaluation via `eval()` in a restricted namespace
- `asyncio.gather()` for parallel wave execution
- `{{template}}` parameter resolution using `re.sub` across dot-separated context keys

**Sequential execution** handles all DSL constructs: sequential steps, `parallel` branches, `conditional` (when/otherwise), `loop` (until/max_iterations), and embedded `graph` sub-workflows.

Limitations are explicit in the module docstring: no circuit breakers, no reflection, no human-in-the-loop gates, no persistence.  The design decision is intentional — local mode optimises for zero-config rapid iteration, not production parity.

### 15.6 Workflow Versioning

`flow_engine/versioning.py` provides a disciplined wrapper around Temporal's `workflow.patched()` API.

**The non-determinism problem**: Temporal replays workflow history on restart. If the code changes between execution and replay, the replayed commands may differ — causing a fatal `NonDeterminismError`. Temporal's `patched()` API solves this by storing a marker in the event history.

**Usage**:

```python
from flow_engine.versioning import patched

if patched("graph-engine-parallel-bfs-v2"):
    # new path: taken by all workflows starting from this deploy
    result = await parallel_bfs_execution()
else:
    # old path: replayed by in-flight workflows started before deploy
    result = await sequential_execution()
```

**`PATCH_REGISTRY`** documents every active patch with deployment date and safe-removal conditions — making patch lifecycle visible to the whole team.

**Deployment gate**:

```python
from flow_engine.versioning import assert_safe_to_upgrade
assert_safe_to_upgrade("GraphWorkflow", pending_workflow_count)
# Raises RuntimeError if any in-flight workflows predate the patch
```

Wire this into CI/CD to prevent accidental removal of still-needed code paths.

**Patch lifecycle**:

1. Add `patch_id` to `PATCH_REGISTRY` with description.
2. Add `if patched(patch_id): new_path() else: old_path()` in workflow code.
3. When all pre-patch workflows complete: call `deprecated_patch(patch_id)`.
4. Remove the `else` branch and the `patched()` guard from workflow code.
5. Remove from `PATCH_REGISTRY`; move to `DEPRECATED_PATCHES` for audit trail.

---

*Multigen is open source under the Apache 2.0 license.*
*Technical questions: open an issue at <https://github.com/Subhagatoadak/Multigen>*
