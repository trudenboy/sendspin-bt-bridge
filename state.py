"""Shared compatibility state for sendspin-bt-bridge runtime and routes."""

from __future__ import annotations

import logging
import os
import socket
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from sendspin_bridge.services.bluetooth import adapter_names as _adapter_names
from sendspin_bridge.services.bluetooth.device_registry import (
    get_active_clients_snapshot as _get_registry_active_clients_snapshot,
)
from sendspin_bridge.services.bluetooth.device_registry import (
    get_device_registry_snapshot as _get_device_registry_snapshot,
)
from sendspin_bridge.services.bluetooth.device_registry import (
    get_disabled_devices_snapshot as _get_registry_disabled_devices_snapshot,
)
from sendspin_bridge.services.bluetooth.device_registry import (
    register_registry_listener as _register_registry_listener,
)
from sendspin_bridge.services.bluetooth.device_registry import (
    set_active_clients as _set_registry_active_clients,
)
from sendspin_bridge.services.bluetooth.device_registry import (
    set_disabled_devices as _set_registry_disabled_devices,
)
from sendspin_bridge.services.diagnostics.event_hooks import dispatch_internal_event_to_hooks
from sendspin_bridge.services.diagnostics.internal_events import (
    DeviceEventType,
    InternalEvent,
    InternalEventPublisher,
    normalize_device_event,
)
from sendspin_bridge.services.lifecycle import async_job_state as _async_job_state
from sendspin_bridge.services.lifecycle import bridge_runtime_state as _bridge_runtime_state
from sendspin_bridge.services.music_assistant import ma_runtime_state as _ma_runtime_state

if TYPE_CHECKING:
    import asyncio

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
    "get_duplicate_device_warnings",
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
    "publish_bridge_event",
    "publish_device_event",
    "publish_internal_event",
    "record_device_event",
    "replace_ma_now_playing",
    "reset_startup_progress",
    "set_clients",
    "set_disabled_devices",
    "set_duplicate_device_warnings",
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
bridge_start_time = _bridge_runtime_state.bridge_start_time


def _detect_runtime_type() -> str:
    """Detect whether we're running as HA addon, Docker, or LXC/bare metal."""
    if os.environ.get("SUPERVISOR_TOKEN"):
        return "ha-addon"
    if os.path.exists("/.dockerenv"):
        return "docker"
    return "lxc"


def get_bridge_system_info() -> dict:
    """Return hostname, IP, uptime and version — always available."""
    from sendspin_bridge.config import BUILD_DATE, CONFIG_SCHEMA_VERSION, get_runtime_version
    from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION

    uptime = datetime.now(tz=timezone.utc) - bridge_start_time
    from sendspin_bridge.config import get_local_ip

    ip = get_local_ip()
    return {
        "version": get_runtime_version(),
        "build_date": BUILD_DATE,
        "hostname": socket.gethostname(),
        "ip_address": ip,
        "uptime": str(timedelta(seconds=int(uptime.total_seconds()))),
        "runtime": _detect_runtime_type(),
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "ipc_protocol_version": IPC_PROTOCOL_VERSION,
    }


def get_startup_progress() -> dict[str, Any]:
    """Return the current bridge startup progress snapshot."""
    return _bridge_runtime_state.get_startup_progress()


def reset_startup_progress(total_steps: int = 0, *, message: str = "Startup initiated") -> dict[str, Any]:
    """Reset startup progress for a new bridge boot sequence."""
    return _bridge_runtime_state.reset_startup_progress(total_steps, message=message)


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
    return _bridge_runtime_state.update_startup_progress(
        phase,
        message,
        current_step=current_step,
        total_steps=total_steps,
        status=status,
        details=details,
    )


def complete_startup_progress(
    message: str = "Startup complete",
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Mark startup progress as ready."""
    return _bridge_runtime_state.complete_startup_progress(message, details=details)


def fail_startup_progress(message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    """Mark startup progress as failed while preserving current phase/step data."""
    return _bridge_runtime_state.fail_startup_progress(message, details=details)


def get_runtime_mode_info() -> dict[str, Any]:
    """Return bridge runtime/mock-mode metadata."""
    return _bridge_runtime_state.get_runtime_mode_info()


def set_runtime_mode_info(data: dict[str, Any] | None = None) -> dict[str, Any]:
    """Replace bridge runtime/mock-mode metadata and notify SSE listeners."""
    return _bridge_runtime_state.set_runtime_mode_info(data)


def set_main_loop(loop: asyncio.AbstractEventLoop | None) -> None:
    """Store the main asyncio event loop for use by Flask/WSGI threads."""
    _bridge_runtime_state.set_main_loop(loop)


def get_main_loop() -> asyncio.AbstractEventLoop | None:
    """Return the main asyncio event loop, or None if not yet set."""
    return _bridge_runtime_state.get_main_loop()


def notify_status_changed() -> None:
    """Signal that at least one client status has changed (wakes SSE listeners).

    Thread-safe: may be called from any thread.
    Notifications are batched within a 100 ms window to prevent SSE storms
    when many devices update simultaneously (e.g., mass reconnect).
    """
    _bridge_runtime_state.notify_status_changed()


def get_status_version() -> int:
    """Return the current status version counter (for SSE change detection)."""
    return _bridge_runtime_state.get_status_version()


def wait_for_status_change(last_version: int, timeout: float = 15) -> tuple[bool, int]:
    """Block until status version changes or *timeout* seconds elapse.

    Returns ``(changed, current_version)``.
    """
    return _bridge_runtime_state.wait_for_status_change(last_version, timeout=timeout)


logger = logging.getLogger(__name__)

# Active SendspinClient instances. Mutated in-place so all existing
# references (imported via `from state import clients`) stay valid.
clients: list[Any] = []
clients_lock = threading.Lock()
_disabled_devices: list[dict] = []
_disabled_devices_lock = threading.Lock()


def _sync_legacy_registry_aliases(snapshot) -> None:
    """Mirror canonical registry state onto legacy module-level aliases."""
    with clients_lock:
        clients[:] = snapshot.active_clients
        with _disabled_devices_lock:
            _disabled_devices[:] = snapshot.disabled_devices


_register_registry_listener(_sync_legacy_registry_aliases)
_sync_legacy_registry_aliases(_get_device_registry_snapshot())


def set_clients(new_clients: list[Any]) -> None:
    """Replace the canonical active client inventory."""
    _set_registry_active_clients(new_clients)
    logger.info("Client references updated: %s client(s)", len(clients))


def get_clients_snapshot() -> list[Any]:
    """Return a snapshot copy of the canonical active client inventory."""
    return _get_registry_active_clients_snapshot()


def set_disabled_devices(devices: list[dict]) -> None:
    """Store disabled device metadata in the canonical registry service."""
    _set_registry_disabled_devices(devices)
    logger.info("Disabled devices registered: %d", len(devices))


def get_disabled_devices() -> list[dict]:
    """Return a copy of the canonical disabled-device inventory."""
    return _get_registry_disabled_devices_snapshot()


def _find_client_for_device_event(device_id: str):
    normalized_id = str(device_id or "").strip()
    if not normalized_id:
        return None
    for client in _get_registry_active_clients_snapshot():
        if str(getattr(client, "player_id", "") or "").strip() == normalized_id:
            return client
        if str(getattr(client, "player_name", "") or "").strip() == normalized_id:
            return client
        bt_mgr = getattr(client, "bt_manager", None)
        if str(getattr(bt_mgr, "mac_address", "") or "").strip().upper() == normalized_id.upper():
            return client
    return None


def _build_device_event_context(device_id: str) -> dict[str, Any]:
    client = _find_client_for_device_event(device_id)
    if client is None:
        return {}
    try:
        from sendspin_bridge.services.lifecycle.status_snapshot import build_device_snapshot

        snapshot = build_device_snapshot(client)
    except Exception as exc:
        logger.debug("Could not build device event context for %s: %s", device_id, exc)
        return {}

    context: dict[str, Any] = {
        "transfer_readiness": dict(getattr(snapshot, "transfer_readiness", {}) or {}),
    }
    if getattr(snapshot, "room_id", None):
        context["room_id"] = snapshot.room_id
    if getattr(snapshot, "room_name", None):
        context["room_name"] = snapshot.room_name
    if getattr(snapshot, "room_source", None):
        context["room_source"] = snapshot.room_source
    if getattr(snapshot, "room_confidence", None):
        context["room_confidence"] = snapshot.room_confidence
    return {key: value for key, value in context.items() if value not in (None, "", {}, [])}


# ---------------------------------------------------------------------------
# Per-device event history — in-memory ring buffer for diagnostics/read models
# ---------------------------------------------------------------------------
_device_events: dict[str, deque[dict[str, Any]]] = {}
_device_events_lock = threading.Lock()
_DEVICE_EVENT_LIMIT = 25
_internal_event_publisher = InternalEventPublisher()


def _store_device_event(
    device_id: str,
    event_type: str | DeviceEventType,
    *,
    level: str = "info",
    message: str | None = None,
    details: dict[str, Any] | None = None,
    at: str | None = None,
) -> dict[str, Any] | None:
    """Append a structured event directly to the per-device ring buffer."""
    if not device_id:
        return None
    normalized = normalize_device_event(event_type, level=level, message=message, details=details)
    if normalized is None:
        return None
    event = dict(normalized)
    event["at"] = at or datetime.now(tz=timezone.utc).isoformat()
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
_internal_event_publisher.subscribe(dispatch_internal_event_to_hooks)


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


def get_internal_event_publisher() -> InternalEventPublisher:
    """Public accessor for subscribers (HA MQTT publisher, custom_component bridge).

    Returns the module-level singleton.  External callers ``subscribe()``
    / ``unsubscribe()`` against this — never construct their own publisher.
    """
    return _internal_event_publisher


def publish_device_event(
    device_id: str,
    event_type: str | DeviceEventType,
    *,
    level: str = "info",
    message: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Publish a per-device operational event through the internal event bus."""
    payload_details = dict(details or {})
    payload_details.update(
        {k: v for k, v in _build_device_event_context(device_id).items() if k not in payload_details}
    )
    normalized = normalize_device_event(event_type, level=level, message=message, details=payload_details)
    if normalized is None:
        return None
    event = publish_internal_event(
        event_type="device.event.recorded",
        category="device_event",
        subject_id=device_id,
        payload=normalized,
    )
    if event is None:
        return None
    return {**normalized, "at": event.at}


def publish_bridge_event(event_type: str, *, payload: dict[str, Any] | None = None) -> InternalEvent | None:
    """Publish a bridge-wide lifecycle/telemetry event through the internal event bus."""
    return publish_internal_event(
        event_type=event_type,
        category="bridge_event",
        subject_id="bridge",
        payload=payload,
    )


def record_device_event(
    device_id: str,
    event_type: str | DeviceEventType,
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
        events = list(reversed(_device_events.get(device_id, ())))
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
def load_adapter_name_cache() -> None:
    """Load adapter friendly names from config into the adapter cache owner."""
    _adapter_names.load_adapter_name_cache()


def get_adapter_name(mac_upper: str) -> str | None:
    """Return adapter friendly name for the given MAC (uppercase)."""
    return _adapter_names.get_adapter_name(mac_upper)


def create_async_job(job_id: str, job_type: str) -> None:
    """Register a new in-progress generic async job."""
    _async_job_state.create_async_job(job_id, job_type)


def finish_async_job(job_id: str, result: dict) -> None:
    """Mark a generic async job as done with its result payload."""
    _async_job_state.finish_async_job(job_id, result)


def get_async_job(job_id: str) -> dict | None:
    """Return a shallow copy of the generic async job dict or None if missing."""
    return _async_job_state.get_async_job(job_id)


def create_scan_job(job_id: str, initial_data: dict | None = None) -> None:
    """Register a new in-progress scan job."""
    _async_job_state.create_scan_job(job_id, initial_data)


def finish_scan_job(job_id: str, result: dict) -> None:
    """Mark a scan job as done with its result."""
    _async_job_state.finish_scan_job(job_id, result)


def is_scan_running() -> bool:
    """Return True if any BT scan job is currently running."""
    return _async_job_state.is_scan_running()


def get_scan_job(job_id: str) -> dict | None:
    """Return a shallow copy of the job dict or None if not found."""
    return _async_job_state.get_scan_job(job_id)


def set_ma_groups(mapping: dict[str, dict], all_groups: list[dict] | None = None) -> None:
    """Store the MA player_id → syncgroup mapping and full group list discovered from MA API."""
    _ma_runtime_state.set_ma_groups(mapping, all_groups)


def set_ma_api_credentials(url: str, token: str) -> None:
    """Store resolved MA API URL and token for use across modules."""
    _ma_runtime_state.set_ma_api_credentials(url, token)


def get_ma_api_credentials() -> tuple[str, str]:
    """Return (ma_api_url, ma_api_token)."""
    return _ma_runtime_state.get_ma_api_credentials()


def get_ma_group_for_player_id(player_id: str) -> dict | None:
    """Return MA syncgroup info {id, name} for the given bridge player_id, or None."""
    return _ma_runtime_state.get_ma_group_for_player_id(player_id)


def get_ma_group_for_player(player_id: str) -> dict | None:
    """Compatibility alias for player → group lookup."""
    return _ma_runtime_state.get_ma_group_for_player(player_id)


def get_ma_group_by_id(syncgroup_id: str) -> dict | None:
    """Return MA syncgroup dict from all_groups by its syncgroup player_id, or None."""
    return _ma_runtime_state.get_ma_group_by_id(syncgroup_id)


def get_ma_groups() -> list[dict]:
    """Return all MA syncgroup players with their members."""
    return _ma_runtime_state.get_ma_groups()


def set_duplicate_device_warnings(warnings: list) -> None:
    """Store cross-bridge duplicate device warnings from startup check."""
    _ma_runtime_state.set_duplicate_device_warnings(warnings)


def get_duplicate_device_warnings() -> list:
    """Return stored duplicate device warnings."""
    return _ma_runtime_state.get_duplicate_device_warnings()


def is_ma_connected() -> bool:
    """Return True if MA API integration is active and discovery succeeded."""
    return _ma_runtime_state.is_ma_connected()


def set_ma_connected(value: bool) -> None:
    """Set MA connection state. Called by MaMonitor on connect/disconnect."""
    _ma_runtime_state.set_ma_connected(value)


def get_ma_server_version() -> str:
    """Return cached MA server version string (e.g. '2.7.10')."""
    return _ma_runtime_state.get_ma_server_version()


def set_ma_server_version(version: str) -> None:
    """Cache MA server version discovered during WS handshake."""
    _ma_runtime_state.set_ma_server_version(version)


def get_ma_now_playing_for_group(syncgroup_id: str) -> dict:
    """Return now-playing dict for a specific MA syncgroup_id."""
    return _ma_runtime_state.get_ma_now_playing_for_group(syncgroup_id)


def set_ma_now_playing_for_group(syncgroup_id: str, data: dict) -> None:
    """Update now-playing for a specific syncgroup. Triggers SSE notification."""
    _ma_runtime_state.set_ma_now_playing_for_group(syncgroup_id, data)


def replace_ma_now_playing(new_data: dict[str, dict]) -> None:
    """Atomically replace all now-playing entries. Removes stale keys."""
    _ma_runtime_state.replace_ma_now_playing(new_data)
    # Check for sync group auto-wake: if any group started playing,
    # wake standby members belonging to that group.
    _check_group_auto_wake(new_data)


def clear_ma_now_playing() -> None:
    """Clear all now-playing state (e.g. on MA disconnect)."""
    _ma_runtime_state.clear_ma_now_playing()


def get_ma_now_playing() -> dict:
    """Legacy: return first group's now-playing or empty dict."""
    return _ma_runtime_state.get_ma_now_playing()


def set_ma_now_playing(data: dict) -> None:
    """Legacy: update now-playing for the first/only group. Triggers SSE notification."""
    _ma_runtime_state.set_ma_now_playing(data)


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
    return _ma_runtime_state.apply_ma_now_playing_prediction(
        syncgroup_id,
        patch,
        op_id=op_id,
        action=action,
        value=value,
        accepted_at=accepted_at,
        ack_latency_ms=ack_latency_ms,
    )


def fail_ma_pending_op(syncgroup_id: str, op_id: str, error: str) -> dict:
    """Fail a pending MA operation while preserving the last known snapshot."""
    return _ma_runtime_state.fail_ma_pending_op(syncgroup_id, op_id, error)


def mark_ma_now_playing_stale(error: str | None = None) -> None:
    """Preserve last confirmed MA state while marking all entries stale."""
    _ma_runtime_state.mark_ma_now_playing_stale(error)


def get_update_available() -> dict | None:
    """Return update info dict if a newer version is available, else None."""
    return _async_job_state.get_update_available()


def set_update_available(data: dict | None) -> None:
    """Store update availability info. Called by update_checker."""
    _async_job_state.set_update_available(data)


# ---------------------------------------------------------------------------
# Sync group auto-wake
# ---------------------------------------------------------------------------


def _check_group_auto_wake(now_playing: dict[str, dict]) -> None:
    """Wake standby devices whose sync group (or solo queue) has started playing.

    Called from ``replace_ma_now_playing()`` after the cache is updated.
    Iterates over all active clients; if a client is in standby and its
    group_id (or player_id for solo players) appears in *now_playing*
    with ``state == "playing"``, schedule a wake on the main asyncio loop.
    """
    import asyncio

    playing_groups: set[str] = set()
    for gid, np in now_playing.items():
        if np.get("state") == "playing":
            playing_groups.add(gid)
    if not playing_groups:
        return

    loop = get_main_loop()
    if not loop or not loop.is_running():
        return

    for client in _get_registry_active_clients_snapshot():
        if not getattr(client, "status", None):
            continue
        if not client.status.get("bt_standby"):
            continue
        lookup_id: str | None = client.status.get("group_id") or getattr(client, "player_id", None)
        if lookup_id and lookup_id in playing_groups:
            logger.info(
                "[%s] Sync group/solo queue %s is playing — auto-waking from standby",
                getattr(client, "player_name", "?"),
                lookup_id,
            )
            asyncio.run_coroutine_threadsafe(client._wake_from_standby(), loop)
