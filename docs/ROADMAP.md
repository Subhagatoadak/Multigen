# Multigen — Strategic Roadmap to Market Leadership

> **Vision**: Become the standard enterprise framework for durable, transparent, human-governed multi-agent AI orchestration.

---

## Current State (v0.2 — March 2026)

The core framework is feature-complete and battle-tested:

- [x] Declarative graph + sequence workflow DSL
- [x] Durable execution via Temporal.io (crash recovery, retries, exactly-once semantics)
- [x] 10 runtime control signals (interrupt, resume, inject, jump, skip, reroute, fan-out, prune, approve, reject)
- [x] Dynamic agent lifecycle with human approval gates
- [x] Epistemic transparency engine (per-node confidence, uncertainty propagation, transparency report)
- [x] Circuit breakers with fallback routing and dead-letter capture
- [x] Reflection loops (auto-critique when confidence < threshold)
- [x] Fan-out consensus reasoning (N parallel agents → best result)
- [x] Python SDK (async + sync/Jupyter-safe)
- [x] OpenTelemetry spans + Prometheus metrics per node
- [x] MongoDB CQRS read model for workflow state
- [x] Apache Kafka distributed messaging
- [x] MCP server (Claude Desktop / Cursor / Windsurf)
- [x] Capability directory microservice
- [x] 10 Jupyter notebooks (quickstart → full autonomy demo)
- [x] M&A due diligence end-to-end example
- [x] Docker Compose full local stack

---

## Phase 1 — Production Hardening (Priority: CRITICAL)
**Target: Q2 2026 | Required before any enterprise deployment**

### Security

- [ ] **API Key authentication** — Bearer token on all endpoints; support multiple keys per tenant
- [ ] **HTTPS / TLS** — TLS termination at orchestrator; mTLS between worker and Temporal
- [ ] **RBAC** — Roles: `admin`, `operator` (can signal), `viewer` (read-only), `approver` (approval gate only)
- [ ] **Secrets management** — Vault / AWS Secrets Manager for `OPENAI_API_KEY` and other credentials; remove from `.env`
- [ ] **Rate limiting** — Per-API-key and per-IP limits; configurable via environment variable
- [ ] **Input sanitization audit** — Review all user-controlled inputs for injection vectors; fuzz test DSL parser
- [ ] **Audit log** — Immutable append-only log of every signal, approval, and rejection with actor identity

### Reliability

- [ ] **`continue_as_new`** — Handle workflows exceeding Temporal's 50K event limit; preserve state across history reset
- [ ] **Graceful worker shutdown** — On SIGTERM, stop accepting new activities; wait for in-flight activities to complete (configurable drain timeout)
- [ ] **Deep health check** — `/health` validates Temporal ping, Kafka consumer lag, MongoDB connectivity; returns degraded vs healthy vs down
- [ ] **Temporal worker pool sizing** — Auto-configure `max_concurrent_activities` based on CPU count and LLM concurrency limits
- [ ] **Dead letter requeue** — API endpoint to requeue dead-lettered workflows after root cause is fixed
- [ ] **MongoDB write retry** — `persist_node_state_activity` currently best-effort; add configurable retry with exponential backoff before giving up

### Testing

- [ ] **Integration test suite** — End-to-end tests using real Temporal sandbox (`temporalio.testing`); no mocking
- [ ] **Property-based tests** — Use `hypothesis` to generate random DSL inputs and verify parser invariants
- [ ] **Chaos tests** — Kill worker mid-activity; assert workflow resumes correctly after restart
- [ ] **Load tests** — `locust` suite; assert P99 < 500ms for signal delivery under 100 concurrent workflows
- [ ] **Contract tests** — Verify SDK ↔ orchestrator API contract using `schemathesis` or `pact`

---

## Phase 2 — True Parallel Execution (Priority: HIGH)
**Target: Q2 2026 | Required for performance-sensitive use cases**

### Architecture Change

Currently the BFS queue is fully sequential — one node executes at a time. This means 5 independent nodes take 5× the time of one. True parallel execution requires a dependency-aware scheduler:

- [ ] **`depends_on` field** — Add `"depends_on": ["node_a", "node_b"]` to node definitions; auto-infer from edges when absent
- [ ] **DAG readiness check** — Before each BFS iteration, find all nodes whose dependencies are fully satisfied in `context`; execute them simultaneously via `asyncio.gather()`
- [ ] **Context write locking** — Prevent two parallel nodes writing to the same context key without coordination
- [ ] **Parallel activity slots** — Configure `max_concurrent_workflow_tasks` on the Temporal worker to reflect true parallelism
- [ ] **Cross-worker fan-out** — Distribute fan-out nodes across multiple Temporal task queues so they execute on different workers simultaneously
- [ ] **Streaming node results** — Server-Sent Events (SSE) endpoint `GET /workflows/{id}/stream` that pushes node completion events in real time; no polling required

---

## Phase 3 — Developer Experience (Priority: HIGH)
**Target: Q3 2026 | Required for community adoption**

### CLI

- [ ] **`multigen` command** — Install via `pip install multigen[cli]`
  - `multigen run <workflow.yaml>` — submit workflow from YAML/JSON file
  - `multigen status <workflow_id>` — show execution state
  - `multigen logs <workflow_id>` — stream Temporal event history
  - `multigen signal interrupt <workflow_id>` — send signals from terminal
  - `multigen approve <workflow_id>` — interactive agent approval from CLI
  - `multigen epistemic <workflow_id>` — print epistemic report as formatted table

### Local Development

- [ ] **Local execution mode** — `multigen.local.LocalRunner` executes GraphWorkflow in-process without Temporal or Kafka; for unit tests and rapid prototyping
- [ ] **Agent hot reload** — Watch `agents/` directory; re-register changed agents without worker restart (using `watchfiles`)
- [ ] **`multigen init`** — Scaffold new project: creates `agents/`, `workflows/`, `tests/`, `.env.example`, `docker-compose.yml`

### Testing Framework

- [ ] **`WorkflowTestRunner`** — Run workflows against mock agents; assert on context state, node execution order, dead letters
- [ ] **`AgentTestCase`** — Base class for agent unit tests; `assert_confidence_above(0.7)`, `assert_epistemic_present()`, `assert_output_keys_include(["result"])`
- [ ] **Recorded playback** — Record a real workflow run; replay against mock agents; verify determinism

### Documentation

- [ ] **Interactive API docs** — Enhanced Swagger UI with example payloads for every endpoint
- [ ] **Architecture diagram generator** — Auto-generate Mermaid diagrams from a running workflow's graph definition
- [ ] **Agent template library** — 20+ example agents covering finance, legal, engineering, marketing
- [ ] **Migration guide** — LangGraph → Multigen, CrewAI → Multigen migration guides with automated converter

### VS Code Extension

- [ ] **Graph visual editor** — Drag-and-drop node/edge construction; live preview of graph definition JSON
- [ ] **Execution overlay** — Show real-time node status (pending/running/done/failed) on the graph view during execution
- [ ] **Epistemic inspector** — Sidebar panel showing confidence and known unknowns per node after execution
- [ ] **Agent registry browser** — List all registered agents with their capabilities; click to insert as graph node

---

## Phase 4 — Intelligence Layer (Priority: MEDIUM)
**Target: Q3-Q4 2026 | Key differentiator for AI-native organisations**

### Learning and Optimization

- [ ] **Agent performance registry** — Track per-agent: mean confidence, p95 latency, error rate, token cost over time; queryable via API
- [ ] **Confidence calibration** — Compare predicted confidence vs actual accuracy on tasks with ground truth; generate calibration curves; alert on overconfident agents
- [ ] **LLM router** — Route agent calls to the cheapest model that achieves the required confidence threshold; configurable cost/quality tradeoff per node
- [ ] **Prompt optimizer** — Use epistemic reports to identify which prompts produce low-confidence outputs; run A/B testing on prompt variants; auto-promote winners
- [ ] **RL orchestration policy** — Learn from workflow outcomes which graph structures produce highest-quality results for each task type; use PPO to optimise routing decisions

### Semantic Discovery

- [ ] **Semantic agent matching** — When an unknown agent is requested, embed the capability description and search the capability directory for similar existing agents; suggest before triggering dynamic creation
- [ ] **Intent → graph translation** — Improved text-to-DSL: given a natural language goal, generate a full multi-node graph (not just a single step) with appropriate agents and edges
- [ ] **Workflow template library** — Curated, tested templates: `due_diligence`, `resume_screening`, `risk_analysis`, `content_generation`, `data_pipeline`; usable via `GraphBuilder.from_template("due_diligence")`

---

## Phase 5 — Enterprise Scale (Priority: HIGH for enterprise sales)
**Target: Q4 2026 | Required for Fortune 500 deployment**

### Kubernetes and Infrastructure

- [ ] **Helm chart** — Production-ready Kubernetes deployment
  - Horizontal Pod Autoscaler for orchestrator and workers
  - Pod Disruption Budgets for zero-downtime deploys
  - Resource requests/limits tuned for LLM workloads
  - NetworkPolicy for micro-segmentation
  - ServiceAccount with minimal RBAC permissions
- [ ] **Operator** — `MultigenWorkflow` CRD; `kubectl apply -f workflow.yaml` starts a workflow
- [ ] **Terraform provider** — Manage capabilities, task queues, and agent configurations as IaC

### Multi-Tenancy

- [ ] **Tenant isolation** — Per-tenant: Temporal namespace, Kafka consumer group, MongoDB database
- [ ] **Tenant admin API** — Create/delete tenants; set per-tenant agent allowlists
- [ ] **Cross-tenant workflow federation** — Workflow in tenant A can dispatch a node to an agent registered in tenant B (with explicit permission)

### Compliance and Governance

- [ ] **SOC 2 readiness** — Access logging, encryption at rest, key rotation, audit log export
- [ ] **GDPR compliance** — PII detection in workflow payloads; automatic redaction in logs and epistemic reports; right-to-erasure support
- [ ] **Compliance export** — One-click PDF export of: epistemic report + Temporal event history + agent approval records for regulatory audit
- [ ] **Data residency** — Route workflows to specific regional worker clusters based on data classification tags

### Cost Management

- [ ] **Token usage tracking** — Count input/output tokens per agent activity; aggregate per workflow, per tenant, per agent
- [ ] **Budget alerts** — Configurable cost thresholds; pause workflow and notify operator when budget is at risk
- [ ] **Cost attribution** — Tag each workflow with project/cost-center; export to FinOps tools (CloudHealth, Apptio)

---

## Phase 6 — Ecosystem (Priority: MEDIUM)
**Target: 2027 | Required for community flywheel**

### Multi-Language Support

- [ ] **TypeScript SDK** — Full-featured async client; browser and Node.js compatible; used by VS Code extension
- [ ] **Go SDK** — High-performance client for infrastructure tooling and sidecars
- [ ] **REST OpenAPI spec** — Auto-generated from FastAPI; used to generate SDKs for any language

### Community and Marketplace

- [ ] **Agent marketplace** — `multigen install SentimentAnalysisAgent` installs a community agent; versioned, tested, certified
- [ ] **Agent certification** — Automated test suite for community agents; `Verified` badge for agents passing confidence calibration tests
- [ ] **Workflow template marketplace** — Share and reuse workflow templates; fork and customise via GitHub

### Integrations

- [ ] **GitHub Actions** — `multigen/run-workflow@v1` action; trigger workflows on PR, push, or schedule
- [ ] **Slack bot** — Receive workflow completion notifications; approve agents from Slack
- [ ] **Zapier / n8n connector** — Trigger Multigen workflows from 1000+ apps via Zapier/n8n
- [ ] **dbt integration** — Trigger analysis workflows after dbt model runs; pass model outputs as workflow payload
- [ ] **Snowflake / BigQuery** — Native DB tool adapter for querying data warehouses from within agent activities

### Hosted Cloud

- [ ] **Multigen Cloud** — Managed deployment: Temporal, Kafka, MongoDB, orchestrator; pay-per-execution
- [ ] **Serverless agent execution** — Lambda/Cloud Run-backed agents; no persistent worker required for low-volume use cases
- [ ] **Web UI** — Browser-based workflow builder, execution monitor, and epistemic report viewer

---

## Immediate Next Steps (This Sprint)

These are the highest-leverage items to execute right now, in priority order:

1. **Parallel BFS execution** — The biggest performance gap vs competitors; unblocks real enterprise workloads
2. **API key authentication** — Blocker for any external sharing or enterprise pilot
3. **Graceful worker shutdown** — Required for zero-downtime K8s deployments
4. **`multigen` CLI** — Dramatically lowers the barrier to try and demo the framework
5. **Integration test suite** — Confidence for rapid iteration; required before v1.0 release
6. **Helm chart** — Required for all enterprise pilots
7. **Streaming SSE endpoint** — Better UX than polling; enables real-time dashboard
8. **`continue_as_new`** — Required for long-running enterprise workflows (days/weeks)

---

## Success Metrics

| Metric | 6-month target | 12-month target |
|---|---|---|
| GitHub stars | 500 | 5,000 |
| PyPI downloads/month | 1,000 | 50,000 |
| Community agents in marketplace | 0 | 50 |
| Enterprise pilots | 1 | 10 |
| Contributing developers | 3 | 25 |
| Notebooks/examples in repo | 10 | 30 |
| Test coverage | 45% | 80% |
| P99 signal delivery latency | <500ms | <200ms |

---

## What Makes Multigen Win

The frameworks to beat are **LangGraph** (most adopted), **CrewAI** (easiest to start), and **AutoGen** (best for conversational agents).

Multigen's path to winning:

1. **Win on reliability first** — "We survived a production incident that would have destroyed a LangGraph workflow" is the most powerful enterprise testimonial. Phase 1 is critical.

2. **Win on transparency** — No other framework makes the AI honest about what it doesn't know. In financial services, healthcare, and legal, this is not a nice-to-have — it's a regulatory requirement.

3. **Win on governance** — The approval gate pattern resonates immediately with CISOs and compliance officers. "You can't deploy an AI agent that creates new capabilities without human sign-off" is a universal enterprise requirement.

4. **Win on developer experience** — Once production hardening is done, the CLI and VS Code extension will make Multigen the easiest framework to use while being the most powerful. This is how you get the community flywheel started.

5. **Win on ecosystem** — The agent marketplace and workflow template library create network effects. Every new agent and template increases the value for all users.
