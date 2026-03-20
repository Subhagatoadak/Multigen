from uuid import uuid4
from fastapi import APIRouter, HTTPException
from confluent_kafka import KafkaError
from opentelemetry import trace

from orchestrator.models.workflow import RunRequest, RunResponse
from orchestrator.services.dsl_parser import parse_workflow, DSLParseError
from orchestrator.services.capability_directory_client import validate_agent
from orchestrator.services.llm_service import text_to_dsl
from messaging.kafka_client import KafkaClient
import orchestrator.services.config as config

router = APIRouter()
_tracer = trace.get_tracer(__name__)


async def _prepare_dsl(req: RunRequest) -> dict:
    """
    If the request includes a DSL dictionary, return it directly.
    Otherwise, if it provides plain-text, use the LLM preprocessor to generate the DSL.
    """
    if getattr(req, "dsl", None):
        return req.dsl
    if getattr(req, "text", None):
        try:
            return await text_to_dsl(req.text)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed generating DSL from text: {e}")
    raise HTTPException(
        status_code=400,
        detail="Request must include either a 'dsl' field or a 'text' field.",
    )


def _serialize_steps(steps):
    """
    Convert parsed Step objects to message-friendly dicts, preserving
    sequential, parallel, conditional, and dynamic_subtree semantics.
    """
    serialized = []
    for step in steps:
        if step.parallel:
            branches = [
                {"name": p.name, "agent": p.agent, "params": p.params}
                for p in step.parallel
            ]
            serialized.append({
                "type": "parallel",
                "name": step.name,
                "parallel_with": branches,
            })
        elif step.conditional:
            branches = [
                {
                    "condition": b.condition,
                    "name": b.step.name,
                    "agent": b.step.agent,
                    "params": b.step.params,
                }
                for b in step.conditional
            ]
            serialized.append({
                "type": "conditional",
                "name": step.name,
                "branches": branches,
            })
        elif step.dynamic_subtree:
            serialized.append({
                "type": "dynamic",
                "name": step.name,
                "agent": step.agent,
                "params": step.params,
                "subtree_config": step.dynamic_subtree,
            })
        elif step.loop:
            serialized.append({
                "type": "loop",
                "name": step.name,
                "loop": step.loop,
            })
        elif step.graph:
            serialized.append({
                "type": "graph",
                "name": step.name,
                "graph": step.graph,
            })
        else:
            serialized.append({
                "type": "sequential",
                "name": step.name,
                "agent": step.agent,
                "params": step.params,
            })
    return serialized


@router.post("/run", response_model=RunResponse)
async def run_workflow(req: RunRequest):
    """
    Start a new workflow execution:
      1. Optional LLM preprocessing of plain-text to DSL
      2. Parse the DSL
      3. Validate agents against capability directory
      4. Serialize steps
      5. Publish to Kafka
    """
    workflow_id = str(uuid4())

    with _tracer.start_as_current_span("workflow.run") as span:
        span.set_attribute("workflow.id", workflow_id)

        # 1) Prepare or generate DSL
        with _tracer.start_as_current_span("workflow.prepare_dsl"):
            dsl = await _prepare_dsl(req)

        # 2) Parse & validate DSL
        with _tracer.start_as_current_span("workflow.parse_dsl"):
            try:
                steps = parse_workflow(dsl)
            except DSLParseError as e:
                span.record_exception(e)
                raise HTTPException(status_code=400, detail=str(e))

        span.set_attribute("workflow.step_count", len(steps))

        # 3) Validate agents against capability directory
        with _tracer.start_as_current_span("workflow.validate_agents"):
            for step in steps:
                if getattr(step, "agent", None):
                    version = getattr(step, "agent_version", None)
                    await validate_agent(step.agent, version)

        # 4) Serialize steps
        serialized_steps = _serialize_steps(steps)
        payload = getattr(req, "payload", {})

        # 5) Dispatch to Kafka
        message = {
            "workflow_id": workflow_id,
            "steps": serialized_steps,
            "payload": payload,
        }

        with _tracer.start_as_current_span("workflow.kafka_publish"):
            kafka = KafkaClient(config.KAFKA_BROKER_URL)
            try:
                kafka.publish(config.FLOW_REQUEST_TOPIC, message)
            except KafkaError as e:
                span.record_exception(e)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to dispatch workflow to Kafka: {e}",
                )

    return RunResponse(instance_id=workflow_id)
