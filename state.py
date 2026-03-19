"""
Shared application state for sendspin-bt-bridge.

Single source of truth for the active SendspinClient list, shared between
web_interface.py (reads for API responses) and sendspin_client.py (writes via set_clients).
"""

from __future__ import annotations

import asyncio  # noqa: TC003 — used at runtime for event loop storage
import copy
import json
import logging
import os
import socket
import threading
import time as _time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any

from config import CONFIG_FILE as _config_file
from services.internal_events import InternalEvent, InternalEventPublisher

__all__ = [
    "apply_ma_now_playing_prediction",
    "bridge_start_time",
    "clear_device_events",
    "clear_ma_now_playing",
    "clients",
    "clients_lock",
    "complete_startup_progress",
    "create_async_job",
    "create_scan_job",
    "fail_ma_pending_op",
    "fail_startup_progress",
    "finish_async_job",
    "finish_scan_job",
    "get_adapter_name",
    "get_async_job",
    "get_bridge_system_info",
    "get_device_events",
    "get_disabled_devices",
    "get_ma_api_credentials",
    "get_ma_group_for_player",
    "get_ma_groups",
    "get_ma_now_playing",
    "get_ma_now_playing_for_group",
    "get_ma_server_version",
    "get_main_loop",
    "get_runtime_mode_info",
    "get_scan_job",
    "get_startup_progress",
    "is_ma_connected",
    "is_scan_running",
    "load_adapter_name_cache",
    "mark_ma_now_playing_stale",
    "notify_status_changed",
    "publish_device_event",
    "publish_internal_event",
    "record_device_event",
    "replace_ma_now_playing",
    "reset_startup_progress",
    "set_clients",
    "set_disabled_devices",
    "set_ma_api_credentials",
    "set_ma_connected",
    "set_ma_groups",
    "set_ma_now_playing",
    "set_ma_now_playing_for_group",
    "set_ma_server_version",
    "set_main_loop",
    "set_runtime_mode_info",
    "update_startup_progress",
]

# ---------------------------------------------------------------------------
# Bridge-level metadata (independent of any client)
# ---------------------------------------------------------------------------
bridge_start_time: datetime = datetime.now(tz=timezone.utc)


def _detect_runtime_type() -> str:
    """Detect whether we're running as HA addon, Docker, or LXC/bare metal."""
    if os.environ.get("SUPERVISOR_TOKEN"):
        return "ha-addon"
    if os.path.exists("/.dockerenv"):
        return "docker"
    return "lxc"


def get_bridge_system_info() -> dict:
    """Return hostname, IP, uptime and version — always available."""
    from config import BUILD_DATE, CONFIG_SCHEMA_VERSION, VERSION
    from services.ipc_protocol import IPC_PROTOCOL_VERSION

    uptime = datetime.now(tz=timezone.utc) - bridge_start_time
    from config import get_local_ip

    ip = get_local_ip()
    return {
        "version": VERSION,
        "build_date": BUILD_DATE,
        "hostname": socket.gethostname(),
        "ip_address": ip,
        "uptime": str(timedelta(seconds=int(uptime.total_seconds()))),
        "runtime": _detect_runtime_type(),
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "ipc_protocol_version": IPC_PROTOCOL_VERSION,
    }


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
    now = datetime.now(tz=timezone.utc).isoformat()
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
    now = datetime.now(tz=timezone.utc).isoformat()
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
    now = datetime.now(tz=timezone.utc).isoformat()
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
    now = datetime.now(tz=timezone.utc).isoformat()
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
    now = datetime.now(tz=timezone.utc).isoformat()
    with _runtime_mode_info_lock:
        _runtime_mode_info = _new_runtime_mode_info()
        if data:
            _runtime_mode_info.update(copy.deepcopy(data))
        _runtime_mode_info["updated_at"] = now
        result = copy.deepcopy(_runtime_mode_info)
    notify_status_changed()
    return result


# ---------------------------------------------------------------------------
# SSE status-change signalling — used by /api/status/stream
# ---------------------------------------------------------------------------
_status_version: int = 0
# Condition used instead of a plain Event to avoid a race between the version
# check and the wait() call in the SSE generator (a plain set()+clear() can
# lose the wakeup if notify fires between them).
_status_condition: threading.Condition = threading.Condition()


_main_loop: asyncio.AbstractEventLoop | None = None


def set_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Store the main asyncio event loop for use by Flask/WSGI threads."""
    global _main_loop
    _main_loop = loop


def get_main_loop() -> asyncio.AbstractEventLoop | None:
    """Return the main asyncio event loop, or None if not yet set."""
    return _main_loop


_notify_lock: threading.Lock = threading.Lock()
_notify_timer: threading.Timer | None = None


def notify_status_changed() -> None:
    """Signal that at least one client status has changed (wakes SSE listeners).

    Thread-safe: may be called from any thread.
    Notifications are batched within a 100 ms window to prevent SSE storms
    when many devices update simultaneously (e.g., mass reconnect).
    """
    global _notify_timer
    with _notify_lock:
        # Race between is_alive() and start() is benign: worst case two timers
        # fire, producing two increments + notify_all() calls which is harmless.
        if _notify_timer is None or not _notify_timer.is_alive():
            _notify_timer = threading.Timer(0.1, _flush_notify)
            _notify_timer.daemon = True
            _notify_timer.start()


def _flush_notify() -> None:
    """Deliver the batched notification to SSE listeners."""
    global _status_version
    with _status_condition:
        _status_version += 1
        _status_condition.notify_all()


def get_status_version() -> int:
    """Return the current status version counter (for SSE change detection)."""
    return _status_version


def wait_for_status_change(last_version: int, timeout: float = 15) -> tuple[bool, int]:
    """Block until status version changes or *timeout* seconds elapse.

    Returns ``(changed, current_version)``.
    """
    with _status_condition:
        changed = _status_condition.wait_for(
            lambda: _status_version != last_version,
            timeout=timeout,
        )
        return changed, _status_version


logger = logging.getLogger(__name__)

# Active SendspinClient instances. Mutated in-place so all existing
# references (imported via `from state import clients`) stay valid.
clients: list[Any] = []
clients_lock = threading.Lock()


def set_clients(new_clients: list[Any]) -> None:
    """Replace active client list in-place (keeps existing references valid)."""
    with clients_lock:
        clients.clear()
        clients.extend(new_clients if new_clients else [])
    logger.info("Client references updated: %s client(s)", len(clients))


def get_clients_snapshot() -> list[Any]:
    """Return a snapshot copy of the active clients list (thread-safe)."""
    with clients_lock:
        return list(clients)


# ---------------------------------------------------------------------------
# Disabled devices — metadata for devices with enabled=false in config.
# Not active (no client/BT/PA), but shown in UI for re-enabling.
# ---------------------------------------------------------------------------
_disabled_devices: list[dict] = []
_disabled_devices_lock = threading.Lock()


def set_disabled_devices(devices: list[dict]) -> None:
    """Store disabled device metadata (called from main() at startup)."""
    with _disabled_devices_lock:
        _disabled_devices.clear()
        _disabled_devices.extend(devices)
    logger.info("Disabled devices registered: %d", len(devices))


def get_disabled_devices() -> list[dict]:
    """Return a copy of the disabled devices list (thread-safe)."""
    with _disabled_devices_lock:
        return list(_disabled_devices)


# ---------------------------------------------------------------------------
# Per-device event history — in-memory ring buffer for diagnostics/read models
# ---------------------------------------------------------------------------
_device_events: dict[str, deque[dict[str, Any]]] = {}
_device_events_lock = threading.Lock()
_DEVICE_EVENT_LIMIT = 25
_internal_event_publisher = InternalEventPublisher()


def _store_device_event(
    device_id: str,
    event_type: str,
    *,
    level: str = "info",
    message: str | None = None,
    details: dict[str, Any] | None = None,
    at: str | None = None,
) -> dict[str, Any] | None:
    """Append a structured event directly to the per-device ring buffer."""
    if not device_id or not event_type:
        return None

    event = {
        "event_type": event_type,
        "level": level,
        "message": message or "",
        "details": dict(details or {}),
        "at": at or datetime.now(tz=timezone.utc).isoformat(),
    }
    with _device_events_lock:
        bucket = _device_events.setdefault(device_id, deque(maxlen=_DEVICE_EVENT_LIMIT))
        bucket.append(event)
    return dict(event)


def _persist_internal_device_event(event: InternalEvent) -> None:
    """Default subscriber: persist published device events into the ring buffer."""
    if event.category != "device_event":
        return
    payload = dict(event.payload)
    _store_device_event(
        event.subject_id,
        str(payload.get("event_type") or ""),
        level=str(payload.get("level") or "info"),
        message=str(payload.get("message") or ""),
        details=payload.get("details") if isinstance(payload.get("details"), dict) else None,
        at=event.at,
    )


_internal_event_publisher.subscribe(_persist_internal_device_event)


def publish_internal_event(
    *,
    event_type: str,
    category: str,
    subject_id: str,
    payload: dict[str, Any] | None = None,
) -> InternalEvent | None:
    """Publish a structured internal runtime event to all subscribers."""
    return _internal_event_publisher.publish(
        event_type=event_type,
        category=category,
        subject_id=subject_id,
        payload=payload,
    )


def publish_device_event(
    device_id: str,
    event_type: str,
    *,
    level: str = "info",
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Publish a per-device operational event through the internal event bus."""
    event = publish_internal_event(
        event_type="device.event.recorded",
        category="device_event",
        subject_id=device_id,
        payload={
            "event_type": event_type,
            "level": level,
            "message": message or "",
            "details": dict(details or {}),
        },
    )
    if event is None:
        return None
    return {
        "event_type": event_type,
        "level": level,
        "message": message or "",
        "details": dict(details or {}),
        "at": event.at,
    }


def record_device_event(
    device_id: str,
    event_type: str,
    *,
    level: str = "info",
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Append a structured event to the per-device ring buffer."""
    return _store_device_event(
        device_id,
        event_type,
        level=level,
        message=message,
        details=details,
    )


def get_device_events(device_id: str, limit: int | None = None) -> list[dict[str, Any]]:
    """Return device events ordered newest-first."""
    if not device_id:
        return []
    with _device_events_lock:
        events = list(_device_events.get(device_id, ()))
    events.reverse()
    if limit is not None:
        return events[:limit]
    return events


def clear_device_events(device_id: str | None = None) -> None:
    """Clear device event history for one device or for all devices."""
    with _device_events_lock:
        if device_id:
            _device_events.pop(device_id, None)
            return
        _device_events.clear()


# ---------------------------------------------------------------------------
# Adapter name cache — populated from config.json on first use, invalidated on save
# ---------------------------------------------------------------------------
_adapter_name_cache: dict[str, str] = {}
_adapter_cache_ready = threading.Event()
_adapter_cache_lock = threading.Lock()


def load_adapter_name_cache() -> None:
    """Load adapter friendly names from config.json into the in-memory cache.

    Called either standalone (already under ``_adapter_cache_lock``) or
    from ``get_adapter_name`` which acquires the lock first.
    ``_adapter_cache_lock`` is **not** reentrant, so this function must
    NOT acquire it itself.
    """
    global _adapter_name_cache
    try:
        with open(_config_file) as _f:
            _cfg = json.load(_f)
        _adapter_name_cache = {
            a.get("mac", a.get("id", "")).upper(): a.get("name", "")
            for a in _cfg.get("BLUETOOTH_ADAPTERS", [])
            if a.get("mac") or a.get("id")
        }
    except (OSError, json.JSONDecodeError, ValueError) as _exc:
        _adapter_name_cache = {}
        logger.debug("Could not load adapter name cache: %s", _exc)
    _adapter_cache_ready.set()


def get_adapter_name(mac_upper: str) -> str | None:
    """Return adapter friendly name for the given MAC (uppercase), loading cache if needed."""
    if not _adapter_cache_ready.is_set():
        with _adapter_cache_lock:
            if not _adapter_cache_ready.is_set():  # double-checked locking
                load_adapter_name_cache()
    return _adapter_name_cache.get(mac_upper)


# ---------------------------------------------------------------------------
# Generic async job store — keyed by UUID, TTL ~2 min
# ---------------------------------------------------------------------------

_async_jobs: dict[str, dict] = {}
_async_jobs_lock = threading.Lock()
_ASYNC_JOB_TTL = 120  # seconds


def _evict_expired_jobs(store: dict[str, dict], ttl: int) -> None:
    """Remove expired jobs from *store* based on their ``created`` timestamp."""
    now = _time.time()
    expired = [jid for jid, job in store.items() if now - job.get("created", 0) > ttl]
    for jid in expired:
        del store[jid]


def create_async_job(job_id: str, job_type: str) -> None:
    """Register a new in-progress generic async job."""
    with _async_jobs_lock:
        _evict_expired_jobs(_async_jobs, _ASYNC_JOB_TTL)
        _async_jobs[job_id] = {"status": "running", "created": _time.time(), "job_type": job_type}


def finish_async_job(job_id: str, result: dict) -> None:
    """Mark a generic async job as done with its result payload."""
    with _async_jobs_lock:
        if job_id in _async_jobs:
            _async_jobs[job_id]["status"] = "done"
            _async_jobs[job_id].update(result)


def get_async_job(job_id: str) -> dict | None:
    """Return a shallow copy of the generic async job dict or None if missing."""
    with _async_jobs_lock:
        _evict_expired_jobs(_async_jobs, _ASYNC_JOB_TTL)
        job = _async_jobs.get(job_id)
        return dict(job) if job else None


# ---------------------------------------------------------------------------
# BT scan job store — keyed by UUID, TTL ~2 min
# ---------------------------------------------------------------------------

_scan_jobs: dict[str, dict] = {}
_scan_jobs_lock = threading.Lock()
_SCAN_JOB_TTL = 120  # seconds


def create_scan_job(job_id: str) -> None:
    """Register a new in-progress scan job."""
    with _scan_jobs_lock:
        _evict_expired_jobs(_scan_jobs, _SCAN_JOB_TTL)
        _scan_jobs[job_id] = {"status": "running", "created": _time.time()}


def finish_scan_job(job_id: str, result: dict) -> None:
    """Mark a scan job as done with its result."""
    with _scan_jobs_lock:
        if job_id in _scan_jobs:
            _scan_jobs[job_id]["status"] = "done"
            _scan_jobs[job_id].update(result)


def is_scan_running() -> bool:
    """Return True if any BT scan job is currently running."""
    with _scan_jobs_lock:
        return any(j["status"] == "running" for j in _scan_jobs.values())


def get_scan_job(job_id: str) -> dict | None:
    """Return a shallow copy of the job dict or None if not found."""
    with _scan_jobs_lock:
        _evict_expired_jobs(_scan_jobs, _SCAN_JOB_TTL)
        job = _scan_jobs.get(job_id)
        return dict(job) if job else None


# ---------------------------------------------------------------------------
# MA syncgroup cache — player_id → {"id": syncgroup_id, "name": group_name}
# Populated at startup if MA_API_URL + MA_API_TOKEN are configured.
# ---------------------------------------------------------------------------

_ma_groups: dict[str, dict] = {}
_ma_groups_lock = threading.Lock()
_ma_all_groups: list[dict] = []  # full list: [{id, name, members: [{id, name, state, volume, available}]}]
_ma_api_url: str = ""
_ma_api_token: str = ""


def set_ma_groups(mapping: dict[str, dict], all_groups: list[dict] | None = None) -> None:
    """Store the MA player_id → syncgroup mapping and full group list discovered from MA API."""
    with _ma_groups_lock:
        _ma_groups.clear()
        _ma_groups.update(mapping)
        if all_groups is not None:
            _ma_all_groups.clear()
            _ma_all_groups.extend(all_groups)
    logger.info("MA syncgroup cache updated: %d mapped, %d total group(s)", len(mapping), len(_ma_all_groups))


_ma_api_lock = threading.Lock()


def set_ma_api_credentials(url: str, token: str) -> None:
    """Store resolved MA API URL and token for use across modules."""
    global _ma_api_url, _ma_api_token
    with _ma_api_lock:
        _ma_api_url = url
        _ma_api_token = token


def get_ma_api_credentials() -> tuple[str, str]:
    """Return (ma_api_url, ma_api_token)."""
    with _ma_api_lock:
        return _ma_api_url, _ma_api_token


def get_ma_group_for_player_id(player_id: str) -> dict | None:
    """Return MA syncgroup info {id, name} for the given bridge player_id, or None."""
    if not player_id:
        return None
    with _ma_groups_lock:
        return _ma_groups.get(player_id)


# Legacy alias kept for compatibility
get_ma_group_for_player = get_ma_group_for_player_id


def get_ma_group_by_id(syncgroup_id: str) -> dict | None:
    """Return MA syncgroup dict from all_groups by its syncgroup player_id, or None."""
    if not syncgroup_id:
        return None
    with _ma_groups_lock:
        return next((g for g in _ma_all_groups if g["id"] == syncgroup_id), None)


def get_ma_groups() -> list[dict]:
    """Return all MA syncgroup players with their members."""
    with _ma_groups_lock:
        return list(_ma_all_groups)


# ---------------------------------------------------------------------------
# MA connection state and now-playing cache
# ---------------------------------------------------------------------------

_ma_connected: bool = False
_ma_connected_lock = threading.Lock()
_ma_server_version: str = ""
_ma_now_playing: dict[str, dict] = {}  # keyed by syncgroup_id
_ma_now_playing_lock = threading.Lock()
_MA_SYNC_META_KEY = "_sync_meta"


def _copy_ma_snapshot(data: dict | None) -> dict:
    return copy.deepcopy(data or {})


def _ensure_ma_sync_meta(data: dict | None) -> dict:
    snapshot = data if isinstance(data, dict) else {}
    meta = snapshot.get(_MA_SYNC_META_KEY)
    if not isinstance(meta, dict):
        meta = {}
    pending_ops = meta.get("pending_ops")
    if not isinstance(pending_ops, list):
        pending_ops = []
    normalized = {
        "pending": bool(meta.get("pending", bool(pending_ops))),
        "pending_ops": pending_ops,
        "stale": bool(meta.get("stale", False)),
        "last_event_at": meta.get("last_event_at"),
        "last_accepted_at": meta.get("last_accepted_at"),
        "last_confirmed_at": meta.get("last_confirmed_at"),
        "last_command_at": meta.get("last_command_at"),
        "last_ack_latency_ms": meta.get("last_ack_latency_ms"),
        "last_error": meta.get("last_error"),
        "source": meta.get("source", "unknown"),
    }
    snapshot[_MA_SYNC_META_KEY] = normalized
    return normalized


def _with_ma_sync_meta(snapshot: dict, *, previous: dict | None = None, source: str = "unknown") -> dict:
    result = _copy_ma_snapshot(snapshot)
    previous_meta = _ensure_ma_sync_meta(_copy_ma_snapshot(previous or {}))
    meta = _ensure_ma_sync_meta(result)
    meta["source"] = source
    if meta.get("last_command_at") is None:
        meta["last_command_at"] = previous_meta.get("last_command_at")
    if meta.get("last_accepted_at") is None:
        meta["last_accepted_at"] = previous_meta.get("last_accepted_at")
    if meta.get("last_ack_latency_ms") is None:
        meta["last_ack_latency_ms"] = previous_meta.get("last_ack_latency_ms")
    if meta.get("last_error") is None:
        meta["last_error"] = previous_meta.get("last_error")
    return result


def _compose_pending_flag(meta: dict) -> None:
    meta["pending"] = bool(meta.get("pending_ops"))


def is_ma_connected() -> bool:
    """Return True if MA API integration is active and discovery succeeded."""
    with _ma_connected_lock:
        return _ma_connected


def set_ma_connected(value: bool) -> None:
    """Set MA connection state. Called by MaMonitor on connect/disconnect."""
    global _ma_connected
    with _ma_connected_lock:
        _ma_connected = value


def get_ma_server_version() -> str:
    """Return cached MA server version string (e.g. '2.7.10')."""
    with _ma_connected_lock:
        return _ma_server_version


def set_ma_server_version(version: str) -> None:
    """Cache MA server version discovered during WS handshake."""
    global _ma_server_version
    with _ma_connected_lock:
        _ma_server_version = version


def get_ma_now_playing_for_group(syncgroup_id: str) -> dict:
    """Return now-playing dict for a specific MA syncgroup_id."""
    with _ma_now_playing_lock:
        return _copy_ma_snapshot(_ma_now_playing.get(syncgroup_id, {}))


def set_ma_now_playing_for_group(syncgroup_id: str, data: dict) -> None:
    """Update now-playing for a specific syncgroup. Triggers SSE notification."""
    now = _time.time()
    with _ma_now_playing_lock:
        previous = _ma_now_playing.get(syncgroup_id, {})
        snapshot = _with_ma_sync_meta(data, previous=previous, source="direct")
        snapshot["connected"] = bool(snapshot.get("connected", True))
        meta = _ensure_ma_sync_meta(snapshot)
        meta["pending_ops"] = []
        meta["stale"] = False
        meta["last_event_at"] = now
        meta["last_confirmed_at"] = now
        meta["last_accepted_at"] = None
        meta["last_ack_latency_ms"] = None
        meta["last_error"] = None
        _compose_pending_flag(meta)
        _ma_now_playing[syncgroup_id] = snapshot
    notify_status_changed()


def replace_ma_now_playing(new_data: dict[str, dict]) -> None:
    """Atomically replace all now-playing entries. Removes stale keys."""
    now = _time.time()
    with _ma_now_playing_lock:
        fresh: dict[str, dict] = {}
        for syncgroup_id, data in new_data.items():
            previous = _ma_now_playing.get(syncgroup_id, {})
            snapshot = _with_ma_sync_meta(data, previous=previous, source="monitor")
            snapshot["connected"] = bool(snapshot.get("connected", True))
            meta = _ensure_ma_sync_meta(snapshot)
            meta["pending_ops"] = []
            meta["stale"] = False
            meta["last_event_at"] = now
            meta["last_confirmed_at"] = now
            meta["last_accepted_at"] = None
            meta["last_ack_latency_ms"] = None
            meta["last_error"] = None
            _compose_pending_flag(meta)
            fresh[syncgroup_id] = snapshot
        _ma_now_playing.clear()
        _ma_now_playing.update(fresh)
    notify_status_changed()


def clear_ma_now_playing() -> None:
    """Clear all now-playing state (e.g. on MA disconnect)."""
    with _ma_now_playing_lock:
        _ma_now_playing.clear()
    notify_status_changed()


def get_ma_now_playing() -> dict:
    """Legacy: return first group's now-playing or empty dict."""
    with _ma_now_playing_lock:
        if _ma_now_playing:
            return _copy_ma_snapshot(next(iter(_ma_now_playing.values())))
        return {}


def set_ma_now_playing(data: dict) -> None:
    """Legacy: update now-playing for the first/only group. Triggers SSE notification."""
    syncgroup_id = data.get("syncgroup_id", "__default__")
    set_ma_now_playing_for_group(syncgroup_id, data)


def apply_ma_now_playing_prediction(
    syncgroup_id: str,
    patch: dict,
    *,
    op_id: str,
    action: str,
    value=None,
    accepted_at: float | None = None,
    ack_latency_ms: int | None = None,
) -> dict:
    """Apply a predicted MA patch and mark it pending in the shared cache."""
    now = _time.time()
    with _ma_now_playing_lock:
        previous = _ma_now_playing.get(syncgroup_id, {"syncgroup_id": syncgroup_id})
        snapshot = _with_ma_sync_meta(previous, previous=previous, source="predicted")
        snapshot.update(_copy_ma_snapshot(patch))
        snapshot["syncgroup_id"] = snapshot.get("syncgroup_id") or syncgroup_id
        snapshot["connected"] = bool(snapshot.get("connected", True))
        meta = _ensure_ma_sync_meta(snapshot)
        meta["last_command_at"] = now
        meta["last_accepted_at"] = accepted_at or now
        meta["last_ack_latency_ms"] = ack_latency_ms
        meta["last_error"] = None
        meta["stale"] = False
        pending_ops = [op for op in meta["pending_ops"] if op.get("op_id") != op_id]
        pending_ops.append(
            {
                "op_id": op_id,
                "action": action,
                "value": value,
                "created_at": now,
            }
        )
        meta["pending_ops"] = pending_ops
        _compose_pending_flag(meta)
        _ma_now_playing[syncgroup_id] = snapshot
        result = _copy_ma_snapshot(snapshot)
    notify_status_changed()
    return result


def fail_ma_pending_op(syncgroup_id: str, op_id: str, error: str) -> dict:
    """Fail a pending MA operation while preserving the last known snapshot."""
    now = _time.time()
    with _ma_now_playing_lock:
        snapshot = _copy_ma_snapshot(_ma_now_playing.get(syncgroup_id, {}))
        if not snapshot:
            return {}
        meta = _ensure_ma_sync_meta(snapshot)
        meta["pending_ops"] = [op for op in meta["pending_ops"] if op.get("op_id") != op_id]
        meta["last_error"] = error
        meta["last_event_at"] = now
        meta["last_ack_latency_ms"] = None
        _compose_pending_flag(meta)
        _ma_now_playing[syncgroup_id] = snapshot
        result = _copy_ma_snapshot(snapshot)
    notify_status_changed()
    return result


def mark_ma_now_playing_stale(error: str | None = None) -> None:
    """Preserve last confirmed MA state while marking all entries stale."""
    now = _time.time()
    with _ma_now_playing_lock:
        for syncgroup_id, data in list(_ma_now_playing.items()):
            snapshot = _copy_ma_snapshot(data)
            snapshot["connected"] = False
            meta = _ensure_ma_sync_meta(snapshot)
            meta["stale"] = True
            meta["source"] = "disconnect"
            meta["last_event_at"] = now
            if error:
                meta["last_error"] = error
            _compose_pending_flag(meta)
            _ma_now_playing[syncgroup_id] = snapshot
    notify_status_changed()


# ---------------------------------------------------------------------------
# Update availability
# ---------------------------------------------------------------------------

_update_available: dict | None = None
_update_available_lock = threading.Lock()


def get_update_available() -> dict | None:
    """Return update info dict if a newer version is available, else None."""
    with _update_available_lock:
        return dict(_update_available) if _update_available else None


def set_update_available(data: dict | None) -> None:
    """Store update availability info. Called by update_checker."""
    global _update_available
    with _update_available_lock:
        _update_available = dict(data) if data else None
