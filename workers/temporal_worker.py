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
from temporalio.worker.workflow_sandbox import SandboxedWorkflowRunner, SandboxRestrictions

import orchestrator.services.config as config
from flow_engine.workflows.sequence import ComplexSequenceWorkflow, agent_activity
from flow_engine.graph.engine import (
    GraphWorkflow,
    create_agent_activity,
    deregister_agents_activity,
    generate_agent_spec_activity,
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

# M&A due diligence agents
try:
    import examples.ma_due_diligence.agents.agents  # noqa: F401
except ImportError:
    pass  # Optional example — not required for core operation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main() -> None:
    logger.info("Connecting to Temporal at %s", config.TEMPORAL_SERVER_URL)
    client = await Client.connect(config.TEMPORAL_SERVER_URL)

    # prometheus_client / opentelemetry use stdlib I/O at module-load time,
    # which Temporal's determinism sandbox blocks.  flow_engine.graph.telemetry
    # registers Prometheus Counters at import time; passing it through prevents
    # duplicate-metric errors when the sandbox re-imports it against the host registry.
    sandbox = SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules(
            "prometheus_client",
            "opentelemetry",
            "flow_engine.graph.telemetry",
            # Agent registry must be shared with the worker process so that
            # is_agent_registered() inside the workflow sandbox sees the same
            # populated _registry dict that was built at worker startup.
            # Without this, every agent (including EchoAgent) looks unregistered
            # inside the sandbox and incorrectly triggers the approval gate.
            "orchestrator.services.agent_registry",
        )
    )

    worker = Worker(
        client,
        task_queue=config.TEMPORAL_TASK_QUEUE,
        workflows=[ComplexSequenceWorkflow, GraphWorkflow],
        activities=[
            agent_activity,
            tool_activity,
            persist_node_state_activity,
            create_agent_activity,
            generate_agent_spec_activity,
            deregister_agents_activity,
        ],
        workflow_runner=sandbox,
    )

    logger.info(
        "Temporal worker started. Task queue: %s", config.TEMPORAL_TASK_QUEUE
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
