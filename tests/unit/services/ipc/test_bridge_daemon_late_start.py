"""A stream start whose output is slow to open.

On HAOS (PulseAudio 17, an A2DP speaker) the first chunks of every stream
reached the player 0.7-1.6 s after their play time: handling stream/start
(the decoder and, above all, opening the PulseAudio output on a Bluetooth sink)
held the daemon's loop that long while the server's chunks, sent with the
declared 250 ms lead plus the output delay, waited unread. The daemon now
measures that cost, logs it on the start line, and declares a lead that covers it.
The output delay is not part of the lead: the server adds it on top (Sendspin
spec: required_lead_time_ms "excludes output_delay_ms").
"""

from __future__ import annotations

import asyncio
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


def _start_message(codec: str = "flac") -> MagicMock:
    message = MagicMock()
    message.payload.player.codec = codec
    message.payload.player.sample_rate = 48000
    message.payload.player.bit_depth = 16
    message.payload.player.channels = 2
    message.payload.player.codec_header = None
    return message


def _fake_clock(monkeypatch) -> dict[str, float]:
    import sendspin_bridge.services.ipc.bridge_daemon as daemon_module

    now = {"s": 100.0}
    monkeypatch.setattr(daemon_module, "_monotonic", lambda: now["s"])
    return now


def test_a_stream_start_measures_how_long_the_output_took_to_open(tmp_path, monkeypatch):
    now = _fake_clock(monkeypatch)
    daemon = _daemon(tmp_path)
    daemon._schedule_start_timing_line = MagicMock()  # type: ignore[method-assign]

    def _decoder(*_args):
        now["s"] += 0.004

    def _open(_fmt):
        now["s"] += 0.962
        player.stream_generation += 1

    daemon._reset_decoder = _decoder  # type: ignore[method-assign]
    player = MagicMock()
    player.stream_generation = 1
    player.start.side_effect = _open
    daemon._player = player

    daemon._on_stream_start(_start_message())

    assert daemon._start_cost == {"decoder_ms": pytest.approx(4.0), "output_open_ms": pytest.approx(962.0)}


def test_a_start_that_keeps_the_running_output_measures_nothing_new(tmp_path, monkeypatch):
    now = _fake_clock(monkeypatch)
    daemon = _daemon(tmp_path)
    daemon._schedule_start_timing_line = MagicMock()  # type: ignore[method-assign]
    daemon._reset_decoder = MagicMock()  # type: ignore[method-assign]
    daemon._start_cost = {"decoder_ms": 1.0, "output_open_ms": 900.0}
    player = MagicMock()
    player.stream_generation = 1
    player.start.side_effect = lambda _fmt: now.__setitem__("s", now["s"] + 0.001)
    daemon._player = player

    daemon._on_stream_start(_start_message())

    assert daemon._start_cost == {"decoder_ms": 1.0, "output_open_ms": 900.0}


@pytest.mark.parametrize(
    ("open_ms", "expected"),
    [(None, 250.0), (60.0, 250.0), (962.0, 1100.0), (1500.0, 1600.0), (4000.0, 2000.0)],
)
def test_the_declared_lead_covers_the_time_the_output_took_to_open(tmp_path, open_ms, expected):
    daemon = _daemon(tmp_path)
    if open_ms is not None:
        daemon._start_cost = {"decoder_ms": 1.0, "output_open_ms": open_ms}

    assert daemon.declared_lead_time_ms(250.0) == expected


def test_the_lead_follows_the_latest_start_and_keeps_covering_the_sink_lookahead(tmp_path):
    daemon = _daemon(tmp_path)
    daemon._note_sink_lookahead(230.0)
    daemon._start_cost = {"decoder_ms": 1.0, "output_open_ms": 962.0}
    slow = daemon.declared_lead_time_ms(250.0)
    daemon._start_cost = {"decoder_ms": 1.0, "output_open_ms": 20.0}

    assert slow == 1100.0
    assert daemon.declared_lead_time_ms(250.0) == 280.0


@pytest.mark.parametrize("delay_ms", [0.0, 247.0, 267.0])
def test_the_output_delay_is_not_added_to_the_lead(tmp_path, delay_ms):
    daemon = _daemon(tmp_path)
    daemon._player_delay_ms = delay_ms
    daemon._static_delay_ms = delay_ms
    daemon._start_cost = {"decoder_ms": 1.0, "output_open_ms": 962.0}

    assert daemon.declared_lead_time_ms(250.0) == 1100.0


@pytest.mark.asyncio
async def test_a_slow_output_open_re_declares_the_lead_to_the_server(tmp_path, monkeypatch, caplog):
    import sendspin_bridge.services.audio.pulse as pulse

    async def _latency(_pid):
        return {"buffer_ms": 134.4, "sink_ms": 60.7}

    monkeypatch.setattr(pulse, "apid_sink_input_latency", _latency)
    daemon = _daemon(tmp_path)
    await daemon._load_identity_and_pairing_store()
    daemon._client = daemon._create_client(247.0)
    daemon._report_player_state = MagicMock()  # type: ignore[method-assign]
    daemon._player = MagicMock()
    daemon._player.start_report.return_value = {"compensated_latency_ms": 200.0, "min_latency_ms": 150.0}
    daemon._player.stream_generation = 1
    daemon._start_cost = {"decoder_ms": 1.0, "output_open_ms": 962.0}
    caplog.set_level(logging.INFO, logger="sendspin_bridge.services.ipc.bridge_daemon")

    await daemon._log_stream_start_timing(1)

    assert daemon._client.required_lead_time_ms == 1100.0
    assert daemon._client.min_buffer_ms == 250.0
    assert daemon._bridge_status["declared_lead_time_ms"] == 1100
    daemon._report_player_state.assert_called_once()
    assert any(
        "Declared lead time set to 1100 ms" in r.getMessage() and "962" in r.getMessage() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_the_start_line_tells_the_output_open_time(tmp_path, monkeypatch, caplog):
    import sendspin_bridge.services.audio.pulse as pulse

    async def _none(_pid):
        return None

    monkeypatch.setattr(pulse, "apid_sink_input_latency", _none)
    daemon = _daemon(tmp_path)
    daemon._player = MagicMock()
    daemon._player.start_report.return_value = {
        "compensated_latency_ms": 200.0,
        "first_lead_ms": -747.0,
        "overdue_dropped_ms": 747.0,
        "first_on_schedule_error_ms": 0.0,
    }
    daemon._player.stream_generation = 2
    daemon._start_cost = {"decoder_ms": 1.0, "output_open_ms": 962.0}
    caplog.set_level(logging.INFO, logger="sendspin_bridge.services.ipc.bridge_daemon")

    await daemon._log_stream_start_timing(2)

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Stream start timing:")]
    assert "overdue audio dropped 747.0 ms" in lines[0]
    assert "output open 962.0 ms (decoder 1.0 ms)" in lines[0]


@pytest.mark.asyncio
async def test_the_decoder_library_is_loaded_before_the_first_stream(tmp_path, monkeypatch):
    """Loading PyAV on the first FLAC stream/start held the loop for its import
    (0.05 s warm, 4 s cold here): it is loaded off the loop when the daemon starts."""
    import sendspin_bridge.services.ipc.bridge_daemon as daemon_module

    warmed: list[str] = []
    monkeypatch.setattr(daemon_module, "_load_decoder_library", lambda: warmed.append("av"))
    monkeypatch.setattr(daemon_module, "StreamPlayer", lambda **_kw: MagicMock())
    monkeypatch.setattr(daemon_module, "default_pulsesink_factory", lambda **_kw: MagicMock())
    daemon = _daemon(tmp_path)

    async def _load():
        daemon._identity = None

    async def _connect():
        await asyncio.sleep(0.05)
        raise asyncio.CancelledError

    daemon._load_identity_and_pairing_store = _load  # type: ignore[method-assign]
    daemon._create_client = MagicMock(return_value=None)  # type: ignore[method-assign]
    daemon._run_server_initiated = _connect  # type: ignore[method-assign]
    await daemon.run()

    assert warmed == ["av"]
