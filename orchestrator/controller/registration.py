from fastapi import APIRouter, HTTPException, status
from typing import List, Optional

from orchestrator.models.capability import Capability
from orchestrator.services.capability_directory import (
    register_capability,
    get_capability,
    list_capabilities,
)
from orchestrator.services.agent_registry import AgentRegistryError

router = APIRouter(prefix="/capabilities", tags=["Capabilities"])

@router.post("", response_model=Capability, status_code=status.HTTP_201_CREATED)
async def register(cap_req: Capability):
    """Register a new agent/tool capability."""
    try:
        cap = await register_capability(cap_req)
        return cap
    except AgentRegistryError as e:
        raise HTTPException(status_code=409, detail=str(e))

@router.get("", response_model=List[Capability])
async def list_all() -> List[Capability]:
    """List all registered capabilities."""
    return await list_capabilities()

@router.get("/{name}", response_model=Capability)
async def get_one(name: str, version: Optional[str] = None) -> Capability:
    """Fetch a capability by name and optional version."""
    try:
        return await get_capability(name, version)
    except AgentRegistryError as e:
        raise HTTPException(status_code=404, detail=str(e))
