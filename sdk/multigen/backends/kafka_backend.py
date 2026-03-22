"""
Optional Kafka backend for Multigen event bus.

Install:  pip install aiokafka

Replaces InMemoryBus with Kafka topics for distributed, persistent messaging.
This enables multi-process, multi-host agent networks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class KafkaBackend:
    """Kafka-backed event bus for distributed agent networks."""

    def __init__(self, bootstrap_servers: str = "localhost:9092") -> None:
        self.bootstrap_servers = bootstrap_servers
        self._producer = None
        self._consumers: Dict[str, Any] = {}

    async def _producer_conn(self) -> Any:
        if self._producer:
            return self._producer
        from aiokafka import AIOKafkaProducer  # type: ignore
        self._producer = AIOKafkaProducer(bootstrap_servers=self.bootstrap_servers)
        await self._producer.start()
        return self._producer

    async def publish(self, topic: str, content: Any, headers: Optional[Dict[str, str]] = None) -> None:
        producer = await self._producer_conn()
        data = json.dumps(content, default=str).encode()
        kafka_headers = [(k, v.encode()) for k, v in (headers or {}).items()]
        await producer.send_and_wait(topic, value=data, headers=kafka_headers)

    async def subscribe(self, topic: str, handler: Callable, group_id: str = "multigen") -> None:
        """Start consuming from a Kafka topic in the background."""
        from aiokafka import AIOKafkaConsumer  # type: ignore
        consumer = AIOKafkaConsumer(topic, bootstrap_servers=self.bootstrap_servers, group_id=group_id)
        self._consumers[topic] = consumer
        await consumer.start()

        async def _loop():
            async for msg in consumer:
                try:
                    content = json.loads(msg.value)
                    await handler(content)
                except Exception as e:
                    logger.error("Kafka handler error on topic %s: %s", topic, e)

        asyncio.create_task(_loop())
        logger.info("Kafka subscription started: %s (group=%s)", topic, group_id)

    async def close(self) -> None:
        if self._producer:
            await self._producer.stop()
        for consumer in self._consumers.values():
            await consumer.stop()
