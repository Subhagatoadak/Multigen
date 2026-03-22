# Multigen — Installation & Setup Guide

This guide covers every supported way to run Multigen, from a five-minute local prototype to a production Docker Compose stack.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Quick Start — Local Mode (no infrastructure)](#2-quick-start--local-mode-no-infrastructure)
3. [Full Local Stack — Manual Setup](#3-full-local-stack--manual-setup)
4. [Docker Compose — Recommended for Development](#4-docker-compose--recommended-for-development)
5. [CLI Installation (`multigen` command)](#5-cli-installation-multigen-command)
6. [SDK Installation (Python client)](#6-sdk-installation-python-client)
7. [MCP Server Setup](#7-mcp-server-setup)
8. [Scaffolding a New Project](#8-scaffolding-a-new-project)
9. [Environment Variables Reference](#9-environment-variables-reference)
10. [Verifying Your Installation](#10-verifying-your-installation)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. Prerequisites

### Required

| Tool | Minimum version | Notes |
|---|---|---|
| Python | 3.10 | 3.11 recommended |
| pip | 23+ | `pip install --upgrade pip` |
| Git | any | to clone the repo |

### Required for full stack only

| Tool | Minimum version | Install |
|---|---|---|
| Docker | 24+ | [docs.docker.com](https://docs.docker.com/get-docker/) |
| Docker Compose | v2 (`docker compose`) | bundled with Docker Desktop |

### Optional

| Tool | Purpose |
|---|---|
| `httpx` or `requests` | required by the `multigen` CLI for HTTP commands |
| `watchdog>=4.0` | required for agent hot reload |
| `mcp>=1.0.0` | required for the MCP server |

---

## 2. Quick Start — Local Mode (no infrastructure)

Run a workflow entirely in-process — no Temporal, Kafka, or MongoDB required. Ideal for trying Multigen for the first time or writing tests.

```bash
# 1. Clone the repository
git clone https://github.com/Subhagatoadak/Multigen.git
cd Multigen

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 3. Install core dependencies
pip install -r requirements.txt
pip install -e ./Agent-Components  # bundled cognitive layer

# 4. Copy environment config
cp .env.example .env
# Open .env and set OPENAI_API_KEY=sk-...

# 5. Run a workflow locally (no Temporal required)
python - <<'EOF'
import asyncio
from sdk.multigen.local_runner import LocalWorkflowRunner

graph = {
    "nodes": [
        {"id": "greet", "agent": "EchoAgent", "params": {"message": "Hello, Multigen!"}},
    ],
    "edges": [],
    "entry": "greet",
}

import agents.echo_agent.echo_agent  # register EchoAgent
result = asyncio.run(LocalWorkflowRunner().run(graph, payload={}))
print(result)
EOF
```

Or using the CLI directly:

```bash
pip install click pyyaml          # CLI soft dependencies

# Write a minimal workflow
cat > /tmp/hello.yaml <<'EOF'
steps:
  - name: greet
    agent: EchoAgent
    params:
      message: "Hello!"
EOF

python -m cli.multigen_cli run /tmp/hello.yaml --local
```

> **Note**: Local mode skips circuit breakers, reflection loops, human-in-the-loop gates, and MongoDB persistence. Use it for rapid prototyping only.

---

## 3. Full Local Stack — Manual Setup

Run every service on your machine without Docker. Useful when you need to attach debuggers or modify infrastructure config directly.

### 3.1 Install infrastructure

**Temporal** (required for durable workflow execution):

```bash
# macOS
brew install temporal

# Or run directly with temporalite (single binary)
curl -sSf https://temporal.download/cli.sh | sh
temporal server start-dev
# Temporal UI will be at http://localhost:8233
# gRPC endpoint: localhost:7233
```

**MongoDB** (required for state persistence):

```bash
# macOS
brew tap mongodb/brew
brew install mongodb-community@7.0
brew services start mongodb-community@7.0
# MongoDB running at mongodb://localhost:27017

# Ubuntu / Debian
sudo apt-get install -y mongodb
sudo systemctl start mongod
```

**Kafka + Zookeeper** (required for the flow-messaging worker):

```bash
# macOS — easiest via Homebrew
brew install kafka
brew services start zookeeper
brew services start kafka
# Kafka broker at localhost:9092

# Or run with Docker for just Kafka:
docker run -d --name kafka \
  -p 9092:9092 \
  -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
  confluentinc/cp-kafka:7.6.0
```

### 3.2 Install Python dependencies

```bash
git clone https://github.com/Subhagatoadak/Multigen.git
cd Multigen
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install -e ./Agent-Components
```

### 3.3 Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

```dotenv
OPENAI_API_KEY=sk-...
KAFKA_BROKER_URL=localhost:9092
TEMPORAL_SERVER_URL=localhost:7233
TEMPORAL_TASK_QUEUE=flow-task-queue
MONGODB_URI=mongodb://localhost:27017
LLM_MODEL=gpt-4o
```

### 3.4 Start services (four separate terminals)

```bash
# Terminal 1 — Orchestrator API (FastAPI)
uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2 — Capability Service
uvicorn capability_service.main:app --host 0.0.0.0 --port 8001 --reload

# Terminal 3 — Temporal worker (executes agent_activity)
python -m workers.temporal_worker

# Terminal 4 — Kafka flow worker (bridges Kafka → Temporal)
python -m orchestrator.services.flow_messaging
```

### 3.5 Verify

```bash
curl http://localhost:8000/health
# {"status":"ok","version":"0.2.0"}
```

---

## 4. Docker Compose — Recommended for Development

The Compose file starts the complete stack (Zookeeper, Kafka, MongoDB, PostgreSQL, Temporal, Temporal UI, Capability Service, Orchestrator, Flow Worker, Temporal Worker) with a single command.

### 4.1 Clone and configure

```bash
git clone https://github.com/Subhagatoadak/Multigen.git
cd Multigen
cp .env.example .env
# Edit .env — at minimum set OPENAI_API_KEY
```

### 4.2 Build and start

```bash
docker compose up --build
```

First run takes 3–5 minutes to pull images and build the Python services. Subsequent starts are faster:

```bash
docker compose up
```

### 4.3 Service endpoints

| Service | URL | Notes |
|---|---|---|
| Orchestrator API | `http://localhost:8000` | FastAPI, Swagger at `/docs` |
| Capability Service | `http://localhost:8001` | Agent capability directory |
| Temporal UI | `http://localhost:8080` | Workflow execution monitor |
| Kafka broker | `localhost:9092` | For external producers/consumers |
| MongoDB | `localhost:27017` | Direct connection for inspection |

### 4.4 Useful Compose commands

```bash
# Start in background
docker compose up -d

# Watch logs for a specific service
docker compose logs -f orchestrator
docker compose logs -f temporal-worker

# Restart a single service after code change
docker compose restart orchestrator

# Rebuild only changed images
docker compose up --build orchestrator temporal-worker

# Stop all services (preserves volumes)
docker compose down

# Stop and wipe all data volumes (full reset)
docker compose down -v

# Open a shell inside the orchestrator container
docker compose exec orchestrator bash
```

### 4.5 Hot-reloading code changes

The Compose file does not mount source volumes by default. For inner-loop development, either:

**Option A** — Mount source and use `--reload`:

```yaml
# Add to orchestrator service in docker-compose.yml:
volumes:
  - .:/app
command: uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000 --reload
```

**Option B** — Run only infrastructure via Compose, services locally:

```bash
# Start only infrastructure
docker compose up zookeeper kafka mongodb temporal-db temporal temporal-ui -d

# Run services locally with hot reload
uvicorn orchestrator.main:app --host 0.0.0.0 --port 8000 --reload
python -m workers.temporal_worker
```

---

## 5. CLI Installation (`multigen` command)

The `multigen` CLI provides `run`, `status`, `logs`, `signal`, and `init` commands.

### 5.1 Install as a package entry point

Add to your `pyproject.toml` (or `setup.py`):

```toml
[project.scripts]
multigen = "cli.multigen_cli:cli"
```

Then install:

```bash
pip install -e .
multigen --help
```

### 5.2 Run directly without installing

```bash
# From the repo root
python -m cli.multigen_cli --help

# Or as an alias
alias multigen="python -m cli.multigen_cli"
```

### 5.3 Install CLI dependencies

```bash
pip install click pyyaml httpx
# httpx is used for HTTP calls; requests works as a fallback
```

### 5.4 Usage examples

```bash
# Point at a running orchestrator (default: http://localhost:8000)
export MULTIGEN_BASE_URL=http://localhost:8000

# Submit a graph workflow
multigen run workflows/my_workflow.yaml --payload '{"topic": "AI safety"}'

# Submit and stream events in real time
multigen run workflows/my_workflow.yaml --payload '{"topic": "AI"}' --watch

# Run locally without Temporal (in-process)
multigen run workflows/my_workflow.yaml --local

# Check workflow status
multigen status cli-abc12345

# Stream live node completion events
multigen logs cli-abc12345

# Poll instead of SSE (for restricted networks)
multigen logs cli-abc12345 --poll

# Runtime control signals
multigen signal interrupt  cli-abc12345
multigen signal resume     cli-abc12345
multigen signal skip-node  cli-abc12345 --node analyse
multigen signal jump-to    cli-abc12345 --node report
multigen signal prune      cli-abc12345 --node slow_branch
multigen signal approve-agent cli-abc12345 --agent DynamicResearcher
```

---

## 6. SDK Installation (Python client)

The Python SDK (`sdk/multigen/`) provides async and sync clients, a fluent DSL builder, type stubs, and the local runner.

### 6.1 Install from the repository

```bash
# Editable install (recommended during development)
pip install -e ./sdk

# Or add as a path dependency in pyproject.toml:
# multigen-sdk @ file:///path/to/Multigen/sdk
```

### 6.2 Install from PyPI (once published)

```bash
pip install multigen-sdk
```

### 6.3 Using the async client

```python
from sdk.multigen.client import MultigenClient
from sdk.multigen.dsl import GraphBuilder

graph_def = (
    GraphBuilder()
    .node("research").agent("ResearchAgent").timeout(60).done()
    .node("report").agent("ReportAgent").done()
    .edge("research", "report")
    .entry("research")
    .build()
)

async with MultigenClient(base_url="http://localhost:8000") as client:
    result = await client.run_graph(graph_def, payload={"topic": "quantum computing"})
    print(result)
```

### 6.4 Using the sync client

```python
from sdk.multigen.sync_client import SyncMultigenClient

with SyncMultigenClient(base_url="http://localhost:8000") as client:
    result = client.run_graph(graph_def, payload={"topic": "quantum computing"})
```

### 6.5 Type-safe DSL stubs

```python
from sdk.multigen.types import GraphDef, NodeDef, EdgeDef

graph: GraphDef = {
    "nodes": [
        NodeDef(id="step1", agent="EchoAgent", params={"message": "hi"}),
        NodeDef(id="step2", agent="EchoAgent", depends_on=["step1"]),
    ],
    "edges": [EdgeDef(source="step1", target="step2")],
    "entry": "step1",
}
```

### 6.6 Local runner (no infrastructure)

```python
from sdk.multigen.local_runner import LocalWorkflowRunner

runner = LocalWorkflowRunner()
result = await runner.run(graph_def, payload={"topic": "AI"}, workflow_id="test-1")
```

---

## 7. MCP Server Setup

The MCP server exposes the full Multigen orchestrator as tools to any MCP-capable host (Claude Code, Claude Desktop, Cursor, Windsurf).

### 7.1 Install dependencies

```bash
pip install -r mcp_server/requirements.txt
# mcp>=1.0.0, httpx>=0.27, pydantic>=2.0
```

### 7.2 Run standalone

```bash
# From the repo root
python -m mcp_server.server

# Or using the MCP CLI
pip install mcp
mcp run mcp_server/server.py
```

### 7.3 Configure Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "multigen": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "env": {
        "MULTIGEN_BASE_URL": "http://localhost:8000",
        "MULTIGEN_API_KEY": ""
      }
    }
  }
}
```

Restart Claude Desktop. The Multigen tools will appear in the tool selector.

### 7.4 Configure Claude Code

```bash
# Add to your project's MCP config
claude mcp add multigen python -m mcp_server.server \
  -e MULTIGEN_BASE_URL=http://localhost:8000
```

### 7.5 Environment variables

| Variable | Default | Description |
|---|---|---|
| `MULTIGEN_BASE_URL` | `http://localhost:8000` | Orchestrator API URL |
| `MULTIGEN_API_KEY` | *(empty)* | Bearer token for authenticated deployments |
| `MULTIGEN_TIMEOUT` | `30` | HTTP request timeout in seconds |

---

## 8. Scaffolding a New Project

`multigen init` creates a complete starter project structure.

```bash
# Basic sequential workflow template (default)
multigen init my-project

# Graph workflow template (parallel nodes + depends_on + reflection)
multigen init my-project --template graph

# Explicit sequential template
multigen init my-project --template sequence
```

Generated structure:

```
my-project/
├── agents/
│   ├── __init__.py
│   └── my_agent.py          # @register_agent("MyAgent") with epistemic envelope
├── workflows/
│   └── my_workflow.yaml     # ready-to-run workflow DSL
├── tests/
│   ├── __init__.py
│   └── test_my_agent.py     # pytest: echo, confidence, missing-param tests
├── .env.example
├── requirements.txt
└── README.md
```

After scaffolding:

```bash
cd my-project
cp .env.example .env          # add OPENAI_API_KEY
pip install -r requirements.txt

# Run locally (no infrastructure)
multigen run workflows/my_workflow.yaml --local

# Run against a live stack
multigen run workflows/my_workflow.yaml --payload '{"message": "hello"}'

# Run tests
pytest tests/ -v
```

---

## 9. Environment Variables Reference

All variables are read at startup. Copy `.env.example` to `.env` and edit.

### Core (required for full stack)

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | — | OpenAI API key for LLM-backed agents |
| `LLM_MODEL` | `gpt-4o` | Default LLM model name |

### Kafka

| Variable | Default | Description |
|---|---|---|
| `KAFKA_BROKER_URL` | `localhost:9092` | Kafka bootstrap broker |
| `FLOW_REQUEST_TOPIC` | `flow-requests` | Incoming workflow request topic |
| `FLOW_RESPONSE_TOPIC` | `flow-responses` | Workflow result topic |
| `FLOW_DLQ_TOPIC` | `flow-dead-letter` | Dead-letter queue for failed messages |

### Temporal

| Variable | Default | Description |
|---|---|---|
| `TEMPORAL_SERVER_URL` | `localhost:7233` | Temporal gRPC endpoint |
| `TEMPORAL_TASK_QUEUE` | `flow-task-queue` | Default worker task queue |
| `TEMPORAL_TASK_QUEUES` | *(same as above)* | Comma-separated list for partition-aware fan-out |

### MongoDB

| Variable | Default | Description |
|---|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `CAPABILITY_DB_NAME` | `multigen` | Database name |
| `CAPABILITY_COLLECTION_NAME` | `capabilities` | Capability directory collection |
| `GRAPH_STATE_DB_NAME` | `multigen` | Graph node state database |

### Services

| Variable | Default | Description |
|---|---|---|
| `MULTIGEN_BASE_URL` | `http://localhost:8000` | Orchestrator URL (used by CLI and SDK) |
| `CAPABILITY_SERVICE_URL` | `http://localhost:8001` | Capability service URL |
| `ALLOWED_ORIGINS` | `*` | CORS allowed origins (comma-separated) |

### Observability

| Variable | Default | Description |
|---|---|---|
| `OTEL_EXPORTER_OTLP_ENDPOINT` | *(empty)* | OTLP collector endpoint (e.g. `http://jaeger:4317`) |
| `OTEL_SERVICE_NAME` | `multigen-orchestrator` | Service name in traces |
| `METRICS_PORT` | `8000` | Prometheus metrics scrape port |

### Local / Dev

| Variable | Default | Description |
|---|---|---|
| `MULTIGEN_LOCAL_MODE` | `false` | Force local execution mode globally |
| `LLAMAINDEX_DOCS_PATH` | `./data/docs` | Document directory for LlamaIndex agents |

---

## 10. Verifying Your Installation

### Health check

```bash
curl http://localhost:8000/health
# Expected: {"status":"ok","version":"0.2.0"}
```

### List registered agents

```bash
curl http://localhost:8000/agents
# Expected: {"agents":[{"name":"EchoAgent","class":"EchoAgent"},...]}'
```

### Submit a test workflow via CLI

```bash
multigen run workflows/examples/echo.yaml --local
```

### Submit via the API directly

```bash
curl -s -X POST http://localhost:8000/workflows/run \
  -H "Content-Type: application/json" \
  -d '{
    "dsl": {
      "steps": [
        {"name": "greet", "agent": "EchoAgent", "params": {"message": "ping"}}
      ]
    },
    "payload": {},
    "workflow_id": "verify-001"
  }' | python -m json.tool
```

### Check Temporal UI

Open `http://localhost:8080` — you should see the workflow execution appear under the `flow-task-queue` namespace.

### Run the test suite

```bash
pytest -x -q
```

---

## 11. Troubleshooting

### `No agent registered under name 'X'`

The worker imports agents at startup. If an agent is missing:

1. Check that the agent module is imported in `workers/temporal_worker.py`.
2. Verify the `@register_agent("X")` decorator is applied.
3. Restart the worker — imports only run at startup (unless using hot reload).

### `NonDeterminismError` from Temporal

A code change was deployed while workflows were in-flight. Use `workflow.patched()` (see `flow_engine/versioning.py`) to support both old and new code paths simultaneously. Never change workflow logic without a patch guard.

### `kafka.errors.NoBrokersAvailable`

Kafka is not running or the `KAFKA_BROKER_URL` is wrong. Verify with:

```bash
docker compose ps kafka
# or
nc -zv localhost 9092
```

### `Motor: ServerSelectionTimeoutError`

MongoDB is not running or `MONGODB_URI` is incorrect. Verify with:

```bash
mongosh --eval "db.adminCommand('ping')"
```

### `Connection refused: temporal:7233`

Temporal is starting up (the auto-setup image runs DB migrations first). Wait ~30 seconds and retry. The worker and orchestrator services have `restart: unless-stopped` to retry automatically in Compose.

### Port conflicts

If any default port (8000, 8001, 8080, 7233, 9092, 27017) is already in use, override it in `docker-compose.yml` by changing the left side of the port mapping:

```yaml
ports:
  - "9000:8000"   # host:container — change 9000 to any free port
```

Then update `MULTIGEN_BASE_URL` accordingly.

### Python version issues

Multigen requires Python 3.10+. Check your version:

```bash
python --version
# Python 3.11.x  ✓
```

If you have multiple versions, use `python3.11 -m venv .venv` explicitly.

### `watchdog` not installed (hot reload disabled)

```bash
pip install watchdog>=4.0
```

The hot reloader raises an `ImportError` with this message if `watchdog` is missing — the rest of the system continues to work normally without it.

### `ImportError: No module named 'agentic_codex'`

The cognitive layer lives in `Agent-Components/` and must be installed separately:

```bash
pip install -e ./Agent-Components
```

This is handled automatically in Docker builds but must be done manually in local setups.
