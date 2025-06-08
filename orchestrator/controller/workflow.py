# File: orchestrator/controllers/workflow.py

from uuid import uuid4
from fastapi import APIRouter, HTTPException
from confluent_kafka import KafkaError

from orchestrator.models.workflow import RunRequest, RunResponse
from orchestrator.services.dsl_parser import parse_workflow, DSLParseError
from orchestrator.services.capability_directory_client import validate_agent
from orchestrator.services.llm_service import text_to_dsl
from messaging.kafka_client import KafkaClient
import orchestrator.services.config as config

router = APIRouter()

async def _prepare_dsl(req: RunRequest) -> dict:
    """
    If the request includes a DSL dictionary, return it directly.
    Otherwise, if it provides plain-text, use the LLM preprocessor to generate the DSL.
    """
    if getattr(req, 'dsl', None):
        return req.dsl
    if getattr(req, 'text', None):
        try:
            return await text_to_dsl(req.text)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed generating DSL from text: {e}")
    raise HTTPException(
        status_code=400,
        detail="Request must include either a 'dsl' field or a 'text' field."
    )


def _serialize_steps(steps):
    """
    Convert parsed Step objects to message-friendly dicts,
    handling sequential and parallel groups.
    """
    serialized = []
    for step in steps:
        if step.parallel:
            branches = [{'name': p.name, 'params': p.params} for p in step.parallel]
            serialized.append({'parallel_with': branches})
        else:
            serialized.append({'name': step.name, 'params': step.params})
    return serialized


@router.post("/run", response_model=RunResponse)
async def run_workflow(req: RunRequest):
    """
    Start a new workflow execution:
      1. Optional LLM preprocessing of plain-text to DSL
      2. Parse the DSL
      3. Serialize steps
      4. Publish to Kafka
    """
    # 1) Prepare or generate DSL
    dsl = await _prepare_dsl(req)

    # 2) Parse & validate DSL
    try:
        steps = parse_workflow(dsl)
    except DSLParseError as e:
        raise HTTPException(status_code=400, detail=str(e))
    
    # Validate each step’s agent against the capability directory.
    # Some parsed steps (e.g. parallel parents) may not specify an agent and
    # the DSL parser currently does not attach a version attribute.  Guard
    # against missing attributes so dynamic dummy steps used in tests work
    # correctly.
    for step in steps:
        if getattr(step, "agent", None):
            version = getattr(step, "agent_version", None)
            await validate_agent(step.agent, version)

    # 3) Serialize steps (parallel groups supported)
    serialized_steps = _serialize_steps(steps)
    payload = getattr(req, 'payload', {})

    # 4) Dispatch to Kafka
    workflow_id = str(uuid4())
    message = {
        'workflow_id': workflow_id,
        'steps': serialized_steps,
        'payload': payload,
    }

    kafka = KafkaClient(config.KAFKA_BROKER_URL)
    try:
        kafka.publish(config.FLOW_REQUEST_TOPIC, message)
    except KafkaError as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to dispatch workflow to Kafka: {e}"
        )

    return RunResponse(instance_id=workflow_id)
