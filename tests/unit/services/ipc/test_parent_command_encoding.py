"""The parent sends commands, not hand-built dictionaries.

Every caller used to assemble `{"cmd": ..., ...}` itself, so the payload keys
and the ranges the daemon enforces were duplicated across seven modules.  The
client now accepts a command object and encodes it at the boundary; the dict
form still works for the routes that forward an already-validated action.
"""

from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.bridge.client import SendspinClient
from sendspin_bridge.services.ipc.commands import Reconnect, SetStandby, SetVolume


class _RecordingCommandService:
    def __init__(self):
        self.sent: list[dict] = []

    async def send(self, _proc, cmd):
        self.sent.append(cmd)


@pytest.fixture()
def client():
    cl = SendspinClient.__new__(SendspinClient)
    cl.player_name = "TestSpeaker"
    cl._daemon_proc = object()
    cl._command_service = _RecordingCommandService()
    return cl


def test_a_command_object_is_encoded_for_the_wire(client):
    asyncio.run(client._send_subprocess_command(SetVolume(value=40)))

    sent = client._command_service.sent[0]
    assert sent["cmd"] == "set_volume"
    assert sent["value"] == 40
    assert sent["protocol_version"]


def test_command_ranges_are_applied_before_the_wire(client):
    asyncio.run(client._send_subprocess_command(Reconnect(delay_s=1.5)))

    assert client._command_service.sent[0] == {
        "cmd": "reconnect",
        "delay": 1.5,
        "protocol_version": client._command_service.sent[0]["protocol_version"],
    }


def test_a_command_without_a_payload_still_encodes(client):
    asyncio.run(client._send_subprocess_command(SetStandby()))

    assert client._command_service.sent[0]["cmd"] == "set_standby"
    assert client._command_service.sent[0]["sink"] is None


def test_a_plain_dict_is_still_accepted(client):
    asyncio.run(client._send_subprocess_command({"cmd": "pause"}))

    assert client._command_service.sent[0] == {"cmd": "pause"}
