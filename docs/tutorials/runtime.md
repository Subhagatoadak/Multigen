# Tutorial — Runtime & Simulator Integration

## Overview

The Multigen **Runtime** connects your agent workflows to two infrastructure components:

1. **Temporal Workflow Engine** — provides durable, fault-tolerant execution. Workflows survive process restarts, network failures, and service outages. Every step is checkpointed.
2. **Agentic Simulator** — a visual web dashboard for monitoring, replaying, and stress-testing your pipelines in real time.

```
Your Agent Pipeline
       │
  Multigen SDK
       │
  ┌────┴────────────────────────────┐
  │    Temporal Worker              │  ← handles durability + retries
  │  (flow_engine/workflows/)       │
  └────────────────────────────────┘
       │
  ┌────┴────────────────────────────┐
  │  Agentic Simulator              │  ← real-time visualization
  │  (agentic-simulator/)           │
  └────────────────────────────────┘
```

---

## 1. Starting the Runtime Locally

### Option A — Docker Compose (recommended)

```bash
cd Multigen
docker compose up
```

This starts:
- Temporal server at `localhost:7233`
- Orchestrator API at `localhost:8009`
- Simulator backend at `localhost:8009`
- Simulator frontend at `localhost:3000`

### Option B — Manual startup

```bash
# Terminal 1: Temporal server
docker run --rm -p 7233:7233 temporalio/auto-setup:1.24

# Terminal 2: Orchestrator + Simulator backend
cd Multigen/agentic-simulator/backend
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8009 --reload

# Terminal 3: Simulator frontend
cd Multigen/agentic-simulator/frontend
npm install
npm run dev
```

---

## 2. Using the SDK Client

The `SyncMultigenClient` is a synchronous Python client for interacting with the orchestrator from notebooks and scripts.

```python
from multigen import SyncMultigenClient
from multigen.dsl import WorkflowBuilder, GraphBuilder

# Connect to the orchestrator
client = SyncMultigenClient(base_url="http://localhost:8009", timeout=60.0)

# Verify connectivity
assert client.ping(), "Orchestrator not reachable"
print(f"Connected. Server version: {client.version()}")

# List registered agents
agents = client.list_agents()
print(f"Available agents: {[a['name'] for a in agents]}")
```

---

## 3. Running a Workflow

Use `WorkflowBuilder` to construct a workflow DSL definition, then run it:

```python
from multigen.dsl import WorkflowBuilder

# Build a simple 2-step workflow
workflow = (
    WorkflowBuilder()
    .step("parse")
        .agent("EchoAgent")
        .params({"message": "Parsing input data"})
        .timeout(30)
        .done()
    .step("analyse")
        .agent("EchoAgent")
        .params({"message": "Analysing parsed data"})
        .depends_on("parse")
        .timeout(60)
        .done()
    .build()
)

# Submit the workflow
payload = {
    "document": "Sample input text for analysis",
    "metadata": {"source": "notebook", "run_id": "demo-001"}
}

response = client.run_workflow(workflow_def=workflow, payload=payload)
instance_id = response.instance_id
print(f"Workflow launched: instance_id={instance_id}")
```

---

## 4. Running a Graph Workflow

```python
from multigen.dsl import GraphBuilder

graph_def = (
    GraphBuilder()
    .node("ingest")
        .agent("EchoAgent")
        .params({"stage": "ingest"})
        .timeout(30)
        .done()
    .node("analyse_a")
        .agent("EchoAgent")
        .params({"stage": "analyse_a"})
        .timeout(60)
        .done()
    .node("analyse_b")
        .agent("EchoAgent")
        .params({"stage": "analyse_b"})
        .timeout(60)
        .done()
    .node("report")
        .agent("EchoAgent")
        .params({"stage": "report"})
        .timeout(30)
        .done()
    .edge("ingest", "analyse_a")
    .edge("ingest", "analyse_b")
    .edge("analyse_a", "report")
    .edge("analyse_b", "report")
    .entry("ingest")
    .max_cycles(10)
    .build()
)

response = client.run_graph(graph_def=graph_def, payload={"data": "test"})
print(f"Graph instance: {response.instance_id}")
```

---

## 5. Pushing Events to the Simulator

Push custom events to the simulator for real-time visualisation of your pipeline:

```python
import time

# Push a custom event to the simulator's event stream
client.push_event(
    instance_id=instance_id,
    event_type="agent.step.completed",
    data={
        "agent": "AnalysisAgent",
        "step": "analyse",
        "duration_ms": 342,
        "confidence": 0.87,
        "output_keys": ["risk_score", "decision"],
    }
)

# Push multiple events for a full pipeline run
pipeline_events = [
    {"type": "pipeline.started",    "data": {"run_id": "run-001"}},
    {"type": "agent.called",        "data": {"agent": "DataFetcher", "t": 0}},
    {"type": "agent.completed",     "data": {"agent": "DataFetcher", "t": 120, "status": "OK"}},
    {"type": "agent.called",        "data": {"agent": "Analyser",   "t": 125}},
    {"type": "agent.completed",     "data": {"agent": "Analyser",   "t": 580, "status": "OK"}},
    {"type": "pipeline.completed",  "data": {"run_id": "run-001",   "duration_ms": 590}},
]

for event in pipeline_events:
    client.push_event(instance_id=instance_id, event_type=event["type"], data=event["data"])
    time.sleep(0.05)  # small delay for animation in simulator UI
```

---

## 6. Workflow State Inspection

Poll workflow state and inspect outputs:

```python
import time

# Wait for workflow to complete
def wait_for_completion(client, instance_id, timeout=120, poll_interval=2):
    """Poll workflow state until completed or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = client.get_state(instance_id)
        status = state.get("status", "UNKNOWN")
        print(f"  Status: {status}")
        if status in ("COMPLETED", "FAILED", "CANCELLED"):
            return state
        time.sleep(poll_interval)
    raise TimeoutError(f"Workflow {instance_id} did not complete within {timeout}s")

state = wait_for_completion(client, instance_id)
print(f"\nFinal status: {state['status']}")
print(f"Step outputs: {state.get('step_outputs', {})}")
print(f"Metrics: {state.get('metrics', {})}")
```

---

## 7. Temporal Worker for Production

For production workloads, run a dedicated Temporal worker process:

```python
# worker.py
import asyncio
from temporalio.client import Client
from temporalio.worker import Worker

from flow_engine.workflows.sequence import AgentWorkflow
from flow_engine.workflows.sequence import run_agent_activity


async def main():
    client = await Client.connect("localhost:7233")

    worker = Worker(
        client,
        task_queue="multigen-task-queue",
        workflows=[AgentWorkflow],
        activities=[run_agent_activity],
    )

    print("Temporal worker started. Waiting for workflows...")
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python worker.py
```

### Retry Policies

Configure retry behaviour in the Temporal workflow definition:

```python
from temporalio.common import RetryPolicy
from datetime import timedelta

retry_policy = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=30),
    maximum_attempts=5,
    non_retryable_error_types=["ValueError", "PermissionError"],
)
```

---

## 8. Simulator UI Features

When the simulator is running at `http://localhost:3000`, you can:

| Feature | Where to find |
|---------|--------------|
| Live agent network graph | Dashboard → Network view |
| Execution trace replay | Dashboard → Replay → select instance |
| Real-time metrics | Dashboard → Metrics tab |
| Stress test | Dashboard → Experiments → Stress test |
| Export trace as JSON | Dashboard → Replay → Export |
| KPI dashboard | Dashboard → KPIs |

---

## Related Notebooks

- `notebooks/01_quickstart.ipynb` — first runtime connection
- `notebooks/08_observability.ipynb` — tracing and metrics
- `notebooks/09_mmm_autopilot.ipynb` — autonomous workflow with Temporal
