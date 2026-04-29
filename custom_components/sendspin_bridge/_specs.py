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

AVAILABILITY_CLASSES = ("sendspin_bridge.config", "runtime", "cumulative")


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
    # See services/ha_entity_model.EntitySpec.availability_class for semantics.
    availability_class: str = "runtime"


DEVICE_ENTITIES: tuple[EntitySpec, ...] = (
    # Connectivity
    EntitySpec(
        "bluetooth_connected",
        "binary_sensor",
        "Bluetooth connected",
        device_class="connectivity",
        entity_category="diagnostic",
        icon="mdi:bluetooth",
        availability_class="cumulative",
    ),
    EntitySpec(
        "audio_streaming",
        "binary_sensor",
        "Audio streaming",
        entity_category="diagnostic",
        icon="mdi:music-note",
        availability_class="runtime",
    ),
    EntitySpec(
        "reanchoring",
        "binary_sensor",
        "Reanchoring",
        entity_category="diagnostic",
        icon="mdi:sync-alert",
        availability_class="runtime",
    ),
    EntitySpec(
        "reconnecting",
        "binary_sensor",
        "Reconnecting",
        entity_category="diagnostic",
        icon="mdi:sync",
        availability_class="cumulative",
    ),
    # ``bt_standby`` and ``bt_power_save`` binary sensors removed in
    # v2.65.0-rc.6 — replaced by the ``standby`` and ``power_save``
    # switches below which expose both the current state AND the toggle
    # action in one HA entity.
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
        availability_class="runtime",
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
        availability_class="runtime",
    ),
    EntitySpec(
        "audio_format",
        "sensor",
        "Audio codec",
        entity_category="diagnostic",
        icon="mdi:music-clef-treble",
        availability_class="runtime",
    ),
    EntitySpec(
        "reanchor_count",
        "sensor",
        "Reanchor count",
        state_class="total_increasing",
        entity_category="diagnostic",
        icon="mdi:sync-alert",
        availability_class="cumulative",
    ),
    EntitySpec(
        "last_sync_error_ms",
        "sensor",
        "Last sync error",
        device_class="duration",
        state_class="measurement",
        unit="ms",
        entity_category="diagnostic",
        availability_class="cumulative",
    ),
    EntitySpec(
        "reconnect_attempt",
        "sensor",
        "Reconnect attempt",
        state_class="measurement",
        entity_category="diagnostic",
        availability_class="cumulative",
    ),
    EntitySpec(
        "last_error",
        "sensor",
        "Last error",
        entity_category="diagnostic",
        icon="mdi:alert-circle",
        availability_class="cumulative",
    ),
    EntitySpec(
        "health_state",
        "sensor",
        "Health",
        entity_category="diagnostic",
        icon="mdi:heart-pulse",
        availability_class="cumulative",
    ),
    # Config — always reachable while in fleet
    EntitySpec(
        "enabled",
        "switch",
        "Enabled",
        entity_category="sendspin_bridge.config",
        icon="mdi:check-circle-outline",
        command="set_enabled",
        availability_class="sendspin_bridge.config",
    ),
    EntitySpec(
        "bt_management_enabled",
        "switch",
        "BT management",
        entity_category="sendspin_bridge.config",
        icon="mdi:tools",
        command="set_bt_management",
        availability_class="sendspin_bridge.config",
    ),
    EntitySpec(
        "standby",
        "switch",
        "Standby",
        entity_category="sendspin_bridge.config",
        icon="mdi:power-sleep",
        command="set_standby",
        availability_class="cumulative",
    ),
    EntitySpec(
        "power_save",
        "switch",
        "Power save",
        entity_category="sendspin_bridge.config",
        icon="mdi:leaf",
        command="set_power_save",
        availability_class="cumulative",
    ),
    EntitySpec(
        "idle_mode",
        "select",
        "Idle mode",
        entity_category="sendspin_bridge.config",
        icon="mdi:power-sleep",
        options=("default", "power_save", "auto_disconnect", "keep_alive"),
        command="set_idle_mode",
        availability_class="sendspin_bridge.config",
    ),
    EntitySpec(
        "keep_alive_method",
        "select",
        "Keep-alive method",
        entity_category="sendspin_bridge.config",
        icon="mdi:waveform",
        options=("infrasound", "silence", "none"),
        command="set_keep_alive_method",
        availability_class="sendspin_bridge.config",
    ),
    EntitySpec(
        "static_delay_ms",
        "number",
        "Static delay",
        entity_category="sendspin_bridge.config",
        unit="ms",
        min_value=0,
        max_value=5000,
        step=10,
        icon="mdi:timer-cog",
        command="set_static_delay_ms",
        availability_class="sendspin_bridge.config",
    ),
    EntitySpec(
        "power_save_delay_minutes",
        "number",
        "Power save delay",
        entity_category="sendspin_bridge.config",
        unit="min",
        min_value=0,
        max_value=60,
        step=1,
        icon="mdi:timer-outline",
        command="set_power_save_delay_minutes",
        availability_class="sendspin_bridge.config",
    ),
    # Buttons — always pressable while in fleet
    EntitySpec(
        "reconnect",
        "button",
        "Reconnect",
        icon="mdi:bluetooth-connect",
        command="reconnect",
        availability_class="sendspin_bridge.config",
    ),
    EntitySpec(
        "disconnect",
        "button",
        "Disconnect",
        icon="mdi:bluetooth-off",
        command="disconnect",
        availability_class="sendspin_bridge.config",
    ),
    # Pairing and reset_reconnect intentionally NOT exposed (see
    # services/ha_entity_model.py).
    # ``wake`` / ``standby`` / ``power_save_toggle`` buttons removed in
    # v2.65.0-rc.6 — see the ``standby`` and ``power_save`` switches above.
    EntitySpec(
        "claim_audio",
        "button",
        "Claim audio",
        entity_category="diagnostic",
        icon="mdi:hand-back-right",
        command="claim_audio",
        availability_class="sendspin_bridge.config",
    ),
)


BRIDGE_ENTITIES: tuple[EntitySpec, ...] = (
    EntitySpec(
        "version",
        "sensor",
        "Version",
        entity_category="diagnostic",
        icon="mdi:tag-outline",
        availability_class="sendspin_bridge.config",
    ),
    EntitySpec(
        "ma_connected",
        "binary_sensor",
        "Music Assistant connected",
        device_class="connectivity",
        entity_category="diagnostic",
        icon="mdi:music",
        availability_class="sendspin_bridge.config",
    ),
    EntitySpec(
        "startup_phase",
        "sensor",
        "Startup phase",
        entity_category="diagnostic",
        icon="mdi:rocket-launch",
        availability_class="sendspin_bridge.config",
    ),
    EntitySpec(
        "runtime_mode",
        "sensor",
        "Runtime mode",
        entity_category="diagnostic",
        icon="mdi:cog-outline",
        availability_class="sendspin_bridge.config",
    ),
    EntitySpec(
        "update_available",
        "update",
        "Update",
        entity_category="diagnostic",
        icon="mdi:package-up",
        availability_class="sendspin_bridge.config",
    ),
    EntitySpec(
        "restart",
        "button",
        "Restart bridge",
        entity_category="diagnostic",
        icon="mdi:restart",
        command="restart",
        availability_class="sendspin_bridge.config",
    ),
    # Scan intentionally NOT exposed (see services/ha_entity_model.py).
)


def device_specs_by_kind(kind: str) -> list[EntitySpec]:
    return [s for s in DEVICE_ENTITIES if s.kind == kind]


def bridge_specs_by_kind(kind: str) -> list[EntitySpec]:
    return [s for s in BRIDGE_ENTITIES if s.kind == kind]
