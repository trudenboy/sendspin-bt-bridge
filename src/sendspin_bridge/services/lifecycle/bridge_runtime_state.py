"""Bridge runtime state owner and helpers."""

from __future__ import annotations

import copy
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio

    from sendspin_bridge.services.bluetooth.device_activation import DeviceActivationContext

UTC = timezone.utc

bridge_start_time: datetime = datetime.now(tz=UTC)


def _new_startup_progress() -> dict[str, Any]:
    return {
        "status": "idle",
        "phase": "idle",
        "current_step": 0,
        "total_steps": 0,
        "message": "",
        "details": {},
        "started_at": None,
        "updated_at": None,
        "completed_at": None,
    }


def _new_runtime_mode_info() -> dict[str, Any]:
    return {
        "mode": "production",
        "is_mocked": False,
        "mocked_layers": [],
        "simulator_active": False,
        "fixture_devices": 0,
        "fixture_groups": 0,
        "disclaimer": "",
        "details": {},
        "updated_at": None,
    }


_startup_progress: dict[str, Any] = _new_startup_progress()
_startup_progress_lock = threading.Lock()
_runtime_mode_info: dict[str, Any] = _new_runtime_mode_info()
_runtime_mode_info_lock = threading.Lock()


def _copy_startup_progress(snapshot: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(snapshot)
    total = int(data.get("total_steps") or 0)
    current = int(data.get("current_step") or 0)
    data["percent"] = 0 if total <= 0 else min(100, round(current * 100 / total))
    return data


def get_startup_progress() -> dict[str, Any]:
    """Return the current bridge startup progress snapshot."""
    with _startup_progress_lock:
        return _copy_startup_progress(_startup_progress)


def reset_startup_progress(total_steps: int = 0, *, message: str = "Startup initiated") -> dict[str, Any]:
    """Reset startup progress for a new bridge boot sequence."""
    global _startup_progress
    now = datetime.now(tz=UTC).isoformat()
    normalized_total_steps = max(0, int(total_steps or 0))
    with _startup_progress_lock:
        _startup_progress = _new_startup_progress()
        _startup_progress.update(
            {
                "status": "running" if normalized_total_steps > 0 else "idle",
                "message": message,
                "total_steps": normalized_total_steps,
                "started_at": now,
                "updated_at": now,
            }
        )
        result = _copy_startup_progress(_startup_progress)
    notify_status_changed()
    return result


def update_startup_progress(
    phase: str,
    message: str,
    *,
    current_step: int | None = None,
    total_steps: int | None = None,
    status: str = "running",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update the current startup progress snapshot and notify SSE listeners."""
    now = datetime.now(tz=UTC).isoformat()
    with _startup_progress_lock:
        _startup_progress["phase"] = phase
        _startup_progress["message"] = message
        _startup_progress["status"] = status
        _startup_progress["updated_at"] = now
        if current_step is not None:
            _startup_progress["current_step"] = max(0, int(current_step))
        if total_steps is not None:
            _startup_progress["total_steps"] = max(0, int(total_steps))
        if details is not None:
            _startup_progress["details"] = copy.deepcopy(details)
        result = _copy_startup_progress(_startup_progress)
    notify_status_changed()
    return result


def complete_startup_progress(
    message: str = "Startup complete",
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark startup progress as ready."""
    now = datetime.now(tz=UTC).isoformat()
    with _startup_progress_lock:
        total_steps = int(_startup_progress.get("total_steps") or _startup_progress.get("current_step") or 0)
        _startup_progress.update(
            {
                "status": "ready",
                "phase": "ready",
                "message": message,
                "current_step": total_steps,
                "total_steps": total_steps,
                "completed_at": now,
                "updated_at": now,
            }
        )
        if details is not None:
            _startup_progress["details"] = copy.deepcopy(details)
        result = _copy_startup_progress(_startup_progress)
    notify_status_changed()
    return result


def fail_startup_progress(message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mark startup progress as failed while preserving current phase/step data."""
    now = datetime.now(tz=UTC).isoformat()
    with _startup_progress_lock:
        _startup_progress["status"] = "error"
        _startup_progress["message"] = message
        _startup_progress["updated_at"] = now
        _startup_progress["completed_at"] = now
        if details is not None:
            _startup_progress["details"] = copy.deepcopy(details)
        result = _copy_startup_progress(_startup_progress)
    notify_status_changed()
    return result


def get_runtime_mode_info() -> dict[str, Any]:
    """Return bridge runtime/mock-mode metadata."""
    with _runtime_mode_info_lock:
        return copy.deepcopy(_runtime_mode_info)


def set_runtime_mode_info(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Replace bridge runtime/mock-mode metadata and notify SSE listeners."""
    global _runtime_mode_info
    now = datetime.now(tz=UTC).isoformat()
    with _runtime_mode_info_lock:
        _runtime_mode_info = _new_runtime_mode_info()
        if data:
            _runtime_mode_info.update(copy.deepcopy(data))
        _runtime_mode_info["updated_at"] = now
        result = copy.deepcopy(_runtime_mode_info)
    notify_status_changed()
    return result


_status_version = 0
_status_condition = threading.Condition()
_main_loop: asyncio.AbstractEventLoop | None = None
_notify_lock = threading.Lock()
_NotifyThreadBase = threading.Thread


class _NotifyTimer(_NotifyThreadBase):
    """Small timer thread resilient to monkeypatches of `threading.Thread`."""

    def __init__(self, interval: float, callback) -> None:
        super().__init__(daemon=True)
        self._interval = interval
        self._callback = callback

    def run(self) -> None:
        _time.sleep(self._interval)
        self._callback()


_notify_timer: _NotifyTimer | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Store the main asyncio event loop for use by Flask/WSGI threads."""
    global _main_loop
    _main_loop = loop


def get_main_loop() -> asyncio.AbstractEventLoop | None:
    """Return the main asyncio event loop, or None if not yet set."""
    return _main_loop


_activation_context: DeviceActivationContext | None = None


def set_activation_context(context: DeviceActivationContext | None) -> None:
    """Publish the device-activation context captured at bridge startup.

    Needed so :class:`~services.reconfig_orchestrator.ReconfigOrchestrator`
    can materialize a new ``SendspinClient`` from a Flask request thread
    when the user adds a device via ``POST /api/config``. ``None`` clears
    the context (used in tests and on shutdown).
    """
    global _activation_context
    _activation_context = context


def get_activation_context() -> DeviceActivationContext | None:
    """Return the captured device-activation context, or ``None`` if the
    bridge hasn't finished its startup sequence yet."""
    return _activation_context


def notify_status_changed() -> None:
    """Signal that at least one client status has changed."""
    global _notify_timer
    with _notify_lock:
        if _notify_timer is None or not _notify_timer.is_alive():
            _notify_timer = _NotifyTimer(0.1, _flush_notify)
            _notify_timer.start()


def _flush_notify() -> None:
    global _status_version
    with _status_condition:
        _status_version += 1
        _status_condition.notify_all()


def get_status_version() -> int:
    """Return the current status version counter."""
    return _status_version


def wait_for_status_change(last_version: int, timeout: float = 15) -> tuple[bool, int]:
    """Block until status version changes or *timeout* seconds elapse."""
    with _status_condition:
        changed = _status_condition.wait_for(
            lambda: _status_version != last_version,
            timeout=timeout,
        )
        return changed, _status_version


def get_bridge_start_time() -> datetime:
    """Return the bridge process start time."""
    return bridge_start_time


def get_bridge_uptime(now: datetime | None = None) -> timedelta:
    """Return bridge uptime as a timedelta."""
    current = now if now is not None else datetime.now(tz=UTC)
    return current - bridge_start_time


def get_bridge_uptime_seconds(now: datetime | None = None) -> float:
    """Return bridge uptime in seconds."""
    return round(get_bridge_uptime(now).total_seconds(), 1)


def get_bridge_uptime_text(now: datetime | None = None) -> str:
    """Return bridge uptime formatted without fractional seconds."""
    return str(get_bridge_uptime(now)).split(".")[0]
