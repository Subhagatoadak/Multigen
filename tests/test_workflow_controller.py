import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.controller.workflow import router, _serialize_steps
from orchestrator.services.dsl_parser import DSLParseError
from confluent_kafka import KafkaError

class DummyStep:
    def __init__(self, name, params=None, parallel=None, agent=None):
        self.name = name
        self.params = params or {}
        self.parallel = parallel or []
        self.agent = agent
        self.conditional = None
        self.dynamic_subtree = None
        self.loop = None
        self.graph = None

@pytest.fixture
def app_and_client(monkeypatch):
    # Fake parse_workflow
    def fake_parse(dsl):
        return [DummyStep('A'), DummyStep('B')]
    monkeypatch.setattr(
        'orchestrator.controller.workflow.parse_workflow', fake_parse
    )
    # Fake KafkaClient
    class FakeKafka:
        def __init__(self, broker): pass
        def publish(self, topic, msg): self.published = (topic, msg)
    monkeypatch.setattr('orchestrator.controller.workflow.KafkaClient', FakeKafka)

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_serialize_steps_sequential():
    steps = [DummyStep('s1', {'a': 1}), DummyStep('s2')]
    out = _serialize_steps(steps)
    assert out == [
        {'type': 'sequential', 'name': 's1', 'agent': None, 'params': {'a': 1}},
        {'type': 'sequential', 'name': 's2', 'agent': None, 'params': {}},
    ]


def test_serialize_steps_parallel():
    p1 = DummyStep('p1', {'x': 1})
    p2 = DummyStep('p2')
    parent = DummyStep('parent', parallel=[p1, p2])
    out = _serialize_steps([parent])
    assert out == [
        {
            'type': 'parallel',
            'name': 'parent',
            'parallel_with': [
                {'name': 'p1', 'agent': None, 'params': {'x': 1}},
                {'name': 'p2', 'agent': None, 'params': {}},
            ],
        }
    ]


def test_run_workflow_success(app_and_client):
    client = app_and_client
    resp = client.post('/run', json={'dsl': {'steps': []}, 'payload': {}})
    assert resp.status_code == 200
    data = resp.json()
    assert 'instance_id' in data


def test_run_workflow_parse_error(monkeypatch):
    def bp(dsl): raise DSLParseError('bad', node=None)
    monkeypatch.setattr('orchestrator.controller.workflow.parse_workflow', bp)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    resp = client.post('/run', json={'dsl': {}})
    assert resp.status_code == 400


def test_run_workflow_kafka_error(monkeypatch):
    def fake_parse(dsl): return [DummyStep('A')]
    monkeypatch.setattr('orchestrator.controller.workflow.parse_workflow', fake_parse)

    class FK: # Fake Kafka Client
        def __init__(self, b): pass
        # Corrected publish method:
        def publish(self, t, m):
            # Raise KafkaError with an integer code and optional message
            raise KafkaError(KafkaError._FAIL, 'Mock Kafka publish failure')

    monkeypatch.setattr('orchestrator.controller.workflow.KafkaClient', FK)
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    resp = client.post('/run', json={'dsl': {'steps': []}})
    assert resp.status_code == 500 # Should still expect 500 for internal server error
