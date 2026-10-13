"""``volume_controller: sendspin`` scales the stream and never touches the sink's level."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiosendspin.models.types import PlayerCommand

from sendspin_bridge.services.ipc.bridge_daemon import BridgeDaemon, DaemonArgs

if TYPE_CHECKING:
    from pathlib import Path

SINK = "alsa_output.pci-0000_00_1b.0.analog-stereo"


@pytest.fixture
def sink_writes(monkeypatch):
    """Every write to a sink's volume or mute, from any controller."""
    import sendspin_bridge.services.audio.pa_volume_controller as pa_controller
    import sendspin_bridge.services.audio.pulse as pulse

    writes: list[tuple] = []

    async def _record(*args):
        writes.append(args)
        return True

    for module in (pulse, pa_controller):
        monkeypatch.setattr(module, "aset_sink_volume", _record)
        monkeypatch.setattr(module, "aset_sink_mute", _record)
    return writes


def _daemon(tmp_path: Path, volume_controller: str, *, volume: int = 40) -> BridgeDaemon:
    args = DaemonArgs(
        client_id="dac-1",
        client_name="Desk DAC",
        identity_path=str(tmp_path / "identity.key"),
        initial_volume=volume,
        sink_name=SINK,
        volume_controller=volume_controller,
    )
    return BridgeDaemon(args, {}, SINK)


def _volume_command(volume: int):
    return SimpleNamespace(player=SimpleNamespace(command=PlayerCommand.VOLUME, volume=volume, mute=None))


def _mute_command(muted: bool):
    return SimpleNamespace(player=SimpleNamespace(command=PlayerCommand.MUTE, volume=None, mute=muted))


async def _run_until_started(daemon: BridgeDaemon, monkeypatch) -> MagicMock:
    """Run the daemon's startup with a fake player and no connection."""
    import sendspin_bridge.services.ipc.bridge_daemon as daemon_module

    player = MagicMock()
    monkeypatch.setattr(daemon_module, "StreamPlayer", lambda **_kw: player)

    async def _load():
        daemon._identity = None

    async def _connect():
        raise asyncio.CancelledError

    daemon._load_identity_and_pairing_store = _load  # type: ignore[method-assign]
    daemon._create_client = MagicMock(return_value=None)  # type: ignore[method-assign]
    daemon._run_server_initiated = _connect  # type: ignore[method-assign]
    await daemon.run()
    return player


@pytest.mark.asyncio
async def test_sendspin_controller_starts_without_touching_the_sink(tmp_path, monkeypatch, sink_writes):
    created: list[str] = []

    import sendspin_bridge.services.audio.pa_volume_controller as pa_controller

    monkeypatch.setattr(pa_controller, "PulseVolumeController", lambda sink: created.append(sink) or MagicMock())
    daemon = _daemon(tmp_path, "sendspin", volume=40)

    player = await _run_until_started(daemon, monkeypatch)

    assert created == []
    assert sink_writes == []
    # The start volume is the stream's, on a cubic taper (PulseAudio's percent scale).
    player.set_volume.assert_called_with(pytest.approx(0.4**3), False)


@pytest.mark.asyncio
async def test_pa_controller_still_drives_the_sink(tmp_path, monkeypatch):
    created: list[str] = []

    import sendspin_bridge.services.audio.pa_volume_controller as pa_controller

    def _controller(sink):
        created.append(sink)
        return SimpleNamespace(start_monitoring=AsyncMock(), stop_monitoring=AsyncMock())

    monkeypatch.setattr(pa_controller, "PulseVolumeController", _controller)
    daemon = _daemon(tmp_path, "pa")

    player = await _run_until_started(daemon, monkeypatch)

    assert created == [SINK]
    player.set_volume.assert_not_called()


@pytest.mark.asyncio
async def test_music_assistant_volume_scales_the_stream_and_is_reported(tmp_path, sink_writes):
    daemon = _daemon(tmp_path, "sendspin")
    daemon._player = MagicMock()
    daemon._client = SimpleNamespace(connected=True, send_player_state=AsyncMock())

    daemon._handle_server_command(_volume_command(50))
    daemon._handle_server_command(_mute_command(True))
    await asyncio.sleep(0)

    assert sink_writes == []
    assert daemon._player.set_volume.call_args_list[0].args == (pytest.approx(0.125), False)
    assert daemon._player.set_volume.call_args_list[-1].args == (pytest.approx(0.125), True)
    assert daemon._bridge_status["volume"] == 50
    assert daemon._bridge_status["muted"] is True


@pytest.mark.asyncio
async def test_a_bridge_volume_change_scales_the_stream_and_tells_music_assistant(tmp_path, sink_writes):
    daemon = _daemon(tmp_path, "sendspin")
    daemon._player = MagicMock()
    client = SimpleNamespace(connected=True, send_player_state=AsyncMock())
    daemon._client = client

    daemon.apply_bridge_volume(volume=20)
    daemon.apply_bridge_volume(muted=True)
    await asyncio.sleep(0)

    assert sink_writes == []
    assert daemon._player.set_volume.call_args_list[-1].args == (pytest.approx(0.008), True)
    client.send_player_state.assert_awaited_with(available=True, volume=20, muted=True)


@pytest.mark.asyncio
async def test_a_bridge_volume_change_under_pa_is_left_to_the_sink_controller(tmp_path):
    """Under ``pa`` the parent already changed the sink; the daemon's controller reports it."""
    daemon = _daemon(tmp_path, "pa")
    daemon._player = MagicMock()
    client = SimpleNamespace(connected=True, send_player_state=AsyncMock())
    daemon._client = client

    daemon.apply_bridge_volume(volume=20)
    await asyncio.sleep(0)

    daemon._player.set_volume.assert_not_called()
    client.send_player_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_set_volume_command_reaches_the_stream(tmp_path, sink_writes, monkeypatch):
    import json

    from sendspin_bridge.services.ipc.daemon_process import _read_commands
    from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION

    daemon = _daemon(tmp_path, "sendspin")
    daemon._player = MagicMock()
    lines = [
        json.dumps({"cmd": "set_volume", "value": 30, "protocol_version": IPC_PROTOCOL_VERSION}),
        json.dumps({"cmd": "set_mute", "muted": True, "protocol_version": IPC_PROTOCOL_VERSION}),
    ]

    async def _fake_connect_read_pipe(protocol_factory, _pipe):
        protocol = protocol_factory()
        protocol.connection_made(MagicMock())
        protocol.data_received(("\n".join(lines) + "\n").encode())
        protocol.eof_received()

    monkeypatch.setattr(asyncio.get_running_loop(), "connect_read_pipe", _fake_connect_read_pipe)
    await asyncio.wait_for(_read_commands([daemon], asyncio.Event()), timeout=2.0)

    assert daemon._bridge_status["volume"] == 30
    assert daemon._bridge_status["muted"] is True
    assert daemon._player.set_volume.call_args_list[-1].args == (pytest.approx(0.027), True)
    assert sink_writes == []
