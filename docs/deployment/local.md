# Local Development Guide

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.10+ | [python.org](https://python.org) |
| Node.js | 18+ | [nodejs.org](https://nodejs.org) |
| Docker | 24+ | [docker.com](https://docker.com) |
| Docker Compose | v2+ | Bundled with Docker Desktop |
| Git | 2.30+ | System package manager |

---

## Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/Subhagatoadak/Multigen.git
cd Multigen

# 2. Create and activate Python virtual environment
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate

# 3. Install all dependencies
pip install -e Agent-Components/
pip install -r requirements.txt
pip install -r requirement_dev.txt

# 4. Start the full stack
docker compose up

# 5. Verify everything is running
curl http://localhost:8009/ping        # → {"status": "ok"}
open http://localhost:3000             # Simulator UI
```

---

## Component Ports

| Component | Default Port | Description |
|-----------|-------------|-------------|
| Orchestrator API | `8009` | REST API for workflow management |
| Simulator Frontend | `3000` | Visual dashboard UI |
| Temporal Server | `7233` | Workflow engine gRPC |
| Temporal Web UI | `8080` | Temporal management console |
| Prometheus | `9090` | Metrics scraping (optional) |

---

## Development Workflow

### Running Tests

```bash
# Unit tests (no external dependencies)
pytest tests/ -v --tb=short

# With coverage
pytest tests/ --cov=agents --cov=flow_engine --cov-report=html

# Run specific test file
pytest tests/test_pattern_agents.py -v

# Run tests matching a pattern
pytest tests/ -k "test_mapreduce" -v
```

### Code Quality

```bash
# Linting (project uses ruff)
ruff check .

# Auto-fix
ruff check . --fix

# Format
ruff format .
```

### Hot Reload

The orchestrator starts with `--reload` by default in development mode:

```bash
cd agentic-simulator/backend
uvicorn main:app --host 0.0.0.0 --port 8009 --reload
```

Changes to Python files are picked up automatically.

For the frontend:

```bash
cd agentic-simulator/frontend
npm run dev    # hot reload on http://localhost:3000
```

---

## Environment Variables

Create a `.env` file in the project root (not committed to Git):

```bash
# .env
# LLM (optional — not required for testing)
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini

# Temporal (optional — uses embedded server in dev mode)
TEMPORAL_HOST=localhost
TEMPORAL_PORT=7233
TEMPORAL_NAMESPACE=default

# Observability (optional)
OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
PROMETHEUS_PORT=9090
```

Load with:

```bash
export $(cat .env | xargs)
```

---

## Notebooks

```bash
# Install Jupyter
pip install jupyter

# Start notebook server
jupyter notebook notebooks/

# Or use JupyterLab
pip install jupyterlab
jupyter lab notebooks/
```

All notebooks in `notebooks/` are runnable without API keys. The use-case notebooks are in `notebooks/use_cases/`.

---

## Troubleshooting

### Port already in use

```bash
# Find what's using port 8009
lsof -i :8009   # macOS/Linux
netstat -ano | findstr 8009   # Windows

# Kill the process
kill -9 <PID>
```

### Docker compose fails

```bash
# Full reset (removes volumes and containers)
docker compose down -v
docker compose up --build
```

### Import errors after dependency updates

```bash
pip install -e Agent-Components/ --force-reinstall
```
