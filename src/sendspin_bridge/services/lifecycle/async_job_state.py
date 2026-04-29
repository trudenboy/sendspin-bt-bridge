"""Async job, scan job, and update availability state owners."""

from __future__ import annotations

import threading
import time as _time
from typing import Any

_async_jobs: dict[str, dict[str, Any]] = {}
_async_jobs_lock = threading.Lock()
_ASYNC_JOB_TTL = 120

_scan_jobs: dict[str, dict[str, Any]] = {}
_scan_jobs_lock = threading.Lock()
_SCAN_JOB_TTL = 120

_update_available: dict[str, Any] | None = None
_update_available_lock = threading.Lock()


def _evict_expired_jobs(store: dict[str, dict[str, Any]], ttl: int) -> None:
    now = _time.time()
    expired = [job_id for job_id, job in store.items() if now - job.get("created", 0) > ttl]
    for job_id in expired:
        del store[job_id]


def create_async_job(job_id: str, job_type: str) -> None:
    """Register a new in-progress generic async job."""
    with _async_jobs_lock:
        _evict_expired_jobs(_async_jobs, _ASYNC_JOB_TTL)
        _async_jobs[job_id] = {"status": "running", "created": _time.time(), "job_type": job_type}


def finish_async_job(job_id: str, result: dict[str, Any]) -> None:
    """Mark a generic async job as done with its result payload."""
    with _async_jobs_lock:
        if job_id in _async_jobs:
            _async_jobs[job_id]["status"] = "done"
            protected = {"created", "status", "job_type"}
            filtered = {k: v for k, v in result.items() if k not in protected}
            _async_jobs[job_id].update(filtered)


def get_async_job(job_id: str) -> dict[str, Any] | None:
    """Return a shallow copy of the generic async job dict or None if missing."""
    with _async_jobs_lock:
        _evict_expired_jobs(_async_jobs, _ASYNC_JOB_TTL)
        job = _async_jobs.get(job_id)
        return dict(job) if job else None


def create_scan_job(job_id: str, initial_data: dict[str, Any] | None = None) -> None:
    """Register a new in-progress scan job."""
    with _scan_jobs_lock:
        _evict_expired_jobs(_scan_jobs, _SCAN_JOB_TTL)
        _scan_jobs[job_id] = {"status": "running", "created": _time.time()}
        if initial_data:
            _scan_jobs[job_id].update(initial_data)


def finish_scan_job(job_id: str, result: dict[str, Any]) -> None:
    """Mark a scan job as done with its result."""
    with _scan_jobs_lock:
        if job_id in _scan_jobs:
            _scan_jobs[job_id]["status"] = "done"
            _scan_jobs[job_id].update(result)


def is_scan_running() -> bool:
    """Return True if any Bluetooth scan job is currently running."""
    with _scan_jobs_lock:
        return any(job["status"] == "running" for job in _scan_jobs.values())


def get_scan_job(job_id: str) -> dict[str, Any] | None:
    """Return a shallow copy of the scan job dict or None if not found."""
    with _scan_jobs_lock:
        _evict_expired_jobs(_scan_jobs, _SCAN_JOB_TTL)
        job = _scan_jobs.get(job_id)
        return dict(job) if job else None


def get_update_available() -> dict[str, Any] | None:
    """Return update info dict if a newer version is available."""
    with _update_available_lock:
        return dict(_update_available) if _update_available else None


def set_update_available(data: dict[str, Any] | None) -> None:
    """Store update availability info."""
    global _update_available
    with _update_available_lock:
        _update_available = dict(data) if data else None
