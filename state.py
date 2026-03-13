"""
Shared application state for sendspin-bt-bridge.

Single source of truth for the active SendspinClient list, shared between
web_interface.py (reads for API responses) and sendspin_client.py (writes via set_clients).
"""

from __future__ import annotations

import asyncio  # noqa: TC003 — used at runtime for event loop storage
import json
import logging
import os
import socket
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any

from config import CONFIG_FILE as _config_file

__all__ = [
    "bridge_start_time",
    "clear_ma_now_playing",
    "clients",
    "clients_lock",
    "create_scan_job",
    "finish_scan_job",
    "get_adapter_name",
    "get_bridge_system_info",
    "get_disabled_devices",
    "get_ma_api_credentials",
    "get_ma_group_for_player",
    "get_ma_groups",
    "get_ma_now_playing",
    "get_ma_now_playing_for_group",
    "get_ma_server_version",
    "get_main_loop",
    "get_scan_job",
    "is_ma_connected",
    "is_scan_running",
    "load_adapter_name_cache",
    "notify_status_changed",
    "set_clients",
    "set_disabled_devices",
    "set_ma_api_credentials",
    "set_ma_connected",
    "set_ma_groups",
    "set_ma_now_playing",
    "set_ma_now_playing_for_group",
    "set_ma_server_version",
    "set_main_loop",
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
    from config import BUILD_DATE, VERSION

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
    }


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
# BT scan job store — keyed by UUID, TTL ~2 min
# ---------------------------------------------------------------------------

_scan_jobs: dict[str, dict] = {}
_scan_jobs_lock = threading.Lock()
_SCAN_JOB_TTL = 120  # seconds


def create_scan_job(job_id: str) -> None:
    """Register a new in-progress scan job."""
    with _scan_jobs_lock:
        # Evict expired jobs before adding new one
        now = _time.time()
        expired = [jid for jid, j in _scan_jobs.items() if now - j.get("created", 0) > _SCAN_JOB_TTL]
        for jid in expired:
            del _scan_jobs[jid]
        _scan_jobs[job_id] = {"status": "running", "created": now}


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
    """Return the job dict or None if not found."""
    with _scan_jobs_lock:
        return _scan_jobs.get(job_id)


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
        return dict(_ma_now_playing.get(syncgroup_id, {}))


def set_ma_now_playing_for_group(syncgroup_id: str, data: dict) -> None:
    """Update now-playing for a specific syncgroup. Triggers SSE notification."""
    with _ma_now_playing_lock:
        _ma_now_playing[syncgroup_id] = data
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
            return dict(next(iter(_ma_now_playing.values())))
        return {}


def set_ma_now_playing(data: dict) -> None:
    """Legacy: update now-playing for the first/only group. Triggers SSE notification."""
    syncgroup_id = data.get("syncgroup_id", "__default__")
    with _ma_now_playing_lock:
        _ma_now_playing[syncgroup_id] = data
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
