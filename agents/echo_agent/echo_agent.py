from typing import Any, Dict
from orchestrator.services.agent_registry import register_agent
from agents.base_agent import BaseAgent


@register_agent("EchoAgent")
class EchoAgent(BaseAgent):
    """
    A simple demo agent that echoes back the input parameters.
    """

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {"echo": params}

