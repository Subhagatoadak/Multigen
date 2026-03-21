"""
Real-time workflow streaming via Server-Sent Events (SSE).

Endpoints
─────────
GET /workflows/{workflow_id}/stream
    SSE stream — emits one JSON event per completed node as it finishes.
    Closes automatically when the workflow completes or errors out.
    Supports ``since`` query param to resume a broken connection.

GET /workflows/{workflow_id}/events
    Polling fallback — returns all completed-node events (or those after
    a given ``since`` index) as a plain JSON array.  Use this from
    environments that do not support EventSource (e.g. some mobile clients).

Event format (each SSE ``data:`` line is a JSON object):
  {
    "index":      int,          # monotonic counter starting at 0
    "node_id":    str,
    "agent":      str,
    "confidence": float,
    "timestamp":  str,          # ISO-8601
    "output":     dict | any,
    "done":       bool          # true on the terminal sentinel event
  }

Clients track ``index`` and pass ``?since=<last_index>`` to resume
after a dropped connection.  The SSE endpoint handles this automatically
via the ``Last-Event-ID`` header (standard EventSource reconnect protocol).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from temporalio.client import Client
from temporalio.service import RPCError

import orchestrator.services.config as config

logger = logging.getLogger(__name__)

router = APIRouter(tags=["streaming"])

# How often (seconds) the SSE endpoint polls the workflow query.
# Lower = lower latency; higher = fewer Temporal RPC calls.
_POLL_INTERVAL = 0.5

# Give up after this many consecutive Temporal errors before closing the stream.
_MAX_CONSECUTIVE_ERRORS = 5


async def _get_temporal_client() -> Client:
    return await Client.connect(config.TEMPORAL_SERVER_URL)


# ── SSE streaming endpoint ─────────────────────────────────────────────────────

@router.get(
    "/workflows/{workflow_id}/stream",
    summary="Stream node-completion events via SSE",
    response_class=StreamingResponse,
)
async def stream_workflow_events(
    workflow_id: str,
    request: Request,
    since: Optional[int] = Query(
        default=None,
        description="Return only events with index > since.  "
                    "Automatically populated from the Last-Event-ID header.",
    ),
) -> StreamingResponse:
    """
    Open an SSE stream for the given workflow.  Emits one event per completed
    node in real time.  The stream closes with a terminal ``done`` event when
    the workflow finishes.

    Reconnect support: EventSource sends ``Last-Event-ID`` on reconnect;
    the endpoint resumes from that index so no events are missed.
    """
    # Standard SSE reconnect: Last-Event-ID header takes precedence over ?since
    last_event_id = request.headers.get("Last-Event-ID")
    if last_event_id is not None:
        try:
            since = int(last_event_id)
        except ValueError:
            pass

    return StreamingResponse(
        _event_generator(workflow_id, since=since or -1),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",       # disable nginx buffering
            "Connection": "keep-alive",
        },
    )


async def _event_generator(
    workflow_id: str,
    since: int = -1,
) -> AsyncGenerator[str, None]:
    """
    Poll ``GraphWorkflow.get_completed_nodes`` every _POLL_INTERVAL seconds
    and yield new events as SSE lines.
    """
    try:
        client = await _get_temporal_client()
        handle = client.get_workflow_handle(workflow_id)
    except Exception as exc:
        yield _sse({"error": f"Cannot connect to Temporal: {exc}", "done": True})
        return

    cursor = since          # last index already delivered to this client
    consecutive_errors = 0

    while True:
        try:
            payload = await handle.query("get_completed_nodes")
            consecutive_errors = 0
        except RPCError as exc:
            # Workflow not found or already closed — emit terminal event
            logger.warning("SSE: workflow '%s' query failed: %s", workflow_id, exc)
            yield _sse({"error": str(exc), "done": True, "workflow_id": workflow_id})
            return
        except Exception as exc:
            consecutive_errors += 1
            logger.warning(
                "SSE: transient error on '%s' (%d/%d): %s",
                workflow_id, consecutive_errors, _MAX_CONSECUTIVE_ERRORS, exc,
            )
            if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                yield _sse({"error": "Too many Temporal errors", "done": True})
                return
            await asyncio.sleep(_POLL_INTERVAL)
            continue

        events = payload.get("events", [])
        done = payload.get("done", False)

        # Emit all events with index > cursor
        for ev in events:
            if ev["index"] > cursor:
                cursor = ev["index"]
                # id: field enables Last-Event-ID reconnect
                yield _sse(ev, event_id=str(ev["index"]))

        if done:
            # Terminal sentinel so clients know to close
            yield _sse({"done": True, "total": len(events), "workflow_id": workflow_id})
            return

        await asyncio.sleep(_POLL_INTERVAL)


def _sse(data: dict, event_id: Optional[str] = None) -> str:
    """Format a dict as an SSE ``data:`` line, with optional ``id:`` field."""
    lines = []
    if event_id is not None:
        lines.append(f"id: {event_id}")
    lines.append(f"data: {json.dumps(data, default=str)}")
    lines.append("")   # blank line terminates the event
    lines.append("")
    return "\n".join(lines)


# ── Polling fallback endpoint ──────────────────────────────────────────────────

@router.get(
    "/workflows/{workflow_id}/events",
    summary="Polling fallback — fetch completed-node events as JSON",
)
async def poll_workflow_events(
    workflow_id: str,
    since: int = Query(
        default=-1,
        description="Return only events with index > since (default: all events).",
    ),
) -> JSONResponse:
    """
    Returns completed node events as a plain JSON array.
    Use when EventSource / SSE is not available (e.g. some mobile clients,
    internal tooling that prefers polling).
    """
    try:
        client = await _get_temporal_client()
        handle = client.get_workflow_handle(workflow_id)
        payload = await handle.query("get_completed_nodes")
    except RPCError as exc:
        return JSONResponse(status_code=404, content={"error": str(exc)})
    except Exception as exc:
        return JSONResponse(status_code=502, content={"error": str(exc)})

    events = [e for e in payload.get("events", []) if e["index"] > since]
    return JSONResponse({
        "workflow_id": workflow_id,
        "events":      events,
        "total":       payload.get("total", 0),
        "done":        payload.get("done", False),
        "next_since":  events[-1]["index"] if events else since,
    })
