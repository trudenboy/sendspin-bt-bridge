"""Compute reconfiguration actions by diffing two config snapshots.

Given the previous and the newly-persisted ``config.json`` payloads, produce
an ordered list of :class:`ReconfigAction` instances that the runtime can
apply without restarting the whole bridge.  Keeps the classification of
"what needs what" in one pure place so it is easy to test and to extend.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------------------
# Field classification
# ---------------------------------------------------------------------------

# Per-device fields that can be applied via IPC / parent-state update without
# restarting the subprocess.
_DEVICE_HOT_FIELDS: frozenset[str] = frozenset(
    {
        "static_delay_ms",
        "idle_mode",
        "idle_disconnect_minutes",
        "power_save_delay_minutes",
        "keepalive_enabled",
        "keepalive_interval",
        "room_id",
        "room_name",
    }
)

# Per-device fields that require a subprocess warm-restart.
_DEVICE_WARM_FIELDS: frozenset[str] = frozenset(
    {
        "player_name",
        "listen_port",
        "listen_host",
        "preferred_format",
        "adapter",
        "volume_controller",
    }
)

# Global keys that can be pushed to all running clients without a restart.
_GLOBAL_BROADCAST_FIELDS: frozenset[str] = frozenset(
    {
        "LOG_LEVEL",
        "VOLUME_VIA_MA",
        "MUTE_VIA_MA",
        "MA_API_URL",
        "MA_API_TOKEN",
        "HA_AREA_NAME_ASSIST_ENABLED",
        "HA_ADAPTER_AREA_MAP",
        "MA_AUTO_SILENT_AUTH",
        "MA_WEBSOCKET_MONITOR",
        "DUPLICATE_DEVICE_CHECK",
    }
)

# Global keys that require a warm-restart of every active subprocess.
_GLOBAL_RESTART_FIELDS: frozenset[str] = frozenset(
    {
        "SENDSPIN_SERVER",
        "SENDSPIN_PORT",
        "BRIDGE_NAME",
        "PULSE_LATENCY_MSEC",
        "PREFER_SBC_CODEC",
        "BT_CHECK_INTERVAL",
        "BT_MAX_RECONNECT_FAILS",
        "BT_CHURN_THRESHOLD",
        "BT_CHURN_WINDOW",
        "DISABLE_PA_RESCUE_STREAMS",
        "BASE_LISTEN_PORT",
    }
)

# Global keys that genuinely require a bridge process restart (Flask-bound,
# auth secrets, session machinery).
_RESTART_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        "WEB_PORT",
        "AUTH_ENABLED",
        "AUTH_PASSWORD_HASH",
        "SECRET_KEY",
        "SESSION_TIMEOUT_HOURS",
        "BRUTE_FORCE_PROTECTION",
        "BRUTE_FORCE_MAX_ATTEMPTS",
        "BRUTE_FORCE_WINDOW_MINUTES",
        "BRUTE_FORCE_LOCKOUT_MINUTES",
        "TRUSTED_PROXIES",
        "TZ",
        "EXPERIMENTAL_A2DP_SINK_RECOVERY_DANCE",
        "EXPERIMENTAL_PA_MODULE_RELOAD",
    }
)

# Keys that are pure runtime state and never trigger any action.
_IGNORED_FIELDS: frozenset[str] = frozenset(
    {
        "LAST_VOLUMES",
        "LAST_SINKS",
        "CONFIG_SCHEMA_VERSION",
        "MA_AUTH_PROVIDER",
        "MA_USERNAME",
        "MA_TOKEN_INSTANCE_HOSTNAME",
        "MA_TOKEN_LABEL",
        "MA_ACCESS_TOKEN",
        "MA_REFRESH_TOKEN",
        "BLUETOOTH_ADAPTERS",
        "UPDATE_CHANNEL",
        "AUTO_UPDATE",
        "CHECK_UPDATES",
        "SMOOTH_RESTART",
        "STARTUP_BANNER_GRACE_SECONDS",
        "RECOVERY_BANNER_GRACE_SECONDS",
    }
)


# ---------------------------------------------------------------------------
# Action types
# ---------------------------------------------------------------------------


class ActionKind(str, Enum):
    HOT_APPLY = "hot_apply"
    WARM_RESTART = "warm_restart"
    GLOBAL_BROADCAST = "global_broadcast"
    GLOBAL_RESTART = "global_restart"
    RESTART_REQUIRED = "restart_required"
    BT_REMOVE = "bt_remove"
    STOP_CLIENT = "stop_client"
    START_CLIENT = "start_client"


@dataclass
class ReconfigAction:
    """A single unit of work produced by :func:`diff_configs`."""

    kind: ActionKind
    # ``mac`` identifies the device for per-device actions; ``None`` for global.
    mac: str | None = None
    # ``fields`` lists the config keys that motivated the action — useful for
    # UI summaries ("restart because listen_port, preferred_format changed").
    fields: list[str] = field(default_factory=list)
    # ``payload`` carries the target values the executor needs (new device
    # config dict for warm restart, new scalar for hot apply, etc.).
    payload: dict[str, Any] = field(default_factory=dict)
    # ``label`` is a human-readable identifier for the affected entity
    # (player name or global).  Populated so routes don't have to re-resolve.
    label: str = ""

    def to_summary(self) -> dict[str, Any]:
        """Render as a JSON-serialisable summary dict for API responses."""
        return {
            "kind": self.kind.value,
            "mac": self.mac,
            "label": self.label,
            "fields": list(self.fields),
        }


# ---------------------------------------------------------------------------
# Diff entry point
# ---------------------------------------------------------------------------


def _devices_by_mac(config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for dev in config.get("BLUETOOTH_DEVICES", []) or []:
        if not isinstance(dev, dict):
            continue
        mac = dev.get("mac")
        if isinstance(mac, str) and mac:
            result[mac.upper()] = dev
    return result


def _normalize_scalar(value: Any) -> Any:
    """Canonical form for comparing config values.

    JSON round-trips may turn ``None`` into missing keys; empty strings in the
    UI often map to ``None`` in normalised configs.  Treat them as equivalent
    so we don't spuriously flag changes.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return value


def _field_changed(old_dev: dict[str, Any], new_dev: dict[str, Any], key: str) -> bool:
    return _normalize_scalar(old_dev.get(key)) != _normalize_scalar(new_dev.get(key))


def _label_for_device(dev: dict[str, Any]) -> str:
    return str(dev.get("player_name") or dev.get("mac") or "device")


def _diff_device(
    mac: str,
    old_dev: dict[str, Any] | None,
    new_dev: dict[str, Any] | None,
) -> list[ReconfigAction]:
    """Compute actions for a single device MAC's config change."""
    actions: list[ReconfigAction] = []

    old_enabled = bool(old_dev.get("enabled", True)) if old_dev else False
    new_enabled = bool(new_dev.get("enabled", True)) if new_dev else False

    # Device removed entirely.
    if old_dev and not new_dev:
        if old_enabled:
            actions.append(
                ReconfigAction(
                    kind=ActionKind.STOP_CLIENT,
                    mac=mac,
                    fields=["removed"],
                    label=_label_for_device(old_dev),
                )
            )
        return actions

    # Newly added device.
    if new_dev and not old_dev:
        if new_enabled:
            actions.append(
                ReconfigAction(
                    kind=ActionKind.START_CLIENT,
                    mac=mac,
                    fields=["added"],
                    payload={"device": dict(new_dev)},
                    label=_label_for_device(new_dev),
                )
            )
        return actions

    assert old_dev is not None and new_dev is not None  # for type-checker

    # Enable transition.
    if old_enabled and not new_enabled:
        actions.append(
            ReconfigAction(
                kind=ActionKind.STOP_CLIENT,
                mac=mac,
                fields=["enabled"],
                label=_label_for_device(new_dev),
            )
        )
        return actions
    if not old_enabled and new_enabled:
        actions.append(
            ReconfigAction(
                kind=ActionKind.START_CLIENT,
                mac=mac,
                fields=["enabled"],
                payload={"device": dict(new_dev)},
                label=_label_for_device(new_dev),
            )
        )
        return actions

    # Both sides disabled — no action.
    if not new_enabled:
        return actions

    label = _label_for_device(new_dev)

    # Adapter change implies BT stack cleanup before warm restart.
    if _field_changed(old_dev, new_dev, "adapter"):
        actions.append(
            ReconfigAction(
                kind=ActionKind.BT_REMOVE,
                mac=mac,
                fields=["adapter"],
                payload={"old_adapter": old_dev.get("adapter") or ""},
                label=label,
            )
        )

    warm_fields = sorted(f for f in _DEVICE_WARM_FIELDS if _field_changed(old_dev, new_dev, f))
    hot_fields = sorted(f for f in _DEVICE_HOT_FIELDS if _field_changed(old_dev, new_dev, f))

    if warm_fields:
        # Any warm-restart change supersedes hot-apply for the same subprocess
        # because the restart re-reads every field from the new device dict.
        actions.append(
            ReconfigAction(
                kind=ActionKind.WARM_RESTART,
                mac=mac,
                fields=warm_fields,
                payload={"device": dict(new_dev)},
                label=label,
            )
        )
    elif hot_fields:
        # Build the hot-apply payload from the new device snapshot, limited to
        # the fields that actually changed.
        payload = {key: new_dev.get(key) for key in hot_fields}
        actions.append(
            ReconfigAction(
                kind=ActionKind.HOT_APPLY,
                mac=mac,
                fields=hot_fields,
                payload=payload,
                label=label,
            )
        )

    return actions


def _diff_global(old: dict[str, Any], new: dict[str, Any]) -> list[ReconfigAction]:
    """Compute global (non-device) reconfiguration actions."""
    actions: list[ReconfigAction] = []

    def _changed(key: str) -> bool:
        return _normalize_scalar(old.get(key)) != _normalize_scalar(new.get(key))

    broadcast_fields = sorted(f for f in _GLOBAL_BROADCAST_FIELDS if _changed(f))
    if broadcast_fields:
        payload = {key: new.get(key) for key in broadcast_fields}
        actions.append(
            ReconfigAction(
                kind=ActionKind.GLOBAL_BROADCAST,
                mac=None,
                fields=broadcast_fields,
                payload=payload,
                label="global",
            )
        )

    restart_fields = sorted(f for f in _GLOBAL_RESTART_FIELDS if _changed(f))
    if restart_fields:
        actions.append(
            ReconfigAction(
                kind=ActionKind.GLOBAL_RESTART,
                mac=None,
                fields=restart_fields,
                label="global",
            )
        )

    required_fields = sorted(f for f in _RESTART_REQUIRED_FIELDS if _changed(f))
    if required_fields:
        actions.append(
            ReconfigAction(
                kind=ActionKind.RESTART_REQUIRED,
                mac=None,
                fields=required_fields,
                label="global",
            )
        )

    return actions


def diff_configs(old: dict[str, Any] | None, new: dict[str, Any] | None) -> list[ReconfigAction]:
    """Return the ordered list of actions needed to migrate from ``old`` to ``new``.

    Ordering guarantees (required by the orchestrator):

    1. Per-device BT_REMOVE precedes the WARM_RESTART for the same MAC.
    2. Per-device actions are grouped by MAC, in stable insertion order.
    3. Global actions come after per-device actions so any hot-applied global
       field can observe the post-restart subprocesses.
    """
    old = old or {}
    new = new or {}

    actions: list[ReconfigAction] = []

    old_devices = _devices_by_mac(old)
    new_devices = _devices_by_mac(new)

    # Preserve the order of the new config (user-visible order in the UI),
    # append any removed devices last.
    ordered_macs: list[str] = []
    seen: set[str] = set()
    for mac in new_devices:
        ordered_macs.append(mac)
        seen.add(mac)
    for mac in old_devices:
        if mac not in seen:
            ordered_macs.append(mac)

    for mac in ordered_macs:
        actions.extend(_diff_device(mac, old_devices.get(mac), new_devices.get(mac)))

    actions.extend(_diff_global(old, new))

    return actions


# ---------------------------------------------------------------------------
# Convenience introspection helpers (exposed for tests and UI)
# ---------------------------------------------------------------------------


def iter_device_hot_fields() -> frozenset[str]:
    return _DEVICE_HOT_FIELDS


def iter_device_warm_fields() -> frozenset[str]:
    return _DEVICE_WARM_FIELDS


def iter_global_broadcast_fields() -> frozenset[str]:
    return _GLOBAL_BROADCAST_FIELDS


def iter_global_restart_fields() -> frozenset[str]:
    return _GLOBAL_RESTART_FIELDS


def iter_restart_required_fields() -> frozenset[str]:
    return _RESTART_REQUIRED_FIELDS


__all__ = [
    "ActionKind",
    "ReconfigAction",
    "diff_configs",
    "iter_device_hot_fields",
    "iter_device_warm_fields",
    "iter_global_broadcast_fields",
    "iter_global_restart_fields",
    "iter_restart_required_fields",
]
