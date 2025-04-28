
## 1. Phased Component Roadmap

| Phase | Components to Build                                                                                   | Key Goals                                             |
|-------|-------------------------------------------------------------------------------------------------------|-------------------------------------------------------|
| **1** | • API Gateway (FastAPI + Traefik proxy)  <br> • Orchestrator core (FastAPI service)  <br> • DSL parser | Stand up public API, basic “start workflow” endpoint, parse YAML/JSON into an in-memory graph |
| **2** | • Flow Engine integration (Temporal Python SDK)  <br> • Message Bus wiring (Kafka + confluent-kafka)  | Execute a simple sequence of steps via Kafka topics and Temporal workflows |
| **3** | • BaseAgent framework (Python abstract class)  <br> • Simple demo agent (EchoAgent)                   | Define agent interface, test end-to-end invocation over Kafka |
| **4** | • Capability Directory (MongoDB)  <br> • RegistrationService (FastAPI)                                 | Agents self-register their metadata; orchestrator can look them up |
| **5** | • Guardrails Engine (OPA server + Python client)  <br> • Pre-invoke policy hooks in Orchestrator     | Enforce “allow/deny/pause” on each step                |
| **6** | • Approval Engine (Node.js + minimal React UI)  <br> • Error Handler (Python)                         | Human-in-the-loop gate and retry/dead-letter logic      |
| **7** | • Feedback Collector (Python/Node.js)  <br> • Feedback Store (Postgres)  <br> • FeedbackRewarder       | Capture approvals & corrections, generate reward signals |
| **8** | • RL Policy module (PyTorch + simple PPO)  <br> • Plug‐in into Orchestrator for `selectAction`        | Learn to pick the best agent over time                 |
| **9** | • Prompt & Flow Optimizers (Node.js + LLM API)  <br> • RLHF Trainer (SageMaker/Python)                | Automate prompt-tuning and small DSL tweaks via feedback |
| **10**| • AgentFactory & ToolFactory (Python + OpenAI)  <br> • GitHub Actions CI for auto-build/deploy       | LLM-driven codegen, CI/CD integration                   |
| **11**| • Observability (Prometheus exporters, Grafana dashboards, Jaeger tracing)                            | End-to-end visibility and SLOs                         |
| **12**| • Python SDK / CLI (multigen-sdk, multigen-cli)                                                        | DSL-as-code + workflow management from the terminal    |



---

## 2. Technology Stack

| Layer                   | Stack                                               |
|-------------------------|-----------------------------------------------------|
| Orchestration API       | Python, FastAPI                                     |
| DSL Parsing/Validation  | PyYAML, Pydantic schemas                            |
| Workflow Engine         | Temporal (Python SDK) or Argo (K8s CRD)             |
| Messaging               | Apache Kafka + confluent-kafka Python client        |
| Agents & Adapters       | Python 3.11                                         |
| Capability Store        | MongoDB (metadata), Neo4j (optional graph queries)  |
| Context Store           | Redis (snapshots), Milvus (vector embeddings)       |
| Feedback Store          | PostgreSQL                                          |
| Policy Engine           | Open Policy Agent (OPA)                             |
| Approval UI             | React + Node.js backend                             |
| RL & Learning           | PyTorch + stable-baselines3 (PPO), AWS SageMaker    |
| LLM Codegen             | OpenAI / Anthropic API                              |
| CI/CD                   | GitHub Actions, Docker Compose / ArgoCD             |
| Observability           | Prometheus, Grafana, Jaeger, Chaos Mesh             |
| CLI / SDK               | Python package + Click or Typer                     |

---

## 3. Repository Folder Structure

```
multigen/  
├── orchestrator/           # FastAPI service  
│   ├── main.py  
│   ├── controllers/  
│   ├── models/  
│   └── services/  
│  
├── flow_engine/            # Temporal workflows or Argo definitions  
│   └── workflows/  
│  
├── agents/                 # BaseAgent + built-in agents  
│   ├── base_agent.py  
│   ├── echo_agent/  
│   ├── dynamic_spawner_agent/  
│   ├── langchain_agent/  
│   └── llamaindex_agent/  
│  
├── adapters/               # ToolAdapter templates  
│   └── openapi_adapter/  
│  
├── services/               # Auxiliary microservices  
│   ├── guardrails/         # OPA server + client  
│   ├── approval/           # Node.js + React  
│   ├── error_handler/      # Retry & DLQ logic  
│   └── registration/       # CapabilityDirectory API  
│  
├── feedback/               # Collector, store, processors  
│   ├── collector/  
│   ├── store/              # DB models/migrations  
│   └── processors/         # rewarder, RLHF trainer, optimizers  
│  
├── policy/                 # RLPolicy module (PPO)  
│   └── ppo_agent.py  
│  
├── factories/              # AgentFactory + ToolFactory  
│   └── codegen/  
│  
├── observability/          # Prometheus exporters, Jaeger config  
│   └── metrics/  
│  
├── sdk/                    # Python SDK / DSL-as-code  
│   └── multigen_sdk/  
│  
├── cli/                    # multigen-cli (Typer)  
│   └── commands.py  
│  
├── ci/                     # GitHub Actions workflows  
│   └── docker-compose.yml  # Local dev compose setup  
│  
└── docs/                   # Architecture docs, PlantUML, ERDs  
```


## Branchs

| Phase | Branch                                  | Components to Build                                                                                   | Key Goals                                             |
|-------|-----------------------------------------|-------------------------------------------------------------------------------------------------------|-------------------------------------------------------|
| 1     | `feature/api-orchestrator-core`         | API Gateway (FastAPI + Traefik) <br> Orchestrator core (FastAPI) <br> DSL parser                      | Stand up public API, basic “start workflow” endpoint, parse YAML/JSON into an in-memory graph |
| 2     | `feature/flow-messaging-integration`    | Flow Engine integration (Temporal Python SDK) <br> Message Bus wiring (Kafka + confluent-kafka)       | Execute a simple sequence of steps via Kafka topics and Temporal workflows |
| 3     | `feature/base-agent-framework`          | BaseAgent framework (Python abstract class) <br> Simple demo agent (EchoAgent)                       | Define agent interface, test end-to-end invocation over Kafka |
| 4     | `feature/capability-directory`          | Capability Directory (MongoDB) <br> RegistrationService (FastAPI)                                     | Agents self-register their metadata; orchestrator can look them up |
| 5     | `feature/opa-guardrails`                | Guardrails Engine (OPA server + Python client) <br> Pre-invoke policy hooks in Orchestrator          | Enforce “allow/deny/pause” on each step                |
| 6     | `feature/approval-error-handling`       | Approval Engine (Node.js + React UI) <br> Error Handler (Python)                                      | Human-in-the-loop gate and retry/dead-letter logic      |
| 7     | `feature/feedback-reward`               | Feedback Collector (Python/Node.js) <br> Feedback Store (Postgres) <br> FeedbackRewarder              | Capture approvals & corrections, generate reward signals |
| 8     | `feature/rl-policy-integration`         | RL Policy module (PyTorch + PPO) <br> Plug-in into Orchestrator for `selectAction`                    | Learn to pick the best agent over time                 |
| 9     | `feature/prompt-flow-optimizers`        | Prompt & Flow Optimizers (Node.js + LLM API) <br> RLHF Trainer (SageMaker Local)                     | Automate prompt-tuning and small DSL tweaks via feedback |
| 10    | `feature/agent-tool-factory-codegen`    | AgentFactory & ToolFactory (Python + OpenAI) <br> GitHub Actions CI for auto-build/deploy             | LLM-driven codegen, CI/CD integration                   |
| 11    | `feature/observability-chaos`           | Observability (Prometheus exporters, Grafana dashboards, Jaeger tracing) <br> Chaos Mesh integration   | End-to-end visibility and resilience drills            |
| 12    | `feature/python-sdk-cli`                | Python SDK / CLI (multigen-sdk, multigen-cli)                                                          | DSL-as-code + workflow management from the terminal    |



Client (CLI / UI)
     │   POST /v1/workflows/run  {DSL + payload}
     ▼
FastAPI Controller
     ├─> parse_workflow(dsl)  (in-memory graph)
     └─> KafkaClient.publish("flow-requests", parsedMessage)
              │
              ▼
flow_messaging.py (Kafka consumer)
     ├─> poll "flow-requests"
     ├─> run_simple_workflow(steps, payload)
     └─> publish("flow-responses", results)
              │
              ▼
(Optionally) FastAPI or UI polls/subscribes
     └─> GET /v1/workflows/{id}/status or WebSocket
