"""Catalog of Home Assistant entities the bridge exposes per speaker and per bridge.

Pure data: each ``EntitySpec`` declares one HA entity (one row in HA's entity
registry).  Two transports consume this catalog without re-deriving it:

- ``services/ha_mqtt_publisher.py`` — turns specs into MQTT discovery payloads.
- ``custom_components/sendspin_bridge/`` — turns specs into Python entity
  classes registered with HA core.

**MA-deduplication rule (hard).** Music Assistant's own HA integration already
exposes ``media_player.<player_name>`` per Sendspin BT speaker with full
playback / queue / volume / mute / metadata.  This catalog must NOT contain
``media_player`` entries or any volume / mute / transport fields.  We expose
only what MA does not know about: BT-link diagnostics, sync health, idle-mode
config, and BT-level commands.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class EntityKind(str, Enum):
    """Subset of HA platforms we register entities under."""

    SENSOR = "sensor"
    BINARY_SENSOR = "binary_sensor"
    SWITCH = "switch"
    BUTTON = "button"
    NUMBER = "number"
    SELECT = "select"
    UPDATE = "update"


# Pure extractors: ``(device_dict, bridge_dict) -> Any``.  ``device_dict`` is
# the result of ``DeviceSnapshot.to_dict()`` (which flattens ``extra``), so
# every top-level DeviceStatus field is reachable as a direct key.
DeviceExtractor = Callable[[dict[str, Any], dict[str, Any]], Any]
BridgeExtractor = Callable[[dict[str, Any]], Any]


@dataclass(frozen=True)
class EntitySpec:
    """One HA entity row.

    ``object_id`` is the part after the last dot in the entity ID (e.g.
    ``rssi_dbm`` → ``sensor.<player_slug>_rssi_dbm``).  Combined with the
    bridge-issued ``player_id`` it forms a stable ``unique_id``.
    """

    object_id: str
    kind: EntityKind
    name: str
    extractor: DeviceExtractor | BridgeExtractor | None = None
    device_class: str | None = None
    state_class: str | None = None
    unit: str | None = None
    entity_category: str | None = None  # "diagnostic" | "config" | None
    icon: str | None = None
    options: tuple[str, ...] = ()  # for select
    min_value: float | None = None  # for number
    max_value: float | None = None
    step: float | None = None
    command: str | None = None  # for button/switch/select/number setter dispatch
    payload_on: str = "ON"
    payload_off: str = "OFF"
    expose_attrs: tuple[str, ...] = ()  # extra keys to surface as entity attrs


# ---------------------------------------------------------------------------
# Extractors
# ---------------------------------------------------------------------------
#
# Naming: ``_d_*`` — device-scoped, ``_b_*`` — bridge-scoped.  Each is a small
# pure lambda over the dehydrated dict shape so tests can feed synthetic dicts
# without spinning up a full snapshot.


def _bool(value: Any) -> bool:
    return bool(value) if value is not None else False


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


# Device extractors -----------------------------------------------------------


def _d_bluetooth_connected(d: dict[str, Any], _b: dict[str, Any]) -> bool:
    return _bool(d.get("bluetooth_connected"))


def _d_audio_streaming(d: dict[str, Any], _b: dict[str, Any]) -> bool:
    return _bool(d.get("audio_streaming"))


def _d_reanchoring(d: dict[str, Any], _b: dict[str, Any]) -> bool:
    return _bool(d.get("reanchoring"))


def _d_reconnecting(d: dict[str, Any], _b: dict[str, Any]) -> bool:
    return _bool(d.get("reconnecting"))


def _d_bt_standby(d: dict[str, Any], _b: dict[str, Any]) -> bool:
    return _bool(d.get("bt_standby"))


def _d_bt_power_save(d: dict[str, Any], _b: dict[str, Any]) -> bool:
    return _bool(d.get("bt_power_save"))


def _d_bt_management_enabled(d: dict[str, Any], _b: dict[str, Any]) -> bool:
    return _bool(d.get("bt_management_enabled"))


def _d_enabled(d: dict[str, Any], _b: dict[str, Any]) -> bool:
    value = d.get("enabled")
    return True if value is None else _bool(value)


def _d_rssi_dbm(d: dict[str, Any], _b: dict[str, Any]) -> int | None:
    return _int_or_none(d.get("rssi_dbm"))


def _d_battery_level(d: dict[str, Any], _b: dict[str, Any]) -> int | None:
    return _int_or_none(d.get("battery_level"))


def _d_audio_format(d: dict[str, Any], _b: dict[str, Any]) -> str | None:
    return _str_or_none(d.get("audio_format"))


def _d_reanchor_count(d: dict[str, Any], _b: dict[str, Any]) -> int:
    return int(d.get("reanchor_count") or 0)


def _d_last_sync_error_ms(d: dict[str, Any], _b: dict[str, Any]) -> float | None:
    return _float_or_none(d.get("last_sync_error_ms"))


def _d_reconnect_attempt(d: dict[str, Any], _b: dict[str, Any]) -> int:
    return int(d.get("reconnect_attempt") or 0)


def _d_last_error(d: dict[str, Any], _b: dict[str, Any]) -> str | None:
    return _str_or_none(d.get("last_error"))


def _d_health_state(d: dict[str, Any], _b: dict[str, Any]) -> str | None:
    health = d.get("health_summary") or {}
    if isinstance(health, dict):
        return _str_or_none(health.get("state"))
    return None


def _d_idle_mode(d: dict[str, Any], _b: dict[str, Any]) -> str:
    return _str_or_none(d.get("idle_mode")) or "default"


def _d_keep_alive_method(d: dict[str, Any], _b: dict[str, Any]) -> str:
    return _str_or_none(d.get("keep_alive_method")) or "infrasound"


def _d_static_delay_ms(d: dict[str, Any], _b: dict[str, Any]) -> int:
    return int(d.get("static_delay_ms") or 0)


def _d_power_save_delay_minutes(d: dict[str, Any], _b: dict[str, Any]) -> int:
    return int(d.get("power_save_delay_minutes") or 1)


# Bridge extractors -----------------------------------------------------------


def _b_version(b: dict[str, Any]) -> str:
    return str(b.get("version") or "")


def _b_ma_connected(b: dict[str, Any]) -> bool:
    return _bool(b.get("ma_connected"))


def _b_startup_phase(b: dict[str, Any]) -> str:
    progress = b.get("startup_progress") or {}
    if isinstance(progress, dict):
        return str(progress.get("phase") or "idle")
    return "idle"


def _b_runtime_mode(b: dict[str, Any]) -> str:
    return str(b.get("runtime_mode") or "production")


def _b_update_available(b: dict[str, Any]) -> bool:
    info = b.get("update_available") or {}
    if isinstance(info, dict):
        return _bool(info.get("available"))
    return _bool(info)


def _b_update_latest(b: dict[str, Any]) -> str | None:
    info = b.get("update_available") or {}
    if isinstance(info, dict):
        return _str_or_none(info.get("latest"))
    return None


# ---------------------------------------------------------------------------
# Catalog: per-device entities
# ---------------------------------------------------------------------------

DEVICE_ENTITIES: tuple[EntitySpec, ...] = (
    # Connectivity & runtime --------------------------------------------------
    EntitySpec(
        object_id="bluetooth_connected",
        kind=EntityKind.BINARY_SENSOR,
        name="Bluetooth connected",
        extractor=_d_bluetooth_connected,
        device_class="connectivity",
        entity_category="diagnostic",
        icon="mdi:bluetooth",
    ),
    EntitySpec(
        object_id="audio_streaming",
        kind=EntityKind.BINARY_SENSOR,
        name="Audio streaming",
        extractor=_d_audio_streaming,
        entity_category="diagnostic",
        icon="mdi:music-note",
    ),
    EntitySpec(
        object_id="reanchoring",
        kind=EntityKind.BINARY_SENSOR,
        name="Reanchoring",
        extractor=_d_reanchoring,
        entity_category="diagnostic",
        icon="mdi:sync-alert",
    ),
    EntitySpec(
        object_id="reconnecting",
        kind=EntityKind.BINARY_SENSOR,
        name="Reconnecting",
        extractor=_d_reconnecting,
        entity_category="diagnostic",
        icon="mdi:sync",
    ),
    EntitySpec(
        object_id="bt_standby",
        kind=EntityKind.BINARY_SENSOR,
        name="BT standby",
        extractor=_d_bt_standby,
        entity_category="diagnostic",
        icon="mdi:power-sleep",
    ),
    EntitySpec(
        object_id="bt_power_save",
        kind=EntityKind.BINARY_SENSOR,
        name="BT power save",
        extractor=_d_bt_power_save,
        entity_category="diagnostic",
        icon="mdi:leaf",
    ),
    # Diagnostics sensors -----------------------------------------------------
    EntitySpec(
        object_id="rssi_dbm",
        kind=EntityKind.SENSOR,
        name="RSSI",
        extractor=_d_rssi_dbm,
        device_class="signal_strength",
        state_class="measurement",
        unit="dBm",
        entity_category="diagnostic",
        icon="mdi:signal",
    ),
    EntitySpec(
        object_id="battery_level",
        kind=EntityKind.SENSOR,
        name="Battery",
        extractor=_d_battery_level,
        device_class="battery",
        state_class="measurement",
        unit="%",
        entity_category="diagnostic",
        icon="mdi:battery",
    ),
    EntitySpec(
        object_id="audio_format",
        kind=EntityKind.SENSOR,
        name="Audio codec",
        extractor=_d_audio_format,
        entity_category="diagnostic",
        icon="mdi:music-clef-treble",
    ),
    EntitySpec(
        object_id="reanchor_count",
        kind=EntityKind.SENSOR,
        name="Reanchor count",
        extractor=_d_reanchor_count,
        state_class="total_increasing",
        entity_category="diagnostic",
        icon="mdi:sync-alert",
    ),
    EntitySpec(
        object_id="last_sync_error_ms",
        kind=EntityKind.SENSOR,
        name="Last sync error",
        extractor=_d_last_sync_error_ms,
        device_class="duration",
        state_class="measurement",
        unit="ms",
        entity_category="diagnostic",
    ),
    EntitySpec(
        object_id="reconnect_attempt",
        kind=EntityKind.SENSOR,
        name="Reconnect attempt",
        extractor=_d_reconnect_attempt,
        state_class="measurement",
        entity_category="diagnostic",
    ),
    EntitySpec(
        object_id="last_error",
        kind=EntityKind.SENSOR,
        name="Last error",
        extractor=_d_last_error,
        entity_category="diagnostic",
        icon="mdi:alert-circle",
    ),
    EntitySpec(
        object_id="health_state",
        kind=EntityKind.SENSOR,
        name="Health",
        extractor=_d_health_state,
        entity_category="diagnostic",
        icon="mdi:heart-pulse",
    ),
    # Config knobs (writable) -------------------------------------------------
    EntitySpec(
        object_id="enabled",
        kind=EntityKind.SWITCH,
        name="Enabled",
        extractor=_d_enabled,
        entity_category="config",
        icon="mdi:check-circle-outline",
        command="set_enabled",
    ),
    EntitySpec(
        object_id="bt_management_enabled",
        kind=EntityKind.SWITCH,
        name="BT management",
        extractor=_d_bt_management_enabled,
        entity_category="config",
        icon="mdi:tools",
        command="set_bt_management",
    ),
    EntitySpec(
        object_id="idle_mode",
        kind=EntityKind.SELECT,
        name="Idle mode",
        extractor=_d_idle_mode,
        entity_category="config",
        icon="mdi:power-sleep",
        options=("default", "power_save", "auto_disconnect", "keep_alive"),
        command="set_idle_mode",
    ),
    EntitySpec(
        object_id="keep_alive_method",
        kind=EntityKind.SELECT,
        name="Keep-alive method",
        extractor=_d_keep_alive_method,
        entity_category="config",
        icon="mdi:waveform",
        options=("infrasound", "silence", "none"),
        command="set_keep_alive_method",
    ),
    EntitySpec(
        object_id="static_delay_ms",
        kind=EntityKind.NUMBER,
        name="Static delay",
        extractor=_d_static_delay_ms,
        entity_category="config",
        unit="ms",
        min_value=0,
        max_value=5000,
        step=10,
        icon="mdi:timer-cog",
        command="set_static_delay_ms",
    ),
    EntitySpec(
        object_id="power_save_delay_minutes",
        kind=EntityKind.NUMBER,
        name="Power save delay",
        extractor=_d_power_save_delay_minutes,
        entity_category="config",
        unit="min",
        min_value=0,
        max_value=60,
        step=1,
        icon="mdi:timer-outline",
        command="set_power_save_delay_minutes",
    ),
    # Buttons (BT-level commands MA cannot perform) ---------------------------
    EntitySpec(
        object_id="reconnect",
        kind=EntityKind.BUTTON,
        name="Reconnect",
        icon="mdi:bluetooth-connect",
        command="reconnect",
    ),
    EntitySpec(
        object_id="disconnect",
        kind=EntityKind.BUTTON,
        name="Disconnect",
        icon="mdi:bluetooth-off",
        command="disconnect",
    ),
    EntitySpec(
        object_id="wake",
        kind=EntityKind.BUTTON,
        name="Wake from standby",
        icon="mdi:bluetooth-audio",
        command="wake",
    ),
    EntitySpec(
        object_id="standby",
        kind=EntityKind.BUTTON,
        name="Enter standby",
        icon="mdi:power-sleep",
        command="standby",
    ),
    EntitySpec(
        object_id="power_save_toggle",
        kind=EntityKind.BUTTON,
        name="Toggle power save",
        icon="mdi:leaf",
        command="power_save_toggle",
    ),
    EntitySpec(
        object_id="reset_reconnect",
        kind=EntityKind.BUTTON,
        name="Full BT reset",
        entity_category="diagnostic",
        icon="mdi:restart-alert",
        command="reset_reconnect",
    ),
    # Pairing intentionally NOT exposed as an HA button — it's a one-shot
    # workflow that needs the speaker in pairing mode and produces a
    # secret on success.  Triggering it from an HA automation has no
    # safe failure path; bridge web UI keeps the button.  See PR #216
    # discussion for the rationale.
    EntitySpec(
        object_id="claim_audio",
        kind=EntityKind.BUTTON,
        name="Claim audio",
        entity_category="diagnostic",
        icon="mdi:hand-back-right",
        command="claim_audio",
    ),
)


# ---------------------------------------------------------------------------
# Catalog: bridge-level entities (one HA Device "Sendspin Bridge: <name>")
# ---------------------------------------------------------------------------

BRIDGE_ENTITIES: tuple[EntitySpec, ...] = (
    EntitySpec(
        object_id="version",
        kind=EntityKind.SENSOR,
        name="Version",
        extractor=_b_version,
        entity_category="diagnostic",
        icon="mdi:tag-outline",
    ),
    EntitySpec(
        object_id="ma_connected",
        kind=EntityKind.BINARY_SENSOR,
        name="Music Assistant connected",
        extractor=_b_ma_connected,
        device_class="connectivity",
        entity_category="diagnostic",
        icon="mdi:music",
    ),
    EntitySpec(
        object_id="startup_phase",
        kind=EntityKind.SENSOR,
        name="Startup phase",
        extractor=_b_startup_phase,
        entity_category="diagnostic",
        icon="mdi:rocket-launch",
    ),
    EntitySpec(
        object_id="runtime_mode",
        kind=EntityKind.SENSOR,
        name="Runtime mode",
        extractor=_b_runtime_mode,
        entity_category="diagnostic",
        icon="mdi:cog-outline",
    ),
    EntitySpec(
        object_id="update_available",
        kind=EntityKind.UPDATE,
        name="Update",
        extractor=_b_update_available,
        entity_category="diagnostic",
        icon="mdi:package-up",
        expose_attrs=("latest_version",),
    ),
    EntitySpec(
        object_id="restart",
        kind=EntityKind.BUTTON,
        name="Restart bridge",
        entity_category="diagnostic",
        icon="mdi:restart",
        command="restart",
    ),
    # ``Scan for devices`` intentionally NOT exposed — scan results
    # only mean anything inside the bridge web UI's pair-flow modal,
    # which HA can't open.  Triggering a bare scan from an automation
    # produces no observable effect.
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def device_unique_id(player_id: str, spec: EntitySpec) -> str:
    """Stable unique_id for a per-device entity.

    ``player_id`` is the bridge's UUID5-derived stable key (see
    ``config._player_id_from_mac``).  Combined with the spec's ``object_id``
    it gives an entity ID that survives renames and adapter changes.
    """
    return f"sendspin_{player_id}_{spec.object_id}"


def bridge_unique_id(bridge_id: str, spec: EntitySpec) -> str:
    return f"sendspin_bridge_{bridge_id}_{spec.object_id}"


def entity_index_by_object_id() -> dict[str, EntitySpec]:
    """Build a lookup table of all device + bridge specs by object_id.

    Used by command-topic handlers to resolve incoming MQTT / REST commands
    back to their EntitySpec for validation (option lists, number bounds).
    """
    table: dict[str, EntitySpec] = {}
    for spec in DEVICE_ENTITIES:
        table[spec.object_id] = spec
    for spec in BRIDGE_ENTITIES:
        table[spec.object_id] = spec
    return table


def device_command_specs() -> dict[str, EntitySpec]:
    """Map command names → spec, restricted to per-device commands."""
    return {spec.command: spec for spec in DEVICE_ENTITIES if spec.command}


def bridge_command_specs() -> dict[str, EntitySpec]:
    return {spec.command: spec for spec in BRIDGE_ENTITIES if spec.command}


# Hard-coded sentinel set of HA platforms that this catalog must NEVER
# include — enforced by ``test_ha_entity_model.py``.  Documents the
# MA-deduplication contract in code, not just docs.
MA_OWNED_KINDS: frozenset[str] = frozenset({"media_player"})

# Field names from DeviceStatus that MA owns; never read by extractors.
MA_OWNED_DEVICE_FIELDS: frozenset[str] = frozenset(
    {
        "playing",
        "volume",
        "muted",
        "current_track",
        "current_artist",
        "current_album",
        "current_album_artist",
        "artwork_url",
        "track_year",
        "track_number",
        "track_progress_ms",
        "track_duration_ms",
        "shuffle",
        "repeat_mode",
        "playback_speed",
        "supported_commands",
        "group_id",
        "group_name",
        "group_volume",
        "group_muted",
        "ma_syncgroup_id",
        "ma_now_playing",
    }
)


__all__ = [
    "BRIDGE_ENTITIES",
    "DEVICE_ENTITIES",
    "MA_OWNED_DEVICE_FIELDS",
    "MA_OWNED_KINDS",
    "EntityKind",
    "EntitySpec",
    "bridge_command_specs",
    "bridge_unique_id",
    "device_command_specs",
    "device_unique_id",
    "entity_index_by_object_id",
]
