import json
import logging
import pytest
from confluent_kafka import KafkaError

from messaging.kafka_client import KafkaClient

# Dummy classes to monkeypatch
class DummyProducer:
    def __init__(self, conf):
        self.conf = conf
        self.produced = []
    def produce(self, topic, payload):
        self.produced.append((topic, payload))
    def flush(self):
        pass

class DummyConsumer:
    def __init__(self, conf):
        self.conf = conf
        self.subscribed = []
        self.to_return = None
        self.closed = False
        self.committed = False
    def subscribe(self, topics):
        self.subscribed = topics
    def poll(self, timeout):
        return self.to_return
    def commit(self):
        self.committed = True
    def close(self):
        self.closed = True

class DummyMsg:
    def __init__(self, value, error=None):
        self._value = value
        self._error = error
    def error(self):
        return self._error
    def value(self):
        return self._value

@pytest.fixture(autouse=True)
def kafka_mocks(monkeypatch):
    monkeypatch.setattr('messaging.kafka_client.Producer', DummyProducer)
    monkeypatch.setattr('messaging.kafka_client.Consumer', DummyConsumer)
    return None


def test_publish_and_flush():
    client = KafkaClient('broker1')
    client.publish('topic1', {'a': 1})
    assert client.producer.produced == [('topic1', b'{"a": 1}')]  # JSON encoded


def test_subscribe_and_poll_none():
    client = KafkaClient('broker')
    client.subscribe(['t1', 't2'])
    assert client.consumer.subscribed == ['t1', 't2']
    client.consumer.to_return = None
    assert client.poll(0.1) is None


def test_poll_returns_message():
    msg = DummyMsg(b'{"x":2}')
    client = KafkaClient('broker')
    client.consumer.to_return = msg
    assert client.poll() == {"x": 2}


def test_poll_error(monkeypatch):
    err = KafkaError._PARTITION_EOF
    msg = DummyMsg(b'', error=err)
    client = KafkaClient('broker')
    client.consumer.to_return = msg
    assert client.poll() is None


def test_poll_decode_error(caplog):
    msg = DummyMsg(b'not json')
    client = KafkaClient('broker')
    caplog.set_level(logging.ERROR)
    client.consumer.to_return = msg
    assert client.poll() is None
    assert "Failed to decode Kafka message" in caplog.text


def test_commit_and_close():
    client = KafkaClient('broker')
    client.consumer.committed = False
    client.commit()
    assert client.consumer.committed
    client.close()
    assert client.consumer.closed