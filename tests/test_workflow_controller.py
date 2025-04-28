import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from orchestrator.controllers.workflow import router, _serialize_steps
from orchestrator.services.dsl_parser import DSLParseError
from orchestrator.models.workflow import RunRequest, RunResponse
from confluent_kafka import KafkaError

class DummyStep:
    def __init__(self, name, params=None, parallel=None):
        self.name = name
        self.params = params or {}
        self.parallel = parallel or []

@pytest.fixture
def app_and_client(monkeypatch):
    # Fake parse_workflow
    def fake_parse(dsl):
        return [DummyStep('A'), DummyStep('B')]
    monkeypatch.setattr(
        'orchestrator.controllers.workflow.parse_workflow', fake_parse
    )
    # Fake KafkaClient
    class FakeKafka:
        def __init__(self, broker): pass
        def publish(self, topic, msg): self.published = (topic, msg)
    monkeypatch.setattr('orchestrator.controllers.workflow.KafkaClient', FakeKafka)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_serialize_steps_sequential():
    steps = [DummyStep('s1', {'a': 1}), DummyStep('s2')]
    out = _serialize_steps(steps)
    assert out == [
        {'name': 's1', 'params': {'a': 1}},
        {'name': 's2', 'params': {}}
    ]


def test_serialize_steps_parallel():
    p1 = DummyStep('p1', {'x': 1})
    p2 = DummyStep('p2')
    parent = DummyStep('parent', parallel=[p1, p2])
    out = _serialize_steps([parent])
    assert out == [
        {'parallel_with': [
            {'name': 'p1', 'params': {'x': 1}},
            {'name': 'p2', 'params': {}}
        ]}
    ]


def test_run_workflow_success(app_and_client):
    client = app_and_client
    resp = client.post('/run', json={'dsl': {'steps': []}, 'payload': {}})
    assert resp.status_code == 200
    data = resp.json()
    assert 'instance_id' in data


def test_run_workflow_parse_error(monkeypatch):
    def bp(dsl): raise DSLParseError('bad', node=None)
    monkeypatch.setattr('orchestrator.controllers.workflow.parse_workflow', bp)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    resp = client.post('/run', json={'dsl': {}})
    assert resp.status_code == 400


def test_run_workflow_kafka_error(monkeypatch):
    def fake_parse(dsl): return [DummyStep('A')]
    monkeypatch.setattr('orchestrator.controllers.workflow.parse_workflow', fake_parse)
    class FK:
        def __init__(self, b): pass
        def publish(self, t, m): raise KafkaError('fail')
    monkeypatch.setattr('orchestrator.controllers.workflow.KafkaClient', FK)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    resp = client.post('/run', json={'dsl': {'steps': []}})
    assert resp.status_code == 500
