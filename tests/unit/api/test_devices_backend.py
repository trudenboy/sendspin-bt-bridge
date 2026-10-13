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
    assert resp.status_code != 409 or resp.json()["code"] != "not_supported_for_backend"


def test_pulse_sink_player_keeps_volume_and_transport_actions(api_client, tmp_config):
    _write_v6(tmp_config, [{"id": "dac-1", "name": "DAC", "backend": {"type": "pulse_sink", "sink": "s"}}])
    for path in ("volume", "mute"):
        resp = api_client.put(f"/api/v1/devices/dac-1/{path}", json={"level": 5, "muted": True})
        assert resp.json().get("code") != "not_supported_for_backend", path


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
