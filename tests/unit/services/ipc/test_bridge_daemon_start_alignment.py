"""The daemon fixes and compensates the output latency, declares a lead that
covers it, and logs one timing line per stream start."""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from sendspin_bridge.services.ipc.bridge_daemon import BridgeDaemon, DaemonArgs

if TYPE_CHECKING:
    from pathlib import Path

SINK = "bluez_sink.6C_5C_3D_35_17_99.a2dp_sink"


def _daemon(tmp_path: Path, **overrides) -> BridgeDaemon:
    args = DaemonArgs(
        client_id="eneby",
        client_name="ENEBY Portable",
        identity_path=str(tmp_path / "identity.key"),
        sink_name=SINK,
        pulse_latency_msec=200,
    )
    return BridgeDaemon(replace(args, **overrides), {}, SINK)


async def _start(daemon: BridgeDaemon, monkeypatch) -> tuple[list[dict], list[dict]]:
    """Run the daemon's startup; return the StreamPlayer and sink factory kwargs."""
    import sendspin_bridge.services.ipc.bridge_daemon as daemon_module

    players: list[dict] = []
    sinks: list[dict] = []
    monkeypatch.setattr(daemon_module, "StreamPlayer", lambda **kw: players.append(kw) or MagicMock())
    monkeypatch.setattr(daemon_module, "default_pulsesink_factory", lambda **kw: sinks.append(kw) or MagicMock())

    async def _load():
        daemon._identity = None

    async def _connect():
        raise asyncio.CancelledError

    daemon._load_identity_and_pairing_store = _load  # type: ignore[method-assign]
    daemon._create_client = MagicMock(return_value=None)  # type: ignore[method-assign]
    daemon._run_server_initiated = _connect  # type: ignore[method-assign]
    await daemon.run()
    return players, sinks


@pytest.mark.asyncio
async def test_player_compensates_the_pulse_buffer_latency(tmp_path, monkeypatch):
    players, sinks = await _start(_daemon(tmp_path, pulse_latency_msec=200), monkeypatch)

    assert players[0]["output_latency_ns"] == 200_000_000
    assert sinks[0]["buffer_time_us"] == 200_000


@pytest.mark.asyncio
async def test_compensation_can_be_turned_off(tmp_path, monkeypatch):
    players, _sinks = await _start(_daemon(tmp_path, compensate_output_latency=False), monkeypatch)

    assert players[0]["output_latency_ns"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("diag", [False, True])
async def test_only_playout_diag_lets_the_sink_provide_its_clock(tmp_path, monkeypatch, diag):
    _players, sinks = await _start(_daemon(tmp_path, playout_diag=diag), monkeypatch)

    assert sinks[0]["provide_clock"] is diag


def test_a_retargeted_player_keeps_the_compensation(tmp_path, monkeypatch):
    import sendspin_bridge.services.ipc.bridge_daemon as daemon_module

    players: list[dict] = []
    monkeypatch.setattr(daemon_module, "StreamPlayer", lambda **kw: players.append(kw) or MagicMock())
    daemon = _daemon(tmp_path, pulse_latency_msec=300)
    daemon._player = MagicMock()

    daemon.retarget_sink("sendspin_fallback")

    assert players[0]["output_latency_ns"] == 300_000_000


@pytest.mark.parametrize(
    ("pulse_ms", "configured", "expected"),
    [(200, 250.0, 250.0), (600, 250.0, 650.0), (800, 250.0, 850.0), (200, 1000.0, 1000.0)],
)
def test_declared_lead_covers_the_compensated_latency(tmp_path, pulse_ms, configured, expected):
    daemon = _daemon(tmp_path, pulse_latency_msec=pulse_ms)

    assert daemon.declared_timing_ms(configured) == expected


def test_without_compensation_the_lead_is_as_configured(tmp_path):
    daemon = _daemon(tmp_path, pulse_latency_msec=800, compensate_output_latency=False)

    assert daemon.declared_timing_ms(250.0) == 250.0


@pytest.mark.asyncio
async def test_the_client_declares_the_covering_lead_to_the_server(tmp_path):
    daemon = _daemon(tmp_path, pulse_latency_msec=600)
    await daemon._load_identity_and_pairing_store()

    client = daemon._create_client(0.0)

    assert client.required_lead_time_ms == 650.0
    assert client.min_buffer_ms == 650.0


@pytest.mark.asyncio
async def test_a_lead_set_at_runtime_still_covers_the_latency(monkeypatch):
    from sendspin_bridge.services.ipc.daemon_process import _read_commands
    from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION

    client = MagicMock()
    client.connected = False
    daemon = MagicMock()
    daemon._client = client
    daemon._bridge_status = {}
    daemon.declared_timing_ms = lambda value: max(value, 650.0)
    line = json.dumps({"cmd": "set_required_lead_time_ms", "value": 100, "protocol_version": IPC_PROTOCOL_VERSION})

    async def _fake_connect_read_pipe(protocol_factory, _pipe):
        protocol = protocol_factory()
        protocol.connection_made(MagicMock())
        protocol.data_received((line + "\n").encode())
        protocol.eof_received()

    monkeypatch.setattr(asyncio.get_running_loop(), "connect_read_pipe", _fake_connect_read_pipe)
    await asyncio.wait_for(_read_commands([daemon], asyncio.Event()), timeout=2.0)

    client.set_required_lead_time_ms.assert_called_once_with(650.0)
    assert daemon._bridge_status["required_lead_time_ms"] == 100


@pytest.mark.asyncio
async def test_a_stream_start_logs_one_timing_line(tmp_path, monkeypatch, caplog):
    import sendspin_bridge.services.audio.pulse as pulse

    async def _latency(_pid):
        return {"buffer_ms": 87.0, "sink_ms": 25.3}

    monkeypatch.setattr(pulse, "apid_sink_input_latency", _latency)
    daemon = _daemon(tmp_path)
    daemon._player = MagicMock()
    daemon._player.start_report.return_value = {
        "compensated_latency_ms": 200.0,
        "configured_latency_ms": 200.0,
        "min_latency_ms": 110.0,
        "first_lead_ms": 1250.0,
        "first_buffer_margin_ms": 1249.6,
    }
    daemon._player.stream_generation = 3
    daemon._player_delay_ms = 0.0
    caplog.set_level(logging.INFO, logger="sendspin_bridge.services.ipc.bridge_daemon")

    await daemon._log_stream_start_timing(3)

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Stream start timing:")]
    assert len(lines) == 1
    assert "PA sink-input latency 112.3 ms" in lines[0]
    assert "compensated 200.0 ms" in lines[0]


@pytest.mark.asyncio
async def test_no_timing_line_for_an_output_that_is_already_gone(tmp_path, caplog):
    daemon = _daemon(tmp_path)
    daemon._player = MagicMock()
    daemon._player.stream_generation = 4  # a newer output replaced it
    caplog.set_level(logging.INFO, logger="sendspin_bridge.services.ipc.bridge_daemon")

    await daemon._log_stream_start_timing(3)

    assert not [r for r in caplog.records if r.getMessage().startswith("Stream start timing:")]


@pytest.mark.parametrize(
    ("value", "default", "expected"),
    [(None, True, True), ("", True, True), ("0", True, False), ("off", True, False), ("1", False, True)],
)
def test_env_flags(monkeypatch, value, default, expected):
    from sendspin_bridge.services.ipc.daemon_process import _env_flag

    if value is None:
        monkeypatch.delenv("SENDSPIN_TEST_FLAG", raising=False)
    else:
        monkeypatch.setenv("SENDSPIN_TEST_FLAG", value)

    assert _env_flag("SENDSPIN_TEST_FLAG", default=default) is expected
