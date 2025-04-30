from typing import List, Optional
from datetime import datetime

import orchestrator.services.mongo as mongo_mod
from orchestrator.services.config import CAPABILITY_DB_NAME, CAPABILITY_COLLECTION_NAME
from orchestrator.models.capability import Capability
from orchestrator.services.agent_registry import AgentRegistryError


async def register_capability(cap: Capability) -> Capability:
    client = mongo_mod.get_mongo_client()
    coll = client[CAPABILITY_DB_NAME][CAPABILITY_COLLECTION_NAME]

    existing = await coll.find_one({"name": cap.name, "version": cap.version})
    if existing:
        raise AgentRegistryError(
            f"Capability '{cap.name}@{cap.version}' is already registered"
        )

    cap.created_at = datetime.utcnow()
    # Use model_dump() instead of deprecated dict()
    await coll.insert_one(cap.model_dump())  
    return cap


async def get_capability(name: str, version: Optional[str] = None) -> Capability:
    client = mongo_mod.get_mongo_client()
    coll = client[CAPABILITY_DB_NAME][CAPABILITY_COLLECTION_NAME]

    query: dict = {"name": name}
    if version is not None:
        query["version"] = version

    doc = await coll.find_one(query)
    if not doc:
        ident = f"{name}@{version}" if version else name
        raise AgentRegistryError(f"Capability '{ident}' not found")

    # Use model_validate() instead of deprecated parse_obj()
    return Capability.model_validate(doc)  


async def list_capabilities() -> List[Capability]:
    client = mongo_mod.get_mongo_client()
    coll = client[CAPABILITY_DB_NAME][CAPABILITY_COLLECTION_NAME]

    result: List[Capability] = []
    cursor = coll.find({})
    async for doc in cursor:
        result.append(Capability.model_validate(doc))  # model_validate over parse_obj
    return result
