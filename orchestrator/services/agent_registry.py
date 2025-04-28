"""
Agent Registry for Multigen Orchestrator

Provides dynamic registration and retrieval of agent implementations.
"""
from typing import Type, Dict

from agents.base_agent import BaseAgent


# Internal registry mapping agent names to their classes
_registry: Dict[str, Type[BaseAgent]] = {}


class AgentRegistryError(Exception):
    """
    Raised when agent registration or lookup fails.
    """
    pass


def register_agent(name: str):  # type: ignore
    """
    Decorator to register a BaseAgent subclass under a unique name.

    Usage:
        @register_agent("EchoAgent")
        class EchoAgent(BaseAgent): ...
    """
    def decorator(cls: Type[BaseAgent]) -> Type[BaseAgent]:
        if name in _registry:
            raise AgentRegistryError(f"Agent '{name}' is already registered")
        if not issubclass(cls, BaseAgent):
            raise AgentRegistryError(
                f"Can only register subclasses of BaseAgent, got {cls.__name__}"
            )
        _registry[name] = cls
        return cls

    return decorator


def get_agent(name: str) -> BaseAgent:
    """
    Retrieve a new instance of the agent class registered under 'name'.

    Raises AgentRegistryError if no such agent is registered.
    """
    cls = _registry.get(name)
    if cls is None:
        available = ', '.join(_registry.keys()) or 'none'
        raise AgentRegistryError(
            f"No agent registered under name '{name}'. Available: {available}"
        )
    return cls()


def list_agents() -> Dict[str, Type[BaseAgent]]:
    """
    Return the mapping of registered agent names to their classes.
    """
    return dict(_registry)
