import pytest
import asyncio
from datetime import timedelta
import temporalio.workflow as workflow_module

from flow_engine.workflows.sequence import (
    ComplexSequenceWorkflow,
    agent_activity,
    run_complex_workflow,
)

# A dummy agent class to simulate actual agent implementations
class DummyAgent:
    def __init__(self):
        self.calls = []

    async def run(self, params: dict):
        self.calls.append(params)
        return {"result": params}

@pytest.fixture(autouse=True)
def patch_agent_registry(monkeypatch):
    dummy = DummyAgent()
    # Patch get_agent to always return our DummyAgent
    monkeypatch.setattr(
        'flow_engine.workflows.sequence.get_agent',
        lambda name: dummy,
    )
    # Also insert into _registry so the pre-check in agent_activity passes
    # without attempting a MongoDB lookup.
    import orchestrator.services.agent_registry as _reg_mod
    monkeypatch.setitem(_reg_mod._registry, 'TestAgent', dummy)
    return dummy

@pytest.fixture(autouse=True)
def patch_execute_activity(monkeypatch):
    # workflow.execute_activity is called as:
    #   workflow.execute_activity(fn, args=[name, params], **temporal_kwargs)
    # Unwrap args= and call fn directly.
    async def fake_execute_activity(fn, *pos, **kwargs):
        actual_args = kwargs.pop('args', pos)
        return await fn(*actual_args)
    monkeypatch.setattr(
        'flow_engine.workflows.sequence.workflow.execute_activity',
        fake_execute_activity,
    )

@pytest.mark.asyncio
async def test_agent_activity_success(patch_agent_registry):
    # Ensure agent_activity calls DummyAgent.run and returns correct structure
    params = {"key": "value"}
    result = await agent_activity("TestAgent", params)
    assert result == {"agent": "TestAgent", "output": {"result": params}}
    assert patch_agent_registry.calls == [params]

@pytest.mark.asyncio
async def test_complex_sequence_sequential(patch_agent_registry):
    # Two sequential steps should execute in order
    steps = [
        {"name": "A", "params": {"x": 1}},
        {"name": "B"}  # uses payload fallback
    ]
    payload = {"fallback": True}
    wf = ComplexSequenceWorkflow()
    results = await wf.run(steps, payload)
    assert results == [
        {"agent": "A", "output": {"result": {"x": 1}}},
        {"agent": "B", "output": {"result": payload}}
    ]

@pytest.mark.asyncio
async def test_complex_sequence_parallel(patch_agent_registry):
    # A parallel group defined by 'parallel_with'
    group = [
        {"name": "X"},
        {"name": "Y", "params": {"y": 2}}
    ]
    steps = [{"parallel_with": group}]
    wf = ComplexSequenceWorkflow()
    results = await wf.run(steps, {})
    # Order is maintained
    assert results == [
        {"agent": "X", "output": {"result": {}}},
        {"agent": "Y", "output": {"result": {"y": 2}}}
    ]

@pytest.mark.asyncio
async def test_complex_sequence_error_aggregation(monkeypatch):
    # Make one activity raise to trigger error collection
    async def fake_execute(fn, *pos, **kwargs):
        actual_args = kwargs.pop('args', pos)
        name = actual_args[0]
        params = actual_args[1] if len(actual_args) > 1 else {}
        if name == "bad":
            raise RuntimeError("fail")
        return {"agent": name, "output": params}
    monkeypatch.setattr(
        'flow_engine.workflows.sequence.workflow.execute_activity',
        fake_execute
    )

    steps = [
        {"name": "good"},
        {"name": "bad"}
    ]
    wf = ComplexSequenceWorkflow()
    with pytest.raises(workflow_module.WorkflowError) as excinfo:
        await wf.run(steps, {})
    msg = str(excinfo.value)
    assert "bad: fail" in msg
    assert "good" not in msg

@pytest.mark.asyncio
async def test_run_complex_workflow_client(monkeypatch):
    # Stub out the Temporal client connection and workflow handle
    class StubHandle:
        def __init__(self, result):
            self._result = result
        async def result(self):
            return self._result

    class StubClient:
        async def start_workflow(self, method, *args, **kwargs):
            return StubHandle([{"agent": "Z", "output": {}}])

    async def fake_connect(url):
        return StubClient()

    monkeypatch.setattr(
        'flow_engine.workflows.sequence.Client.connect',
        fake_connect
    )

    out = await run_complex_workflow(
        [{"name": "Z"}], {"foo": "bar"}, "wid"
    )
    assert out == [{"agent": "Z", "output": {}}]