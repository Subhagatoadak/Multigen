"""Optional FastAPI service to run workflows and expose health/metrics."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping

try:  # pragma: no cover - optional dependency
    from fastapi import FastAPI, HTTPException
    from pydantic import BaseModel
except Exception as exc:  # pragma: no cover
    raise ImportError("fastapi is required for agentic_codex.service.http") from exc

from ..manifests.loaders import build_from_manifest
from ..core.observability.run_store import RunStore
from ..core.safety.rate_limit import MultiLimiter
from ..core.safety.policy_apply import apply_rate_limits, load_prompts
from ..core.message_bus import CommunicationHub
from ..core.prompting import PromptManager
from .queue import RunnerQueue


class RunRequest(BaseModel):
    workflow: str
    goal: str
    vars: Dict[str, str] = {}
    async_mode: bool = True


def build_app(run_store: str | None = None) -> Any:
    app = FastAPI(title="Agentic Codex Service", version="0.1.0")
    store = RunStore(run_store) if run_store else None
    from ..core.observability.prometheus import render_metrics
    queue = RunnerQueue()

    @app.get("/healthz")
    def health() -> Mapping[str, str]:
        return {"status": "ok"}

    @app.post("/run")
    def run(req: RunRequest) -> Mapping[str, Any]:
        try:
            manifest = build_from_manifest(req.workflow)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        hub = CommunicationHub()
        limiter = MultiLimiter()
        apply_rate_limits(manifest, limiter=limiter, hub=hub)
        prompt_manager = PromptManager()
        load_prompts(manifest, prompt_manager)
        payload = {
            "workflow": manifest.name,
            "goal": req.goal,
            "vars": req.vars,
        }
        def _run() -> Dict[str, Any]:
            if store:
                store.save(payload)
            return payload

        if req.async_mode:
            job = queue.submit(_run, async_mode=True)
            return {"job_id": job.id, "status": job.status}
        result = queue.submit(_run, async_mode=False)
        return {"job_id": result.id, "status": result.status, "result": result.result}

    @app.get("/metrics")
    def metrics() -> Mapping[str, Any]:
        payload = {"status": "ok", "note": "Use render_metrics on collected data; OTLP export supported via Tracer."}
        return payload

    @app.post("/metrics/render")
    def metrics_render(data: Mapping[str, Any]) -> Any:
        metrics = data.get("_metrics", {})
        return render_metrics(metrics)

    @app.get("/jobs/{job_id}")
    def job_status(job_id: str) -> Mapping[str, Any]:
        try:
            job = queue.status(job_id)
            return {"job_id": job.id, "status": job.status, "result": job.result, "error": job.error}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="job not found") from exc

    return app


__all__ = ["build_app"]
