import json
import logging
from typing import Any, Dict, List, Optional

from confluent_kafka import Consumer, KafkaError, Producer

import orchestrator.services.config as config

logger = logging.getLogger(__name__)


class KafkaClient:
    """
    Thin wrapper around confluent_kafka Producer and Consumer for JSON messages.
    """
    def __init__(self, brokers: str) -> None:
        common_conf: Dict[str, Any] = {
            'bootstrap.servers': brokers,
        }
        self.producer = Producer(common_conf)

        consumer_conf: Dict[str, Any] = {
            **common_conf,
            'group.id': 'multigen-flow',
            'auto.offset.reset': 'earliest',
            'enable.auto.commit': False,
        }
        self.consumer = Consumer(consumer_conf)

    def publish(self, topic: str, message: Dict[str, Any]) -> None:
        payload = json.dumps(message).encode('utf-8')
        self.producer.produce(topic, payload)
        self.producer.flush()

    def subscribe(self, topics: List[str]) -> None:
        self.consumer.subscribe(topics)

    def poll(self, timeout: float = 1.0) -> Optional[Dict[str, Any]]:
        msg = self.consumer.poll(timeout)
        if msg is None:
            return None
        err = msg.error()
        if err:
            # Handle native KafkaError objects and integer error codes
            code = err.code() if hasattr(err, 'code') else err
            if code != KafkaError._PARTITION_EOF:
                logger.error(f"Kafka error: {err}")
            return None
        try:
            return json.loads(msg.value().decode('utf-8'))
        except Exception:
            logger.exception("Failed to decode Kafka message")
            return None

    def commit(self) -> None:
        try:
            self.consumer.commit()
        except Exception:
            logger.exception("Failed to commit Kafka offset")

    def close(self) -> None:
        self.consumer.close()
