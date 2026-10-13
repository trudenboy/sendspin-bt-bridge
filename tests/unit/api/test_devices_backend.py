"""``/devices`` reports each speaker's backend and what it can do; other commands are 409."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

import sendspin_bridge.application.diagnostics as diagnostics
import sendspin_bridge.application.status as status_uc
from sendspin_bridge.config.players import supported_actions_for
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

MAC = "AA:BB:CC:DD:EE:FF"


def _write_v6(tmp_config, players):
    tmp_config.write_text(json.dumps({"CONFIG_SCHEMA_VERSION": 6, "players": players, "PLAYER_STATE": {}}))


@pytest.fixture
def running(monkeypatch, tmp_config):
    clients: list = []
    snapshot = lambda: DeviceRegistrySnapshot(active_clients=clients)  # noqa: E731
    monkeypatch.setattr(status_uc, "get_device_registry_snapshot", snapshot)
    monkeypatch.setattr(diagnostics, "get_device_registry_snapshot", snapshot)
    clients.append(
        SimpleNamespace(
            status={"bluetooth_available": True},
            _status_lock=threading.Lock(),
            player_name="Kitchen",
            player_id="kitchen",
            listen_port=8928,
            server_host="ma.local",
            server_port=9000,
            static_delay_ms=0.0,
            connected_server_url="",
            bt_manager=SimpleNamespace(mac_address=MAC, adapter_hci_name="hci0"),
            bluetooth_sink_name="bluez_sink.AA.a2dp_sink",
            bt_management_enabled=True,
            is_running=lambda: True,
        )
    )
    return clients


def test_running_bluetooth_device_reports_backend_and_actions(api_client, running):
    d = api_client.get("/api/v1/devices").json()[0]
    assert d["backend"] == {"type": "bluetooth_a2dp", "mac": MAC, "adapter": "hci0", "sink": None}
    assert d["supported_actions"] == supported_actions_for("bluetooth_a2dp")
    assert d["bluetooth"]["mac"] == MAC


def test_bluetooth_commands_on_a_pulse_sink_player_are_409(api_client, tmp_config):
    _write_v6(tmp_config, [{"id": "dac-1", "name": "DAC", "backend": {"type": "pulse_sink", "sink": "s"}}])
    for path in ("reconnect", "repair", "standby", "wake", "claim", "pairing-window"):
        resp = api_client.post(f"/api/v1/devices/dac-1/{path}")
        assert resp.status_code == 409, path
        assert resp.json()["code"] == "not_supported_for_backend"
    resp = api_client.put("/api/v1/devices/dac-1/power-save", json={"enabled": True})
    assert resp.status_code == 404


def test_pulse_sink_player_keeps_volume_and_transport_actions(api_client, tmp_config):
    _write_v6(tmp_config, [{"id": "dac-1", "name": "DAC", "backend": {"type": "pulse_sink", "sink": "s"}}])
    # Not running, so a definite "unknown device" - never the backend refusal.
    calls = [
        ("put", "volume", {"level": 5}),
        ("put", "mute", {"muted": True}),
        ("post", "playback", {"action": "pause"}),
        ("post", "transport", {"command": "next"}),
    ]
    for method, path, body in calls:
        resp = getattr(api_client, method)(f"/api/v1/devices/dac-1/{path}", json=body)
        assert resp.status_code == 404, path
        assert resp.json()["code"] == "unknown_device", path


def test_bluetooth_player_commands_are_not_blocked(api_client, tmp_config):
    _write_v6(tmp_config, [{"id": "bt-1", "name": "BT", "backend": {"type": "bluetooth_a2dp", "mac": MAC}}])
    resp = api_client.post("/api/v1/devices/bt-1/reconnect")
    assert resp.status_code == 404  # not running, but not a backend refusal


def test_unknown_device_is_still_404(api_client, tmp_config):
    assert api_client.post("/api/v1/devices/nope/reconnect").status_code == 404


def test_disabled_device_reports_backend_type(api_client, running, tmp_config):
    import sendspin_bridge.bridge.state as state

    state.set_disabled_devices([{"player_name": "Off", "mac": MAC, "enabled": False}])
    try:
        disabled = api_client.get("/api/v1/status").json()["bridge"]["disabled_devices"]
        assert disabled[0]["backend_type"] == "bluetooth_a2dp"
    finally:
        state.set_disabled_devices([])


def test_running_device_reports_the_config_player_id(api_client, running, tmp_config):
    from sendspin_bridge.config.players import player_id_for_mac

    _write_v6(
        tmp_config,
        [
            {"id": "other", "name": "Other", "backend": {"type": "bluetooth_a2dp", "mac": "11:22:33:44:55:66"}},
            {"id": player_id_for_mac(MAC), "name": "Kitchen", "backend": {"type": "bluetooth_a2dp", "mac": MAC}},
        ],
    )
    d = api_client.get("/api/v1/devices").json()[0]
    assert d["id"] == "kitchen"  # runtime id, unchanged
    assert d["config_id"] == player_id_for_mac(MAC)


def test_running_device_missing_from_config_has_no_config_id(api_client, running, tmp_config):
    _write_v6(tmp_config, [])
    assert api_client.get("/api/v1/devices").json()[0]["config_id"] is None


def test_running_speaker_takes_its_backend_from_the_client_not_the_config(api_client, running, tmp_config):
    # A config entry that happens to share the runtime id must not decide what the running speaker can do.
    _write_v6(tmp_config, [{"id": "kitchen", "name": "DAC", "backend": {"type": "pulse_sink", "sink": "s"}}])
    for path in ("reconnect", "standby"):
        resp = api_client.post(f"/api/v1/devices/kitchen/{path}")
        assert resp.json().get("code") != "not_supported_for_backend", path


def test_disabled_device_id_comes_from_its_config_entry(api_client, running, tmp_config):
    import sendspin_bridge.bridge.state as state

    state.set_disabled_devices([{"id": "custom-id", "player_name": "Off", "mac": MAC, "enabled": False}])
    try:
        disabled = api_client.get("/api/v1/status").json()["bridge"]["disabled_devices"]
        assert disabled[0]["id"] == "custom-id"
    finally:
        state.set_disabled_devices([])
