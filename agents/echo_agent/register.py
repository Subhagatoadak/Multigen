# agents/echo_agent/register.py

import os, asyncio
import httpx
from orchestrator.models.capability import Capability

CAP_URL = os.getenv("CAPABILITY_SERVICE_URL", "http://localhost:8000").rstrip("/")

async def self_register():
    cap = Capability(
        name="EchoAgent",
        version="1.0.0",
        description="Simple echo agent",
        metadata={"supported": ["echo"]},
    )
    async with httpx.AsyncClient(follow_redirects=True) as client:
        # POST to /capabilities (no trailing slash)
        resp = await client.post(f"{CAP_URL}/capabilities", json=cap.model_dump())
        resp.raise_for_status()
        print("Registered capability:", resp.json())

if __name__ == "__main__":
    asyncio.run(self_register())
