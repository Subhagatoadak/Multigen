"""
Temporal worker process.

Connects to the Temporal server, registers the ComplexSequenceWorkflow
and agent_activity, then polls the task queue for work.

Run with:
    python -m workers.temporal_worker
"""
import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

import orchestrator.services.config as config
from flow_engine.workflows.sequence import ComplexSequenceWorkflow, agent_activity
from flow_engine.graph.engine import (
    GraphWorkflow,
    create_agent_activity,
    persist_node_state_activity,
    tool_activity,
)

# Import all agent modules so @register_agent decorators fire and
# the registry is populated before any activity tries to call get_agent().

# Core Multigen agents
import agents.echo_agent.echo_agent  # noqa: F401
import agents.spawner_agent.spawner_agent  # noqa: F401
import agents.screening_agents.agents  # noqa: F401

# agentic_codex pattern agents (MinistryOfExperts, Swarm, Debate, Guardrail, etc.)
import agents.pattern_agents.agents  # noqa: F401

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Connecting to Temporal at %s", config.TEMPORAL_SERVER_URL)
    client = await Client.connect(config.TEMPORAL_SERVER_URL)

    worker = Worker(
        client,
        task_queue=config.TEMPORAL_TASK_QUEUE,
        workflows=[ComplexSequenceWorkflow, GraphWorkflow],
        activities=[agent_activity, tool_activity, persist_node_state_activity, create_agent_activity],
    )

    logger.info(
        "Temporal worker started. Task queue: %s", config.TEMPORAL_TASK_QUEUE
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
