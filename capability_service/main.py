from fastapi import FastAPI
from orchestrator.controllers.registration import router as reg_router

app = FastAPI(
    title="Capability Directory",
    version="1.0.0",
    docs_url="/docs",
    openapi_url="/openapi.json",
)

# Mount the /capabilities endpoints
app.include_router(reg_router)
