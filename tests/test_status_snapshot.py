import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import state
from services.status_snapshot import (
    build_bridge_snapshot,
    build_device_snapshot,
    build_device_snapshot_pairs,
    build_group_snapshots,
)

UTC = timezone.utc


def _make_client(
    *,
    player_name="Kitchen",
    player_id="sendspin-kitchen",
    group_id=None,
    group_name=None,
    volume=55,
    playing=False,
):
    return SimpleNamespace(
        status={
            "server_connected": True,
            "bluetooth_connected": True,
            "bluetooth_available": True,
            "playing": playing,
            "group_id": group_id,
            "group_name": group_name,
            "volume": volume,
            "uptime_start": datetime.now(tz=UTC),
        },
        _status_lock=threading.Lock(),
        player_name=player_name,
        player_id=player_id,
        listen_port=8928,
        server_host="music-assistant.local",
        server_port=9000,
        static_delay_ms=-500.0,
        connected_server_url="",
        bt_manager=SimpleNamespace(
            mac_address="AA:BB:CC:DD:EE:FF",
            effective_adapter_mac="11:22:33:44:55:66",
            adapter="hci0",
            adapter_hci_name="hci0",
            battery_level=88,
        ),
        bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
        bt_management_enabled=True,
        is_running=lambda: True,
    )


def test_build_device_snapshot_includes_ma_syncgroup_metadata():
    client = _make_client(group_id="runtime-group-id")
    state.set_ma_groups(
        {"sendspin-kitchen": {"id": "syncgroup_abc", "name": "Kitchen Group"}},
        [{"id": "syncgroup_abc", "name": "Kitchen Group", "members": []}],
    )
    try:
        snapshot = build_device_snapshot(client)
        data = snapshot.to_dict()
        assert data["player_name"] == "Kitchen"
        assert data["ma_syncgroup_id"] == "syncgroup_abc"
        assert data["group_name"] == "Kitchen Group"
        assert data["battery_level"] == 88
        assert data["connected_server_url"] == "ws://music-assistant.local:9000/sendspin"
        assert "uptime" in data
    finally:
        state.set_ma_groups({}, [])


def test_build_device_snapshot_includes_recent_events_and_health_summary():
    client = _make_client()
    client.status.update(
        {
            "server_connected": True,
            "bluetooth_connected": True,
            "playing": True,
            "audio_streaming": False,
            "last_error": "Route degraded",
            "last_error_at": "2026-03-18T00:00:00+00:00",
        }
    )
    state.clear_device_events(client.player_id)
    state.record_device_event(client.player_id, "runtime-error", level="error", message="Route degraded")
    try:
        snapshot = build_device_snapshot(client)
        data = snapshot.to_dict()
        assert data["recent_events"][0]["event_type"] == "runtime-error"
        assert data["health_summary"]["state"] == "degraded"
        assert data["health_summary"]["severity"] == "error"
        assert "last_error" in data["health_summary"]["reasons"]
        assert "recent_runtime_error" in data["health_summary"]["reasons"]
    finally:
        state.clear_device_events(client.player_id)


def test_build_device_snapshot_includes_global_enabled_flag(monkeypatch):
    client = _make_client(player_name="Kitchen @ LXC")
    monkeypatch.setattr(
        "services.status_snapshot.load_config",
        lambda: {"BLUETOOTH_DEVICES": [{"player_name": "Kitchen", "enabled": False}]},
    )

    snapshot = build_device_snapshot(client)
    data = snapshot.to_dict()

    assert data["enabled"] is False
    assert snapshot.enabled is False


def test_build_device_snapshot_includes_capability_payload():
    client = _make_client()
    client.status.update({"server_connected": False, "bluetooth_connected": False, "reconnecting": True})

    snapshot = build_device_snapshot(client)
    data = snapshot.to_dict()

    assert data["capabilities"]["health_state"] == data["health_summary"]["state"]
    assert data["capabilities"]["domains"]["playback"]["currently_available"] is True
    assert data["capabilities"]["actions"]["play_pause"]["blocked_reason"] == "Sendspin is not connected."
    assert data["capabilities"]["actions"]["volume"]["currently_available"] is True
    assert data["capabilities"]["actions"]["reconnect"]["currently_available"] is False
    assert "Reconnect is already in progress." in data["capabilities"]["actions"]["reconnect"]["blocked_reason"]
    assert data["capabilities"]["actions"]["reconnect"]["recommended_action"] == "toggle_bt_management"
    assert data["capabilities"]["actions"]["reconnect"]["depends_on"] == ["reconnect_idle"]
    assert data["capabilities"]["actions"]["toggle_bt_management"]["currently_available"] is True
    assert data["capabilities"]["actions"]["toggle_bt_management"]["safe_actions"][0] == "toggle_bt_management"
    assert data["capabilities"]["actions"]["queue_control"]["blocked_reason"] == "Sendspin is not connected."


def test_build_device_snapshot_prefers_repair_when_device_is_unpaired():
    client = _make_client()
    client.status.update(
        {"server_connected": False, "bluetooth_connected": False, "reconnecting": True, "reconnect_attempt": 3}
    )
    client.bt_manager.paired = False
    client.bt_manager.max_reconnect_fails = 5

    snapshot = build_device_snapshot(client)
    data = snapshot.to_dict()

    assert data["bluetooth_paired"] is False
    assert data["reconnect_attempts_remaining"] == 2
    assert data["capabilities"]["actions"]["reconnect"]["currently_available"] is False
    assert "run re-pair" in data["capabilities"]["actions"]["reconnect"]["blocked_reason"]
    assert data["capabilities"]["actions"]["reconnect"]["safe_actions"][0] == "pair_device"
    assert data["capabilities"]["actions"]["reconnect"]["recommended_action"] == "pair_device"
    assert data["capabilities"]["actions"]["reconnect"]["depends_on"] == ["bluetooth_paired"]
    assert data["capabilities"]["actions"]["toggle_bt_management"]["currently_available"] is True
    assert data["capabilities"]["actions"]["toggle_bt_management"]["safe_actions"][0] == "toggle_bt_management"


def test_build_device_snapshot_exposes_normalized_state_model_and_reason_details():
    client = _make_client()
    client.status.update({"server_connected": False, "bluetooth_connected": False, "reconnecting": True})

    snapshot = build_device_snapshot(client)
    data = snapshot.to_dict()

    assert data["state_model"]["management"]["bridge_managed"] is True
    assert data["state_model"]["bluetooth"]["connected"] is False
    assert data["state_model"]["transport"]["daemon_connected"] is False
    assert data["state_model"]["health"]["state"] == data["health_summary"]["state"]
    assert data["capabilities"]["actions"]["reconnect"]["blocked_reason_detail"]["code"] == "reconnecting"
    assert (
        data["capabilities"]["actions"]["reconnect"]["blocked_reason_detail"]["recommended_action"]
        == "toggle_bt_management"
    )
    assert "toggle_bt_management" in data["capabilities"]["domains"]["connectivity"]["safe_actions"]


def test_build_device_snapshot_reports_stopping_transition():
    client = _make_client()
    client.status.update(
        {
            "server_connected": True,
            "bluetooth_connected": True,
            "stopping": True,
            "playing": False,
        }
    )

    snapshot = build_device_snapshot(client)
    data = snapshot.to_dict()

    assert data["stopping"] is True
    assert data["health_summary"]["state"] == "transitioning"
    assert data["health_summary"]["summary"] == "Stopping playback service"
    assert "stopping" in data["health_summary"]["reasons"]


def test_build_device_snapshot_surfaces_recent_event_reasons_when_ready():
    client = _make_client()
    state.clear_device_events(client.player_id)
    state.record_device_event(client.player_id, "bluetooth-reconnect-failed", details={"attempt": 2})
    state.record_device_event(client.player_id, "ma-monitor-stale", details={"error": "connection lost"})
    try:
        snapshot = build_device_snapshot(client)
        data = snapshot.to_dict()
        assert data["health_summary"]["state"] == "ready"
        assert "recent_reconnect_failure" in data["health_summary"]["reasons"]
        assert "ma_monitor_stale" in data["health_summary"]["reasons"]
    finally:
        state.clear_device_events(client.player_id)


def test_build_device_snapshot_pairs_return_client_and_snapshot_together():
    client = _make_client(group_id="runtime-group-id")

    pairs = build_device_snapshot_pairs([client])

    assert len(pairs) == 1
    returned_client, snapshot = pairs[0]
    assert returned_client is client
    assert snapshot.extra["group_id"] == "runtime-group-id"
    assert snapshot.player_name == "Kitchen"


def test_build_group_snapshots_merges_ma_syncgroup_members():
    client_a = _make_client(
        player_name="Kitchen",
        player_id="sendspin-kitchen",
        group_id="runtime-group-a",
        volume=40,
        playing=True,
    )
    client_b = _make_client(
        player_name="Living Room",
        player_id="sendspin-living-room",
        group_id="runtime-group-b",
        volume=60,
        playing=False,
    )
    client_b.bt_manager = SimpleNamespace(
        mac_address="FF:EE:DD:CC:BB:AA",
        effective_adapter_mac="11:22:33:44:55:77",
        adapter="hci1",
        adapter_hci_name="hci1",
        battery_level=75,
    )
    state.set_ma_groups(
        {
            "sendspin-kitchen": {"id": "syncgroup_shared", "name": "Whole Home"},
            "sendspin-living-room": {"id": "syncgroup_shared", "name": "Whole Home"},
        },
        [
            {
                "id": "syncgroup_shared",
                "name": "Whole Home",
                "members": [
                    {"id": "sendspin-kitchen", "name": "Kitchen"},
                    {"id": "sendspin-living-room", "name": "Living Room"},
                    {"id": "external-player", "name": "Bedroom", "available": True},
                ],
            }
        ],
    )
    try:
        groups = build_group_snapshots([client_a, client_b])
        assert len(groups) == 1
        group = groups[0].to_dict()
        assert group["group_name"] == "Whole Home"
        assert group["avg_volume"] == 50
        assert group["playing"] is True
        assert len(group["members"]) == 2
        assert group["external_count"] == 1
        assert group["external_members"][0]["name"] == "Bedroom"
    finally:
        state.set_ma_groups({}, [])


def test_build_bridge_snapshot_no_clients_preserves_bridge_metadata():
    from config import CONFIG_SCHEMA_VERSION
    from services.ipc_protocol import IPC_PROTOCOL_VERSION

    state.set_disabled_devices([{"player_name": "Disabled", "enabled": False}])
    state.set_ma_api_credentials("http://ma.local:8095", "token")
    state.set_ma_connected(False)
    state.set_update_available({"version": "2.33.0"})
    state.reset_startup_progress(3, message="Booting")
    state.update_startup_progress("devices", "Preparing devices", current_step=2)
    state.set_runtime_mode_info(
        {
            "mode": "demo",
            "is_mocked": True,
            "simulator_active": True,
            "fixture_devices": 2,
            "mocked_layers": [{"layer": "BluetoothManager", "summary": "Mocked adapter"}],
        }
    )
    try:
        snapshot = build_bridge_snapshot([])
        payload = snapshot.to_status_payload()
        assert payload["error"] == "No clients"
        assert payload["devices"] == []
        assert payload["groups"] == []
        assert payload["ma_connected"] is False
        assert payload["ma_web_url"] == "http://ma.local:8095"
        assert payload["disabled_devices"][0]["player_name"] == "Disabled"
        assert payload["update_available"]["version"] == "2.33.0"
        assert payload["startup_progress"]["phase"] == "devices"
        assert payload["startup_progress"]["percent"] == 67
        assert payload["runtime_mode"] == "demo"
        assert payload["mock_runtime"]["is_mocked"] is True
        assert payload["mock_runtime"]["mocked_layers"][0]["layer"] == "BluetoothManager"
        assert payload["version"]
        assert payload["runtime"]
        assert payload["config_schema_version"] == CONFIG_SCHEMA_VERSION
        assert payload["ipc_protocol_version"] == IPC_PROTOCOL_VERSION
    finally:
        state.set_disabled_devices([])
        state.set_ma_api_credentials("", "")
        state.set_ma_connected(False)
        state.set_update_available(None)
        state.reset_startup_progress()
        state.set_runtime_mode_info(None)
