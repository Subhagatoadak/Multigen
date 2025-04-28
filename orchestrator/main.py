from fastapi import FastAPI
from orchestrator.controllers.workflow import router as workflow_router

app = FastAPI(title="Multigen Orchestrator")

# Include workflow routes
app.include_router(workflow_router, prefix="/workflows", tags=["workflows"])

@app.get("/health")
def health_check():
    return {"status": "ok"}