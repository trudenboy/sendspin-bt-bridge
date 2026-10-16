"""A delay changed in Music Assistant is kept by the bridge and survives a restart.

Music Assistant changes a player's delay with ``server/command`` →
``set_output_delay``.  aiosendspin applies it to the live client, the daemon
mirrors it into its status, and the parent must take it from there: show it,
save it to the player's config, and spawn the next daemon with it — without
treating the echo of a delay the bridge itself set as a change from MA.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiosendspin.client.connection import SendspinConnection
from aiosendspin.models.core import ServerCommandPayload
from aiosendspin.models.player import PlayerCommandPayload
from aiosendspin.models.types import PlayerCommand

from sendspin_bridge.bridge.client import SendspinClient
from sendspin_bridge.services.ipc.bridge_daemon import BridgeDaemon, DaemonArgs

if TYPE_CHECKING:
    from pathlib import Path

PLAYER_ID = "dac-1"
MAC = "AA:BB:CC:DD:EE:FF"


def _write_config(path: Path, players: list[dict]) -> None:
    path.write_text(json.dumps({"CONFIG_SCHEMA_VERSION": 6, "players": players, "PLAYER_STATE": {}}))


def _players(path: Path) -> dict[str, dict]:
    return {p["id"]: p for p in json.loads(path.read_text())["players"]}


def _pulse_player() -> dict:
    return {"id": PLAYER_ID, "name": "Desk DAC", "backend": {"type": "pulse_sink", "sink": "alsa_output.usb"}}


def _bt_player() -> dict:
    return {"id": "spk-1", "name": "Speaker", "backend": {"type": "bluetooth_a2dp", "mac": MAC, "adapter": ""}}


def _parent(config_id: str, *, mac: str | None = None, static_delay_ms: float | None = None) -> SendspinClient:
    client = SendspinClient("Desk DAC", "localhost", 9000, static_delay_ms=static_delay_ms)
    client.player_id = client.config_id = config_id
    if mac is None:
        client.backend_type = "pulse_sink"
        client.bt_manager = None  # type: ignore[assignment]
    else:
        client.bt_manager = SimpleNamespace(mac_address=mac)  # type: ignore[assignment]
    # Run the debounced write inline so the test reads the file it produced.
    client._schedule_persist = lambda _key, fn, ident, value: fn(ident, value)  # type: ignore[method-assign]
    return client


async def _status(client: SendspinClient, **fields) -> None:
    await client._handle_daemon_message({"type": "status", **fields})


# ── (a) the daemon mirrors a server-set delay ───────────────────────────────


@pytest.mark.asyncio
async def test_daemon_mirrors_a_delay_applied_by_aiosendspin_from_the_server(tmp_path: Path):
    args = DaemonArgs(
        client_id="x",
        client_name="Speaker",
        identity_path=str(tmp_path / "identity.key"),
        pairing_store_path=str(tmp_path / "pairing.json"),
    )
    status: dict = {}
    daemon = BridgeDaemon(args, status, None)
    await daemon._load_identity_and_pairing_store()
    client = daemon._create_client(0.0)
    daemon._client = client
    connection = SendspinConnection(client)
    connection._reported_supported_commands = frozenset(client.state_supported_commands)
    client._admitted_connection = connection

    await connection._handle_server_command(
        ServerCommandPayload(player=PlayerCommandPayload(command=PlayerCommand.SET_OUTPUT_DELAY, output_delay_ms=120))
    )

    assert client.output_delay_ms == 120.0
    assert status["static_delay_ms"] == 120
    assert daemon._static_delay_ms == 120.0


@pytest.mark.asyncio
async def test_daemon_reports_its_start_delay_before_any_server_command(tmp_path: Path):
    args = DaemonArgs(
        client_id="x",
        client_name="Speaker",
        identity_path=str(tmp_path / "identity.key"),
        static_delay_ms=120.0,
    )
    status: dict = {}
    daemon = BridgeDaemon(args, status, None)
    daemon._player = MagicMock()
    seen: dict = {}

    async def load():
        daemon._identity = SimpleNamespace(peer_id="peer")

    def create(delay):
        seen["delay"] = delay
        seen["status"] = dict(status)
        return SimpleNamespace(disconnect=AsyncMock())

    async def inbound():
        raise asyncio.CancelledError

    daemon._load_identity_and_pairing_store = load  # type: ignore[method-assign]
    daemon._create_client = create  # type: ignore[method-assign]
    daemon._run_server_initiated = inbound  # type: ignore[method-assign]

    await daemon.run()

    assert seen["delay"] == 120.0
    assert seen["status"]["static_delay_ms"] == 120


# ── (a)+(b) the parent shows and saves a delay set in Music Assistant ──────


@pytest.mark.asyncio
async def test_a_delay_from_music_assistant_is_shown_and_saved_for_a_local_output(tmp_config):
    _write_config(tmp_config, [_pulse_player()])
    client = _parent(PLAYER_ID)

    await _status(client, static_delay_ms=120)

    assert client.static_delay_ms == 120.0
    assert client.status["static_delay_ms"] == 120
    assert client.status["static_delay_source"] == "music_assistant"
    saved = _players(tmp_config)[PLAYER_ID]
    assert saved["static_delay_ms"] == 120
    assert saved["static_delay_source"] == "music_assistant"


@pytest.mark.asyncio
async def test_a_delay_from_music_assistant_is_saved_for_a_bluetooth_speaker(tmp_config):
    _write_config(tmp_config, [_bt_player()])
    client = _parent("spk-1", mac=MAC)

    await _status(client, static_delay_ms=120)

    assert client.static_delay_ms == 120.0
    saved = _players(tmp_config)["spk-1"]
    assert saved["static_delay_ms"] == 120
    assert saved["static_delay_source"] == "music_assistant"


@pytest.mark.asyncio
async def test_repeated_status_with_the_same_delay_is_saved_once(tmp_config):
    _write_config(tmp_config, [_pulse_player()])
    client = _parent(PLAYER_ID)
    writes: list[int] = []
    client._schedule_persist = lambda key, _fn, _ident, value: key == "static_delay" and writes.append(value)  # type: ignore[method-assign]

    for _ in range(5):
        await _status(client, static_delay_ms=120, volume=50)

    assert writes == [120]


# ── no feedback loop: the bridge's own delay is not taken for MA's ─────────


@pytest.mark.asyncio
async def test_the_spawn_delay_reported_back_is_not_a_change(tmp_config):
    _write_config(tmp_config, [{**_pulse_player(), "static_delay_ms": 80, "static_delay_source": "manual"}])
    client = _parent(PLAYER_ID, static_delay_ms=80.0)
    client._last_daemon_static_delay_ms = 80.0

    await _status(client, static_delay_ms=80)

    saved = _players(tmp_config)[PLAYER_ID]
    assert saved["static_delay_source"] == "manual"
    assert client.status["static_delay_source"] != "music_assistant"


@pytest.mark.asyncio
@pytest.mark.parametrize("early_report", [80, 200], ids=["stale-status", "echo-before-send-returns"])
async def test_a_delay_set_from_the_bridge_is_not_echoed_back_as_music_assistant(tmp_config, early_report):
    _write_config(tmp_config, [{**_pulse_player(), "static_delay_ms": 80, "static_delay_source": "manual"}])
    client = _parent(PLAYER_ID, static_delay_ms=80.0)
    client._last_daemon_static_delay_ms = 80.0
    client._daemon_proc = SimpleNamespace(returncode=None, pid=1)  # type: ignore[assignment]
    sent: list[object] = []

    async def record(cmd):
        sent.append(cmd)
        # A status emitted before the daemon processed the command still
        # carries the old delay, or the daemon echoes the new one before the
        # send returns; neither is a change from Music Assistant.
        await _status(client, static_delay_ms=early_report)

    client._send_subprocess_command = record  # type: ignore[method-assign]
    client.is_running = lambda: True  # type: ignore[method-assign]

    assert await client.apply_hot_config({"static_delay_ms": 200}) == ["static_delay_ms"]
    await _status(client, static_delay_ms=200)

    assert len(sent) == 1
    assert client.static_delay_ms == 200.0
    saved = _players(tmp_config)[PLAYER_ID]
    assert saved["static_delay_ms"] == 80  # this path is saved by its caller, not as an MA change
    assert saved["static_delay_source"] == "manual"
    assert client.status["static_delay_source"] != "music_assistant"


# ── (c) after a restart the daemon starts with the saved delay ────────────


@pytest.mark.asyncio
async def test_after_a_restart_the_daemon_starts_with_and_reports_the_saved_delay(tmp_config, tmp_path: Path):
    from sendspin_bridge.services.bluetooth.device_activation import _client_kwargs

    _write_config(tmp_config, [_pulse_player()])
    await _status(_parent(PLAYER_ID), static_delay_ms=120)

    player = _players(tmp_config)[PLAYER_ID]
    kwargs = _client_kwargs(
        player,
        listen_port=8930,
        context=SimpleNamespace(client_factory=SendspinClient, effective_bridge=""),  # type: ignore[arg-type]
        keep_alive_default="silence",
    )
    assert kwargs["static_delay_ms"] == 120.0

    args = DaemonArgs(
        client_id="x",
        client_name="Desk DAC",
        identity_path=str(tmp_path / "identity.key"),
        pairing_store_path=str(tmp_path / "pairing.json"),
        static_delay_ms=kwargs["static_delay_ms"],
    )
    daemon = BridgeDaemon(args, {}, None)
    await daemon._load_identity_and_pairing_store()
    client = daemon._create_client(daemon._static_delay_ms)
    # client/state carries the connection's delay, seeded from the client.
    assert SendspinConnection(client).output_delay_ms == 120.0
