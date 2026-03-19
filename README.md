# Multigen

**Autonomous, self-expanding multi-agent orchestration framework.**

Multigen lets you compose AI agents into workflows using a declarative DSL — with built-in support for parallel execution, conditional branching, dynamic step generation, durable execution via Temporal, and a reinforcement-learning feedback loop that continuously optimises orchestration policy.

[![CI](https://github.com/Subhagatoadak/Multigen/actions/workflows/ci.yml/badge.svg)](https://github.com/Subhagatoadak/Multigen/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)](https://www.python.org)
[![License](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.1.0--dev-orange)](CHANGELOG.md)

---

## Why Multigen?

| Feature | LangGraph | CrewAI | AutoGen | **Multigen** |
| --------- | ----------- | ------ | ------- | ------------ |
| Declarative DSL | ✗ | ✗ | ✗ | ✅ |
| Durable execution (Temporal) | ✗ | ✗ | ✗ | ✅ |
| Parallel + conditional steps | Partial | ✗ | ✗ | ✅ |
| Dynamic agent generation | ✗ | ✗ | ✗ | ✅ *(roadmap)* |
| RL policy optimisation | ✗ | ✗ | ✗ | ✅ *(roadmap)* |
| Self-registering agents | ✗ | ✗ | ✗ | ✅ |
| OpenTelemetry tracing | ✗ | ✗ | ✗ | ✅ |

---

## Quickstart

**Requirements:** Docker, Docker Compose, Python 3.11+

```bash
git clone https://github.com/Subhagatoadak/Multigen.git
cd Multigen

# Copy and fill in your environment variables
cp .env.example .env
# Required: set OPENAI_API_KEY in .env (only needed for text-to-DSL feature)

# Start all services
docker-compose up -d --build

# Wait ~60s for Temporal to initialise, then check health
curl http://localhost:8000/health   # {"status":"ok"}
curl http://localhost:8001/health   # {"status":"ok"}
```

**Register an agent and run your first workflow:**

```bash
# 1. Register EchoAgent with the capability directory
curl -X POST http://localhost:8001/capabilities \
  -H "Content-Type: application/json" \
  -d '{"name":"EchoAgent","version":"1.0.0","description":"Echoes input","metadata":{}}'

# 2. Submit a workflow
curl -X POST http://localhost:8000/workflows/run \
  -H "Content-Type: application/json" \
  -d '{
    "dsl": {
      "steps": [
        {"name":"step1","agent":"EchoAgent","params":{"msg":"hello multigen"}}
      ]
    }
  }'
# → {"instance_id": "..."}

# 3. Watch it execute in the Temporal UI
open http://localhost:8080
```

---

## Services

| Service | URL | Description |
| --------- | ----- | ------------- |
| Orchestrator | `http://localhost:8000` | FastAPI — submit workflows, register agents |
| Capability Service | `http://localhost:8001` | Agent capability directory (MongoDB-backed) |
| Temporal UI | `http://localhost:8080` | Workflow execution dashboard |
| Temporal gRPC | `localhost:7233` | Workflow engine |
| Kafka | `localhost:9092` | Message bus |
| MongoDB | `localhost:27017` | Capability + feedback store |

---

## Architecture

```text
┌─────────────────────────────────────────────────────────────┐
│  Client (curl / SDK / CLI)                                   │
└─────────────────────┬───────────────────────────────────────┘
                      │ POST /workflows/run
┌─────────────────────▼───────────────────────────────────────┐
│  Orchestrator  :8000  (FastAPI)                              │
│  • DSL Parser  • LLM text→DSL  • Capability Validator        │
│  • OTel Tracer                                               │
└──────┬──────────────────────────────────┬───────────────────┘
       │ validate                          │ publish
       ▼                                   ▼
┌─────────────────┐              ┌─────────────────────────────┐
│ Capability Svc  │              │  Kafka: flow-requests        │
│ :8001 (MongoDB) │              └──────────────┬──────────────┘
└─────────────────┘                             │ consume
                                   ┌────────────▼────────────┐
                                   │  Flow Worker             │
                                   │  (Kafka Consumer)        │
                                   └────────────┬────────────┘
                                                │ start_workflow
                                   ┌────────────▼────────────┐
                                   │  Temporal               │
                                   │  ComplexSequenceWorkflow │
                                   │  • sequential            │
                                   │  • parallel              │
                                   │  • conditional (AST eval)│
                                   │  • dynamic subtrees      │
                                   └────────────┬────────────┘
                                                │ agent_activity
                                   ┌────────────▼────────────┐
                                   │  Temporal Worker         │
                                   │  • EchoAgent             │
                                   │  • LangChainAgent        │
                                   │  • LlamaIndexAgent       │
                                   │  • SpawnerAgent          │
                                   │  • Custom agents         │
                                   └─────────────────────────┘
```

See [`docs/architecture.html`](docs/architecture.html) for the full interactive architecture diagram (open in browser).

---

## DSL Reference

Workflows are defined as a JSON/YAML `steps` array. Four step types are supported:

### Sequential

```json
{
  "name": "summarise",
  "agent": "LangChainAgent",
  "params": {"text": "..."}
}
```

### Parallel

```json
{
  "name": "evaluate",
  "parallel": [
    {"name": "skill_check",   "agent": "SkillMatcherAgent",   "params": {}},
    {"name": "culture_check", "agent": "CultureFitAgent",     "params": {}}
  ]
}
```

### Conditional

Conditions are evaluated against accumulated step outputs using a safe AST evaluator — no `eval()`.

```json
{
  "name": "route",
  "conditional": [
    {
      "condition": "score >= 70",
      "then": {"name": "interview", "agent": "InterviewSchedulerAgent", "params": {}}
    }
  ],
  "else": {"name": "reject", "agent": "RejectionAgent", "params": {}}
}
```

**Supported condition syntax:** `==`, `!=`, `<`, `<=`, `>`, `>=`, `and`, `or`, `not`, subscript access (`result["key"]`), attribute access.

### Dynamic Subtree

Runs a spawner agent whose output is a list of steps, then executes those steps.

```json
{
  "name":  "expand",
  "agent": "SpawnerAgent",
  "params": {"count": 3, "agent": "EchoAgent"},
  "dynamic_subtree": {}
}
```

---

## Building an Agent

1. **Implement** — subclass `BaseAgent`:

```python
# agents/my_agent/my_agent.py
from agents.base_agent import BaseAgent
from orchestrator.services.agent_registry import register_agent

@register_agent("MyAgent")
class MyAgent(BaseAgent):
    async def run(self, params: dict) -> dict:
        # your logic here
        return {"result": params.get("input", "") + " processed"}
```

1. **Import** in the Temporal worker so the decorator fires:

```python
# workers/temporal_worker.py
import agents.my_agent.my_agent  # noqa: F401
```

1. **Register** with the capability directory:

```bash
curl -X POST http://localhost:8001/capabilities \
  -H "Content-Type: application/json" \
  -d '{"name":"MyAgent","version":"1.0.0","description":"...","metadata":{}}'
```

1. **Rebuild** the worker container:

```bash
docker-compose up -d --build temporal-worker
```

That's it — your agent is now available in any workflow DSL.

---

## Example: Resume Screening Pipeline

A complete end-to-end example that exercises all four step types.

**Pipeline:**

```text
Resume Input
    │
    ▼ sequential
ResumeParserAgent          — normalise raw input
    │
    ▼ parallel (3 concurrent)
SkillMatcherAgent          — score skill match vs. job requirements
ExperienceEvaluatorAgent   — score seniority & years
CultureFitAgent            — score values alignment
    │
    ▼ sequential
ScoreAggregatorAgent       — weighted overall score (skill 50%, exp 30%, culture 20%)
    │
    ▼ conditional  (score >= 70 ?)
InterviewSchedulerAgent    — book interview slot        ← YES
RejectionAgent             — polite rejection           ← NO
    │
    ▼ sequential
ReportCompilerAgent        — final screening report
```

**Run it:**

```bash
# Register all 8 screening agents
# Submit a strong candidate (score ~98 → interview)
python examples/resume_screening.py --candidate strong

# Submit a weak candidate (score ~10 → reject)
python examples/resume_screening.py --candidate weak

# Show the full DSL before submitting
python examples/resume_screening.py --candidate borderline --show-dsl
```

**Expected outcomes:**

| Profile | Skills | Experience | Culture | Overall | Decision |
| --------- | -------- | ------------ | ------- | ------- | ---------- |
| `strong` | 96 | 100 | 100 | **98** | ✅ Interview |
| `borderline` | 73 | 100 | 67 | **79** | ✅ Interview |
| `weak` | 0 | 33 | 0 | **10** | ❌ Reject |

Watch execution live at `http://localhost:8080` (Temporal UI).

---

## Running Tests

```bash
pip install -r requirements.txt -r requirement_dev.txt
pytest tests/ -v
```

---

## Project Structure

```text
Multigen/
├── agents/
│   ├── base_agent.py              # BaseAgent abstract class
│   ├── echo_agent/                # Demo agent
│   ├── langchain_agent/           # LangChain LCEL agent
│   ├── llamaindex_agent/          # LlamaIndex retrieval agent
│   ├── spawner_agent/             # Dynamic step generator
│   └── screening_agents/          # Resume screening example agents
├── orchestrator/
│   ├── main.py                    # FastAPI app + OTel setup
│   ├── controller/
│   │   ├── workflow.py            # POST /workflows/run
│   │   └── registration.py        # POST /capabilities
│   ├── services/
│   │   ├── config.py              # Environment config
│   │   ├── agent_registry.py      # @register_agent decorator
│   │   ├── dsl_parser.py          # DSL → Step objects
│   │   ├── capability_directory.py
│   │   ├── capability_directory_client.py
│   │   ├── flow_messaging.py      # Kafka consumer worker
│   │   └── llm_service.py         # text → DSL via OpenAI
│   └── telemetry.py               # OpenTelemetry setup
├── flow_engine/
│   └── workflows/sequence.py      # ComplexSequenceWorkflow (Temporal)
├── capability_service/
│   └── main.py                    # Capability directory FastAPI app
├── workers/
│   └── temporal_worker.py         # Temporal worker process
├── messaging/
│   └── kafka_client.py            # Confluent Kafka wrapper
├── examples/
│   └── resume_screening.py        # End-to-end pipeline demo
├── tests/                         # pytest test suite
├── docs/
│   ├── architecture.html          # Interactive architecture diagram
│   ├── architecture.md            # Mermaid diagrams
│   └── ...
├── docker-compose.yml
├── Dockerfile.orchestrator
├── Dockerfile.capability
├── requirements.txt
└── .env.example
```

---

## Configuration

Copy `.env.example` to `.env` and set the values:

| Variable | Default | Description |
| ---------- | --------- | ------------- |
| `OPENAI_API_KEY` | — | Required for text-to-DSL preprocessing |
| `LLM_MODEL` | `gpt-4o` | OpenAI model to use |
| `KAFKA_BROKER_URL` | `localhost:9092` | Kafka broker |
| `TEMPORAL_SERVER_URL` | `localhost:7233` | Temporal gRPC address |
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection |
| `CAPABILITY_SERVICE_URL` | `http://localhost:8000` | Capability service base URL |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OTLP collector (Jaeger/Grafana). Console if unset |
| `OTEL_SERVICE_NAME` | `multigen-orchestrator` | Service name in traces |
| `LLAMAINDEX_DOCS_PATH` | `./data/docs` | Document directory for LlamaIndexAgent |

---

## Roadmap

### v0.1 — Foundation ✅

- [x] FastAPI orchestrator with DSL parsing
- [x] Sequential, parallel, conditional, dynamic step execution
- [x] Temporal workflow engine integration
- [x] Kafka message bus (request / response / DLQ)
- [x] MongoDB capability directory
- [x] `@register_agent` decorator system
- [x] OpenTelemetry tracing
- [x] Prometheus metrics
- [x] Docker Compose local dev stack
- [x] GitHub Actions CI

### v0.2 — Developer Experience

- [ ] Python SDK (`pip install multigen-sdk`)
- [ ] CLI (`multigen run workflow.yaml`)
- [ ] Output variable interpolation between steps (`{{steps.step1.output.key}}`)
- [ ] Per-step timeout configuration
- [ ] Agent input/output schema validation

### v0.3 — Enterprise

- [ ] JWT authentication on orchestrator
- [ ] OPA (Open Policy Agent) guardrails
- [ ] Human-in-the-loop approval engine (Temporal signals)
- [ ] Error handler with escalation policies
- [ ] Helm chart for Kubernetes deployment
- [ ] Multi-tenancy (namespaced capability directories)

### v0.4 — Intelligence

- [ ] Feedback collector (structured outcome events per workflow)
- [ ] RL policy learner (PPO — optimise agent routing + retry policy)
- [ ] AgentFactory (LLM-driven agent code generation + SAST + CI deploy)
- [ ] Drift monitor + prompt optimiser

---

## Contributing

1. Fork the repo and create a branch: `git checkout -b feature/my-agent`
2. Build a new agent (see [Building an Agent](#building-an-agent) above)
3. Add tests in `tests/`
4. Open a PR — CI runs automatically

See `CONTRIBUTING.md` for full guidelines.

---

## Tech Stack

| Layer | Technology |
| ------- | ----------- |
| API | FastAPI 0.115, Pydantic v2, Uvicorn |
| Workflow engine | Temporal.io 1.11 |
| Message bus | Apache Kafka (confluent-kafka 2.10) |
| Persistence | MongoDB (Motor async driver) |
| LLM / AI | OpenAI 1.76, LangChain, LlamaIndex |
| Observability | OpenTelemetry, Prometheus |
| Infrastructure | Docker Compose, Kubernetes / Helm *(roadmap)* |
| CI | GitHub Actions, ruff |

---

## References

1. IBM Research, "Autonomic Computing," 2003
2. Microsoft Research, "AutoGen: A Framework for Agentic AI," 2024
3. Kreps et al., "Kafka: a Distributed Messaging System for Log Processing," 2011
4. Schulman et al., "Proximal Policy Optimization Algorithms," 2017
5. [Temporal.io Documentation](https://docs.temporal.io)

---

Version: 0.1.0-dev · Apache 2.0 License
