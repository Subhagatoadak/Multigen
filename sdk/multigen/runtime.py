"""
multigen.runtime
================
LocalRuntime: a full agentic orchestrator that runs entirely in-process.
No Kafka, Temporal, MongoDB, or any external service required.

Features:
- Run Chain, Parallel, Graph, StateMachine from a single entry point
- Optional event bus (InMemoryBus) for inter-agent messaging
- Optional Temporal backend (if installed and configured)
- Optional Kafka backend (if installed and configured)
- Built-in structured logging (pushes events to agentic-simulator if configured)
- Concurrency control (semaphore + worker pool)
- Execution history + replay
- Middleware stack (tracing, logging, auth)

Usage
-----
    # Zero-config local mode
    from multigen.runtime import Runtime

    rt = Runtime()
    result = await rt.run_chain([agent_a, agent_b], ctx={"input": "hello"})
    result = await rt.run_graph(graph, ctx={})
    result = await rt.run_state_machine(sm, ctx={})

    # With Temporal (optional)
    rt = Runtime(backend="temporal", temporal_host="localhost:7233")
    result = await rt.run_workflow("my_workflow", dsl, ctx={})

    # Push events to agentic-simulator
    rt = Runtime(simulator_url="http://localhost:8003")
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional, Union

from .agent import BaseAgent
from .bus import InMemoryBus, Message, get_default_bus

logger = logging.getLogger(__name__)
Context = Dict[str, Any]


class Runtime:
    """Unified agentic orchestrator.

    Parameters
    ----------
    backend:
        "local" (default), "temporal", or "kafka"
    simulator_url:
        If set, execution events are pushed to the agentic-simulator at this URL.
        Example: "http://localhost:8003"
    max_concurrency:
        Global semaphore for parallel agent execution.
    trace:
        If True, attach trace IDs to all events.
    """

    def __init__(
        self,
        *,
        backend: str = "local",
        simulator_url: Optional[str] = None,
        max_concurrency: int = 32,
        trace: bool = True,
        bus: Optional[InMemoryBus] = None,
    ) -> None:
        self.backend = backend
        self.simulator_url = simulator_url
        self._trace = trace
        self._sem = asyncio.Semaphore(max_concurrency)
        self._bus = bus or get_default_bus()
        self._history: List[Dict[str, Any]] = []

        # Lazily initialise optional backends
        self._temporal = None
        self._kafka = None

    # ── Chain ─────────────────────────────────────────────────────────────

    async def run_chain(
        self,
        agents: List[BaseAgent],
        *,
        ctx: Optional[Context] = None,
        workflow_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Any:
        from .chain import Chain
        chain = Chain(agents)
        return await self._execute("chain", chain.run, ctx, workflow_id, trace_id)

    # ── Parallel ──────────────────────────────────────────────────────────

    async def run_parallel(
        self,
        agents: List[BaseAgent],
        *,
        ctx: Optional[Context] = None,
        concurrency: Optional[int] = None,
        workflow_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Any:
        from .parallel import Parallel
        par = Parallel(agents, concurrency=concurrency)
        return await self._execute("parallel", par.run, ctx, workflow_id, trace_id)

    # ── Graph ─────────────────────────────────────────────────────────────

    async def run_graph(
        self,
        graph: Any,
        *,
        ctx: Optional[Context] = None,
        workflow_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Any:
        return await self._execute("graph", graph.run, ctx, workflow_id, trace_id)

    # ── State Machine ─────────────────────────────────────────────────────

    async def run_state_machine(
        self,
        sm: Any,
        *,
        ctx: Optional[Context] = None,
        workflow_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Any:
        return await self._execute("state_machine", sm.run, ctx, workflow_id, trace_id)

    # ── DSL (legacy compatibility with existing orchestrator) ─────────────

    async def run_dsl(
        self,
        dsl: Dict[str, Any],
        *,
        payload: Optional[Dict[str, Any]] = None,
        workflow_id: Optional[str] = None,
        trace_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Run a Multigen DSL definition (compatible with existing orchestrator format)."""
        if self.backend == "temporal" and self._temporal:
            return await self._temporal.run(dsl, payload=payload, workflow_id=workflow_id)

        from .local_runner import LocalWorkflowRunner
        runner = LocalWorkflowRunner()
        wid = workflow_id or str(uuid.uuid4())[:8]
        return await runner.run(dsl, payload=payload, workflow_id=wid)

    # ── Internal executor ─────────────────────────────────────────────────

    async def _execute(
        self,
        kind: str,
        run_fn,
        ctx: Optional[Context],
        workflow_id: Optional[str],
        trace_id: Optional[str],
    ) -> Any:
        wid = workflow_id or str(uuid.uuid4())[:8]
        tid = trace_id or str(uuid.uuid4()) if self._trace else ""
        ctx_with_trace = {**(ctx or {}), "_workflow_id": wid, "_trace_id": tid}

        start = time.perf_counter()
        await self._emit_event("workflow_start", wid, tid, kind, {"workflow_id": wid, "kind": kind})

        async with self._sem:
            try:
                result = await run_fn(ctx_with_trace)
                latency_ms = round((time.perf_counter() - start) * 1000, 2)
                await self._emit_event("workflow_end", wid, tid, kind, {"latency_ms": latency_ms, "status": "completed"})
                record = {"workflow_id": wid, "kind": kind, "status": "completed", "latency_ms": latency_ms}
                self._history.append(record)
                return result
            except Exception as exc:
                latency_ms = round((time.perf_counter() - start) * 1000, 2)
                await self._emit_event("workflow_error", wid, tid, kind, {"error": str(exc), "latency_ms": latency_ms})
                raise

    async def _emit_event(self, event_type: str, workflow_id: str, trace_id: str, kind: str, metadata: Dict) -> None:
        """Push an event to the agentic-simulator if configured, and the local bus."""
        # Always publish to local bus
        msg = Message(
            topic=f"runtime.{event_type}",
            sender="runtime",
            content={"workflow_id": workflow_id, "event_type": event_type, "kind": kind, **metadata},
        )
        asyncio.create_task(self._bus.publish(msg))

        # Optionally push to agentic-simulator REST API
        if self.simulator_url:
            asyncio.create_task(self._push_to_simulator(event_type, workflow_id, trace_id, kind, metadata))

    async def _push_to_simulator(self, event_type: str, workflow_id: str, trace_id: str, kind: str, metadata: Dict) -> None:
        payload = {
            "source":     f"multigen.{self.backend}",
            "event_type": event_type,
            "level":      "ERROR" if "error" in metadata else "INFO",
            "trace_id":   trace_id,
            "sender":     kind,
            "receiver":   "simulator",
            "content":    metadata.get("error") or event_type,
            "turn":       0,
            "metadata":   {"workflow_id": workflow_id, **metadata},
        }
        try:
            import json, urllib.request
            data = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{self.simulator_url}/api/v1/events",
                data=data,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2):
                pass
        except Exception as e:
            logger.debug("Could not push to simulator: %s", e)

    # ── Backend management ────────────────────────────────────────────────

    def use_temporal(self, host: str = "localhost:7233", namespace: str = "default") -> "Runtime":
        """Enable Temporal backend (requires temporalio package)."""
        try:
            from .backends.temporal_backend import TemporalBackend
            self._temporal = TemporalBackend(host=host, namespace=namespace)
            self.backend = "temporal"
            logger.info("Temporal backend enabled: %s", host)
        except ImportError:
            raise RuntimeError("Install 'temporalio' to use the Temporal backend")
        return self

    def use_kafka(self, bootstrap_servers: str = "localhost:9092") -> "Runtime":
        """Enable Kafka event bus (requires aiokafka package)."""
        try:
            from .backends.kafka_backend import KafkaBackend
            self._kafka = KafkaBackend(bootstrap_servers=bootstrap_servers)
            logger.info("Kafka backend enabled: %s", bootstrap_servers)
        except ImportError:
            raise RuntimeError("Install 'aiokafka' to use the Kafka backend")
        return self

    # ── Introspection ─────────────────────────────────────────────────────

    @property
    def history(self) -> List[Dict[str, Any]]:
        return list(self._history)

    def stats(self) -> Dict[str, Any]:
        total = len(self._history)
        completed = sum(1 for h in self._history if h["status"] == "completed")
        avg_latency = sum(h.get("latency_ms", 0) for h in self._history) / total if total else 0
        return {
            "total_runs":   total,
            "completed":    completed,
            "failed":       total - completed,
            "avg_latency_ms": round(avg_latency, 2),
            "bus_stats":    self._bus.stats,
        }

    def __repr__(self) -> str:
        return f"Runtime(backend={self.backend!r}, simulator={self.simulator_url!r})"


# ─── Convenience singleton ───────────────────────────────────────────────────

_default_runtime: Optional[Runtime] = None


def get_runtime(**kwargs: Any) -> Runtime:
    """Return (or create) the process-level default Runtime."""
    global _default_runtime
    if _default_runtime is None:
        _default_runtime = Runtime(**kwargs)
    return _default_runtime
