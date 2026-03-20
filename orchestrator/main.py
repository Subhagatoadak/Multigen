import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from orchestrator.controller.workflow import router as workflow_router
from orchestrator.controller.registration import router as cap_router
from orchestrator.controller.graph import router as graph_router
from orchestrator.services.agent_registry import list_agents
from orchestrator.telemetry import setup_tracing

# Initialise tracing before the app starts receiving requests
setup_tracing()

try:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    _OTEL_FASTAPI = True
except ImportError:
    _OTEL_FASTAPI = False

app = FastAPI(
    title="Multigen Orchestrator",
    version="0.2.0",
    description="Multi-agent orchestration framework — enterprise edition",
)

# ── Middleware ────────────────────────────────────────────────────────────────

_allowed_origins = os.getenv("ALLOWED_ORIGINS", "*").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)

if _OTEL_FASTAPI:
    FastAPIInstrumentor.instrument_app(app)

# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(workflow_router, prefix="/workflows", tags=["workflows"])
app.include_router(graph_router, prefix="/workflows", tags=["graph"])
app.include_router(cap_router)


# ── Built-in endpoints ────────────────────────────────────────────────────────

@app.get("/health", tags=["ops"])
def health_check():
    return {"status": "ok", "version": app.version}


@app.get("/agents", tags=["ops"])
def list_registered_agents():
    """Return the names and class paths of all agents in the registry."""
    return {
        "agents": [
            {"name": name, "class": cls.__name__}
            for name, cls in list_agents().items()
        ]
    }
