"""A stream start whose output is slow to open.

On HAOS (PulseAudio 17, an A2DP speaker) the first chunks of every stream
reached the player 0.7-1.6 s after their play time: stream/start opened the
PulseAudio output on the daemon's loop (a Bluetooth sink waits for its A2DP
transport), and the server's chunks waited unread meanwhile. The output is now
opened in a worker thread; chunks that arrive meanwhile wait and are handed to
the output once it is open (the player drops what became overdue). The lead
declared to Music Assistant is not raised for it, and never includes the output
delay: the server adds that on top (required_lead_time_ms "excludes output_delay_ms").
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
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


def _start_message() -> MagicMock:
    message = MagicMock()
    message.payload.player.codec = "pcm"
    message.payload.player.sample_rate = 48000
    message.payload.player.bit_depth = 16
    message.payload.player.channels = 2
    message.payload.player.codec_header = None
    return message


def _pcm_format() -> MagicMock:
    fmt = MagicMock()
    fmt.pcm_format.sample_rate = 48000
    fmt.pcm_format.channels = 2
    fmt.pcm_format.bit_depth = 16
    fmt.codec = "pcm"
    fmt.codec_header = None
    return fmt


class _SlowOutputPlayer:
    """A player whose output takes ``open_s`` to open, like an A2DP sink."""

    def __init__(self, open_s: float) -> None:
        self.open_s = open_s
        self.stream_generation = 0
        self.output: object | None = None
        self.discarded: list[object] = []
        self.submitted: list[int] = []
        self.opened_in: list[str] = []

    def needs_output(self) -> bool:
        return self.output is None

    def now_us(self) -> int:
        return 10_000_000

    def prepare_output(self, fmt):
        self.opened_in.append(threading.current_thread().name)
        time.sleep(self.open_s)
        return object()

    def adopt_output(self, output, fmt) -> None:
        self.output = output
        self.stream_generation += 1

    def discard_output(self, output) -> None:
        self.discarded.append(output)

    def start(self, fmt) -> None:
        assert self.output is not None, "the output must be adopted before audio is handed on"

    def submit(self, play_time_us: int, payload: bytes) -> None:
        self.submitted.append(play_time_us)

    def stop(self) -> None:
        self.output = None

    def clear(self) -> None:
        pass

    def resync(self, shift_us: int) -> bool:
        return False


async def _wait_for(predicate, timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met before timeout")
        await asyncio.sleep(0.01)


def _with_player(tmp_path, open_s: float) -> tuple[BridgeDaemon, _SlowOutputPlayer]:
    daemon = _daemon(tmp_path)
    daemon._schedule_start_timing_line = MagicMock()  # type: ignore[method-assign]
    player = _SlowOutputPlayer(open_s=open_s)
    daemon._player = player  # type: ignore[assignment]
    return daemon, player


@pytest.mark.asyncio
async def test_the_output_is_opened_off_the_loop_and_waiting_chunks_follow_in_order(tmp_path):
    daemon, player = _with_player(tmp_path, open_s=0.4)

    began = time.monotonic()
    daemon._on_stream_start(_start_message())
    for ts in (10_250_000, 10_260_000, 10_270_000):
        daemon._on_audio_chunk(ts, bytes(1920), _pcm_format())
    held_s = time.monotonic() - began

    assert held_s < 0.1
    assert player.submitted == []
    assert player.opened_in
    assert player.opened_in[0] != threading.current_thread().name
    await _wait_for(lambda: len(player.submitted) == 3)
    assert player.submitted == [10_250_000, 10_260_000, 10_270_000]
    assert daemon._start_cost is not None
    assert daemon._start_cost["output_open_ms"] >= 350
    assert daemon._start_cost["loop_block_ms"] < 100
    assert daemon._first_arrival_lead_us == 250_000
    daemon._schedule_start_timing_line.assert_called_once()


@pytest.mark.asyncio
async def test_a_stream_end_while_opening_discards_the_output(tmp_path):
    daemon, player = _with_player(tmp_path, open_s=0.2)

    daemon._on_stream_start(_start_message())
    daemon._on_audio_chunk(10_250_000, bytes(1920), _pcm_format())
    daemon._on_stream_end()
    await _wait_for(lambda: daemon._opening is None)

    assert len(player.discarded) == 1
    assert player.output is None
    assert player.submitted == []


@pytest.mark.asyncio
async def test_a_new_stream_start_while_opening_keeps_the_output(tmp_path):
    daemon, player = _with_player(tmp_path, open_s=0.2)

    daemon._on_stream_start(_start_message())
    daemon._on_stream_end()
    daemon._on_stream_start(_start_message())
    daemon._on_audio_chunk(10_250_000, bytes(1920), _pcm_format())
    await _wait_for(lambda: player.submitted)

    assert player.discarded == []
    assert len(player.opened_in) == 1
    assert player.submitted == [10_250_000]


@pytest.mark.asyncio
async def test_a_delay_change_while_opening_moves_the_waiting_chunks(tmp_path):
    daemon, player = _with_player(tmp_path, open_s=0.3)
    daemon._player_delay_ms = 247.0

    daemon._on_stream_start(_start_message())
    daemon._on_audio_chunk(10_250_000, bytes(1920), _pcm_format())
    daemon._target_delay_ms = 267.0
    daemon._apply_output_delay()  # 20 ms more delay plays 20 ms earlier
    await _wait_for(lambda: player.submitted)

    assert player.submitted == [10_230_000]


@pytest.mark.asyncio
async def test_a_failed_open_is_logged_and_the_next_chunk_tries_again(tmp_path, caplog):
    daemon, player = _with_player(tmp_path, open_s=0.0)
    calls = {"n": 0}
    original = player.prepare_output

    def _flaky(fmt):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("failed to set GStreamer pipeline to PLAYING")
        return original(fmt)

    player.prepare_output = _flaky  # type: ignore[method-assign]
    caplog.set_level(logging.WARNING, logger="sendspin_bridge.services.ipc.bridge_daemon")

    daemon._on_stream_start(_start_message())
    await _wait_for(lambda: daemon._opening is None)
    daemon._on_audio_chunk(10_250_000, bytes(1920), _pcm_format())
    await _wait_for(lambda: bool(player.submitted))

    assert any("Opening the audio output failed" in r.getMessage() for r in caplog.records)
    assert player.submitted == [10_250_000]


@pytest.mark.parametrize("delay_ms", [0.0, 247.0, 267.0])
def test_the_lead_covers_neither_the_output_open_nor_the_output_delay(tmp_path, delay_ms):
    daemon = _daemon(tmp_path)
    daemon._player_delay_ms = delay_ms
    daemon._static_delay_ms = delay_ms
    daemon._start_cost = {"output_open_ms": 962.0, "loop_block_ms": 1.0}

    assert daemon.declared_lead_time_ms(250.0) == 250.0


@pytest.mark.asyncio
async def test_the_start_line_tells_the_arrival_lead_and_the_output_open(tmp_path, monkeypatch, caplog):
    import sendspin_bridge.services.audio.pulse as pulse

    async def _none(_pid):
        return None

    monkeypatch.setattr(pulse, "apid_sink_input_latency", _none)
    daemon = _daemon(tmp_path)
    daemon._player = MagicMock()
    daemon._player.start_report.return_value = {
        "compensated_latency_ms": 200.0,
        "first_lead_ms": -712.0,  # when the opened output took it
        "overdue_dropped_ms": 712.0,
        "first_on_schedule_error_ms": 0.2,
    }
    daemon._player.stream_generation = 2
    daemon._first_arrival_lead_us = 250_000
    daemon._start_cost = {"output_open_ms": 962.0, "loop_block_ms": 1.0}
    caplog.set_level(logging.INFO, logger="sendspin_bridge.services.ipc.bridge_daemon")

    await daemon._log_stream_start_timing(2)

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Stream start timing:")]
    assert "first chunk lead 250.0 ms" in lines[0]
    assert "overdue audio dropped 712.0 ms" in lines[0]
    assert "output open 962.0 ms (off the loop, loop held 1.0 ms)" in lines[0]


@pytest.mark.asyncio
async def test_the_decoder_library_is_loaded_in_a_worker_thread_after_startup(tmp_path, monkeypatch):
    """Loading PyAV on the first FLAC stream/start held the loop for its import
    (0.07 s warm, 4 s cold here): it is loaded in a worker thread a little
    after the daemon starts instead."""
    import sendspin_bridge.services.ipc.bridge_daemon as daemon_module

    loaded_in: list[str] = []
    monkeypatch.setattr(daemon_module, "_DECODER_WARMUP_DELAY_S", 0.0)
    monkeypatch.setattr(
        daemon_module, "_load_decoder_library", lambda: loaded_in.append(threading.current_thread().name)
    )
    monkeypatch.setattr(daemon_module, "StreamPlayer", lambda **_kw: MagicMock())
    monkeypatch.setattr(daemon_module, "default_pulsesink_factory", lambda **_kw: MagicMock())
    daemon = _daemon(tmp_path)

    async def _load():
        daemon._identity = None

    async def _connect():
        assert daemon._decoder_warmup_task is not None
        await daemon._decoder_warmup_task
        raise asyncio.CancelledError

    daemon._load_identity_and_pairing_store = _load  # type: ignore[method-assign]
    daemon._create_client = MagicMock(return_value=None)  # type: ignore[method-assign]
    daemon._run_server_initiated = _connect  # type: ignore[method-assign]
    await daemon.run()

    assert len(loaded_in) == 1
    assert loaded_in[0] != threading.current_thread().name
