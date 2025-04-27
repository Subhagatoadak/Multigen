from fastapi import APIRouter, HTTPException
from orchestrator.models.workflow import RunRequest, RunResponse
from orchestrator.services.dsl_parser import parse_workflow

router = APIRouter()

@router.post("/run", response_model=RunResponse)
def run_workflow(req: RunRequest):
    """
    Start a new workflow execution.
    """
    try:
        workflow_graph = parse_workflow(req.dsl)
        # TODO: dispatch to Flow Engine / emit event
        instance_id = "lambda-generated-id"
        return RunResponse(instance_id=instance_id)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))