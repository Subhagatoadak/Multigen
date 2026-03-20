"""
Dynamic agent factory for GraphWorkflow.

Enables runtime creation of specialised agents from blueprint dicts,
registered on-the-fly into the Multigen agent registry so they can be
called immediately via agent_activity without pre-deployment.

Blueprint dict shape:
    {
      "system_prompt":  str   — role/persona
      "instruction":    str   — task instruction (may use {{…}} refs)
      "output_schema":  dict  — expected output fields (for validation hints)
      "tools":          list  — tool names the agent may call
      "model":          str   — LLM model id (future: passed to LLM service)
    }

Usage from a graph node:
    {
      "id": "custom_analyst",
      "blueprint": {
        "system_prompt": "You are a financial risk analyst.",
        "instruction": "Evaluate the credit profile and return risk_score, rationale."
      },
      "params": { ... }
    }

The engine calls create_agent_from_blueprint() inside a Temporal activity
so the registry mutation happens in activity context (not in sandboxed
workflow code).
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Type

from agents.base_agent import BaseAgent
from orchestrator.services.agent_registry import _registry  # internal dict

logger = logging.getLogger(__name__)


class BlueprintAgent(BaseAgent):
    """
    Dynamically-created agent that executes a configurable prompt blueprint.

    Subclasses are generated at runtime via create_agent_from_blueprint().
    The class-level 'blueprint' dict is set on the generated subclass so
    each dynamic agent type carries its own instruction set.

    In v0.1 the blueprint is executed locally (echo + metadata).
    In v0.2 this will call the LLM service with the compiled prompt.
    """

    blueprint: Dict[str, Any] = {}

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        system = self.blueprint.get("system_prompt", "You are a helpful assistant.")
        instruction = self.blueprint.get("instruction", "Process the input.")
        output_schema = self.blueprint.get("output_schema", {})

        # v0.1: structured echo — replace with LLM call in v0.2
        return {
            "agent": self.__class__.__name__,
            "blueprint_executed": True,
            "system_prompt": system,
            "instruction": instruction,
            "params_received": params,
            "expected_output_schema": output_schema,
            # Simulate a high-confidence structured response
            "result": f"[{self.__class__.__name__}] processed: {list(params.keys())}",
            "confidence": 0.88,
        }


def create_agent_from_blueprint(
    agent_name: str,
    blueprint: Dict[str, Any],
) -> Type[BaseAgent]:
    """
    Dynamically create and register a new BaseAgent subclass from blueprint.

    If an agent with agent_name is already registered the existing class
    is returned unchanged — idempotent so Temporal retries are safe.

    The generated class is registered in the global _registry so that
    agent_activity can resolve it by name immediately after this call.
    """
    if agent_name in _registry:
        logger.info("DynamicAgentFactory: '%s' already registered — reusing", agent_name)
        return _registry[agent_name]

    new_cls: Type[BaseAgent] = type(
        agent_name,
        (BlueprintAgent,),
        {
            "blueprint": blueprint,
            "__doc__": f"Dynamic blueprint agent: {agent_name}",
            "__module__": __name__,
        },
    )
    _registry[agent_name] = new_cls
    logger.info("DynamicAgentFactory: registered '%s' from blueprint", agent_name)
    return new_cls


def list_dynamic_agents() -> Dict[str, str]:
    """Return names and doc-strings of all blueprint-generated agents."""
    return {
        name: (cls.__doc__ or "")
        for name, cls in _registry.items()
        if issubclass(cls, BlueprintAgent) and cls is not BlueprintAgent
    }
