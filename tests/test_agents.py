# tests/test_agents.py
import pytest
import logging

from orchestrator.services.agent_registry import (
    get_agent,
    list_agents,
    AgentRegistryError,
)
from agents.base_agent import BaseAgent
from agents.echo_agent.echo_agent import EchoAgent


@pytest.fixture(autouse=True)
def import_echo_agent():
    # Ensure EchoAgent class is registered
    import agents.echo_agent.echo_agent  # noqa: F401
    yield


def test_list_agents_contains_echo():
    agents = list_agents()
    assert 'EchoAgent' in agents


def test_get_agent_success():
    inst = get_agent('EchoAgent')
    assert isinstance(inst, EchoAgent)


def test_get_agent_unknown():
    with pytest.raises(AgentRegistryError):
        get_agent('NoSuchAgent')


@pytest.mark.asyncio
async def test_echo_agent_run_and_call(caplog):
    inst = get_agent('EchoAgent')
    params = {'k': 'v'}
    caplog.set_level(logging.INFO)

    # Test call, which wraps run
    result = await inst(params)
    assert result == {'echo': params}

    # Ensure logs contain start and success messages
    assert f"Starting agent with params: {params}" in caplog.text
    assert "Agent completed successfully" in caplog.text


@pytest.mark.asyncio
async def test_base_agent_default_run_and_call(caplog):
    base = BaseAgent()
    params = {'a': 123}

    # Default run logs a warning and echoes
    caplog.set_level(logging.WARNING)
    run_res = await base.run(params)
    assert run_res == {'echo': params}
    assert "no implementation provided" in caplog.text

    # __call__ logs info and success
    caplog.clear()
    caplog.set_level(logging.INFO)
    call_res = await base(params)
    assert call_res == {'echo': params}
    assert "Starting agent with params:" in caplog.text
    assert "Agent completed successfully" in caplog.text
