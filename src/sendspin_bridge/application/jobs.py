"""Long-running operations as jobs.

Anything that takes longer than a request — a scan, a pairing, an update,
a reset-reconnect, Music Assistant discovery — starts a job and returns its id.
Clients poll ``GET /jobs/{id}`` or listen for ``job`` events; both carry the
same ``Job`` document.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.models.base import ResponseModel

logger = logging.getLogger(__name__)

JobStatus = Literal["running", "succeeded", "failed", "cancelled"]

_TTL_S = 600.0


class JobError(ResponseModel):
    code: str
    detail: str | None = None
    status: int = 500


class Job(ResponseModel):
    id: str
    kind: str = Field(description="What the job does, e.g. bluetooth.scan, bluetooth.pairing, updates.check.")
    status: JobStatus = "running"
    created_at: str
    updated_at: str
    subject: str | None = Field(default=None, description="What the job acts on (a MAC, a device id…).")
    progress: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: JobError | None = None


class JobContext:
    """Handed to a running job: report progress, check for cancellation."""

    def __init__(self, registry: JobRegistry, job_id: str) -> None:
        self._registry = registry
        self.job_id = job_id
        self.cancel_requested = threading.Event()

    def progress(self, **fields: Any) -> None:
        self._registry._update(self.job_id, progress=fields)

    @property
    def cancelled(self) -> bool:
        return self.cancel_requested.is_set()


def _now() -> str:
    return datetime.now(tz=UTC).isoformat()


class JobRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._contexts: dict[str, JobContext] = {}
        self._touched: dict[str, float] = {}
        self._listeners: list[Callable[[Job], None]] = []

    # -- observation ----------------------------------------------------

    def add_listener(self, callback: Callable[[Job], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Job], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _emit(self, job: Job) -> None:
        with self._lock:
            listeners = list(self._listeners)
        for callback in listeners:
            try:
                callback(job)
            except Exception:
                logger.debug("job listener failed", exc_info=True)

    # -- lifecycle ------------------------------------------------------

    def clear(self) -> None:
        """Forget every job (tests; running work finishes unobserved)."""
        with self._lock:
            self._jobs.clear()
            self._contexts.clear()
            self._touched.clear()

    def _evict(self) -> None:
        cutoff = time.monotonic() - _TTL_S
        for job_id in [jid for jid, at in self._touched.items() if at < cutoff and self._jobs[jid].status != "running"]:
            self._jobs.pop(job_id, None)
            self._contexts.pop(job_id, None)
            self._touched.pop(job_id, None)

    def start(
        self,
        kind: str,
        work: Callable[[JobContext], Any],
        *,
        subject: str | None = None,
        exclusive: bool = False,
        progress: dict[str, Any] | None = None,
    ) -> Job:
        """Run *work* in a thread and return the running job.

        *work* returns the result, or raises ``UseCaseError`` to fail with a code.
        With *exclusive*, a second job of the same kind is refused while one runs.
        """
        with self._lock:
            self._evict()
            if exclusive and any(j.kind == kind and j.status == "running" for j in self._jobs.values()):
                raise UseCaseError(409, "job_in_progress", f"A {kind} job is already running")
            now = _now()
            job = Job(
                id=uuid.uuid4().hex,
                kind=kind,
                created_at=now,
                updated_at=now,
                subject=subject,
                progress=dict(progress or {}),
            )
            context = JobContext(self, job.id)
            self._jobs[job.id] = job
            self._contexts[job.id] = context
            self._touched[job.id] = time.monotonic()
        self._emit(job)

        def _run() -> None:
            try:
                result = work(context)
            except UseCaseError as exc:
                self._finish(job.id, "failed", error=JobError(code=exc.code, detail=exc.detail, status=exc.status))
            except Exception:
                # The traceback goes to the log; clients get no internals.
                logger.exception("Job %s (%s) failed", job.id, kind)
                self._finish(job.id, "failed", error=JobError(code="internal_error", detail="Internal error"))
            else:
                status: JobStatus = "cancelled" if context.cancelled else "succeeded"
                self._finish(job.id, status, result=result)

        try:
            threading.Thread(target=_run, name=f"job-{kind}", daemon=True).start()
        except Exception:
            # A job that never ran must not stay "running": an exclusive kind
            # would refuse every later start until the process restarts.
            self._finish(job.id, "failed", error=JobError(code="internal_error", detail="Could not start the job"))
            raise
        return job

    def _update(self, job_id: str, **changes: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if "progress" in changes:
                merged = dict(job.progress)
                merged.update(changes.pop("progress"))
                changes["progress"] = merged
            job = job.model_copy(update={**changes, "updated_at": _now()})
            self._jobs[job_id] = job
            self._touched[job_id] = time.monotonic()
        self._emit(job)

    def _finish(self, job_id: str, status: JobStatus, *, result: Any = None, error: JobError | None = None) -> None:
        self._update(job_id, status=status, result=result, error=error)

    def get(self, job_id: str) -> Job:
        with self._lock:
            self._evict()
            job = self._jobs.get(job_id)
        if job is None:
            raise UseCaseError(404, "unknown_job", f"Unknown or expired job: {job_id}")
        return job

    def cancel(self, job_id: str) -> Job:
        job = self.get(job_id)
        with self._lock:
            context = self._contexts.get(job_id)
        if context is not None and job.status == "running":
            context.cancel_requested.set()
        return self.get(job_id)

    def running(self, kind: str | None = None) -> list[Job]:
        with self._lock:
            return [j for j in self._jobs.values() if j.status == "running" and (kind is None or j.kind == kind)]


#: The process-wide registry.
jobs = JobRegistry()
