from fastapi import FastAPI
from orchestrator.controller.workflow import router as workflow_router
from orchestrator.controller.registration import router as cap_router
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
    version="0.1.0",
    description="Multi-agent orchestration framework",
)

if _OTEL_FASTAPI:
    FastAPIInstrumentor.instrument_app(app)

app.include_router(workflow_router, prefix="/workflows", tags=["workflows"])
app.include_router(cap_router)


@app.get("/health", tags=["ops"])
def health_check():
    return {"status": "ok"}
