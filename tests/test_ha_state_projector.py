"""Tests for ``services/ha_state_projector.py``."""

from __future__ import annotations

from services.ha_entity_model import DEVICE_ENTITIES, EntityKind
from services.ha_state_projector import (
    EntityState,
    HAStateProjection,
    StateDelta,
    compute_delta,
    project_snapshot,
)
from services.status_snapshot import (
    BridgeSnapshot,
    DeviceSnapshot,
    StartupProgressSnapshot,
)


def _make_device(
    *,
    player_id="player-aaa",
    mac="FC:58:FA:EB:08:6C",
    player_name="ENEBY20",
    bluetooth_connected=True,
    audio_streaming=True,
    rssi_dbm=-55,
    battery_level=80,
    static_delay_ms=0.0,
    idle_mode="default",
    extra_overrides: dict | None = None,
) -> DeviceSnapshot:
    extra = {
        "audio_streaming": audio_streaming,
        "audio_format": "SBC",
        "reanchoring": False,
        "reanchor_count": 0,
        "last_sync_error_ms": None,
        "reconnecting": False,
        "reconnect_attempt": 0,
        "bt_standby": False,
        "bt_power_save": False,
        "rssi_dbm": rssi_dbm,
        "idle_mode": idle_mode,
        "keep_alive_method": "infrasound",
        "power_save_delay_minutes": 1,
        "last_error": None,
    }
    if extra_overrides:
        extra.update(extra_overrides)
    return DeviceSnapshot(
        connected=True,
        server_connected=True,
        bluetooth_connected=bluetooth_connected,
        bluetooth_available=True,
        playing=False,
        bluetooth_mac=mac,
        player_name=player_name,
        player_id=player_id,
        battery_level=battery_level,
        static_delay_ms=static_delay_ms,
        enabled=True,
        bt_management_enabled=True,
        health_summary={"state": "streaming", "severity": "info", "summary": "", "reasons": []},
        extra=extra,
    )


def _make_bridge_snapshot(devices: list[DeviceSnapshot] | None = None) -> BridgeSnapshot:
    return BridgeSnapshot(
        devices=devices or [],
        groups=[],
        ma_connected=True,
        disabled_devices=[],
        startup_progress=StartupProgressSnapshot(
            status="ready",
            phase="ready",
            current_step=5,
            total_steps=5,
            percent=100,
            message="ready",
        ),
        runtime_mode="production",
        update_available={"available": False, "installed": "2.65.0"},
    )


# ---------------------------------------------------------------------------
# project_snapshot
# ---------------------------------------------------------------------------


def test_project_snapshot_empty_returns_no_devices():
    snap = _make_bridge_snapshot()
    proj = project_snapshot(snap, bridge_id="haos", bridge_name="HAOS Bridge")
    assert proj.devices == {}
    assert proj.availability == {}
    # Bridge entities still projected from the bridge slice
    assert "ma_connected" in proj.bridge
    assert proj.bridge["ma_connected"].value is True


def test_project_snapshot_one_device_full_entity_set():
    device = _make_device()
    snap = _make_bridge_snapshot([device])
    proj = project_snapshot(snap, bridge_id="haos", bridge_name="HAOS Bridge")

    assert "player-aaa" in proj.devices
    entities = proj.devices["player-aaa"]

    # Every spec with an extractor produces a state row
    expected_oids = {s.object_id for s in DEVICE_ENTITIES if s.extractor is not None}
    assert set(entities.keys()) == expected_oids

    # Spot checks across kinds
    assert entities["bluetooth_connected"].value is True
    assert entities["audio_streaming"].value is True
    assert entities["rssi_dbm"].value == -55
    assert entities["battery_level"].value == 80
    assert entities["idle_mode"].value == "default"
    assert entities["static_delay_ms"].value == 0
    assert entities["health_state"].value == "streaming"

    # Availability mirrors bluetooth_connected
    assert proj.availability["player-aaa"] is True

    # Device meta carries the MAC HA needs for connections=[(bluetooth, mac)]
    meta = proj.device_meta["player-aaa"]
    assert meta.mac == "fc:58:fa:eb:08:6c"
    assert meta.player_name == "ENEBY20"
    assert meta.player_id == "player-aaa"


def test_project_snapshot_disconnected_device_marks_unavailable():
    device = _make_device(bluetooth_connected=False, audio_streaming=False)
    snap = _make_bridge_snapshot([device])
    proj = project_snapshot(snap, bridge_id="haos", bridge_name="HAOS")

    assert proj.availability["player-aaa"] is False
    assert proj.devices["player-aaa"]["bluetooth_connected"].value is False
    assert proj.devices["player-aaa"]["audio_streaming"].value is False


def test_project_snapshot_skips_devices_without_player_id():
    device = _make_device(player_id="")
    snap = _make_bridge_snapshot([device])
    proj = project_snapshot(snap, bridge_id="haos", bridge_name="HAOS")
    assert proj.devices == {}


def test_project_snapshot_unit_attached_to_attrs():
    device = _make_device()
    snap = _make_bridge_snapshot([device])
    proj = project_snapshot(snap, bridge_id="haos", bridge_name="HAOS")

    assert proj.devices["player-aaa"]["rssi_dbm"].attrs.get("unit_of_measurement") == "dBm"
    assert proj.devices["player-aaa"]["battery_level"].attrs.get("unit_of_measurement") == "%"


def test_project_snapshot_update_entity_carries_latest_version_attr():
    device = _make_device()
    snap = _make_bridge_snapshot([device])
    snap.update_available = {"available": True, "latest": "2.66.0", "installed": "2.65.0"}
    proj = project_snapshot(snap, bridge_id="haos", bridge_name="HAOS")
    assert proj.bridge["update_available"].value is True
    assert proj.bridge["update_available"].attrs["latest_version"] == "2.66.0"
    assert proj.bridge["update_available"].attrs["installed_version"] == "2.65.0"


def test_project_snapshot_runtime_extras_fills_version_when_no_devices():
    snap = _make_bridge_snapshot()
    proj = project_snapshot(
        snap,
        bridge_id="haos",
        bridge_name="HAOS",
        runtime_extras={"version": "2.65.0"},
    )
    assert proj.bridge["version"].value == "2.65.0"
    assert proj.bridge_meta and proj.bridge_meta.version == "2.65.0"


def test_project_snapshot_no_button_or_command_only_specs_in_state():
    """Buttons have no extractor, so they must NOT appear in projected state.
    Their command flow goes via the dispatcher, not state topics."""
    device = _make_device()
    snap = _make_bridge_snapshot([device])
    proj = project_snapshot(snap, bridge_id="haos", bridge_name="HAOS")
    button_oids = {s.object_id for s in DEVICE_ENTITIES if s.kind is EntityKind.BUTTON}
    assert button_oids.isdisjoint(proj.devices["player-aaa"].keys())


# ---------------------------------------------------------------------------
# compute_delta
# ---------------------------------------------------------------------------


def _make_projection(
    devices_state: dict, bridge_state: dict | None = None, availability: dict | None = None
) -> HAStateProjection:
    return HAStateProjection(
        devices={
            pid: {oid: EntityState(value=v) for oid, v in entities.items()} for pid, entities in devices_state.items()
        },
        bridge={oid: EntityState(value=v) for oid, v in (bridge_state or {}).items()},
        availability=availability or {pid: True for pid in devices_state},
        bridge_available=True,
    )


def test_delta_first_projection_is_full():
    current = _make_projection({"p1": {"rssi_dbm": -50}}, {"version": "2.65.0"})
    delta = compute_delta(None, current)
    assert delta.devices == {"p1": current.devices["p1"]}
    assert delta.bridge == current.bridge
    assert delta.availability_changed == {"p1": True}
    assert "p1" in delta.devices_added


def test_delta_no_change_is_empty():
    proj = _make_projection({"p1": {"rssi_dbm": -50}}, {"version": "2.65.0"})
    delta = compute_delta(proj, proj)
    assert delta.is_empty


def test_delta_only_changed_entities():
    prior = _make_projection({"p1": {"rssi_dbm": -50, "battery_level": 80}})
    current = _make_projection({"p1": {"rssi_dbm": -55, "battery_level": 80}})
    delta = compute_delta(prior, current)
    assert "p1" in delta.devices
    assert "rssi_dbm" in delta.devices["p1"]
    assert "battery_level" not in delta.devices["p1"]


def test_delta_availability_change():
    prior = _make_projection({"p1": {"rssi_dbm": -50}}, availability={"p1": True})
    current = _make_projection({"p1": {"rssi_dbm": -50}}, availability={"p1": False})
    delta = compute_delta(prior, current)
    assert delta.availability_changed == {"p1": False}
    assert delta.devices == {}  # value didn't change


def test_delta_device_added_then_removed():
    prior = _make_projection({"p1": {"rssi_dbm": -50}})
    current = _make_projection({"p1": {"rssi_dbm": -50}, "p2": {"rssi_dbm": -60}})
    delta = compute_delta(prior, current)
    assert "p2" in delta.devices_added
    assert "p2" in delta.availability_changed
    assert "p1" not in delta.devices

    next_state = _make_projection({"p1": {"rssi_dbm": -50}})
    delta2 = compute_delta(current, next_state)
    assert delta2.devices_removed == ("p2",)
    assert delta2.availability_changed == {"p2": False}


def test_delta_bridge_available_change():
    online = _make_projection({}, {"version": "2.65.0"})
    offline = _make_projection({}, {"version": "2.65.0"})
    offline.bridge_available = False
    delta = compute_delta(online, offline)
    assert delta.bridge_available_changed is False


def test_delta_attrs_change_triggers_emit():
    prior = HAStateProjection(
        devices={"p1": {"rssi_dbm": EntityState(value=-50, attrs={"unit_of_measurement": "dBm"})}},
        bridge={},
        availability={"p1": True},
    )
    current = HAStateProjection(
        devices={"p1": {"rssi_dbm": EntityState(value=-50, attrs={"unit_of_measurement": "dBm", "extra": "x"})}},
        bridge={},
        availability={"p1": True},
    )
    delta = compute_delta(prior, current)
    assert "rssi_dbm" in delta.devices["p1"]


def test_delta_is_empty_property():
    empty = StateDelta(devices={}, bridge={}, availability_changed={})
    assert empty.is_empty is True
    not_empty = StateDelta(
        devices={"p1": {"rssi_dbm": EntityState(value=-50)}},
        bridge={},
        availability_changed={},
    )
    assert not_empty.is_empty is False
