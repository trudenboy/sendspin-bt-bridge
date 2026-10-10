"""Volume, mute and pause/play for one speaker, a group, or everything."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

import sendspin_bridge.application.playback as playback
import sendspin_bridge.application.status as status
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot
from sendspin_bridge.services.ipc.commands import Pause, Play, SetVolume


class _Speaker:
    def __init__(self, player_id: str, *, sink: str | None = "bluez_sink.AA.a2dp_sink", running: bool = True, **extra):
        self.player_id = player_id
        self.player_name = player_id.title()
        self.bluetooth_sink_name = sink
        self.bt_manager = SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF")
        self.status: dict = {}
        self.updates: list[dict] = []
        self.commands: list = []
        self._running = running
        self.extra = extra

    def is_running(self) -> bool:
        return self._running

    def _update_status(self, payload: dict) -> None:
        self.updates.append(payload)
        self.status.update(payload)

    async def _send_subprocess_command(self, command) -> None:
        self.commands.append(command)


@pytest.fixture
def speakers(monkeypatch):
    found: list[_Speaker] = []
    monkeypatch.setattr(status, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=found))
    monkeypatch.setattr(playback, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=found))
    return found


@pytest.fixture
def sinks(monkeypatch):
    calls: list[tuple] = []
    monkeypatch.setattr(playback, "set_sink_volume", lambda sink, volume: calls.append((sink, volume)) or True)
    monkeypatch.setattr(playback, "schedule_volume_persist", lambda mac, volume: calls.append(("persist", mac, volume)))
    return calls


def _settle(speaker: _Speaker, count: int = 1) -> None:
    deadline = time.monotonic() + 2
    while len(speaker.commands) < count and time.monotonic() < deadline:
        time.sleep(0.01)


# -- volume ------------------------------------------------------------------------------------


def test_volume_goes_to_the_sink_the_daemon_and_the_config(api_client, speakers, sinks, bridge_loop):
    kitchen = _Speaker("kitchen")
    speakers.append(kitchen)

    resp = api_client.put("/api/v1/devices/kitchen/volume", json={"level": 33})

    assert resp.status_code == 200
    assert resp.json()["volume"] == 33
    assert sinks == [("bluez_sink.AA.a2dp_sink", 33), ("persist", "AA:BB:CC:DD:EE:FF", 33)]
    assert kitchen.updates == [{"volume": 33}]
    _settle(kitchen)
    assert kitchen.commands == [SetVolume(value=33)]


def test_volume_needs_a_known_device(api_client, speakers, sinks):
    assert api_client.put("/api/v1/devices/ghost/volume", json={"level": 10}).status_code == 404


def test_volume_needs_a_sink(api_client, speakers, sinks):
    speakers.append(_Speaker("kitchen", sink=None))
    resp = api_client.put("/api/v1/devices/kitchen/volume", json={"level": 10})
    assert resp.status_code == 409
    assert resp.json()["code"] == "no_sink"
    assert sinks == []


@pytest.mark.parametrize("body", [{}, {"level": None}, {"volume": 10}])
def test_volume_body_is_validated(api_client, speakers, body):
    assert api_client.put("/api/v1/devices/kitchen/volume", json=body).status_code == 422


def test_a_rejected_sink_change_is_502(api_client, speakers, monkeypatch):
    speakers.append(_Speaker("kitchen"))
    monkeypatch.setattr(playback, "set_sink_volume", lambda *_a: False)
    assert api_client.put("/api/v1/devices/kitchen/volume", json={"level": 10}).status_code == 502


# -- mute ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("body", "sink_reports", "expected"), [({"muted": True}, True, True), ({}, None, True)])
def test_mute_sets_or_toggles(api_client, speakers, monkeypatch, bridge_loop, body, sink_reports, expected):
    speakers.append(_Speaker("kitchen"))
    asked: list = []
    monkeypatch.setattr(playback, "set_sink_mute", lambda sink, muted: asked.append(muted) or True)
    monkeypatch.setattr(playback, "get_sink_mute", lambda sink: sink_reports)

    resp = api_client.put("/api/v1/devices/kitchen/mute", json=body)

    assert resp.status_code == 200
    assert resp.json() == {"muted": expected}
    assert asked == [body.get("muted")]


# -- pause / play -----------------------------------------------------------------------------------


@pytest.mark.parametrize(("action", "command"), [("pause", Pause()), ("play", Play())])
def test_device_playback_sends_a_typed_command(api_client, speakers, bridge_loop, action, command):
    kitchen = _Speaker("kitchen")
    speakers.append(kitchen)

    assert api_client.post("/api/v1/devices/kitchen/playback", json={"action": action}).status_code == 202
    _settle(kitchen)
    assert kitchen.commands == [command]


def test_device_playback_needs_a_running_player(api_client, speakers, bridge_loop):
    speakers.append(_Speaker("kitchen", running=False))
    resp = api_client.post("/api/v1/devices/kitchen/playback", json={"action": "pause"})
    assert resp.status_code == 409


def test_device_playback_without_the_bridge_loop_is_503(api_client, speakers):
    speakers.append(_Speaker("kitchen"))
    resp = api_client.post("/api/v1/devices/kitchen/playback", json={"action": "pause"})
    assert resp.status_code == 503


def _grouped(monkeypatch, members: dict[str, str | None]) -> None:
    """Pretend the snapshot says which Sendspin session group each speaker is in."""

    def _pairs(clients):
        return [
            (c, SimpleNamespace(extra={"group_id": members.get(c.player_id), "group_name": "Downstairs"}))
            for c in clients
        ]

    monkeypatch.setattr(playback, "build_device_snapshot_pairs", _pairs)


def test_group_pause_goes_to_one_member(api_client, speakers, monkeypatch, bridge_loop):
    kitchen, lounge, attic = _Speaker("kitchen"), _Speaker("lounge"), _Speaker("attic")
    speakers.extend([kitchen, lounge, attic])
    _grouped(monkeypatch, {"kitchen": "g1", "lounge": "g1", "attic": None})

    resp = api_client.post("/api/v1/groups/g1/playback", json={"action": "pause"})

    assert resp.status_code == 200
    assert resp.json()["group_name"] == "Downstairs"
    _settle(kitchen)
    assert kitchen.commands == [Pause()]
    assert lounge.commands == attic.commands == []


def test_unknown_group_is_404(api_client, speakers, monkeypatch):
    speakers.append(_Speaker("kitchen"))
    _grouped(monkeypatch, {"kitchen": "g1"})
    assert api_client.post("/api/v1/groups/nope/playback", json={"action": "pause"}).status_code == 404


def test_pause_all_sends_one_command_per_group_and_one_per_solo_player(api_client, speakers, monkeypatch, bridge_loop):
    kitchen, lounge, attic = _Speaker("kitchen"), _Speaker("lounge"), _Speaker("attic")
    stopped = _Speaker("garage", running=False)
    speakers.extend([kitchen, lounge, attic, stopped])
    _grouped(monkeypatch, {"kitchen": "g1", "lounge": "g1", "attic": None})

    resp = api_client.post("/api/v1/playback", json={"action": "pause"})

    assert resp.json() == {"action": "pause", "count": 2}
    _settle(kitchen)
    _settle(attic)
    assert kitchen.commands == [Pause()]
    assert attic.commands == [Pause()]
    assert lounge.commands == stopped.commands == []


def test_group_volume_sets_every_member(api_client, speakers, sinks, monkeypatch, bridge_loop):
    kitchen, lounge = _Speaker("kitchen"), _Speaker("lounge", sink="bluez_sink.BB.a2dp_sink")
    speakers.extend([kitchen, lounge])
    _grouped(monkeypatch, {"kitchen": "g1", "lounge": "g1"})

    resp = api_client.put("/api/v1/groups/g1/volume", json={"level": 40})

    assert resp.json()["results"] == [{"device_id": "kitchen", "ok": True}, {"device_id": "lounge", "ok": True}]
    assert ("bluez_sink.AA.a2dp_sink", 40) in sinks
    assert ("bluez_sink.BB.a2dp_sink", 40) in sinks


def test_group_volume_finds_members_by_music_assistant_sync_group(
    api_client, speakers, sinks, monkeypatch, bridge_loop
):
    """Without a Sendspin group id (Sendspin 1.0), the group is addressed by its
    Music Assistant sync group id — the id /api/v1/groups reports for it."""
    kitchen, lounge = _Speaker("kitchen"), _Speaker("lounge", sink="bluez_sink.BB.a2dp_sink")
    speakers.extend([kitchen, lounge])

    def _pairs(clients):
        return [(c, SimpleNamespace(extra={"group_id": None, "ma_syncgroup_id": "syncgroup_beta"})) for c in clients]

    monkeypatch.setattr(playback, "build_device_snapshot_pairs", _pairs)

    resp = api_client.put("/api/v1/groups/syncgroup_beta/volume", json={"level": 30})

    assert resp.status_code == 200
    assert ("bluez_sink.AA.a2dp_sink", 30) in sinks
    assert ("bluez_sink.BB.a2dp_sink", 30) in sinks


def test_pause_all_counts_a_music_assistant_sync_group_once(api_client, speakers, monkeypatch, bridge_loop):
    kitchen, lounge = _Speaker("kitchen"), _Speaker("lounge")
    speakers.extend([kitchen, lounge])

    def _pairs(clients):
        return [(c, SimpleNamespace(extra={"group_id": None, "ma_syncgroup_id": "syncgroup_beta"})) for c in clients]

    monkeypatch.setattr(playback, "build_device_snapshot_pairs", _pairs)

    assert api_client.post("/api/v1/playback", json={"action": "pause"}).json() == {"action": "pause", "count": 1}
