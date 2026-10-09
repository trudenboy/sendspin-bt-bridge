"""``POST /devices/{id}/pairing-window``: let Music Assistant pair a Sendspin player."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import sendspin_bridge.application.playback as playback
import sendspin_bridge.application.status as status
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot
from sendspin_bridge.services.ipc.commands import OpenPairingWindow


def _speaker(*, running: bool = True):
    commands = []

    async def send(command):
        commands.append(command)

    return SimpleNamespace(
        player_id="stable-player-id",
        player_name="Kitchen",
        is_running=lambda: running,
        _send_subprocess_command=send,
        commands=commands,
    )


@pytest.fixture
def speaker(monkeypatch):
    found = _speaker()
    monkeypatch.setattr(status, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=[found]))
    monkeypatch.setattr(playback, "load_config", lambda: {"SENDSPIN_PAIRING": True})
    return found


def test_the_window_opens_on_the_addressed_player(api_client, speaker, bridge_loop):
    resp = api_client.post("/api/v1/devices/stable-player-id/pairing-window")

    assert resp.status_code == 202
    assert len(speaker.commands) == 1
    assert isinstance(speaker.commands[0], OpenPairingWindow)


def test_players_are_addressed_by_their_stable_id_not_their_name(api_client, speaker):
    assert api_client.post("/api/v1/devices/Kitchen/pairing-window").status_code == 404


def test_disabled_pairing_is_409(api_client, speaker, monkeypatch):
    monkeypatch.setattr(playback, "load_config", lambda: {"SENDSPIN_PAIRING": False})
    resp = api_client.post("/api/v1/devices/stable-player-id/pairing-window")
    assert resp.status_code == 409
    assert resp.json()["code"] == "pairing_disabled"


def test_a_stopped_daemon_is_409(api_client, speaker, monkeypatch):
    stopped = _speaker(running=False)
    monkeypatch.setattr(
        status, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=[stopped])
    )
    resp = api_client.post("/api/v1/devices/stable-player-id/pairing-window")
    assert resp.status_code == 409
    assert stopped.commands == []


def test_no_runtime_loop_is_503_and_sends_nothing(api_client, speaker):
    resp = api_client.post("/api/v1/devices/stable-player-id/pairing-window")
    assert resp.status_code == 503
    assert resp.json()["code"] == "not_ready"
    assert speaker.commands == []
