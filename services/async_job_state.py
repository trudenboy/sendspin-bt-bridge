"""Route-facing async-job and update-state helpers backed by shared state."""

from __future__ import annotations

from typing import Any

import state


def create_async_job(job_id: str, job_type: str) -> None:
    """Register a new in-progress async job."""
    state.create_async_job(job_id, job_type)


def finish_async_job(job_id: str, result: dict[str, Any]) -> None:
    """Mark an async job as complete."""
    state.finish_async_job(job_id, result)


def get_async_job(job_id: str) -> dict[str, Any] | None:
    """Return a snapshot of an async job."""
    return state.get_async_job(job_id)


def get_update_available() -> dict[str, Any] | None:
    """Return cached update availability info."""
    return state.get_update_available()


def set_update_available(data: dict[str, Any] | None) -> None:
    """Store cached update availability info."""
    state.set_update_available(data)


def create_scan_job(job_id: str) -> None:
    """Register a new Bluetooth scan job."""
    state.create_scan_job(job_id)


def finish_scan_job(job_id: str, result: dict[str, Any]) -> None:
    """Mark a Bluetooth scan job as complete."""
    state.finish_scan_job(job_id, result)


def get_scan_job(job_id: str) -> dict[str, Any] | None:
    """Return a snapshot of a Bluetooth scan job."""
    return state.get_scan_job(job_id)


def is_scan_running() -> bool:
    """Return whether any Bluetooth scan job is still running."""
    return state.is_scan_running()
