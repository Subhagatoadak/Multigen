# Observability

## Overview

Multigen ships with three layers of observability:

1. **Structured Logging** — `StructuredLogger` emits JSON logs with consistent field names
2. **Metrics** — Prometheus counters, histograms, and gauges via `record_counter` / `record_latency`
3. **Distributed Tracing** — OpenTelemetry spans emitted by all coordinators, exportable to Jaeger/Tempo

---

## Structured Logging

```python
from agentic_codex.core.observability.logger import StructuredLogger

logger = StructuredLogger(name="my-pipeline")

def my_step(ctx):
    logger.info("step_started",
                agent="my-agent",
                goal=ctx.goal,
                iteration=ctx.scratch.get("iteration", 0))

    # ... do work ...

    logger.info("step_completed",
                agent="my-agent",
                duration_ms=150,
                confidence=0.87)
```

Example JSON log output:
```json
{
  "timestamp": "2026-03-22T14:30:00Z",
  "level": "INFO",
  "logger": "my-pipeline",
  "event": "step_started",
  "agent": "my-agent",
  "goal": "analyse quarterly report",
  "iteration": 2
}
```

---

## Prometheus Metrics

```python
from agentic_codex.core.observability.metrics import record_counter, record_latency

# Increment a counter
record_counter("agent.step.completed", tags={"agent": "AnalysisAgent", "pipeline": "credit_risk"})
record_counter("agent.step.failed",    tags={"agent": "BureauAgent", "error_type": "timeout"})

# Record a latency histogram
record_latency("agent.step.latency_ms", 342.5, tags={"agent": "AnalysisAgent"})
```

### Starting Prometheus

```bash
# Start with observability profile
docker compose --profile observability up
```

Or manually:

```bash
docker run -d \
  -p 9090:9090 \
  -v ./prometheus.yml:/etc/prometheus/prometheus.yml \
  prom/prometheus
```

**`prometheus.yml`:**

```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'multigen-orchestrator'
    static_configs:
      - targets: ['localhost:8009']
    metrics_path: '/metrics'
```

---

## OpenTelemetry Tracing

```python
from agentic_codex.core.observability.otel import setup_otel

# Configure OTLP exporter (Jaeger, Tempo, Zipkin, etc.)
setup_otel(
    service_name="multigen-pipeline",
    otlp_endpoint="http://localhost:4318",   # gRPC endpoint
)
```

### Viewing Traces

**Jaeger** (easiest for development):

```bash
docker run -d \
  -p 16686:16686 \
  -p 4318:4318 \
  jaegertracing/all-in-one:latest

open http://localhost:16686
```

**Grafana Tempo** (production):

```bash
# Add to docker-compose.yml:
# tempo:
#   image: grafana/tempo:latest
#   ports:
#     - "4318:4318"
#     - "3200:3200"
```

---

## Agentic Simulator Event Ingestion

The Agentic Simulator ingests real-time events from your pipeline runs. Push events from your code:

```python
from multigen import SyncMultigenClient

client = SyncMultigenClient(base_url="http://localhost:8009")

# Push pipeline events for real-time visualisation
for step_name, agent_name, duration_ms in pipeline_events:
    client.push_event(
        instance_id=instance_id,
        event_type=f"agent.{step_name}.completed",
        data={
            "agent": agent_name,
            "duration_ms": duration_ms,
            "timestamp": "2026-03-22T14:30:00Z",
        }
    )
```

Events appear in real time in the Simulator dashboard at `http://localhost:3000`.

---

## RunStore (Execution History)

`RunStore` persists agent run history for replay and debugging:

```python
from agentic_codex import RunStore

store = RunStore(backend="sqlite", path="./runs.db")

# Runs are automatically recorded when you use the orchestrator
# or manually via:
run_id = store.start_run(agent="my-agent", goal="analyse")
store.record_step(run_id, step_result)
store.finish_run(run_id, status="completed")

# Query past runs
runs = store.list_runs(agent="my-agent", limit=10)
```

---

## Observability Checklist

- [ ] `StructuredLogger` attached to each pipeline step function
- [ ] `record_counter` called on step completion + failure
- [ ] `record_latency` called with actual wall-clock timing
- [ ] OTLP endpoint configured in production
- [ ] Prometheus scrape config includes the orchestrator endpoint
- [ ] Dashboard in Grafana / Simulator shows all key pipelines
- [ ] Alert rules configured for: error rate > 5%, latency P99 > 5s, circuit breaker OPEN
