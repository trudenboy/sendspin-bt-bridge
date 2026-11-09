"""A running ``pulse_sink`` player as ``/devices`` shows it and as its commands treat it."""

from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

import sendspin_bridge.application.diagnostics as diagnostics
import sendspin_bridge.application.playback as playback
import sendspin_bridge.application.status as status_uc
from sendspin_bridge.config.players import supported_actions_for
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

SINK = "alsa_output.usb-Generic_USB_DAC_0000A1-00.analog-stereo"


def _pulse_client(*, sink: str | None, volume_controller: str = "sendspin") -> SimpleNamespace:
    sent: list = []

    async def _send(cmd):
        sent.append(cmd)

    status = {
        "backend_type": "pulse_sink",
        "audio_sink": SINK,
        "sink_missing": sink is None,
        "sink_match_status": "ok" if sink else "sink_missing",
        "volume": 100,
    }
    return SimpleNamespace(
        status=status,
        _status_lock=threading.Lock(),
        _update_status=status.update,
        _send_subprocess_command=_send,
        sent=sent,
        player_name="Desk DAC",
        player_id="dac-1",
        config_id="dac-1",
        backend_type="pulse_sink",
        volume_controller=volume_controller,
        listen_port=8930,
        server_host="ma.local",
        server_port=9000,
        static_delay_ms=0.0,
        connected_server_url="",
        bt_manager=None,
        bluetooth_sink_name=sink,
        bt_management_enabled=True,
        is_running=lambda: sink is not None,
    )


@pytest.fixture
def running(monkeypatch, tmp_config):
    clients: list = []
    snapshot = lambda: DeviceRegistrySnapshot(active_clients=clients)  # noqa: E731
    monkeypatch.setattr(status_uc, "get_device_registry_snapshot", snapshot)
    monkeypatch.setattr(diagnostics, "get_device_registry_snapshot", snapshot)
    tmp_config.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [{"id": "dac-1", "name": "Desk DAC", "backend": {"type": "pulse_sink", "sink": SINK}}],
                "PLAYER_STATE": {},
            }
        )
    )
    return clients


def test_a_pulse_sink_device_reports_its_backend_without_bluetooth(api_client, running):
    running.append(_pulse_client(sink=SINK))

    d = api_client.get("/api/v1/devices").json()[0]

    assert d["id"] == "dac-1"
    assert d["config_id"] == "dac-1"
    assert d["backend"] == {"type": "pulse_sink", "mac": None, "adapter": None, "sink": SINK}
    assert d["bluetooth"] is None
    assert d["supported_actions"] == supported_actions_for("pulse_sink") == ["volume", "transport", "power_save"]
    assert d["audio"]["has_sink"] is True
    assert d["audio"]["sink_missing"] is False
    assert d["audio"]["sink_match_status"] == "ok"


def test_a_pulse_sink_device_without_its_output_says_so(api_client, running):
    running.append(_pulse_client(sink=None))

    d = api_client.get("/api/v1/devices").json()[0]

    assert d["audio"]["has_sink"] is False
    assert d["audio"]["sink_missing"] is True
    assert d["audio"]["sink_match_status"] == "sink_missing"
    assert d["backend"]["sink"] == SINK


def test_bluetooth_commands_on_a_running_pulse_sink_player_are_409(api_client, running):
    running.append(_pulse_client(sink=SINK))

    for path in ("reconnect", "standby", "claim"):
        resp = api_client.post(f"/api/v1/devices/dac-1/{path}")
        assert resp.status_code == 409, path
        assert resp.json()["code"] == "not_supported_for_backend", path


def test_the_volume_slider_never_moves_a_shared_output(api_client, running, monkeypatch):
    def _no_sink_write(*_args):
        raise AssertionError("the sink's level must not change under volume_controller 'sendspin'")

    monkeypatch.setattr(playback, "set_sink_volume", _no_sink_write)
    monkeypatch.setattr(playback, "set_sink_mute", _no_sink_write)
    scheduled: list = []
    monkeypatch.setattr(
        playback, "submit_on_loop", lambda coro, *, description: scheduled.append(coro) or coro.close() or True
    )
    client = _pulse_client(sink=SINK)
    running.append(client)

    assert api_client.put("/api/v1/devices/dac-1/volume", json={"level": 30}).status_code == 200
    assert api_client.put("/api/v1/devices/dac-1/mute", json={"muted": True}).status_code == 200

    assert client.status["volume"] == 30
    assert client.status["muted"] is True
    assert len(scheduled) == 2  # SetVolume and SetMute go to the daemon, which scales the stream


def test_the_volume_slider_drives_the_sink_when_pa_was_chosen(api_client, running, monkeypatch):
    writes: list = []
    monkeypatch.setattr(playback, "set_sink_volume", lambda sink, vol: writes.append((sink, vol)) or True)
    monkeypatch.setattr(playback, "submit_on_loop", lambda coro, *, description: coro.close() or True)
    running.append(_pulse_client(sink=SINK, volume_controller="pa"))

    assert api_client.put("/api/v1/devices/dac-1/volume", json={"level": 30}).status_code == 200

    assert writes == [(SINK, 30)]


# -- the Timing panel's values survive a restart for a local output too --------------------------


def _with_hot_apply(client):
    async def _apply(updates):
        return list(updates)

    client.apply_hot_config = _apply
    return client


@pytest.mark.parametrize(("field", "value"), [("static_delay_ms", 120), ("min_buffer_ms", 400)])
def test_a_local_outputs_latency_setting_is_saved(api_client, running, tmp_config, bridge_loop, field, value):
    running.append(_with_hot_apply(_pulse_client(sink=SINK)))

    resp = api_client.put("/api/v1/devices/dac-1/latency", json={"field": field, "value": value})

    assert resp.status_code == 200, resp.text
    player = json.loads(tmp_config.read_text())["players"][0]
    assert player[field] == value


def test_a_tuning_session_blocks_external_latency_edits(
    api_client, running, tmp_config, bridge_loop, monkeypatch, tmp_path
):
    from dataclasses import replace

    from sendspin_bridge.sync_tuning import session_store
    from tests.support.sync_fakes import FakeControl, FakeHost

    control = FakeControl()
    control.member_list = [replace(m, client_id="dac-1") if m.ma_player_id == "a" else m for m in control.member_list]
    store = session_store.SessionStore(
        control_factory=lambda: control, host=FakeHost(), publish=lambda _data: None, snapshot_dir=lambda: tmp_path
    )
    monkeypatch.setattr(session_store, "_store", store)
    store.create("g", "semi", "device")
    host = _with_hot_apply(_pulse_client(sink=SINK))
    running.append(host)
    before = tmp_config.read_bytes()
    response = api_client.put("/api/v1/devices/dac-1/latency", json={"value": 120})
    assert response.status_code == 409 and response.json()["code"] == "session_active"
    assert host.sent == []
    assert tmp_config.read_bytes() == before


def test_home_assistant_uses_the_same_timing_owner(running, tmp_config, bridge_loop, monkeypatch):
    from sendspin_bridge.services.bluetooth import bt_commands

    host = _pulse_client(sink=SINK)
    running.append(host)
    monkeypatch.setattr(bt_commands, "find_client_by_player_id", lambda _id: host)
    result = bt_commands.apply_device_config_change("dac-1", "static_delay_ms", 120)
    assert result.success, result.error
    player = json.loads(tmp_config.read_text())["players"][0]
    assert player["static_delay_ms"] == 120 and player["static_delay_source"] == "manual"
    assert host.status["static_delay_source"] == "manual"


def test_latency_provenance_cannot_be_an_arbitrary_string(api_client, running, bridge_loop):
    host = _pulse_client(sink=SINK)
    running.append(host)
    response = api_client.put("/api/v1/devices/dac-1/latency", json={"value": 120, "source": "pretend_calibrated"})
    assert response.status_code == 422
    assert host.sent == []
