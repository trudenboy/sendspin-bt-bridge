"""The daemon chooses the playout engine by PLAYOUT_SYNC and reports its status."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from sendspin_bridge.services.ipc.bridge_daemon import BridgeDaemon, DaemonArgs

if TYPE_CHECKING:
    from pathlib import Path


def _daemon(tmp_path: Path, **overrides) -> BridgeDaemon:
    args = DaemonArgs(
        client_id="legacy-id",
        client_name="Speaker",
        identity_path=str(tmp_path / "identity.key"),
        **overrides,
    )
    return BridgeDaemon(args, {}, None)


# ── The brief's tests ────────────────────────────────────────────────────────


def test_default_args_keep_the_legacy_player(tmp_path):
    daemon = _daemon(tmp_path)  # helper as in test_bridge_daemon_aiosendspin9.py, real players
    assert type(daemon._make_player("sink")).__name__ == "StreamPlayer"


def test_measure_mode_builds_the_playout_player(tmp_path):
    daemon = _daemon(tmp_path, playout_mode="measure")
    assert type(daemon._make_player("sink")).__name__ == "PlayoutPlayer"


def test_lead_is_raised_to_cover_the_output_buffer(tmp_path):
    daemon = _daemon(tmp_path, playout_mode="measure", pulse_latency_msec=800)
    assert daemon._effective_lead_ms() == 940.0
    assert _daemon(tmp_path)._effective_lead_ms() == 250.0


def test_fallback_switches_to_legacy_once(tmp_path, caplog):
    daemon = _daemon(tmp_path, playout_mode="measure")
    daemon._player = daemon._make_player("sink")
    daemon.fall_back_to_legacy("no audio clock")
    daemon.fall_back_to_legacy("no audio clock")
    assert type(daemon._player).__name__ == "StreamPlayer"
    assert caplog.text.count("falling back to the legacy player") == 1


@pytest.mark.asyncio
async def test_status_carries_playout_and_the_sync_error():
    from sendspin_bridge.services.ipc.daemon_process import _timing_telemetry_watcher

    handler = SimpleNamespace(metrics=lambda: {"playout": {"error_ms": 0.4, "state": "playing"}})
    client = SimpleNamespace(is_time_synchronized=lambda: True, _time_filter=None)
    daemon = SimpleNamespace(
        _audio_handler=handler,
        _client=client,
        _args=SimpleNamespace(playout_mode="measure"),
        fall_back_to_legacy=lambda reason: None,
    )
    status, stop = {"playing": True}, asyncio.Event()
    task = asyncio.create_task(_timing_telemetry_watcher(daemon, status, lambda: None, stop, poll_interval=0.01))
    await asyncio.sleep(0.05)
    stop.set()
    await task
    assert status["playout"]["error_ms"] == 0.4
    assert status["playback_sync_error_ms"] == 0.4


# ── Player choice ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["measure", "loop"])
def test_the_playout_player_gets_the_mode_and_the_settings(tmp_path, mode):
    daemon = _daemon(tmp_path, playout_mode=mode, playout={"mode": mode, "warm_hold_s": 120.0, "unknown": 1})

    player = daemon._make_player("sink")

    assert player._settings.mode == mode
    assert player._settings.warm_hold_s == 120.0


def test_the_playout_sink_uses_the_sink_and_the_pulse_buffer(tmp_path, monkeypatch):
    import sendspin_bridge.services.ipc.bridge_daemon as daemon_module

    made: list[dict] = []
    monkeypatch.setattr(daemon_module, "playout_pulsesink_factory", lambda **kw: made.append(kw) or MagicMock())
    daemon = _daemon(tmp_path, playout_mode="measure", pulse_latency_msec=300)

    daemon._make_player("bluez_sink.AA_BB.a2dp_sink")

    assert made == [
        {"device": "bluez_sink.AA_BB.a2dp_sink", "client_name": "Speaker", "buffer_time_us": 300_000},
    ]


def test_a_retarget_in_measure_mode_keeps_the_playout_player(tmp_path):
    daemon = _daemon(tmp_path, playout_mode="measure")
    daemon._player = daemon._make_player("sink")

    daemon.retarget_sink("other")

    assert type(daemon._player).__name__ == "PlayoutPlayer"
    assert daemon._audio_handler is daemon._player


# ── Start lead ───────────────────────────────────────────────────────────────


def test_the_client_is_given_and_the_status_shows_the_effective_lead(tmp_path):
    daemon = _daemon(tmp_path, playout_mode="measure", pulse_latency_msec=800)

    assert daemon._declared_lead_for_client() == 940.0
    assert daemon._bridge_status["effective_required_lead_time_ms"] == 940


def test_a_configured_lead_above_the_floor_stands(tmp_path):
    args = DaemonArgs(client_id="x", client_name="Speaker", identity_path=str(tmp_path / "id.key"), playout_mode="loop")
    daemon = BridgeDaemon(args, {}, None, required_lead_time_ms=2000.0)

    assert daemon._effective_lead_ms() == 2000.0


def test_a_lead_set_at_runtime_keeps_the_floor_in_measure_mode(tmp_path):
    daemon = _daemon(tmp_path, playout_mode="measure", pulse_latency_msec=800)

    assert daemon.declared_lead_time_ms(100.0) == 940.0
    assert _daemon(tmp_path).declared_lead_time_ms(100.0) == 100.0


# ── Fallback ─────────────────────────────────────────────────────────────────


def test_the_fallback_shows_in_the_status_and_lasts(tmp_path):
    daemon = _daemon(tmp_path, playout_mode="measure")
    daemon._player = daemon._make_player("sink")
    daemon._bridge_status["playout"] = {"state": "playing", "error_ms": 1.0}

    daemon.fall_back_to_legacy("no audio clock")

    assert daemon._bridge_status["playout"]["state"] == "fallback"
    assert daemon._bridge_status["playout"]["fallback_reason"] == "no audio clock"
    assert daemon._audio_handler is daemon._player
    # A later retarget stays on the legacy player for the rest of the process.
    daemon.retarget_sink("other")
    assert type(daemon._player).__name__ == "StreamPlayer"


def test_the_fallback_keeps_the_sink_and_the_stream_volume(tmp_path, monkeypatch):
    import sendspin_bridge.services.ipc.bridge_daemon as daemon_module

    sinks: list[dict] = []
    players: list[MagicMock] = []
    monkeypatch.setattr(daemon_module, "default_pulsesink_factory", lambda **kw: sinks.append(kw) or MagicMock())

    def _player(**_kw):
        players.append(MagicMock())
        return players[-1]

    monkeypatch.setattr(daemon_module, "StreamPlayer", _player)
    daemon = _daemon(
        tmp_path, playout_mode="measure", volume_controller="sendspin", initial_volume=50, initial_muted=True
    )
    daemon._player = daemon._make_player("bluez_sink.AA_BB.a2dp_sink")

    daemon.fall_back_to_legacy("no audio clock")

    assert sinks[0]["device"] == "bluez_sink.AA_BB.a2dp_sink"
    assert daemon._player is players[0]
    players[0].set_volume.assert_called_once_with(0.125, True)


class _BlockingStopPlayer:
    """A player whose stop() blocks like a feeder join plus a pipeline NULL."""

    def __init__(self, block_s: float = 0.3) -> None:
        self.block_s = block_s
        self.stopped = threading.Event()
        self.stop_thread: threading.Thread | None = None

    def stop(self) -> None:
        time.sleep(self.block_s)
        self.stop_thread = threading.current_thread()
        self.stopped.set()

    def set_volume(self, value: float, muted: bool) -> None:
        pass


async def _wait_stopped(player: _BlockingStopPlayer) -> None:
    for _ in range(200):
        if player.stopped.is_set():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the old player was never stopped")


@pytest.mark.asyncio
async def test_the_fallback_stops_the_old_player_off_the_loop(tmp_path):
    daemon = _daemon(tmp_path, playout_mode="measure")
    old = _BlockingStopPlayer()
    daemon._player = old

    began = time.monotonic()
    daemon.fall_back_to_legacy("no audio clock")
    held_ms = (time.monotonic() - began) * 1000

    assert held_ms < 100
    assert type(daemon._player).__name__ == "StreamPlayer"
    await _wait_stopped(old)
    assert old.stop_thread is not threading.main_thread()


@pytest.mark.asyncio
async def test_a_retarget_stops_the_old_player_off_the_loop(tmp_path):
    daemon = _daemon(tmp_path, playout_mode="measure")
    old = _BlockingStopPlayer()
    daemon._player = old

    began = time.monotonic()
    daemon.retarget_sink("other")
    held_ms = (time.monotonic() - began) * 1000

    assert held_ms < 100
    await _wait_stopped(old)
    assert old.stop_thread is not threading.main_thread()


# ── Delay change wording ─────────────────────────────────────────────────────


def test_a_delay_change_on_the_playout_player_does_not_claim_a_rebuild(tmp_path, caplog):
    daemon = _daemon(tmp_path, playout_mode="measure")
    player = daemon._make_player("sink")
    shifts: list[int] = []
    player.resync = lambda shift_us: shifts.append(shift_us) or True  # type: ignore[method-assign]
    daemon._player = player
    daemon._player_delay_ms, daemon._target_delay_ms = 300.0, 310.0
    caplog.set_level(logging.INFO, logger="sendspin_bridge.services.ipc.bridge_daemon")

    daemon._apply_output_delay()

    assert shifts == [-10_000]
    assert "Output delay changed 300→310 ms; player resynchronised (placed exactly, output kept)" in caplog.text
    assert "output rebuilt" not in caplog.text


# ── Telemetry watcher ────────────────────────────────────────────────────────


def _watched(metrics, mode: str = "measure", calls: list[str] | None = None):
    handler = SimpleNamespace(metrics=metrics)
    client = SimpleNamespace(is_time_synchronized=lambda: True, _time_filter=None)
    record = calls if calls is not None else []
    return SimpleNamespace(
        _audio_handler=handler,
        _client=client,
        _args=SimpleNamespace(playout_mode=mode),
        fall_back_to_legacy=record.append,
    )


async def _watch(daemon, status: dict, seconds: float = 0.06) -> None:
    from sendspin_bridge.services.ipc.daemon_process import _timing_telemetry_watcher

    stop = asyncio.Event()
    task = asyncio.create_task(_timing_telemetry_watcher(daemon, status, lambda: None, stop, poll_interval=0.01))
    await asyncio.sleep(seconds)
    stop.set()
    await task


@pytest.mark.asyncio
async def test_the_watcher_falls_back_once_with_the_engine_reason():
    calls: list[str] = []
    playout = {"state": "fallback", "fallback_reason": "no audio clock from the sink within 2 s of PLAYING"}
    status: dict = {"playing": True}

    await _watch(_watched(lambda: {"playout": dict(playout)}, calls=calls), status)

    assert calls == ["no audio clock from the sink within 2 s of PLAYING"]
    assert status["playout"]["state"] == "fallback"


@pytest.mark.asyncio
async def test_the_watcher_leaves_the_status_alone_in_off_mode():
    calls: list[str] = []
    playout = {"state": "fallback", "fallback_reason": "x", "error_ms": 3.0}
    status: dict = {"playing": True}

    await _watch(_watched(lambda: {"playout": playout}, mode="off", calls=calls), status)

    assert "playout" not in status
    assert calls == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("playing", "updates"), [(True, True), (False, False)])
async def test_the_playout_status_is_refreshed_faster_while_playing(monkeypatch, playing, updates):
    import sendspin_bridge.services.ipc.daemon_process as daemon_process

    monkeypatch.setattr(daemon_process, "_PLAYOUT_STATUS_PLAYING_S", 0.03)
    monkeypatch.setattr(daemon_process, "_PLAYOUT_STATUS_IDLE_S", 10.0)
    snapshot = {"state": "playing", "error_ms": 1.0}
    status: dict = {"playing": playing}
    from sendspin_bridge.services.ipc.daemon_process import _timing_telemetry_watcher

    stop = asyncio.Event()
    task = asyncio.create_task(
        _timing_telemetry_watcher(
            _watched(lambda: {"playout": dict(snapshot)}), status, lambda: None, stop, poll_interval=0.005
        )
    )
    await asyncio.sleep(0.02)
    assert status["playout"]["error_ms"] == 1.0
    snapshot["error_ms"] = 2.0
    await asyncio.sleep(0.15)
    stop.set()
    await task

    assert (status["playout"]["error_ms"] == 2.0) is updates


# ── Spawn params → DaemonArgs ────────────────────────────────────────────────


async def _daemon_args_for(monkeypatch, params: dict):
    import sendspin_bridge.services.ipc.bridge_daemon as bridge_daemon_mod
    from sendspin_bridge.services.ipc.daemon_process import _run

    captured: dict = {}

    class _Stop(BaseException):
        pass

    def _daemon_factory(*_args, **kwargs):
        captured.update(kwargs)
        raise _Stop

    monkeypatch.setattr(bridge_daemon_mod, "DaemonArgs", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(bridge_daemon_mod, "BridgeDaemon", _daemon_factory)
    with pytest.raises(_Stop):
        await _run({"player_name": "ENEBY Portable", "client_id": "eneby", "listen_port": 8933, **params})
    return captured["args"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("playout", "mode"),
    [
        ({"mode": "measure"}, "measure"),
        ({"mode": "loop"}, "loop"),
        ({"mode": "bogus"}, "off"),
        (None, "off"),
    ],
)
async def test_the_spawned_playout_mode_reaches_the_daemon_args(monkeypatch, playout, mode):
    params = {} if playout is None else {"playout": playout}

    args = await _daemon_args_for(monkeypatch, params)

    assert args.playout_mode == mode
    assert args.playout == playout


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("playout", "published"), [({"mode": "off"}, {"mode": "off"}), (None, {"mode": "off"}), ({"mode": "loop"}, None)]
)
async def test_the_daemon_says_so_once_when_it_runs_the_legacy_player(monkeypatch, playout, published):
    import sendspin_bridge.services.ipc.bridge_daemon as bridge_daemon_mod
    from sendspin_bridge.services.ipc.daemon_process import _run

    captured: dict = {}

    class _Stop(BaseException):
        pass

    def _daemon_factory(*_args, **kwargs):
        captured.update(kwargs)
        raise _Stop

    monkeypatch.setattr(bridge_daemon_mod, "DaemonArgs", lambda **kw: SimpleNamespace(**kw))
    monkeypatch.setattr(bridge_daemon_mod, "BridgeDaemon", _daemon_factory)
    with pytest.raises(_Stop):
        await _run({"player_name": "X", "client_id": "x", "listen_port": 8933, "playout": playout})

    assert captured["status"].get("playout") == published
