# Agent Components Overview

Welcome to **Agent Components**, a modular toolkit for composing LLM-native agents. This document walks through the core concepts, explains how agents are created and extended with capabilities, and provides a runnable example using OpenAI.

## What is an Agent Here?

An agent is a lightweight wrapper around a step function that operates on a shared `Context`. At runtime the agent:

1. Receives a `Context` containing the goal, shared scratch state, capabilities, memories, policies, etc.
2. Executes its `step` callable (optionally wrapped in an `AgentKernel` to support lifecycle hooks like `init → perceive → decide → act → learn`).
3. Returns an `AgentStep` with outbound messages, state updates, and an optional stop flag.

Agents become powerful by attaching capabilities that expose tools, memories, knowledge bases, registries, or message buses to the `Context`. These capabilities plug into the agent just before the `step` executes, enabling lego-style composition without duplicating glue code.

## How to Create an Agent

You can construct agents directly via the `Agent` dataclass or, more ergonomically, through `AgentBuilder`. The builder captures the agent’s name, role, step function, and capabilities, then returns a ready-to-run `Agent` instance.

```python
from agentic_codex import AgentBuilder, Context
from agentic_codex.core.schemas import AgentStep, Message

def query_step(ctx: Context) -> AgentStep:
    question = ctx.goal
    knowledge = ctx.components["knowledge"]
    answer = knowledge.get(question.lower(), "I don't know.")
    return AgentStep(out_messages=[Message(role="assistant", content=answer)])

builder = (
    AgentBuilder(name="query", role="assistant")
    .with_resource("knowledge", {"what is codex?": "A modular agent toolkit."})
    .with_step(query_step)
)

agent = builder.build()
context = Context(goal="What is Codex?")
result = agent.run(context)
print(result.out_messages[-1].content)  # => "A modular agent toolkit."
```

## Capability System

Capabilities are declarative attachments that configure the context or agent before each run. Core capability types include:

| Capability              | Purpose                                                                 |
|------------------------ |-------------------------------------------------------------------------|
| `ContextCapability`     | Inject structured data into the shared scratchpad.                      |
| `LLMCapability`         | Attach an LLM adapter (e.g., OpenAI) that becomes accessible via `ctx.llm`. |
| `MemoryCapability`      | Add scratch, episodic, semantic, or graph memories to `ctx.memory`.     |
| `SkillRegistryCapability` | Share a tool registry across agents.                                 |
| `ResourceCapability`    | Store arbitrary resources in components/scratch/memory.                |
| `ToolCapability`        | Register tool adapters with permission guards and budgets.              |
| `MessageBusCapability`  | Provide a shared bus to publish/consume inter-agent messages.           |
| `ReinforcementLearningCapability` | Hook a trainer into the learn stage and share its environment through the context. |
| `JEPALearningCapability` | Align predictor/target embeddings each learn step and log JEPA-style losses. |
| `MCPToolRegistry` (via `ToolCapability`) | Surface remote MCP tools as guarded adapters with server metadata baked in. |

### Adding Capabilities

Capabilities can be chained during agent construction:

```python
from agentic_codex import (
    AgentBuilder,
    Context,
    LLMCapability,
    ToolCapability,
    MessageBusCapability,
)
from agentic_codex.core.llm import EnvOpenAIAdapter
from agentic_codex.core.tools import HTTPToolAdapter, ToolPermissions, BudgetGuard

http_tool = HTTPToolAdapter()
permissions = ToolPermissions({"web-agent": {"http.get"}})
budget = BudgetGuard(max_calls=10)

builder = (
    AgentBuilder(name="web-agent", role="researcher")
    .with_capability(MessageBusCapability())          # share a message bus
    .with_llm(EnvOpenAIAdapter(model="gpt-4o-mini"))  # attach an OpenAI LLM
    .with_capability(ToolCapability({"http.get": http_tool}, permissions=permissions, budget=budget))
    .with_resource("knowledge", {"root": "https://example.com"})
    .with_step(lambda ctx: ...)
)
```

Each capability seamlessly augments the `Context` so the step function can access `ctx.llm`, `ctx.get_tool("http.get")`, `ctx.get_message_bus()`, or any other injected component.

### Reinforcement-Friendly Tools

Use `ReinforcementToolAdapter` to wrap any tool and emit rewards/history for downstream RL or policy tuning:

```python
from agentic_codex.core.tools import ReinforcementToolAdapter, MathToolAdapter

math_tool = MathToolAdapter()
rl_math = ReinforcementToolAdapter(math_tool, reward_fn=lambda res: -abs(res["result"]))
```

Attach it through `ToolCapability` to keep budgets/permissions enforced while logging rewards per call.

### MCP Tool Management (Server + Registry)

The built-in MCP registry exposes remote tools without custom glue:

```python
from agentic_codex.mcp import MCPServerConfig, MCPToolDescriptor, MCPToolRegistry
from agentic_codex import AgentBuilder

server = MCPServerConfig(name="demo", base_url="https://mcp.local")
registry = MCPToolRegistry(server)
registry.register(MCPToolDescriptor(name="search.docs", description="Search docs", path="/search"))

agent = (
    AgentBuilder(name="mcp-agent", role="operator")
    .with_capability(registry.as_capability())  # tools surfaced under ctx.tools
    .with_step(lambda ctx: ctx.get_tool("search.docs").invoke(query="policies"))
    .build()
)
```

The default adapter is offline-safe (stub transport); swap in a real transport factory when wiring to live servers.

Manifest support: declare `mcp_servers`/`mcp_tools` blocks and a `run_store` path in your YAML so CLI runners can wire registries and persist outputs.

## Example: Query Agent with OpenAI

The following example shows a query agent that uses OpenAI to synthesize answers from a knowledge base. Set `OPENAI_API_KEY` in your environment before running.

```python
from agentic_codex import AgentBuilder, Context, LLMCapability
from agentic_codex.core.schemas import AgentStep, Message
from agentic_codex.core.llm import EnvOpenAIAdapter

FAQ = {
    "what is openai?": "OpenAI creates safe AGI and tools like GPT models.",
    "what is codex agent components?": "A toolkit for composing modular LLM agents.",
}

def query_step(ctx: Context) -> AgentStep:
    question = ctx.goal.lower()
    base_answer = FAQ.get(question, "I don't know yet.")
    # Use OpenAI to refine the base answer.
    prompt = (
        f"You are a helpful assistant. Answer the question using the fact: {base_answer}.\\n"
        f"Question: {ctx.goal}"
    )
    refined = ctx.llm.generate(prompt) if ctx.llm else base_answer
    return AgentStep(out_messages=[Message(role="assistant", content=refined)])

builder = (
    AgentBuilder(name="faq", role="assistant")
    .with_llm(EnvOpenAIAdapter(model="gpt-4o-mini"))
    .with_resource("knowledge", FAQ)
    .with_step(query_step)
)

agent = builder.build()
context = Context(goal="What is Codex Agent Components?")
result = agent.run(context)
print(result.out_messages[-1].content)
```

This snippet demonstrates how a few capabilities (LLM + knowledge resource) enable a compact, reusable agent that wraps OpenAI to deliver enriched answers.

## Branching, Parallel, and Conditional Flows

`GraphRunner` executes a DAG of agents with batching, parallel groups, and conditional nodes:

```python
from agentic_codex import GraphRunner, GraphNodeSpec

graph = {
    "ingest": GraphNodeSpec(agent=ingest_agent),
    "analyze": GraphNodeSpec(agent=analyze_agent, after=["ingest"], batch_key="documents"),
    "critique": GraphNodeSpec(agent=critique_agent, after=["analyze"], parallel_group="reviewers"),
    "ship": GraphNodeSpec(agent=ship_agent, after=["critique"], condition=lambda ctx: ctx.scratch.get("ok_to_ship", False)),
}

runner = GraphRunner(graph, allow_parallel=True, max_parallel=4)
result = runner.run(goal="Process corpus", inputs={"documents": docs})
```

- **Batch steps:** `batch_key` fans a node across items in scratch.
- **Parallel steps:** `parallel_group` runs siblings concurrently (with optional `max_parallel`).
- **Conditional steps:** `condition` gates execution; skipped nodes are recorded.
- **Stop signal:** any node can set `AgentStep.stop=True` or `ctx.scratch["graph_stop"]` to halt the DAG.

### Isolation and Guards

- Set `isolate_parallel_state=True` to clone scratch for each batch/parallel worker and merge updates afterward (reduces accidental cross-talk).
- Guards registered on the coordinator are evaluated before each node to enforce budgets/policies.

## Tracing, Monitoring, and Run IDs

Every coordinator now threads a `run_id` into spans/metrics:

- `Tracer` records spans (`kind="span"`), metrics, and arbitrary logs with `run_id` attached.
- `RunResult` includes `events` and `run_id` for replay/monitoring dashboards.
- Coordinators emit `run.started` / `run.finished` events automatically.

Use these breadcrumbs to pipe runs into dashboards or OpenTelemetry exporters.

## Run Stores and Replay

Use `RunStore` to persist runs as JSON for later replay/audit:

```python
from agentic_codex.core.observability.run_store import RunStore

store = RunStore("runs/")
path = store.save(run_result)
saved = store.load(run_result.run_id)
```

CLI support:

```bash
codex run --workflow wf.yaml --goal "Ship feature" --vars key=value --run-store runs/
codex validate --workflow wf.yaml
codex replay --run-store runs/ --run-id <id>
```

## How This Differs from Other Frameworks

- **LangGraph:** LangGraph focuses on Pythonic graph building with state machines and interrupts. Agent Components offers a lighter `GraphRunner` for DAG/batch/parallel plus capability-based composition; use it when you want small, explicit graphs with shared scratch/context and built-in safety/rl/mcp tooling. LangGraph’s client/server and persistence layers are more opinionated; here you can slot your own stores and transports.
- **CrewAI:** CrewAI emphasises role-based crews and task delegation. Agent Components ships those patterns (`ManagerWorker`, `MinistryOfExperts`) but also adds JEPA/RL hooks, budgeted tools, and MCP tool surfacing for tighter governance over tools and telemetry.
- **AutoGen:** AutoGen excels at multi-agent chat with function-calling. Agent Components keeps chat loops simple but layers in capability injection, guarded tool registries, structured traces, and branching DAG execution for pipelines that need conditional/parallel batches rather than pure dialogues.

---

**Next Steps**

Explore the provided notebooks (`hierarchy_showcase.ipynb`, `openai_swarm_demo.ipynb`, `model_lab_demo.ipynb`) to see these abstractions in action across hierarchical, swarm, and evaluation/recommendation patterns. Unlock new capabilities by composing additional resources, adapters, and coordinators tailored to your workflows.

### New example notebooks

- `with_steps_orchestration.ipynb`: linear orchestration with batch/parallel/conditional/retry/wait/timeout.
- `graph_runner_branching.ipynb`: DAG branching with parallel groups and batching.
- `mcp_tools_demo.ipynb`: MCP registry + tools wired through `ToolCapability`.
- `run_store_replay.ipynb`: persisting and replaying runs via `RunStore`.
- `with_steps_metrics.ipynb`: `AgentBuilder.with_steps` plus tracing/metrics to monitor batch/parallel steps.
- `observability_overview.ipynb`: end-to-end metrics/error/latency capture across agents, with_steps, and GraphRunner.
- `production_end_to_end.ipynb`: production-style demo combining namespaces, comms, with_steps, GraphRunner, safe tools, MCP, RL tools, RunStore, and metrics.
- `manifest_tool_registration.ipynb`: define a manifest and register it with the `agentic` CLI.
- `code_workflow_agents.ipynb`: writer → reviewer → recommender → docs pipeline with stubbed/OpenAI LLMs.

### with_steps quick reference

- `StepSpec`: name + `fn` plus optional `condition`, `branch` (inject sub-steps), `batch_key`, `parallel`, `isolate_state`, `retry`/`backoff`, `timeout`, `circuit_breaker_key`/`failure_threshold`, `wait_seconds`.
- `run_steps([...], context, max_parallel=4)`: executes in order; batch steps fan out; branches inline additional steps; respects `stop` flag to halt.
- `AgentBuilder.with_steps([...])`: wraps a sequence of `StepSpec` inside an agent step, merging all state updates and emitted messages.

### Metrics & Observability defaults

- Agents record per-run latency (`agent.latency`) and error counts (`agent.error`) into `ctx.scratch["_metrics"]`.
- `with_steps` records per-step latency and success/error counters (`with_steps.latency`, `with_steps.success`, `with_steps.error`).
- `GraphRunner` emits span and metric events for each node (including errors) via the tracer; combine with `RunStore` for replay/audit.
- Communication hub now supports direct, broadcast, group, private broadcast, encrypted, and ephemeral delivery via `CommEnvelope` + `CommunicationHub`.
- Namespaces: define isolated agent groups and link namespaces to allow controlled cross-namespace messaging via the communication hub (`mode="namespace"`).
- Streaming: `EnvOpenAIAdapter` can return iterables when `stream=True` in `chat`/`generate` for token streaming (stubbed if OpenAI streaming is unavailable).
- HTTP service (optional `service` extra): `agentic_codex.service.http.build_app` returns a FastAPI app with `/healthz`, `/run`, and `/metrics`.
- Cancellation: pass a `CancelToken` in `Context.components["cancel_token"]` to cooperatively halt agents, with_steps, and GraphRunner.
- Hot reload: `manifests.hot_reload.watch_manifest` polls manifest files and triggers callbacks on change.
- Plugins: `agentic_codex.plugins.load_plugins` discovers adapters registered via entry points (`agentic_codex.*_adapters` groups).
- Queueing: `agentic_codex.service.queue.RunnerQueue` powers async/sync job handling; HTTP service exposes `/run` (async/sync) and `/jobs/{id}`.
- Rate limits: namespace/agent/tool token-bucket limits via `MultiLimiter`; `CommunicationHub` and `ToolCapability` enforce limits, and GraphRunner can mutate graphs at runtime through `_graph_additions`.
- Prompt versioning: `PromptManager` wraps Prompt-Framework (install `prompt` extra) to register and retrieve versioned prompts inside agents/workflows.
- Logging: `StructuredLogger` emits JSON-formatted logs (used by `CoordinatorBase`); set run IDs for correlation.
- Manifest `rate_limits` and `prompts` blocks are applied automatically by CLI/service via `apply_rate_limits` and `load_prompts`.
- Policies: manifest `policies` can configure namespace ACLs; use `agentic_codex.core.safety.policy_apply.apply_policies` to wire them into `CommunicationHub`.

### Vector DB Integration

- `agentic_codex.core.vector_db` provides a `VectorDBAdapter` protocol, a `FaissAdapter` default (in-memory with optional persistence), and an `ExternalVectorDBConfig` + `create_adapter` factory for future backends (pgvector/MySQL/MongoDB/FerretDB/custom). Install `faiss` to enable the FAISS adapter.
- Embeddings: `agentic_codex.core.embeddings` ships adapters for spaCy, callable functions, and a lightweight hash-based embedder for offline demos.
- OTLP export: `OtelExporter` (optional `otel` extra) can be passed into `Tracer` to forward spans/metrics to OTLP endpoints.
