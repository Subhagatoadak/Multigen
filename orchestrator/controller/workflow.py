from uuid import uuid4
from fastapi import APIRouter, HTTPException
from confluent_kafka import KafkaError
from orchestrator.models.workflow import RunRequest, RunResponse
from orchestrator.services.dsl_parser import parse_workflow, DSLParseError
from messaging.kafka_client import KafkaClient
import orchestrator.services.config as config

router = APIRouter()


def _serialize_steps(steps):
    """
    Convert parsed Step objects to message-friendly dicts,
    handling sequential and parallel groups.
    """
    serialized = []
    for step in steps:
        if step.parallel:
            # group parallel branches
            branches = [ { 'name': p.name, 'params': p.params } for p in step.parallel ]
            serialized.append({ 'parallel_with': branches })
        else:
            serialized.append({ 'name': step.name, 'params': step.params })
    return serialized


@router.post("/run", response_model=RunResponse)
def run_workflow(req: RunRequest):
    """
    Start a new workflow execution:
      1. Parse the DSL
      2. Serialize into step list
      3. Publish to Kafka
    """
    # 1) Parse & validate the DSL
    try:
        steps = parse_workflow(req.dsl)
    except DSLParseError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 2) Serialize steps (supports parallel groups)
    serialized_steps = _serialize_steps(steps)

    # Optional extra payload, default empty dict
    payload = getattr(req, 'payload', {})

    # 3) Build and send message
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
