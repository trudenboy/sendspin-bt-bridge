"""Compute reconfiguration actions by diffing two config snapshots.

Given the previous and the newly-persisted ``config.json`` payloads, produce
an ordered list of :class:`ReconfigAction` instances that the runtime can
apply without restarting the whole bridge.  Keeps the classification of
"what needs what" in one pure place so it is easy to test and to extend.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from sendspin_bridge.config import effective_playout_mode
from sendspin_bridge.config.players import BLUETOOTH, device_entries, player_to_device_entry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Field classification
# ---------------------------------------------------------------------------

# Per-device fields that can be applied via IPC / parent-state update without
# restarting the subprocess.
_DEVICE_HOT_FIELDS: frozenset[str] = frozenset(
    {
        "static_delay_ms",
        "static_delay_source",
        "static_delay_calibrated_at",
        "static_delay_codec",
        "required_lead_time_ms",
        "min_buffer_ms",
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
        # Per-player opt-out of the playout sync engine: the daemon is spawned
        # with another playout mode.
        "playout_sync_enabled",
        # ``backend.sink`` of a non-Bluetooth player: the daemon is spawned
        # with ``PULSE_SINK`` set to it.
        "sink",
    }
)

# Global keys that can be pushed to all running clients without a restart.
_GLOBAL_BROADCAST_FIELDS: frozenset[str] = frozenset(
    {
        "LOG_LEVEL",
        "HA_AREA_NAME_ASSIST_ENABLED",
        "HA_ADAPTER_AREA_MAP",
        "DUPLICATE_DEVICE_CHECK",
    }
)

# Global keys that require a warm-restart of every active subprocess.
_GLOBAL_RESTART_FIELDS: frozenset[str] = frozenset(
    {
        "SENDSPIN_SERVER",
        "SENDSPIN_PORT",
        "SENDSPIN_PAIRING",
        "SENDSPIN_OWN_SERVER_ONLY",
        "BRIDGE_NAME",
        "PULSE_LATENCY_MSEC",
        "PLAYOUT_SYNC",
        "PREFER_SBC_CODEC",
        "BT_CHECK_INTERVAL",
        "BT_MAX_RECONNECT_FAILS",
        "BT_CHURN_THRESHOLD",
        "BT_CHURN_WINDOW",
        "DISABLE_PA_RESCUE_STREAMS",
        "BASE_LISTEN_PORT",
        # Read by each daemon at spawn.
        "COMPENSATE_OUTPUT_LATENCY",
        "PLAYOUT_DIAG",
    }
)

# Global keys that genuinely require a bridge process restart (Flask-bound,
# auth secrets, session machinery).
_RESTART_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {
        # MA connection settings: the broadcast handler doesn't re-init the MA
        # runtime, so these only take effect after a restart.  Classifying them
        # here stops the UI reporting a no-op as "applied live".
        "MA_API_URL",
        "MA_API_TOKEN",
        # Read when a player first starts; a running one has had its turn.
        "PULSE_SINK_RESET_ON_START",
        "MA_AUTO_SILENT_AUTH",
        "MA_WEBSOCKET_MONITOR",
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
        "EXPERIMENTAL_ADAPTER_AUTO_RECOVERY",
        # Decides at start which monitor path owns the HCI socket (R-17).
        "EXPERIMENTAL_AIR_SENSOR",
    }
)

# Keys that are pure runtime state and never trigger any action.
_IGNORED_FIELDS: frozenset[str] = frozenset(
    {
        "PLAYER_STATE",
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
        # Removed in 2.62.0-rc.8: bridge no longer proxies volume / mute
        # through MA — sendspin's PulseVolumeController.start_monitoring
        # pushes external sink changes to MA directly, so the toggles are
        # obsolete.  Listed here so old config.json files don't trigger
        # spurious reconfig actions on save.
        "VOLUME_VIA_MA",
        "MUTE_VIA_MA",
        # v2.65.0 — token list mutated only via /api/auth/tokens routes.
        # Treat like MA_ACCESS_TOKEN: a /api/config save that round-trips
        # the list must not produce any reconfig action.
        "AUTH_TOKENS",
    }
)


# v2.65.0 — HA integration namespace classification.  Nested dict; each
# leaf maps to one of the buckets below.  All produce a single
# HA_INTEGRATION_LIFECYCLE action so the orchestrator can route to a
# dedicated handler (no client subprocess involvement).
_HA_INTEGRATION_LIFECYCLE_PATHS: frozenset[str] = frozenset(
    {
        "enabled",
        "mode",
        "mqtt.broker",
        "mqtt.port",
        "mqtt.username",
        "mqtt.password",
        "mqtt.discovery_prefix",
        "mqtt.tls",
        "mqtt.client_id",
        "rest.advertise_mdns",
        "rest.supervisor_pair",
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
    # v2.65.0 — HA integration subsystem (MQTT publisher / mDNS).  Routes
    # to ``HaIntegrationLifecycle`` instead of touching device subprocesses.
    HA_INTEGRATION_LIFECYCLE = "ha_integration_lifecycle"


@dataclass
class ReconfigAction:
    """A single unit of work produced by :func:`diff_configs`."""

    kind: ActionKind
    # ``mac`` is the Bluetooth MAC of the player a per-device action is for;
    # ``None`` for global actions and for players without a MAC.
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
    # ``player_id`` is the player's id in the config (``players[].id``) — what
    # identifies a player across the two snapshots; ``None`` for global.
    player_id: str | None = None

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


@dataclass(frozen=True)
class _PlayerView:
    """One player of a config snapshot, as the diff sees it."""

    player_id: str
    backend_type: str
    # Bluetooth MAC; ``None`` for other backends.
    mac: str | None
    # Flat, v5-named settings the field classification compares.
    settings: dict[str, Any]
    # What START_CLIENT / WARM_RESTART hand to the runtime: the v5-shaped
    # device entry for a Bluetooth player, the player dict otherwise.
    device: dict[str, Any]
    # ``base_listen_port + port_index`` is the listen port of a player that
    # does not pin ``listen_port``.  See :func:`_players_by_id`.
    port_index: int


def _players_by_id(config: dict[str, Any]) -> dict[str, _PlayerView]:
    """Every player of *config* by id, in list order.

    Port index rule (the ``base_listen_port + index`` fallback): a Bluetooth
    player's index is its position among the Bluetooth players — the index
    the startup path has always used, so their ports do not move when other
    players are added.  Any other player gets the number of Bluetooth players
    plus its position among the non-Bluetooth players, which places it after
    every Bluetooth port.
    """
    raw = config.get("players") if isinstance(config, dict) else None
    players = [p for p in raw if isinstance(p, dict)] if isinstance(raw, list) else []
    bt_entries = device_entries(config)
    bt_count = len(bt_entries)
    # ``device_entries`` yields one entry per Bluetooth player, in list order.
    bt_iter = iter(bt_entries)
    views: dict[str, _PlayerView] = {}
    bt_pos = other_pos = 0
    for player in players:
        if player_to_device_entry(player) is not None:
            entry = next(bt_iter)
            if entry["id"] in views:
                logger.debug("Reconfig diff: ignoring a second player with id %s", entry["id"])
            else:
                views[entry["id"]] = _PlayerView(entry["id"], BLUETOOTH, entry["mac"], entry, entry, bt_pos)
            bt_pos += 1
            continue
        player_id = player.get("id")
        backend = player.get("backend")
        if not isinstance(player_id, str) or not player_id or not isinstance(backend, dict):
            continue
        backend_type = backend.get("type")
        if not isinstance(backend_type, str) or backend_type == BLUETOOTH:
            continue
        settings = {key: value for key, value in player.items() if key not in ("id", "name", "backend")}
        settings["player_name"] = player.get("name")
        settings["sink"] = backend.get("sink")
        if player_id in views:
            logger.debug("Reconfig diff: ignoring a second player with id %s", player_id)
        else:
            views[player_id] = _PlayerView(player_id, backend_type, None, settings, dict(player), bt_count + other_pos)
        other_pos += 1
    return views


def player_port_indexes(config: dict[str, Any]) -> dict[str, int]:
    """Each player's ``base_listen_port + index`` fallback index, by player id.

    The rule of :func:`_players_by_id`, for the startup path to use the same
    ports the live reconfig path does.
    """
    return {player_id: view.port_index for player_id, view in _players_by_id(config).items()}


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
    old: _PlayerView | None,
    new: _PlayerView | None,
    old_cfg: dict[str, Any] | None = None,
    new_cfg: dict[str, Any] | None = None,
) -> list[ReconfigAction]:
    """Compute actions for a single player's config change.

    ``old_cfg``/``new_cfg`` are the whole snapshots, for fields whose effect
    depends on a global key (``playout_sync_enabled`` on ``PLAYOUT_SYNC``).
    """
    actions: list[ReconfigAction] = []
    view = new or old
    if view is None:
        return actions
    mac = view.mac
    player_id = view.player_id

    # Device removed entirely. Stop it even when it was disabled: a speaker
    # disabled at runtime keeps its client until restart, and the stop is a
    # no-op when there is none.
    if new is None:
        actions.append(
            ReconfigAction(
                kind=ActionKind.STOP_CLIENT,
                mac=mac,
                player_id=player_id,
                fields=["removed"],
                label=_label_for_device(view.settings),
            )
        )
        return actions

    new_dev = new.settings
    new_enabled = bool(new_dev.get("enabled", True))

    # Newly added device.
    if old is None:
        if new_enabled:
            payload: dict[str, Any] = {"device": dict(new.device), "device_index": new.port_index}
            actions.append(
                ReconfigAction(
                    kind=ActionKind.START_CLIENT,
                    mac=mac,
                    player_id=player_id,
                    fields=["added"],
                    payload=payload,
                    label=_label_for_device(new_dev),
                )
            )
        return actions

    old_dev = old.settings
    old_enabled = bool(old_dev.get("enabled", True))

    # Enable transition.
    if old_enabled and not new_enabled:
        actions.append(
            ReconfigAction(
                kind=ActionKind.STOP_CLIENT,
                mac=mac,
                player_id=player_id,
                fields=["enabled"],
                label=_label_for_device(new_dev),
            )
        )
        return actions
    if not old_enabled and new_enabled:
        payload_reenable: dict[str, Any] = {"device": dict(new.device), "device_index": new.port_index}
        actions.append(
            ReconfigAction(
                kind=ActionKind.START_CLIENT,
                mac=mac,
                player_id=player_id,
                fields=["enabled"],
                payload=payload_reenable,
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
                player_id=player_id,
                fields=["adapter"],
                payload={"old_adapter": old_dev.get("adapter") or ""},
                label=label,
            )
        )

    warm_fields = sorted(f for f in _DEVICE_WARM_FIELDS if _field_changed(old_dev, new_dev, f))
    if "playout_sync_enabled" in warm_fields and effective_playout_mode(
        old_dev, old_cfg or {}
    ) == effective_playout_mode(new_dev, new_cfg or {}):
        # The daemon would be spawned with the same mode (e.g. PLAYOUT_SYNC off): no restart.
        warm_fields.remove("playout_sync_enabled")
    hot_fields = sorted(f for f in _DEVICE_HOT_FIELDS if _field_changed(old_dev, new_dev, f))

    if warm_fields:
        # Any warm-restart change supersedes hot-apply for the same subprocess
        # because the restart re-reads every field from the new device dict.
        actions.append(
            ReconfigAction(
                kind=ActionKind.WARM_RESTART,
                mac=mac,
                player_id=player_id,
                fields=warm_fields,
                # ``device_index``: a non-Bluetooth player restarts as a new
                # client and needs the same port fallback a start uses.
                payload={"device": dict(new.device), "device_index": new.port_index},
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
                player_id=player_id,
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

    # HA integration subsystem (MQTT publisher / mDNS) — single nested
    # block.  Any change in its dotted-path leafs produces one
    # HA_INTEGRATION_LIFECYCLE action; the executor decides whether to
    # restart the publisher task or just re-register mDNS.  When the old
    # config predates v2.65.0 (no HA_INTEGRATION block) we substitute the
    # *new* defaults block on the old side so a fresh save with all-default
    # values doesn't produce a spurious lifecycle action.
    _ha_default_off = {"enabled": False, "mode": "off", "mqtt": {}, "rest": {}}
    ha_old = old.get("HA_INTEGRATION")
    if ha_old is None:
        ha_old = _ha_default_off
    ha_new = new.get("HA_INTEGRATION")
    if ha_new is None:
        ha_new = _ha_default_off
    ha_changed_paths = _diff_ha_integration(ha_old, ha_new)
    if ha_changed_paths:
        actions.append(
            ReconfigAction(
                kind=ActionKind.HA_INTEGRATION_LIFECYCLE,
                mac=None,
                fields=ha_changed_paths,
                payload={"target": dict(new.get("HA_INTEGRATION") or {})},
                label="ha_integration",
            )
        )

    return actions


def _diff_ha_integration(old_ha: dict[str, Any], new_ha: dict[str, Any]) -> list[str]:
    """Return changed dotted-paths under ``HA_INTEGRATION`` (e.g. ``mqtt.broker``)."""
    changed: list[str] = []
    # Top-level scalars
    for key in ("enabled", "mode"):
        if _normalize_scalar(old_ha.get(key)) != _normalize_scalar(new_ha.get(key)):
            changed.append(key)
    # Nested mqtt + rest objects
    for parent in ("mqtt", "rest"):
        old_inner = old_ha.get(parent) or {}
        new_inner = new_ha.get(parent) or {}
        if not isinstance(old_inner, dict):
            old_inner = {}
        if not isinstance(new_inner, dict):
            new_inner = {}
        keys = set(old_inner.keys()) | set(new_inner.keys())
        for key in sorted(keys):
            if _normalize_scalar(old_inner.get(key)) != _normalize_scalar(new_inner.get(key)):
                path = f"{parent}.{key}"
                if path in _HA_INTEGRATION_LIFECYCLE_PATHS:
                    changed.append(path)
    return sorted(changed)


def diff_configs(old: dict[str, Any] | None, new: dict[str, Any] | None) -> list[ReconfigAction]:
    """Return the ordered list of actions needed to migrate from ``old`` to ``new``.

    Ordering guarantees (required by the orchestrator):

    1. Per-device BT_REMOVE precedes the WARM_RESTART for the same player.
    2. Per-device actions are grouped by player id, in stable insertion order.
    3. Global actions come after per-device actions so any hot-applied global
       field can observe the post-restart subprocesses.
    """
    old = old or {}
    new = new or {}

    actions: list[ReconfigAction] = []

    old_players = _players_by_id(old)
    new_players = _players_by_id(new)

    # Preserve the order of the new config (user-visible order in the UI),
    # append any removed players last.
    ordered_ids = [*new_players, *(pid for pid in old_players if pid not in new_players)]

    for player_id in ordered_ids:
        old_view = old_players.get(player_id)
        new_view = new_players.get(player_id)
        if (
            old_view is not None
            and new_view is not None
            and (old_view.backend_type, old_view.mac) != (new_view.backend_type, new_view.mac)
        ):
            # Same id, different output: stop the old one, start the new one.
            actions.extend(_diff_device(old_view, None))
            actions.extend(_diff_device(None, new_view))
            continue
        actions.extend(_diff_device(old_view, new_view, old, new))

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
