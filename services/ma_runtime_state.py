"""Route-facing Music Assistant runtime helpers backed by shared state."""

from __future__ import annotations

import copy
from typing import Any

import state


def get_ma_api_credentials() -> tuple[str, str]:
    """Return the resolved Music Assistant API credentials."""
    return state.get_ma_api_credentials()


def set_ma_api_credentials(url: str, token: str) -> None:
    """Store resolved Music Assistant API credentials."""
    state.set_ma_api_credentials(url, token)


def get_ma_group_for_player_id(player_id: str) -> dict[str, Any] | None:
    """Return group info for the given bridge player."""
    return state.get_ma_group_for_player_id(player_id)


def get_ma_group_for_player(player_id: str) -> dict[str, Any] | None:
    """Compatibility alias for player → group lookup."""
    return state.get_ma_group_for_player(player_id)


def get_ma_group_by_id(syncgroup_id: str) -> dict[str, Any] | None:
    """Return a full Music Assistant group entry by syncgroup id."""
    return state.get_ma_group_by_id(syncgroup_id)


def get_ma_groups() -> list[dict[str, Any]]:
    """Return all cached Music Assistant groups."""
    return state.get_ma_groups()


def set_ma_groups(mapping: dict[str, dict[str, Any]], all_groups: list[dict[str, Any]] | None = None) -> None:
    """Store the cached Music Assistant groups."""
    state.set_ma_groups(mapping, all_groups)


def is_ma_connected() -> bool:
    """Return True if the Music Assistant integration is active."""
    return state.is_ma_connected()


def get_ma_server_version() -> str:
    """Return the cached Music Assistant server version."""
    return state.get_ma_server_version()


def get_ma_now_playing() -> dict[str, Any]:
    """Return the legacy shared now-playing snapshot."""
    return state.get_ma_now_playing()


def get_ma_now_playing_for_group(syncgroup_id: str) -> dict[str, Any]:
    """Return the now-playing snapshot for one syncgroup."""
    return state.get_ma_now_playing_for_group(syncgroup_id)


def get_ma_now_playing_cache_snapshot() -> dict[str, dict[str, Any]]:
    """Return a deep copy of the full now-playing cache for debug endpoints."""
    with state._ma_now_playing_lock:
        return copy.deepcopy(state._ma_now_playing)


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
    """Apply a predicted MA patch in the shared cache."""
    return state.apply_ma_now_playing_prediction(
        syncgroup_id,
        patch,
        op_id=op_id,
        action=action,
        value=value,
        accepted_at=accepted_at,
        ack_latency_ms=ack_latency_ms,
    )


def fail_ma_pending_op(syncgroup_id: str, op_id: str, error: str) -> dict[str, Any]:
    """Mark a pending MA operation as failed."""
    return state.fail_ma_pending_op(syncgroup_id, op_id, error)
