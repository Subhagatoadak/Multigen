# Agent Components

This repository provides a reference implementation of the **agentic components**
package – a composable toolkit for building multi-agent workflows. The
repository ships with:

- Core protocols, memories, stores, and safety utilities.
- Lego-style capability system with an `AgentBuilder` for incremental composition.
- LLM adapter abstractions (callable + OpenAI env adapter).
- A skills registry with built-in stubs.
- Assembly line and Swarm pattern coordinators.
- YAML manifest loaders and a CLI entry point with Pydantic validation.
- Example notebooks and scripts demonstrating query agents and OpenAI powered workflows.

## Lego-Style Agents

The new capability registry lets you attach LLMs, memories, context packs, skill
registries, or arbitrary resources to an agent without rewriting its loop:

```python
from agentic_codex import AgentBuilder, Context, EnvOpenAIAdapter
from agentic_codex.core.memory import EpisodicMemory

builder = (
    AgentBuilder(name="query", role="assistant")
    .with_llm(EnvOpenAIAdapter(model="gpt-4o-mini"))
    .with_memory(EpisodicMemory())
    .with_context({"knowledge": KNOWLEDGE_BASE})
    .with_step(query_step)
)

agent = builder.build()
context = Context(goal="Where is Orion located?")
result = agent.run(context)
print(result.out_messages[-1].content)
```

You can also prime a `Context` ahead of time via `builder.prime_context` and add
custom capabilities through `builder.with_capability(...)`. See
`advanced_learning.md` for in-depth guidance on reinforcement learning and
JEPA-style alignment hooks.

A reinforcement learning trainer can be attached with
`builder.with_reinforcement_learning(...)`, wiring its `update` method into the
agent kernel's learn stage so policy updates run automatically after each step.
For representation-style alignment, `builder.with_jepa_learning(...)` hooks a
JEPA-inspired predictor/target pair into the learn loop so embeddings, losses,
and updates accumulate without rewriting the agent logic.

## Pattern Catalog

`agentic_codex.patterns` now ships a catalog (`CATALOG`, `list_patterns`) that
captures when to reach for each orchestration style and which personas to cast:

- **Hierarchical:** Ministry of Experts (Minister → Domain Experts → Cabinet Chair),
  Manager–Worker (Supervisor, Specialists, Reviewer), Judge–Referee Gate (Producer, Judge, Referee).
- **Collective Intelligence:** Agent Swarm (Explorers, Swarm Coordinator, Voter),
  Blackboard/Stigmergy (Scribe, Researchers, Arbiter), Debate (Proponent, Opponent, Judge).
- **Market & Game Theory:** Contract Net (Auctioneer, Vendors, Adjudicator),
  Incentive-Aligned Games (Game Master, Participants, Auditor).
- **Graph & Mesh:** Agent Mesh (Peers, Router, Observer) and Mixture-of-Experts Committee (Router, Experts, Chair).
- **Pipeline & Dataflow:** Assembly Line, Map–Reduce (Shard Planner, Mappers, Reducer),
  Streaming Actor (Event Sentinel, Actor, Controller).
- **Federation & Tenancy:** Hub-and-Spoke (Federation Broker, Hub Leads, Compliance Officer),
  Guild / Service Pools (Guild Master, Members, Router).
- **Safety & Governance:** Guardrail Sandwich (Pre-Filter, Primary Agent, Post-Validator),
  Two-Person Rule (Initiator, Co-Signer, Auditor), Policy-as-Code Lattice (Policy Author, Engine, Observer).

Each category is also backed by coordinators such as
`MinistryOfExperts`, `BlackboardCoordinator`, `ContractNetCoordinator`,
`MeshCoordinator`, `MapReduceCoordinator`, `HubAndSpokeCoordinator`,
and safety overlays including `GuardrailSandwich` and `TwoPersonRuleCoordinator`.

## Lifecycle, Tools, and Manifests

- `AgentKernel` turns bare step functions into lifecycle-aware agents (`init → perceive → decide → act → learn`).
- `ToolCapability` attaches guarded tool adapters (HTTP/RAG/DB/Math/Code) with permissions and usage budgets.
- The planner/router/scheduler primitives now expose `TaskPlan`, `PolicyRouter`, and async scheduling helpers.
- A branching `GraphRunner` supports conditional/batched/parallel steps with tracing and monitoring hooks.
- Reinforcement-aware tool wrappers plus an MCP-style tool registry make it easy to expose remote tools safely.
- CLI now supports `run`, `validate`, and `replay` commands with optional run-store persistence.
- A `with_steps` runner supports linear batch/parallel/conditional/retry/wait/timeout flows without building a graph.
- Manifests capture agents, tools, models, policies, and patterns so you can wire workflows as config.

## Quickstart

```bash
pip install -e .
pytest
```

To run a workflow from a manifest:

```bash
agentic run --workflow path/to/workflow.yaml --goal "Ship feature" --vars key=value --run-store runs/
# legacy alias:
codex run --workflow path/to/workflow.yaml --goal "Ship feature" --vars key=value --run-store runs/
agentic validate --workflow path/to/workflow.yaml
agentic replay --run-store runs/ --run-id <id>
```

### Manifest extensions

- `rate_limits`: configure namespace/agent/tool buckets; applied automatically in CLI/service via `apply_rate_limits`.
- `prompts`: versioned prompt definitions loaded into `PromptManager` for use at runtime (Prompt-Framework optional extra).

Interactive examples live under ``agentic_codex/examples``:

- ``query_agent.py`` shows how to answer questions from an in-memory context store.
- ``openai_full_workflow.py`` wires the ``EnvOpenAIAdapter`` into an assembly line.
  Set ``OPENAI_API_KEY`` before running the script to execute against the real API.
- ``openai_swarm_demo.ipynb`` launches a two-agent swarm (researcher + strategist) with
  lego-style capabilities composed via ``AgentBuilder``.
- ``hierarchy_showcase.ipynb`` demonstrates lifecycle-aware agents, guarded tools, and the
  ``MinistryOfExperts`` coordinator.
- ``contract_net_demo.ipynb`` shows a market-style auction using the new Contract Net coordinator.
- ``model_lab_demo.ipynb`` runs a modelling → evaluation → recommendation loop with message bus coordination
  until model quality passes a 95 threshold.
- ``with_steps_orchestration.ipynb`` demonstrates batch/parallel/conditional/retry/timeout in a linear flow.
- ``graph_runner_branching.ipynb`` shows DAG branching with parallel groups and batching.
- ``mcp_tools_demo.ipynb`` wires MCP tools via the built-in registry.
- ``run_store_replay.ipynb`` persists and replays runs with `RunStore`.
- ``manifest_tool_registration.ipynb`` registers a custom tool manifest via the `agentic` CLI.
- ``code_workflow_agents.ipynb`` chains writer, reviewer, recommender, and documentation agents.
- ``production_end_to_end.ipynb`` stitches together namespaces, comms, with_steps, GraphRunner, safe tools, MCP, RL tools, RunStore, and metrics.
- ``code_quality_loop.ipynb`` runs a writer → reviewer → recommender → doc agent loop until code quality score reaches 9.5.
- Vector DB support lives in ``core/vector_db.py`` with a `VectorDBAdapter` protocol, `FaissAdapter` default (install `faiss`), and a factory for future backends (pgvector/MySQL/MongoDB/FerretDB/custom).
- Optional service layer: ``agentic_codex.service.http`` provides a FastAPI app factory (`build_app`) when the `service` extra is installed.
- Policies: manifest `policies` can drive namespace ACLs via `agentic_codex.core.safety.policy_apply.apply_policies` on a `CommunicationHub`.
- Hot reload: ``manifests/hot_reload.py`` provides manifest watchers to rebuild on file changes.
- Plugins: ``agentic_codex.plugins.load_plugins`` loads adapters registered via entry points (`agentic_codex.*_adapters` groups).
- Service queue: ``agentic_codex.service.queue.RunnerQueue`` underpins async/sync runs in the HTTP service (`/run` + `/jobs/{id}`).
- Rate limits: namespace/agent/tool rate limiting via `MultiLimiter`; `CommunicationHub` and `ToolCapability` enforce limits.
- Dynamic graphs: GraphRunner can accept runtime `_graph_additions` from step state updates and enforce branch budgets.
- Prompt Framework integration: optional `PromptManager` wrapper (`agentic_codex.core.prompting`) uses `Prompt-Framework` for versioned prompt storage when installed via the `prompt` extra.
- Structured logging: `StructuredLogger` emits JSON logs (hooked into `CoordinatorBase`).

## Logical Architecture (textual)

- **Builder Layer**: `AgentBuilder` composes step functions with capabilities (`LLMCapability`, `ToolCapability`, `ContextCapability`, `MemoryCapability`, `SkillRegistryCapability`, `ReinforcementLearningCapability`, `JEPALearningCapability`) and optional `with_steps` sequences. Outputs runtime-ready `Agent` instances.
- **Runtime Kernel**: `Agent` wraps an `AgentKernel` lifecycle (`init → perceive → decide → act → learn`), pulling injected components from capabilities. Errors/latency are recorded in `_metrics`.
- **Capabilities & Tools**: Adapters for LLMs (`EnvOpenAIAdapter`, `FunctionAdapter`), tools (HTTP/RAG/DB/math/safe file read/sanitized code/ RL-wrapped), MCP registry integration, memory stores, and safety guards (permissions/budgets).
- **Orchestration**: `with_steps` (linear/batch/parallel/conditional), `GraphRunner` (DAG with tracing, guard hooks), pattern coordinators (hierarchical, swarm, market, pipeline, graph/mesh, safety overlays), and schedulers (serial/async/threaded).
- **Communication**: `CommunicationHub` + `CommEnvelope` for direct/broadcast/group/private/namespace/encrypted/ephemeral messaging; `MessageBus` for durable conversation history.
- **Observability**: `Tracer` (spans/metrics), `_metrics` counters/latency, `RunStore` persistence + replay, CLI `replay` to inspect stored runs.
- **Manifests & CLI**: Pydantic-validated manifests (agents/tools/policies/patterns/MCP/run_store), and `agentic` CLI (`run`, `validate`, `register`, `replay`) plus legacy `codex`.

## Class/Module Relationships (textual diagram)

- `AgentBuilder` → produces `Agent` (has `AgentKernel`, `CapabilityRegistry`, `step`)
  - attaches `Capability` implementations:
    - `LLMCapability` → injects `LLMAdapter` (`EnvOpenAIAdapter` | `FunctionAdapter`)
    - `ToolCapability` → wraps tool adapters (HTTP/RAG/DB/Math/SafeFile/SanitizedCode/Reinforcement/MCP)
    - `ContextCapability` / `ResourceCapability` / `MemoryCapability` / `SkillRegistryCapability`
    - `ReinforcementLearningCapability` / `JEPALearningCapability`
- `AgentKernel` lifecycle hooks invoke `step` and trigger learn loops; metrics captured in `Context.scratch["_metrics"]`.
- `Context` holds `components`, `tools`, `memory`, `scratch`, `inbox`, `registry`, `policies`.
- Orchestration:
  - `run_steps` + `StepSpec` (batch/parallel/conditional/retry/branch/timeout/circuit-breaker) can be embedded via `AgentBuilder.with_steps`.
  - `GraphRunner` executes `GraphNodeSpec` DAGs with tracing, guards, parallel groups, batching, conditional nodes.
  - Pattern coordinators (e.g., `MinistryOfExperts`, `SwarmCoordinator`, `ContractNetCoordinator`, `MapReduceCoordinator`, `MeshCoordinator`, safety overlays) sit atop `CoordinatorBase`.
- Communication:
  - `CommunicationHub` uses `MessageBus` storage and handler dispatch for direct/broadcast/group/private/namespace/encrypted/ephemeral.
- Observability & Persistence:
  - `Tracer` emits spans/metrics; `RunStore` persists `RunResult` for replay; CLI `replay` reads stored runs.
- Manifests/CLI:
  - `WorkflowManifest` (agents/tools/policies/patterns/MCP/run_store) → validated by `validate_workflow`.
  - `agentic` CLI commands (run/validate/register/replay) operate on manifests and run-store outputs.
