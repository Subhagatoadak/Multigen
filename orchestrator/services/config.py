import os
import logging

# Load .env file if present (no-op if absent)
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

# Kafka configuration
FLOW_REQUEST_TOPIC = os.getenv("FLOW_REQUEST_TOPIC", "flow-requests")
FLOW_RESPONSE_TOPIC = os.getenv("FLOW_RESPONSE_TOPIC", "flow-responses")
FLOW_DLQ_TOPIC = os.getenv("FLOW_DLQ_TOPIC", "flow-dead-letter")
KAFKA_BROKER_URL = os.getenv("KAFKA_BROKER_URL", "localhost:9092")

# Temporal configuration
TEMPORAL_SERVER_URL = os.getenv("TEMPORAL_SERVER_URL", "localhost:7233")
TEMPORAL_TASK_QUEUE = os.getenv("TEMPORAL_TASK_QUEUE", "flow-task-queue")

# Metrics HTTP server port
METRICS_PORT = int(os.getenv("METRICS_PORT", "8000"))

# OpenAI configuration — OPENAI_API_KEY must be set when using LLM features
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
if not OPENAI_API_KEY:
    logger.warning(
        "OPENAI_API_KEY is not set. LLM text-to-DSL preprocessing will be unavailable."
    )
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o")

# MongoDB configuration
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
CAPABILITY_DB_NAME = os.getenv("CAPABILITY_DB_NAME", "multigen")
CAPABILITY_COLLECTION_NAME = os.getenv("CAPABILITY_COLLECTION_NAME", "capabilities")

# Capability Directory HTTP API
CAPABILITY_SERVICE_URL = os.getenv("CAPABILITY_SERVICE_URL", "http://localhost:8000")

