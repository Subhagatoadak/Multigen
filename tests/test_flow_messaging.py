import pytest
import asyncio
from orchestrator.services.flow_messaging import handle_message, MESSAGE_PROCESSED, MESSAGE_FAILED
from flow_engine.workflows.sequence import run_complex_workflow
import orchestrator.services.config as config

class FakeKafka:
    def __init__(self):
        self.published = []
        self.committed = False
    def publish(self, topic, msg):
        self.published.append((topic, msg))
    def commit(self):
        self.committed = True

@pytest.fixture(autouse=True)
def reset_metrics():
    MESSAGE_PROCESSED._value.set(0)
    MESSAGE_FAILED._value.set(0)
    yield
    MESSAGE_PROCESSED._value.set(0)
    MESSAGE_FAILED._value.set(0)

@pytest.mark.asyncio
async def test_handle_message_success(monkeypatch):
    fake_kafka = FakeKafka()
    msg = {'workflow_id': 'id', 'steps': [], 'payload': {}}
    async def fake_run(steps, payload, wid): return [{'agent': 'a', 'output': {}}]
    monkeypatch.setattr('flow_engine.workflows.sequence.run_complex_workflow', fake_run)

    await handle_message(fake_kafka, msg)
    assert fake_kafka.published[0][0] == config.FLOW_RESPONSE_TOPIC
    assert fake_kafka.committed
    assert MESSAGE_PROCESSED._value.get() == 1

@pytest.mark.asyncio
async def test_handle_message_failure(monkeypatch):
    fake_kafka = FakeKafka()
    msg = {'workflow_id': 'id2', 'steps': [], 'payload': {}}
    async def fake_run(steps, payload, wid): raise Exception('oops')
    monkeypatch.setattr('flow_engine.workflows.sequence.run_complex_workflow', fake_run)

    await handle_message(fake_kafka, msg)
    assert fake_kafka.published[0][0] == config.FLOW_DLQ_TOPIC
    assert fake_kafka.committed
    assert MESSAGE_FAILED._value.get() == 1
