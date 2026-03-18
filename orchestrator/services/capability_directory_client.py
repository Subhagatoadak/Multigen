# orchestrator/services/capability_directory_client.py

import httpx
import os
from typing import Optional
from orchestrator.models.capability import Capability
from orchestrator.services.agent_registry import AgentRegistryError

BASE_URL = os.getenv("CAPABILITY_SERVICE_URL", "http://localhost:8000")

async def validate_agent(name: str, version: Optional[str] = None) -> Capability:
    """Ensures the agent is present in the Capability Directory."""
    url = f"{BASE_URL}/capabilities/{name}"
    params = {}
    if version:
        params["version"] = version
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params=params)
        if resp.status_code == 404:
            raise AgentRegistryError(f"Agent '{name}{('@'+version) if version else ''}' not registered")
        resp.raise_for_status()
        return Capability.model_validate(resp.json())
