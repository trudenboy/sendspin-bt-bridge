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


# A compensated chunk only has to reach PulseAudio before the sink renders it,
# i.e. the sink's own lookahead (PA "sink latency") ahead of its play time.
# Measured on PulseAudio 17 and pipewire-pulse null sinks at buffer 200/600/800:
# a steady 60 ms lead played every chunk on time. So the declared lead stays as
# configured and is raised only when a measured sink lookahead + margin exceeds it.


@pytest.mark.parametrize("pulse_ms", [200, 600, 800])
def test_the_declared_lead_is_as_configured_until_a_sink_lookahead_is_measured(tmp_path, pulse_ms):
    daemon = _daemon(tmp_path, pulse_latency_msec=pulse_ms)

    assert daemon.declared_lead_time_ms(250.0) == 250.0


@pytest.mark.parametrize(("lookahead_ms", "expected"), [(25.3, 250.0), (230.0, 280.0), (400.0, 450.0)])
def test_the_declared_lead_covers_a_measured_sink_lookahead(tmp_path, lookahead_ms, expected):
    daemon = _daemon(tmp_path)
    daemon._note_sink_lookahead(lookahead_ms)

    assert daemon.declared_lead_time_ms(250.0) == expected


def test_a_larger_configured_lead_wins(tmp_path):
    daemon = _daemon(tmp_path)
    daemon._note_sink_lookahead(400.0)

    assert daemon.declared_lead_time_ms(1000.0) == 1000.0


def test_without_compensation_the_lead_is_as_configured(tmp_path):
    daemon = _daemon(tmp_path, compensate_output_latency=False)
    daemon._note_sink_lookahead(400.0)

    assert daemon.declared_lead_time_ms(250.0) == 250.0


@pytest.mark.asyncio
async def test_the_client_declares_the_configured_lead_and_buffer(tmp_path):
    daemon = _daemon(tmp_path, pulse_latency_msec=600)
    await daemon._load_identity_and_pairing_store()

    client = daemon._create_client(0.0)

    assert client.required_lead_time_ms == 250.0
    assert client.min_buffer_ms == 250.0
    assert daemon._bridge_status["declared_lead_time_ms"] == 250


@pytest.mark.asyncio
async def test_a_measured_lookahead_raises_the_lead_sent_to_the_server(tmp_path, monkeypatch, caplog):
    import sendspin_bridge.services.audio.pulse as pulse

    async def _latency(_pid):
        return {"buffer_ms": 64.0, "sink_ms": 400.0}

    monkeypatch.setattr(pulse, "apid_sink_input_latency", _latency)
    daemon = _daemon(tmp_path)
    await daemon._load_identity_and_pairing_store()
    daemon._client = daemon._create_client(0.0)
    daemon._report_player_state = MagicMock()  # type: ignore[method-assign]
    daemon._player = MagicMock()
    daemon._player.start_report.return_value = {"compensated_latency_ms": 200.0, "min_latency_ms": 110.0}
    daemon._player.stream_generation = 1

    await daemon._log_stream_start_timing(1)

    assert daemon._client.required_lead_time_ms == 450.0
    assert daemon._client.min_buffer_ms == 250.0
    assert daemon._bridge_status["declared_lead_time_ms"] == 450
    daemon._report_player_state.assert_called_once()


@pytest.mark.asyncio
async def test_a_lead_set_at_runtime_still_covers_the_lookahead_and_the_buffer_is_left_alone(monkeypatch):
    from sendspin_bridge.services.ipc.daemon_process import _read_commands
    from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION

    client = MagicMock()
    client.connected = False
    daemon = MagicMock()
    daemon._client = client
    daemon._bridge_status = {}
    daemon.declared_lead_time_ms = lambda value: max(value, 450.0)
    lines = [
        json.dumps({"cmd": "set_required_lead_time_ms", "value": 100, "protocol_version": IPC_PROTOCOL_VERSION}),
        json.dumps({"cmd": "set_min_buffer_ms", "value": 100, "protocol_version": IPC_PROTOCOL_VERSION}),
    ]

    async def _fake_connect_read_pipe(protocol_factory, _pipe):
        protocol = protocol_factory()
        protocol.connection_made(MagicMock())
        protocol.data_received(("\n".join(lines) + "\n").encode())
        protocol.eof_received()

    monkeypatch.setattr(asyncio.get_running_loop(), "connect_read_pipe", _fake_connect_read_pipe)
    await asyncio.wait_for(_read_commands([daemon], asyncio.Event()), timeout=2.0)

    client.set_required_lead_time_ms.assert_called_once_with(450.0)
    client.set_min_buffer_ms.assert_called_once_with(100.0)
    assert daemon._bridge_status["required_lead_time_ms"] == 100
    assert daemon._bridge_status["declared_lead_time_ms"] == 450


@pytest.mark.asyncio
async def test_a_sink_needing_more_than_the_compensated_latency_is_warned_about_once(tmp_path, monkeypatch, caplog):
    import sendspin_bridge.services.audio.pulse as pulse

    async def _none(_pid):
        return None

    monkeypatch.setattr(pulse, "apid_sink_input_latency", _none)
    daemon = _daemon(tmp_path)
    daemon._player = MagicMock()
    daemon._player.start_report.return_value = {"compensated_latency_ms": 200.0, "min_latency_ms": 260.0}
    daemon._player.stream_generation = 1
    caplog.set_level(logging.INFO, logger="sendspin_bridge.services.ipc.bridge_daemon")

    await daemon._log_stream_start_timing(1)
    await daemon._log_stream_start_timing(1)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "min latency" in r.getMessage()]
    assert len(warnings) == 1


def test_a_start_that_reuses_the_output_logs_no_new_timing_line(tmp_path):
    daemon = _daemon(tmp_path)
    player = MagicMock()
    player.stream_generation = 5  # start() keeps the running pipeline
    daemon._player = player
    daemon._schedule_start_timing_line = MagicMock()  # type: ignore[method-assign]
    daemon._reset_decoder = MagicMock()  # type: ignore[method-assign]
    message = MagicMock()
    message.payload.player.codec = "pcm"
    message.payload.player.sample_rate = 48000
    message.payload.player.bit_depth = 16
    message.payload.player.channels = 2

    daemon._on_stream_start(message)
    daemon._schedule_start_timing_line.assert_not_called()

    player.start.side_effect = lambda _fmt: setattr(player, "stream_generation", 6)
    daemon._on_stream_start(message)
    daemon._schedule_start_timing_line.assert_called_once()


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


async def _daemon_args_for(monkeypatch, params: dict):
    from types import SimpleNamespace

    import sendspin_bridge.services.ipc.bridge_daemon as bridge_daemon_mod
    from sendspin_bridge.services.ipc.daemon_process import _run

    captured: dict = {}

    class _Stop(BaseException):
        pass

    def _daemon(*_args, **kwargs):
        captured.update(kwargs)
        raise _Stop

    monkeypatch.setattr(bridge_daemon_mod, "DaemonArgs", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(bridge_daemon_mod, "BridgeDaemon", _daemon)
    with pytest.raises(_Stop):
        await _run({"player_name": "ENEBY Portable", "client_id": "eneby", "listen_port": 8933, **params})
    return captured["args"]


@pytest.mark.asyncio
@pytest.mark.parametrize(("compensate", "diag"), [(True, False), (False, True)])
async def test_the_configured_playout_settings_reach_the_daemon(monkeypatch, compensate, diag):
    monkeypatch.delenv("SENDSPIN_COMPENSATE_OUTPUT_LATENCY", raising=False)
    monkeypatch.delenv("SENDSPIN_PLAYOUT_DIAG", raising=False)

    args = await _daemon_args_for(monkeypatch, {"compensate_output_latency": compensate, "playout_diag": diag})

    assert args.compensate_output_latency is compensate
    assert args.playout_diag is diag


@pytest.mark.asyncio
async def test_env_vars_override_the_configured_playout_settings(monkeypatch):
    monkeypatch.setenv("SENDSPIN_COMPENSATE_OUTPUT_LATENCY", "0")
    monkeypatch.setenv("SENDSPIN_PLAYOUT_DIAG", "1")

    args = await _daemon_args_for(monkeypatch, {"compensate_output_latency": True, "playout_diag": False})

    assert args.compensate_output_latency is False
    assert args.playout_diag is True


@pytest.mark.asyncio
async def test_a_parent_that_sends_no_playout_settings_gets_the_defaults(monkeypatch):
    monkeypatch.delenv("SENDSPIN_COMPENSATE_OUTPUT_LATENCY", raising=False)
    monkeypatch.delenv("SENDSPIN_PLAYOUT_DIAG", raising=False)

    args = await _daemon_args_for(monkeypatch, {})

    assert args.compensate_output_latency is True
    assert args.playout_diag is False
