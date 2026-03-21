"""
Distributed Agent Spec Store.

Solves the cross-worker dynamic agent problem:

  Without this:
    create_agent_activity runs on Worker-1 → registers BlueprintAgent in Worker-1's
    in-memory registry. agent_activity runs on Worker-2 (Temporal load-balances) →
    agent not found → crash.

  With this:
    create_agent_activity stores the blueprint spec in MongoDB.
    agent_activity on ANY worker checks local registry first, then falls back to
    this store and reconstructs the BlueprintAgent on demand (lazy hydration).
    This means ALL workers can serve ANY dynamic agent regardless of which worker
    originally created it.

Architecture:
  ┌─────────────┐   store spec   ┌──────────────────────────┐
  │  Worker-1   │ ─────────────► │  MongoDB: agent_specs    │
  │  create_    │                │  { name, blueprint,      │
  │  agent_     │                │    workflow_id, ttl }    │
  │  activity   │                └──────────────┬───────────┘
  └─────────────┘                               │ lazy fetch
                                                ▼
  ┌─────────────┐   agent not   ┌──────────────────────────┐
  │  Worker-2   │ ─in-registry─►│  fetch spec → create     │
  │  agent_     │               │  BlueprintAgent locally  │
  │  activity   │               │  cache in local registry │
  └─────────────┘               └──────────────────────────┘
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# TTL for dynamic agent specs: 24 hours (they're per-workflow-run)
_SPEC_TTL_HOURS = 24


async def store_agent_spec(
    agent_name: str,
    blueprint: Dict[str, Any],
    workflow_id: str,
) -> bool:
    """
    Persist a dynamic agent's blueprint spec to MongoDB so any worker replica
    can reconstruct the agent on demand.

    Called by create_agent_activity after local registration.
    Returns True on success, False if storage fails (non-fatal).
    """
    try:
        import orchestrator.services.config as config
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(config.MONGODB_URI)
        col = client[config.GRAPH_STATE_DB_NAME]["agent_specs"]
        await col.update_one(
            {"agent_name": agent_name},
            {
                "$set": {
                    "agent_name": agent_name,
                    "blueprint": blueprint,
                    "workflow_id": workflow_id,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "expires_at": (
                        datetime.now(timezone.utc) + timedelta(hours=_SPEC_TTL_HOURS)
                    ).isoformat(),
                }
            },
            upsert=True,
        )
        logger.debug("agent_store: stored spec for '%s'", agent_name)
        return True
    except Exception as exc:
        logger.warning("agent_store: failed to store spec for '%s': %s", agent_name, exc)
        return False


async def fetch_agent_spec(agent_name: str) -> Optional[Dict[str, Any]]:
    """
    Fetch a dynamic agent's blueprint spec from MongoDB.

    Called by agent_activity when an agent is not in the local registry.
    Returns the blueprint dict or None if not found / expired.
    """
    try:
        import orchestrator.services.config as config
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(config.MONGODB_URI)
        col = client[config.GRAPH_STATE_DB_NAME]["agent_specs"]
        doc = await col.find_one({"agent_name": agent_name})
        if not doc:
            return None
        # Check TTL
        expires_at_str = doc.get("expires_at")
        if expires_at_str:
            expires_at = datetime.fromisoformat(expires_at_str)
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) > expires_at:
                logger.debug("agent_store: spec for '%s' has expired", agent_name)
                return None
        blueprint = doc.get("blueprint")
        logger.info("agent_store: hydrated spec for '%s' from store", agent_name)
        return blueprint
    except Exception as exc:
        logger.warning("agent_store: failed to fetch spec for '%s': %s", agent_name, exc)
        return None


async def delete_agent_spec(agent_name: str) -> None:
    """Remove a dynamic agent spec from the store (called at workflow cleanup)."""
    try:
        import orchestrator.services.config as config
        from motor.motor_asyncio import AsyncIOMotorClient

        client = AsyncIOMotorClient(config.MONGODB_URI)
        col = client[config.GRAPH_STATE_DB_NAME]["agent_specs"]
        await col.delete_one({"agent_name": agent_name})
        logger.debug("agent_store: deleted spec for '%s'", agent_name)
    except Exception as exc:
        logger.warning("agent_store: failed to delete spec for '%s': %s", agent_name, exc)


async def hydrate_agent_if_missing(agent_name: str) -> bool:
    """
    If agent_name is not in the local registry, fetch its blueprint from the
    distributed store and register it locally.

    Returns True if the agent is now available (was already registered, or
    successfully hydrated). Returns False if spec not found.

    This is the lazy hydration path called by agent_activity on cross-worker dispatch.
    """
    from orchestrator.services.agent_registry import _registry
    if agent_name in _registry:
        return True

    blueprint = await fetch_agent_spec(agent_name)
    if not blueprint:
        return False

    try:
        from flow_engine.graph.agent_factory import create_agent_from_blueprint
        create_agent_from_blueprint(agent_name, blueprint)
        logger.info(
            "agent_store: lazy-hydrated '%s' on this worker from distributed store",
            agent_name,
        )
        return True
    except Exception as exc:
        logger.error("agent_store: hydration failed for '%s': %s", agent_name, exc)
        return False
