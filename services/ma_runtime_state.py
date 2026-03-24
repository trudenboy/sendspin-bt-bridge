"""Music Assistant runtime state owner and helpers."""

from __future__ import annotations

import copy
import logging
import threading
import time as _time
from typing import Any

from services.bridge_runtime_state import notify_status_changed

logger = logging.getLogger(__name__)

_ma_groups: dict[str, dict[str, Any]] = {}
_ma_groups_lock = threading.Lock()
_ma_all_groups: list[dict[str, Any]] = []
_ma_api_url = ""
_ma_api_token = ""
_ma_api_lock = threading.Lock()
_ma_connected = False
_ma_connected_lock = threading.Lock()
_ma_server_version = ""
_ma_now_playing: dict[str, dict[str, Any]] = {}
_ma_now_playing_lock = threading.Lock()
_MA_SYNC_META_KEY = "_sync_meta"


def set_ma_groups(mapping: dict[str, dict[str, Any]], all_groups: list[dict[str, Any]] | None = None) -> None:
    """Store the MA player_id → syncgroup mapping and full group list."""
    with _ma_groups_lock:
        changed = _ma_groups != mapping
        if all_groups is not None:
            changed = changed or _ma_all_groups != all_groups
        _ma_groups.clear()
        _ma_groups.update(mapping)
        if all_groups is not None:
            _ma_all_groups.clear()
            _ma_all_groups.extend(copy.deepcopy(all_groups))
        total_groups = len(_ma_all_groups)
    log_fn = logger.info if changed else logger.debug
    status = "updated" if changed else "unchanged"
    log_fn("MA syncgroup cache %s: %d mapped, %d total group(s)", status, len(mapping), total_groups)


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


def get_ma_group_for_player_id(player_id: str) -> dict[str, Any] | None:
    """Return MA syncgroup info for the given bridge player_id."""
    if not player_id:
        return None
    with _ma_groups_lock:
        group = _ma_groups.get(player_id)
        return copy.deepcopy(group) if group else None


def get_ma_group_for_player(player_id: str) -> dict[str, Any] | None:
    """Compatibility alias for player → group lookup."""
    return get_ma_group_for_player_id(player_id)


def get_ma_group_by_id(syncgroup_id: str) -> dict[str, Any] | None:
    """Return a full Music Assistant group entry by syncgroup id."""
    if not syncgroup_id:
        return None
    with _ma_groups_lock:
        group = next((group for group in _ma_all_groups if group["id"] == syncgroup_id), None)
        return copy.deepcopy(group) if group else None


def get_ma_groups() -> list[dict[str, Any]]:
    """Return all cached Music Assistant syncgroup players with members."""
    with _ma_groups_lock:
        return copy.deepcopy(_ma_all_groups)


def _copy_ma_snapshot(data: dict[str, Any] | None) -> dict[str, Any]:
    return copy.deepcopy(data or {})


def _ensure_ma_sync_meta(data: dict[str, Any] | None) -> dict[str, Any]:
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


def _with_ma_sync_meta(
    snapshot: dict[str, Any],
    *,
    previous: dict[str, Any] | None = None,
    source: str = "unknown",
) -> dict[str, Any]:
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


def _compose_pending_flag(meta: dict[str, Any]) -> None:
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
    """Return cached MA server version string."""
    with _ma_connected_lock:
        return _ma_server_version


def set_ma_server_version(version: str) -> None:
    """Cache MA server version discovered during WS handshake."""
    global _ma_server_version
    with _ma_connected_lock:
        _ma_server_version = version


def get_ma_now_playing_for_group(syncgroup_id: str) -> dict[str, Any]:
    """Return now-playing dict for a specific MA syncgroup id."""
    with _ma_now_playing_lock:
        return _copy_ma_snapshot(_ma_now_playing.get(syncgroup_id, {}))


def set_ma_now_playing_for_group(syncgroup_id: str, data: dict[str, Any]) -> None:
    """Update now-playing for a specific syncgroup and notify listeners."""
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


def replace_ma_now_playing(new_data: dict[str, dict[str, Any]]) -> None:
    """Atomically replace all now-playing entries."""
    now = _time.time()
    with _ma_now_playing_lock:
        fresh: dict[str, dict[str, Any]] = {}
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
    """Clear all now-playing state."""
    with _ma_now_playing_lock:
        _ma_now_playing.clear()
    notify_status_changed()


def get_ma_now_playing() -> dict[str, Any]:
    """Legacy: return first group's now-playing or empty dict."""
    with _ma_now_playing_lock:
        if _ma_now_playing:
            return _copy_ma_snapshot(next(iter(_ma_now_playing.values())))
        return {}


def set_ma_now_playing(data: dict[str, Any]) -> None:
    """Compatibility helper that updates the default syncgroup."""
    syncgroup_id = data.get("syncgroup_id", "__default__")
    set_ma_now_playing_for_group(syncgroup_id, data)


def get_ma_now_playing_cache_snapshot() -> dict[str, dict[str, Any]]:
    """Return a full deep copy of the now-playing cache."""
    with _ma_now_playing_lock:
        return copy.deepcopy(_ma_now_playing)


def apply_ma_now_playing_prediction(
    syncgroup_id: str,
    patch: dict[str, Any],
    *,
    op_id: str,
    action: str,
    value: Any = None,
    accepted_at: float | None = None,
    ack_latency_ms: int | None = None,
) -> dict[str, Any]:
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


def fail_ma_pending_op(syncgroup_id: str, op_id: str, error: str) -> dict[str, Any]:
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
