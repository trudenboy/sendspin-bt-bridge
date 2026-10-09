"""``POST /devices/{id}/transport``: native Sendspin controller commands."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

import sendspin_bridge.application.status as status
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
]


def _client(player_id: str, supported=None, result=True):
    client = MagicMock()
    client.player_id = player_id
    client.player_name = player_id.title()
    client.status = {"supported_commands": supported}
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
