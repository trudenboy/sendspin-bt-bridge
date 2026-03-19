import threading
from datetime import datetime, timezone
from types import SimpleNamespace

import state
from services.status_snapshot import build_bridge_snapshot, build_device_snapshot, build_group_snapshots

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
    finally:
        state.clear_device_events(client.player_id)


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
