"""``POST /devices/{id}/transport``: native Sendspin controller commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import sendspin_bridge.application.status as status
import sendspin_bridge.bridge.state as state
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

ALL_COMMANDS = [
    "play",
    "pause",
    "stop",
    "next",
    "previous",
    "volume",
    "mute",
    "repeat_off",
    "repeat_one",
    "repeat_all",
    "shuffle",
    "unshuffle",
    "switch",
    "seek",
]


def _client(player_id: str, supported=None, result=True, **status_extra):
    client = MagicMock()
    client.player_id = player_id
    client.player_name = player_id.title()
    client.status = {"supported_commands": supported, **status_extra}
    client.send_transport_command = AsyncMock(return_value=result)
    return client


@pytest.fixture
def registry(monkeypatch):
    clients: list = []
    monkeypatch.setattr(status, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=clients))
    return clients


def test_the_command_reaches_the_addressed_device_only(api_client, registry, bridge_loop):
    """Addressing by id, never by list position: the old index path mis-routed
    Next/Pause after the device list was reordered."""
    kitchen, bedroom = _client("kitchen"), _client("bedroom")
    registry.extend([kitchen, bedroom])

    resp = api_client.post("/api/v1/devices/bedroom/transport", json={"command": "next"})

    assert resp.status_code == 204
    bedroom.send_transport_command.assert_awaited_once_with("next", value=None)
    kitchen.send_transport_command.assert_not_called()


def test_volume_carries_its_value(api_client, registry, bridge_loop):
    registry.append(_client("kitchen", supported=["volume"]))
    assert (
        api_client.post("/api/v1/devices/kitchen/transport", json={"command": "volume", "value": 75}).status_code == 204
    )
    registry[0].send_transport_command.assert_awaited_once_with("volume", value=75)


def test_unknown_device_is_404(api_client, registry):
    assert api_client.post("/api/v1/devices/ghost/transport", json={"command": "play"}).status_code == 404


def test_a_command_the_device_does_not_offer_is_409(api_client, registry):
    registry.append(_client("kitchen", supported=["play", "pause"]))
    resp = api_client.post("/api/v1/devices/kitchen/transport", json={"command": "stop"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "unsupported_command"


def test_before_the_device_reports_its_commands_any_valid_command_passes(api_client, registry, bridge_loop):
    registry.append(_client("kitchen", supported=None))
    assert api_client.post("/api/v1/devices/kitchen/transport", json={"command": "play"}).status_code == 204


def test_a_stopped_daemon_is_409(api_client, registry, bridge_loop):
    registry.append(_client("kitchen", result=False))
    resp = api_client.post("/api/v1/devices/kitchen/transport", json={"command": "play"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "not_running"


@pytest.mark.parametrize("command", ALL_COMMANDS)
def test_every_media_command_is_accepted_by_the_contract(api_client, registry, command):
    # No device: a valid command gets as far as the lookup (404), never 422.
    assert api_client.post("/api/v1/devices/ghost/transport", json={"command": command}).status_code == 404


# ── seek: an absolute position in ms, within the track ──────────────────────


def test_seek_sends_the_position_to_the_native_controller(api_client, registry, bridge_loop):
    registry.append(_client("kitchen", supported=["seek"], track_duration_ms=200_000))
    resp = api_client.post("/api/v1/devices/kitchen/transport", json={"command": "seek", "value": 90_000})
    assert resp.status_code == 204
    registry[0].send_transport_command.assert_awaited_once_with("seek", value=90_000)


@pytest.fixture
def ma_now_playing():
    """Music Assistant's now-playing cache, seeded the way the MA monitor fills it."""
    state.clear_ma_now_playing()
    state.set_ma_groups({})
    yield state
    state.clear_ma_now_playing()
    state.set_ma_groups({})


def test_seek_uses_music_assistants_duration_when_the_daemon_has_none(
    api_client, registry, bridge_loop, ma_now_playing
):
    """The real client status carries no MA now-playing: the length comes from MA's cache."""
    registry.append(_client("kitchen", supported=["seek"]))
    ma_now_playing.set_ma_now_playing_for_group("kitchen", {"duration": 180})
    ok = api_client.post("/api/v1/devices/kitchen/transport", json={"command": "seek", "value": 180_000})
    assert ok.status_code == 204
    past = api_client.post("/api/v1/devices/kitchen/transport", json={"command": "seek", "value": 180_001})
    assert past.status_code == 400
    assert past.json()["code"] == "out_of_range"


def test_seek_finds_the_length_through_the_players_ma_syncgroup(api_client, registry, bridge_loop, ma_now_playing):
    registry.append(_client("kitchen", supported=["seek"]))
    ma_now_playing.set_ma_groups({"kitchen": {"id": "syncgroup_1", "name": "Downstairs"}})
    ma_now_playing.set_ma_now_playing_for_group("syncgroup_1", {"duration": 120.5})
    resp = api_client.post("/api/v1/devices/kitchen/transport", json={"command": "seek", "value": 120_500})
    assert resp.status_code == 204


def test_seek_checks_music_assistants_length_first_as_the_bar_does(api_client, registry, bridge_loop, ma_now_playing):
    """The bar is drawn from MA's length first; a click at its very end must not be rejected
    because the daemon reports a slightly shorter track."""
    registry.append(_client("kitchen", supported=["seek"], track_duration_ms=179_000))
    ma_now_playing.set_ma_now_playing_for_group("kitchen", {"duration": 180.4})
    resp = api_client.post("/api/v1/devices/kitchen/transport", json={"command": "seek", "value": 180_400})
    assert resp.status_code == 204
    registry[0].send_transport_command.assert_awaited_once_with("seek", value=180_400)


@pytest.mark.parametrize("value", [-1, 200_001])
def test_seek_outside_the_track_is_400(api_client, registry, value):
    registry.append(_client("kitchen", supported=["seek"], track_duration_ms=200_000))
    resp = api_client.post("/api/v1/devices/kitchen/transport", json={"command": "seek", "value": value})
    assert resp.status_code == 400
    assert resp.json()["code"] == "out_of_range"
    registry[0].send_transport_command.assert_not_called()


@pytest.mark.parametrize("value", [None, "later", True, 1.5])
def test_seek_needs_a_whole_number_position(api_client, registry, value):
    registry.append(_client("kitchen", supported=["seek"], track_duration_ms=200_000))
    resp = api_client.post("/api/v1/devices/kitchen/transport", json={"command": "seek", "value": value})
    assert resp.status_code == 400
    assert resp.json()["code"] == "invalid_position"


def test_seek_without_a_known_duration_is_409(api_client, registry):
    registry.append(_client("kitchen", supported=["seek"]))
    resp = api_client.post("/api/v1/devices/kitchen/transport", json={"command": "seek", "value": 1000})
    assert resp.status_code == 409
    assert resp.json()["code"] == "unknown_duration"


def test_seek_the_server_does_not_offer_is_409(api_client, registry):
    registry.append(_client("kitchen", supported=["play", "pause"], track_duration_ms=200_000))
    resp = api_client.post("/api/v1/devices/kitchen/transport", json={"command": "seek", "value": 1000})
    assert resp.status_code == 409
    assert resp.json()["code"] == "unsupported_command"
