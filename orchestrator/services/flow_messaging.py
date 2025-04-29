# File: orchestrator/services/flow_messaging.py

import asyncio
import logging
import signal
import time
from typing import Dict, Any

from prometheus_client import Counter, Histogram, start_http_server

import orchestrator.services.config as config
from messaging.kafka_client import KafkaClient
import flow_engine.workflows.sequence as seq  # import module to allow monkeypatch

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Metrics
MESSAGE_PROCESSED = Counter(
    'multigen_flow_messages_processed_total', 'Total flow messages processed'
)
MESSAGE_FAILED = Counter(
    'multigen_flow_messages_failed_total', 'Total flow messages failed'
)
WORKFLOW_LATENCY = Histogram(
    'multigen_flow_workflow_latency_seconds', 'Latency of running workflows'
)

# Start Prometheus metrics server
start_http_server(config.METRICS_PORT)

# Shutdown flag
shutdown_flag = False

def _shutdown() -> None:
    """Signal handler sets shutdown flag for graceful exit."""
    global shutdown_flag
    shutdown_flag = True

async def handle_message(kafka: KafkaClient, msg: Dict[str, Any]) -> None:
    """
    Process one Kafka message: run workflow and publish result or error.
    """
    workflow_id = msg.get('workflow_id')
    steps = msg.get('steps', [])
    payload = msg.get('payload', {})
    start = time.time()
    try:
        logger.info(f"Starting workflow {workflow_id} with steps {steps}")
        # This now picks up any monkeypatch on flow_engine.workflows.sequence.run_complex_workflow
        results = await seq.run_complex_workflow(steps, payload, workflow_id)
        latency = time.time() - start
        WORKFLOW_LATENCY.observe(latency)

        response = {
            'workflow_id': workflow_id,
            'status': 'completed',
            'results': results,
        }
        kafka.publish(config.FLOW_RESPONSE_TOPIC, response)
        kafka.commit()
        MESSAGE_PROCESSED.inc()
        logger.info(f"Completed workflow {workflow_id}")
    except Exception as exc:
        logger.exception(f"Workflow {workflow_id} failed")
        MESSAGE_FAILED.inc()
        error_payload = {
            'workflow_id': workflow_id,
            'error': str(exc),
        }
        kafka.publish(config.FLOW_DLQ_TOPIC, error_payload)
        kafka.commit()

async def main_loop() -> None:
    """
    Main event loop: poll Kafka and dispatch workflow tasks.
    """
    kafka = KafkaClient(config.KAFKA_BROKER_URL)
    kafka.subscribe([config.FLOW_REQUEST_TOPIC])
    loop = asyncio.get_running_loop()

    # Register signal handlers for graceful shutdown
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _shutdown)

    logger.info("Flow messaging service started. Listening for requests...")
    tasks = set()
    try:
        while not shutdown_flag:
            msg = kafka.poll(1.0)
            if msg:
                task = asyncio.create_task(handle_message(kafka, msg))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            else:
                await asyncio.sleep(0.1)
    finally:
        logger.info("Shutting down, waiting for outstanding tasks...")
        await asyncio.gather(*tasks, return_exceptions=True)
        kafka.close()
        logger.info("Shutdown complete.")

if __name__ == '__main__':
    asyncio.run(main_loop())
