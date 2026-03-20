
from typing import Type, Dict

from agents.base_agent import BaseAgent


class AgentRegistryError(Exception):
    """
    Raised when agent registration or lookup fails.

    Attributes:
        message: Explanation of the failure.
        name: The agent name involved in the failure.
    """
    def __init__(self, message: str, name: str = None) -> None:
        self.name = name
        full_message = message
        if name:
            full_message = f"AgentRegistryError[{name}]: {message}"
        super().__init__(full_message)


# Internal registry mapping agent names to their classes
_registry: Dict[str, Type[BaseAgent]] = {}


def register_agent(name: str):
    """
    Decorator to register a BaseAgent subclass under a unique name.

    Usage:
        @register_agent("EchoAgent")
        class EchoAgent(BaseAgent): ...
    """
    def decorator(cls: Type[BaseAgent]) -> Type[BaseAgent]:
        if name in _registry:
            # Idempotent: allow re-registration of the same class (e.g. module
            # imported under two different sys.path roots).  Raise only on
            # genuine name collision between different classes.
            if _registry[name] is not cls and _registry[name].__qualname__ != cls.__qualname__:
                raise AgentRegistryError(
                    f"Agent '{name}' is already registered by a different class "
                    f"({_registry[name].__name__})", name
                )
            return cls
        if not issubclass(cls, BaseAgent):
            raise AgentRegistryError(
                f"Can only register subclasses of BaseAgent, got {cls.__name__}", name
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
        available = ', '.join(sorted(_registry.keys())) or 'none'
        raise AgentRegistryError(
            f"No agent registered under name '{name}'. Available: {available}", name
        )
    return cls()


def list_agents() -> Dict[str, Type[BaseAgent]]:
    """
    Return the mapping of registered agent names to their classes.
    """
    return dict(_registry)
