# Docker Compose Deployment

## Overview

The `docker-compose.yml` in the project root starts the complete Multigen stack:

| Service | Image | Port | Description |
|---------|-------|------|-------------|
| `orchestrator` | Local build | `8009` | FastAPI orchestrator + workflow engine |
| `simulator-frontend` | Local build | `3000` | React dashboard |
| `temporal` | `temporalio/auto-setup` | `7233`, `8080` | Workflow engine |
| `prometheus` | `prom/prometheus` | `9090` | Metrics (optional profile) |

---

## Starting the Stack

```bash
# Start all services
docker compose up

# Start in detached mode
docker compose up -d

# Start with build (after code changes)
docker compose up --build

# Start only core services (no observability)
docker compose up orchestrator simulator-frontend

# View logs
docker compose logs -f orchestrator
docker compose logs -f simulator-frontend
```

---

## Health Checks

```bash
# Orchestrator health
curl http://localhost:8009/ping
# Expected: {"status": "ok", "version": "..."}

# List registered agents
curl http://localhost:8009/api/v1/agents
# Expected: JSON array of agent definitions

# Temporal Web UI
open http://localhost:8080
```

---

## Building Production Images

```bash
# Build orchestrator image
docker build -f Dockerfile.orchestrator -t multigen-orchestrator:latest .

# Build capability service image
docker build -f Dockerfile.capability -t multigen-capability:latest .

# Tag for registry
docker tag multigen-orchestrator:latest your-registry.io/multigen-orchestrator:v1.0.0
docker push your-registry.io/multigen-orchestrator:v1.0.0
```

---

## Scaling

```bash
# Scale orchestrator workers
docker compose up -d --scale orchestrator=3

# Scale with specific memory limits
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
```

**`docker-compose.prod.yml` example:**

```yaml
services:
  orchestrator:
    deploy:
      resources:
        limits:
          memory: 2G
          cpus: '1.5'
        reservations:
          memory: 512M
      restart_policy:
        condition: on-failure
        max_attempts: 3
```

---

## Environment Configuration

Pass secrets via environment file:

```bash
# Create production env file
cat > .env.prod << 'EOF'
OPENAI_API_KEY=sk-...
TEMPORAL_HOST=temporal.internal
TEMPORAL_NAMESPACE=production
LOG_LEVEL=INFO
PROMETHEUS_ENABLED=true
EOF

# Start with production env
docker compose --env-file .env.prod up -d
```

---

## Persistent Storage

The default `docker-compose.yml` uses Docker volumes for Temporal data. To use named volumes for production:

```yaml
volumes:
  temporal-db:
    driver: local
    driver_opts:
      type: none
      device: /data/temporal
      o: bind
```

---

## Stopping and Cleanup

```bash
# Stop all services (preserve data)
docker compose down

# Stop and remove all data volumes
docker compose down -v

# Remove all Multigen images
docker images | grep multigen | awk '{print $3}' | xargs docker rmi
```
