"""SpawnerAgent
================

Demonstration agent that generates additional workflow steps at runtime.
This stub implementation simply echoes a request to spawn ``n`` new
``EchoAgent`` steps.  In a real system the logic could be driven by an
LLM or other dynamic analysis.
"""

from typing import Any, Dict, List

from orchestrator.services.agent_registry import register_agent
from agents.base_agent import BaseAgent


@register_agent("SpawnerAgent")
class SpawnerAgent(BaseAgent):
    """Return a list of step definitions to be injected into a workflow."""

    async def run(self, params: Dict[str, Any]) -> Dict[str, Any]:
        count = int(params.get("count", 1))
        target = params.get("agent", "EchoAgent")
        step_params = params.get("params", {})

        steps: List[Dict[str, Any]] = []
        for i in range(count):
            steps.append({"name": f"spawn_{i}", "agent": target, "params": step_params})

        return {"spawned_steps": steps}

