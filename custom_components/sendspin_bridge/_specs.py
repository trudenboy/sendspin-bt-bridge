"""Mirror of ``services/ha_entity_model.py`` for the HA custom_component.

The bridge keeps its catalog Python-side (``services/ha_entity_model.py``)
so the MQTT publisher and the REST projector stay in sync.  The
custom_component can't import from the bridge package — it ships
separately via HACS — so this module duplicates the spec catalog as a
plain data dump.

Keep this file in lockstep with ``services/ha_entity_model.py``.  A
sync-check test (``tests/test_ha_custom_component_specs_sync.py``) fails
the build if the two diverge.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntitySpec:
    object_id: str
    kind: str  # "sensor" | "binary_sensor" | "switch" | "button" | "number" | "select" | "update"
    name: str
    device_class: str | None = None
    state_class: str | None = None
    unit: str | None = None
    entity_category: str | None = None
    icon: str | None = None
    options: tuple[str, ...] = ()
    min_value: float | None = None
    max_value: float | None = None
    step: float | None = None
    command: str | None = None


DEVICE_ENTITIES: tuple[EntitySpec, ...] = (
    # Connectivity
    EntitySpec(
        "bluetooth_connected",
        "binary_sensor",
        "Bluetooth connected",
        device_class="connectivity",
        entity_category="diagnostic",
        icon="mdi:bluetooth",
    ),
    EntitySpec(
        "audio_streaming", "binary_sensor", "Audio streaming", entity_category="diagnostic", icon="mdi:music-note"
    ),
    EntitySpec("reanchoring", "binary_sensor", "Reanchoring", entity_category="diagnostic", icon="mdi:sync-alert"),
    EntitySpec("reconnecting", "binary_sensor", "Reconnecting", entity_category="diagnostic", icon="mdi:sync"),
    EntitySpec("bt_standby", "binary_sensor", "BT standby", entity_category="diagnostic", icon="mdi:power-sleep"),
    EntitySpec("bt_power_save", "binary_sensor", "BT power save", entity_category="diagnostic", icon="mdi:leaf"),
    # Diagnostic sensors
    EntitySpec(
        "rssi_dbm",
        "sensor",
        "RSSI",
        device_class="signal_strength",
        state_class="measurement",
        unit="dBm",
        entity_category="diagnostic",
        icon="mdi:signal",
    ),
    EntitySpec(
        "battery_level",
        "sensor",
        "Battery",
        device_class="battery",
        state_class="measurement",
        unit="%",
        entity_category="diagnostic",
        icon="mdi:battery",
    ),
    EntitySpec("audio_format", "sensor", "Audio codec", entity_category="diagnostic", icon="mdi:music-clef-treble"),
    EntitySpec(
        "reanchor_count",
        "sensor",
        "Reanchor count",
        state_class="total_increasing",
        entity_category="diagnostic",
        icon="mdi:sync-alert",
    ),
    EntitySpec(
        "last_sync_error_ms",
        "sensor",
        "Last sync error",
        device_class="duration",
        state_class="measurement",
        unit="ms",
        entity_category="diagnostic",
    ),
    EntitySpec(
        "reconnect_attempt", "sensor", "Reconnect attempt", state_class="measurement", entity_category="diagnostic"
    ),
    EntitySpec("last_error", "sensor", "Last error", entity_category="diagnostic", icon="mdi:alert-circle"),
    EntitySpec("health_state", "sensor", "Health", entity_category="diagnostic", icon="mdi:heart-pulse"),
    # Config
    EntitySpec(
        "enabled", "switch", "Enabled", entity_category="config", icon="mdi:check-circle-outline", command="set_enabled"
    ),
    EntitySpec(
        "bt_management_enabled",
        "switch",
        "BT management",
        entity_category="config",
        icon="mdi:tools",
        command="set_bt_management",
    ),
    EntitySpec(
        "idle_mode",
        "select",
        "Idle mode",
        entity_category="config",
        icon="mdi:power-sleep",
        options=("default", "power_save", "auto_disconnect", "keep_alive"),
        command="set_idle_mode",
    ),
    EntitySpec(
        "keep_alive_method",
        "select",
        "Keep-alive method",
        entity_category="config",
        icon="mdi:waveform",
        options=("infrasound", "silence", "none"),
        command="set_keep_alive_method",
    ),
    EntitySpec(
        "static_delay_ms",
        "number",
        "Static delay",
        entity_category="config",
        unit="ms",
        min_value=0,
        max_value=5000,
        step=10,
        icon="mdi:timer-cog",
        command="set_static_delay_ms",
    ),
    EntitySpec(
        "power_save_delay_minutes",
        "number",
        "Power save delay",
        entity_category="config",
        unit="min",
        min_value=0,
        max_value=60,
        step=1,
        icon="mdi:timer-outline",
        command="set_power_save_delay_minutes",
    ),
    # Buttons
    EntitySpec("reconnect", "button", "Reconnect", icon="mdi:bluetooth-connect", command="reconnect"),
    EntitySpec("disconnect", "button", "Disconnect", icon="mdi:bluetooth-off", command="disconnect"),
    EntitySpec("wake", "button", "Wake from standby", icon="mdi:bluetooth-audio", command="wake"),
    EntitySpec("standby", "button", "Enter standby", icon="mdi:power-sleep", command="standby"),
    EntitySpec("power_save_toggle", "button", "Toggle power save", icon="mdi:leaf", command="power_save_toggle"),
    EntitySpec(
        "reset_reconnect",
        "button",
        "Full BT reset",
        entity_category="diagnostic",
        icon="mdi:restart-alert",
        command="reset_reconnect",
    ),
    # Pairing intentionally NOT exposed (see services/ha_entity_model.py).
    EntitySpec(
        "claim_audio",
        "button",
        "Claim audio",
        entity_category="diagnostic",
        icon="mdi:hand-back-right",
        command="claim_audio",
    ),
)


BRIDGE_ENTITIES: tuple[EntitySpec, ...] = (
    EntitySpec("version", "sensor", "Version", entity_category="diagnostic", icon="mdi:tag-outline"),
    EntitySpec(
        "ma_connected",
        "binary_sensor",
        "Music Assistant connected",
        device_class="connectivity",
        entity_category="diagnostic",
        icon="mdi:music",
    ),
    EntitySpec("startup_phase", "sensor", "Startup phase", entity_category="diagnostic", icon="mdi:rocket-launch"),
    EntitySpec("runtime_mode", "sensor", "Runtime mode", entity_category="diagnostic", icon="mdi:cog-outline"),
    EntitySpec("update_available", "update", "Update", entity_category="diagnostic", icon="mdi:package-up"),
    EntitySpec(
        "restart", "button", "Restart bridge", entity_category="diagnostic", icon="mdi:restart", command="restart"
    ),
    # Scan intentionally NOT exposed (see services/ha_entity_model.py).
)


def device_specs_by_kind(kind: str) -> list[EntitySpec]:
    return [s for s in DEVICE_ENTITIES if s.kind == kind]


def bridge_specs_by_kind(kind: str) -> list[EntitySpec]:
    return [s for s in BRIDGE_ENTITIES if s.kind == kind]
