import os
from typing import List

# Kafka configuration\ nKAFKA_BROKER_URL = os.getenv("KAFKA_BROKER_URL", "localhost:9092")
FLOW_REQUEST_TOPIC = os.getenv("FLOW_REQUEST_TOPIC", "flow-requests")
FLOW_RESPONSE_TOPIC = os.getenv("FLOW_RESPONSE_TOPIC", "flow-responses")
FLOW_DLQ_TOPIC = os.getenv("FLOW_DLQ_TOPIC", "flow-dead-letter")

# Temporal configuration
TEMPORAL_SERVER_URL = os.getenv("TEMPORAL_SERVER_URL", "localhost:7233")
TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "flow-task-queue")

# Metrics HTTP server port
METRICS_PORT = int(os.getenv("METRICS_PORT", "8000"))
KAFKA_BROKER_URL = os.getenv("KAFKA_BROKER_URL", "localhost:9092")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY","XXX")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")