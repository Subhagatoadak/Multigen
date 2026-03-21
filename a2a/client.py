"""
A2A Client — call remote A2A-compliant agents from within Multigen workflows.

Allows a Multigen graph node to dispatch work to any external agent that
speaks the Agent2Agent protocol (LangGraph agents, Vertex AI agents,
other Multigen instances, etc.) without coupling to their internal implementation.

Usage (in a graph node definition):
    {
        "id": "remote_risk",
        "a2a_endpoint": "https://partner-system.example.com",
        "a2a_skill": "risk_analysis",
        "params": {"company": "NovaSemi", "scope": "full"},
        "timeout": 120
    }

Usage (directly in Python):
    from a2a.client import A2AClient

    async with A2AClient("https://partner-system.example.com") as client:
        card = await client.get_agent_card()
        result = await client.send_task(skill_id="risk_analysis", data={"company": "NovaSemi"})
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, AsyncGenerator, Dict, Optional

import httpx

from .models import AgentCard, Task, TaskSendParams, TaskState, Message, TextPart, DataPart

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 60.0


class A2AClientError(Exception):
    """Raised when an A2A remote call fails."""


class A2AClient:
    """
    Async HTTP client for the Agent2Agent protocol.

    Discovers remote agent capabilities via their Agent Card and submits
    tasks via JSON-RPC 2.0.
    """

    def __init__(
        self,
        base_url: str,
        api_key: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            headers=headers,
            timeout=timeout,
        )

    async def __aenter__(self) -> "A2AClient":
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.close()

    async def close(self) -> None:
        await self._http.aclose()

    # ── Discovery ──────────────────────────────────────────────────────────────

    async def get_agent_card(self) -> AgentCard:
        """Fetch the remote agent's Agent Card from /.well-known/agent.json."""
        try:
            resp = await self._http.get("/.well-known/agent.json")
            resp.raise_for_status()
            return AgentCard.model_validate(resp.json())
        except Exception as exc:
            # Fall back to /a2a/.well-known/agent.json
            try:
                resp = await self._http.get("/a2a/.well-known/agent.json")
                resp.raise_for_status()
                return AgentCard.model_validate(resp.json())
            except Exception:
                raise A2AClientError(f"Could not fetch Agent Card from {self._base_url}: {exc}")

    # ── Task submission ────────────────────────────────────────────────────────

    async def send_task(
        self,
        skill_id: str = "",
        text: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
        session_id: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Send a synchronous A2A task (tasks/send) and return the result.

        skill_id: agent skill or name to invoke on the remote system.
        text: natural-language message to the agent.
        data: structured key-value data for the agent.
        """
        parts = []
        if text:
            parts.append({"type": "text", "text": text})
        if data:
            parts.append({"type": "data", "data": data})
        if not parts:
            parts.append({"type": "text", "text": skill_id or "run"})

        tid = task_id or str(uuid.uuid4())

        payload = {
            "jsonrpc": "2.0",
            "id": tid,
            "method": "tasks/send",
            "params": {
                "id": tid,
                "message": {"role": "user", "parts": parts},
                "metadata": {"skill_id": skill_id},
                **({"session_id": session_id} if session_id else {}),
            },
        }

        try:
            kwargs: Dict[str, Any] = {}
            if timeout:
                kwargs["timeout"] = timeout
            resp = await self._http.post("/a2a", json=payload, **kwargs)
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as exc:
            raise A2AClientError(f"HTTP {exc.response.status_code}: {exc.response.text}")
        except Exception as exc:
            raise A2AClientError(f"Request failed: {exc}")

        if "error" in body and body["error"]:
            err = body["error"]
            raise A2AClientError(f"A2A error {err.get('code')}: {err.get('message')}")

        task_data = body.get("result", {})
        return _extract_output(task_data, skill_id)

    async def stream_task(
        self,
        skill_id: str = "",
        text: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        task_id: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Send a streaming A2A task (tasks/sendSubscribe) via SSE.
        Yields each state transition event as a dict.
        """
        parts = []
        if text:
            parts.append({"type": "text", "text": text})
        if data:
            parts.append({"type": "data", "data": data})
        if not parts:
            parts.append({"type": "text", "text": skill_id or "run"})

        tid = task_id or str(uuid.uuid4())
        payload = {
            "jsonrpc": "2.0",
            "id": tid,
            "method": "tasks/sendSubscribe",
            "params": {
                "id": tid,
                "message": {"role": "user", "parts": parts},
                "metadata": {"skill_id": skill_id},
            },
        }

        async with self._http.stream("POST", "/a2a/stream", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line.startswith("data: "):
                    try:
                        event = json.loads(line[6:])
                        yield event.get("result", event)
                    except json.JSONDecodeError:
                        continue


# ── Output extraction ──────────────────────────────────────────────────────────

def _extract_output(task_data: Dict[str, Any], skill_id: str) -> Dict[str, Any]:
    """
    Convert an A2A Task response into Multigen's standard agent output format:
    {"output": {...}, "confidence": float}
    """
    status = task_data.get("status", {})
    state = status.get("state", "")

    if state == TaskState.FAILED:
        error_msg = ""
        if status.get("message"):
            parts = status["message"].get("parts", [])
            error_msg = " ".join(p.get("text", "") for p in parts if p.get("type") == "text")
        raise A2AClientError(f"Remote A2A task failed: {error_msg}")

    artifacts = task_data.get("artifacts", [])
    output: Dict[str, Any] = {}
    confidence = 0.7  # default for remote agents

    for artifact in artifacts:
        artifact_meta = artifact.get("metadata", {})
        if "confidence" in artifact_meta:
            confidence = float(artifact_meta["confidence"])

        for part in artifact.get("parts", []):
            if part.get("type") == "data":
                output.update(part.get("data", {}))
            elif part.get("type") == "text":
                text = part.get("text", "")
                # Attempt to parse text as JSON
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        output.update(parsed)
                    else:
                        output["result"] = parsed
                except json.JSONDecodeError:
                    output["result"] = text

    if not output:
        output["result"] = f"Task completed by remote agent (skill: {skill_id})"

    return {
        "output": {
            **output,
            "a2a_task_id": task_data.get("id"),
            "a2a_state": state,
        },
        "confidence": confidence,
    }
