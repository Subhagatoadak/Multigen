"""In-memory job queue for async/sync workflow runs."""
from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional


@dataclass
class Job:
    id: str
    status: str = "queued"
    result: Optional[Mapping[str, Any]] = None
    error: Optional[str] = None


class RunnerQueue:
    """Very small in-memory queue supporting sync or thread-based async runs."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(self, fn: Callable[[], Mapping[str, Any]], *, async_mode: bool = True) -> Job:
        job_id = uuid.uuid4().hex
        job = Job(id=job_id)
        self._jobs[job_id] = job

        def _run() -> None:
            try:
                job.status = "running"
                res = fn()
                job.result = res
                job.status = "succeeded"
            except Exception as exc:
                job.status = "failed"
                job.error = str(exc)

        if async_mode:
            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
        else:
            _run()
        return job

    def status(self, job_id: str) -> Job:
        if job_id not in self._jobs:
            raise KeyError(job_id)
        return self._jobs[job_id]


__all__ = ["Job", "RunnerQueue"]
