from fastapi import FastAPI
from orchestrator.controller.workflow import router as workflow_router
from orchestrator.controller.registration import router as cap_router

app = FastAPI(title="Multigen Orchestrator")

# Include workflow routes
app.include_router(workflow_router, prefix="/workflows", tags=["workflows"])
app.include_router(cap_router)

@app.get("/health")
def health_check():
    return {"status": "ok"}
