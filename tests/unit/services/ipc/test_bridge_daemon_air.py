"""The daemon runs the air sensor in measure mode (Task A11): build, tap, events, step, status."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from sendspin_bridge.services.audio.playout.air_sensor import AirSensor
from sendspin_bridge.services.audio.playout.air_tap import WrittenEnvelope
from sendspin_bridge.services.audio.playout.engine import PlayoutSettings
from sendspin_bridge.services.audio.playout.gst_output import PlayoutPlayer
from sendspin_bridge.services.ipc import daemon_process
from sendspin_bridge.services.ipc.bridge_daemon import BridgeDaemon, DaemonArgs
from sendspin_bridge.services.ipc.commands import AirFold, SetAirCalibration
from tests.unit.services.audio.playout.test_gst_output import PCM_S16_STEREO_48K, _player, requires_gst

if TYPE_CHECKING:
    from pathlib import Path

ON = {"enabled": True, "latency_ms": None}


@pytest.fixture(autouse=True)
def _no_pulseaudio(monkeypatch):
    """The telemetry watcher never asks the host's PulseAudio in these tests."""

    async def _none() -> None:
        return None

    monkeypatch.setattr(daemon_process, "_read_pa_latency_ms", _none)


@pytest.fixture
def make_daemon(tmp_path: Path):
    def _make(**overrides) -> BridgeDaemon:
        args = DaemonArgs(
            client_id="air-id",
            client_name="Speaker",
            identity_path=str(tmp_path / "identity.key"),
            **overrides,
        )
        return BridgeDaemon(args, {}, None)

    return _make


# -- build and switch off -----------------------------------------------------------------


def test_default_args_build_no_sensor_and_no_tap(make_daemon):
    d = make_daemon(playout_mode="measure")
    player = d._make_player("bluez_sink.X")
    assert d.air_snapshot() is None
    assert player._air_tap is None
    assert player._on_engine_event is None
    assert d.air_accepts() == []


def test_tap_is_attached_in_new_playout_player(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor=ON)
    player = d._make_player("bluez_sink.X")
    snap = d.air_snapshot()
    assert isinstance(player._air_tap, WrittenEnvelope)
    assert snap is not None and snap["accepts"] == [1]
    assert d.air_accepts() == [1]


def test_every_playout_player_of_the_daemon_shares_the_sensor_tap(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor=ON)
    first = d._make_player("bluez_sink.X")
    second = d._make_player("bluez_sink.Y")  # a retarget builds a new player
    assert first._air_tap is second._air_tap is d._air_sensor._tap


@pytest.mark.parametrize("mode", ["off", "loop"])
def test_a_mode_other_than_measure_ignores_the_param(make_daemon, mode):
    d = make_daemon(playout_mode=mode, air_sensor=ON)
    assert d.air_snapshot() is None
    assert d.air_accepts() == []


def test_a_disabled_param_builds_nothing(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor={"enabled": False})
    assert d.air_snapshot() is None
    assert d._make_player("bluez_sink.X")._air_tap is None


def test_switch_off_daemon_takes_no_air_commands(make_daemon):
    d = make_daemon(playout_mode="measure")
    daemon_process._dispatch_air_command(d, {"cmd": "set_air_calibration", "v": 1, "latency_ms": 10.0})
    assert d.air_counters["accepted"] == 0 and d.air_counters["ignored"] == 1


def test_the_legacy_player_never_gets_a_tap(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor=ON)
    d._player = d._make_player("bluez_sink.X")
    d.fall_back_to_legacy("no audio clock")
    assert type(d._player).__name__ == "StreamPlayer"
    assert not hasattr(d._player, "_air_tap")


# -- calibration, output delay, fold ------------------------------------------------------


def test_calibration_reaches_the_sensor(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor=ON)
    d.air_intake(SetAirCalibration(latency_ms=203.0))
    snap = d.air_snapshot()
    assert snap["air_to_sound_ms"] == 203.0 and snap["calibrated"] is True


def test_the_spawn_calibration_is_applied_at_build(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor={"enabled": True, "latency_ms": 180.5})
    assert d.air_snapshot()["air_to_sound_ms"] == 180.5


def test_output_delay_follows_the_player_delay(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor=ON, static_delay_ms=150.0)
    assert d._air_sensor._output_delay_ms() == 150.0
    d._player_delay_ms = 90.0  # the delay the queued audio is timed with, once the client runs
    assert d._air_sensor._output_delay_ms() == 90.0


def test_the_tap_gets_the_output_delay_in_microseconds(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor=ON, static_delay_ms=150.0)
    player = d._make_player("bluez_sink.X")
    assert player._output_delay_us() == 150_000.0


def test_a_fold_is_answered_not_implemented_until_a16b(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor=ON)
    d.air_intake(AirFold(id="c1f3a2b4", from_us=1.0, to_us=12e6))
    assert d.air_snapshot()["fold"] == {"id": "c1f3a2b4", "phase_ms": None, "ok": False, "reason": "not_implemented"}


# -- fallback -----------------------------------------------------------------------------


def test_fallback_marks_unavailable_and_withdraws_accepts(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor=ON)
    d._player = d._make_player("bluez_sink.X")
    d.fall_back_to_legacy("no audio clock")
    s = d.air_snapshot()
    assert (s["state"], s["reason"], s["accepts"]) == ("unavailable", "playout_fallback", [])
    assert d.air_accepts() == []
    d.air_intake(SetAirCalibration(latency_ms=10.0))
    assert d.air_counters["ignored"] == 1


# -- step and containment ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_sensor_exception_disables_only_the_sensor(make_daemon, monkeypatch):
    d = make_daemon(playout_mode="measure", air_sensor=ON)

    async def boom():
        raise ZeroDivisionError

    monkeypatch.setattr(d._air_sensor, "step", boom)
    await d.air_step()
    snap = d.air_snapshot()
    assert (snap["state"], snap["reason"]) == ("unavailable", "internal_error")


@pytest.mark.asyncio
async def test_air_step_runs_the_sensor_step(make_daemon, monkeypatch):
    d = make_daemon(playout_mode="measure", air_sensor=ON)
    calls = []

    async def step():
        calls.append(True)

    monkeypatch.setattr(d._air_sensor, "step", step)
    await d.air_step()
    assert calls == [True]


@pytest.mark.asyncio
async def test_air_step_without_a_sensor_does_nothing(make_daemon):
    d = make_daemon(playout_mode="measure")
    await d.air_step()
    assert d.air_snapshot() is None


def test_a_snapshot_failure_is_contained(make_daemon, monkeypatch):
    d = make_daemon(playout_mode="measure", air_sensor=ON)

    def broken():
        raise RuntimeError("boom")

    monkeypatch.setattr(d._air_sensor, "snapshot", broken)
    snap = d.air_snapshot()
    assert (snap["state"], snap["reason"]) == ("unavailable", "internal_error")


# -- engine events -----------------------------------------------------------------------


class _RecordingSensor:
    def __init__(self) -> None:
        self.events: list[tuple[str, int]] = []

    def on_engine_event(self, kind: str) -> None:
        self.events.append((kind, threading.get_ident()))


@pytest.mark.asyncio
async def test_engine_events_from_the_feeder_thread_run_on_the_loop(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor=ON)
    player = d._make_player("bluez_sink.X")  # on the loop: the daemon knows it now
    rec = _RecordingSensor()
    d._air_sensor = rec  # type: ignore[assignment]
    feeder = threading.Thread(target=player._on_engine_event, args=("underrun",))
    feeder.start()
    feeder.join()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert rec.events == [("underrun", threading.get_ident())]


def test_an_engine_event_failure_never_reaches_the_feeder(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor=ON)
    player = d._make_player("bluez_sink.X")  # no loop: handled in place

    class _Broken:
        def on_engine_event(self, kind):
            raise RuntimeError("boom")

    d._air_sensor = _Broken()  # type: ignore[assignment]
    player._on_engine_event("stall")  # must not raise


class _CountingTap(WrittenEnvelope):
    def __init__(self) -> None:
        super().__init__()
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1
        super().reset()


@pytest.fixture
def real_playout_player_with_fakes():
    made: list[PlayoutPlayer] = []

    def _make():
        tap = _CountingTap()
        events: list[str] = []
        p = _player(air_tap=tap, on_engine_event=events.append)
        made.append(p)
        return p, tap, events

    yield _make
    for p in made:
        p.stop()


@requires_gst
def test_every_output_open_resets_the_tap_and_reacquires(real_playout_player_with_fakes):
    p, tap, events = real_playout_player_with_fakes()
    p.start(PCM_S16_STEREO_48K)
    p.stop()  # a stream ends (end_stream == stop in Stage 1)
    p.start(PCM_S16_STEREO_48K)  # the next stream (or a seek) opens a new output
    assert events.count("reopen") == 2 and tap.resets == 2


def test_adopt_output_resets_the_tap_before_the_reopen_event():
    order: list[str] = []

    class _Tap(WrittenEnvelope):
        def reset(self) -> None:
            order.append("reset")
            super().reset()

    p = PlayoutPlayer(
        sink_factory=lambda: None,
        settings=PlayoutSettings(mode="measure"),
        air_tap=_Tap(),
        on_engine_event=order.append,
        port_factory=lambda sink: SimpleNamespace(read=lambda: None),
        thread_factory=lambda **kw: SimpleNamespace(start=lambda: None, join=lambda timeout=None: None),
    )
    out = SimpleNamespace(pipeline=None, appsrc=None, sink=None, out_rate=48000, k0_ns=0, buffer_time_us=0)
    p.adopt_output(out, PCM_S16_STEREO_48K)  # type: ignore[arg-type]
    assert order == ["reset", "reopen"]


def test_a_player_without_a_listener_emits_nothing():
    p = PlayoutPlayer(sink_factory=lambda: None, settings=PlayoutSettings(mode="measure"))
    p._emit_engine_event("reopen")  # no listener: a no-op


class _FakeEngine:
    def __init__(self) -> None:
        self.snap: dict[str, object] = {"state": "playing", "underruns": 0, "pa_steps": 0}

    def snapshot(self) -> dict[str, object]:
        return dict(self.snap)


def _bare_player(events: list[str]) -> PlayoutPlayer:
    return PlayoutPlayer(
        sink_factory=lambda: None,
        settings=PlayoutSettings(mode="measure"),
        air_tap=WrittenEnvelope(),
        on_engine_event=events.append,
    )


def test_engine_counters_become_underrun_pa_step_and_stall_events():
    events: list[str] = []
    p = _bare_player(events)
    engine = _FakeEngine()
    p._publish_snapshot(engine)  # type: ignore[arg-type]
    assert events == []
    engine.snap.update(underruns=1)
    p._publish_snapshot(engine)  # type: ignore[arg-type]
    engine.snap.update(pa_steps=2)
    p._publish_snapshot(engine)  # type: ignore[arg-type]
    engine.snap.update(state="suspended")
    p._publish_snapshot(engine)  # type: ignore[arg-type]
    p._publish_snapshot(engine)  # still suspended: one stall
    engine.snap.update(state="playing")
    p._publish_snapshot(engine)  # type: ignore[arg-type]
    assert events == ["underrun", "pa_step", "stall"]


def test_engine_counters_are_not_forwarded_without_a_listener():
    p = PlayoutPlayer(sink_factory=lambda: None, settings=PlayoutSettings(mode="measure"))
    engine = _FakeEngine()
    engine.snap.update(underruns=3)
    p._publish_snapshot(engine)  # type: ignore[arg-type]  # must not raise


def test_a_listener_failure_never_reaches_the_feeder():
    def broken(kind: str) -> None:
        raise RuntimeError("boom")

    p = PlayoutPlayer(sink_factory=lambda: None, settings=PlayoutSettings(mode="measure"), on_engine_event=broken)
    engine = _FakeEngine()
    engine.snap.update(underruns=1)
    p._publish_snapshot(engine)  # type: ignore[arg-type]  # must not raise


# -- telemetry watcher -------------------------------------------------------------------


class _RecordingStatus(dict):
    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.patches: list[dict] = []

    def patch(self, updates: dict) -> None:
        self.patches.append(dict(updates))
        self.update(updates)


_TICK_S = 0.02  # one watcher tick of the harness = one second of the real watcher


@pytest.fixture
def telemetry_watcher_harness():
    tasks: list[tuple[asyncio.Task, asyncio.Event]] = []

    async def _make(*, air: bool, playing: bool = True):
        steps = {"n": 0}

        async def air_step() -> None:
            steps["n"] += 1

        def air_snapshot():
            if not air:
                return None
            return {"state": "acquiring", "frames": steps["n"], "accepts": [1]}

        daemon = SimpleNamespace(
            _audio_handler=None,
            _client=None,
            _args=SimpleNamespace(playout_mode="measure"),
            fall_back_to_legacy=lambda reason: None,
            air_step=air_step,
            air_snapshot=air_snapshot,
        )
        status = _RecordingStatus(playing=playing)
        stop = asyncio.Event()
        task = asyncio.create_task(
            daemon_process._timing_telemetry_watcher(daemon, status, lambda: None, stop, poll_interval=_TICK_S)
        )
        tasks.append((task, stop))

        async def advance(seconds: float) -> None:
            await asyncio.sleep(seconds * _TICK_S)

        def status_updates_with(key: str) -> int:
            return sum(1 for p in status.patches if key in p)

        return SimpleNamespace(advance=advance, status_updates_with=status_updates_with, status=status, steps=steps)

    yield _make
    for task, stop in tasks:
        stop.set()
        task.cancel()


@pytest.mark.asyncio
async def test_status_has_no_air_key_without_the_sensor(telemetry_watcher_harness):
    h = await telemetry_watcher_harness(air=False)
    await h.advance(3.1)
    assert h.status_updates_with("air_sensor") == 0
    assert "air_sensor" not in h.status


@pytest.mark.asyncio
async def test_status_carries_air_sensor_at_1hz(telemetry_watcher_harness):
    h = await telemetry_watcher_harness(air=True)
    await h.advance(2.1)
    assert h.status_updates_with("air_sensor") >= 2
    assert h.steps["n"] >= 2
    assert h.status["air_sensor"]["accepts"] == [1]


@pytest.mark.asyncio
async def test_an_unchanged_snapshot_is_not_republished(tmp_path):
    snap = {"state": "no_signal", "accepts": [1]}

    async def air_step() -> None:
        return None

    daemon = SimpleNamespace(
        _audio_handler=None,
        _client=None,
        _args=SimpleNamespace(playout_mode="measure"),
        fall_back_to_legacy=lambda reason: None,
        air_step=air_step,
        air_snapshot=lambda: dict(snap),
    )
    status = _RecordingStatus(playing=True)
    stop = asyncio.Event()
    task = asyncio.create_task(
        daemon_process._timing_telemetry_watcher(daemon, status, lambda: None, stop, poll_interval=_TICK_S)
    )
    await asyncio.sleep(4.1 * _TICK_S)
    stop.set()
    await task
    assert sum(1 for p in status.patches if "air_sensor" in p) == 1


@pytest.mark.asyncio
async def test_a_failing_air_step_never_stops_the_watcher():
    async def air_step() -> None:
        raise RuntimeError("boom")

    daemon = SimpleNamespace(
        _audio_handler=None,
        _client=None,
        _args=SimpleNamespace(playout_mode="measure"),
        fall_back_to_legacy=lambda reason: None,
        air_step=air_step,
        air_snapshot=lambda: {"state": "acquiring", "accepts": [1]},
    )
    status = _RecordingStatus(playing=True)
    stop = asyncio.Event()
    task = asyncio.create_task(
        daemon_process._timing_telemetry_watcher(daemon, status, lambda: None, stop, poll_interval=_TICK_S)
    )
    await asyncio.sleep(2.1 * _TICK_S)
    stop.set()
    await task
    assert status["air_sensor"]["state"] == "acquiring"


@pytest.mark.asyncio
async def test_the_fallback_snapshot_is_published_in_the_same_tick(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor=ON)
    d._player = d._make_player("bluez_sink.X")
    d._audio_handler = SimpleNamespace(
        metrics=lambda: {"playout": {"state": "fallback", "fallback_reason": "no audio clock"}}
    )
    status = _RecordingStatus(playing=True)
    stop = asyncio.Event()
    task = asyncio.create_task(
        daemon_process._timing_telemetry_watcher(d, status, lambda: None, stop, poll_interval=_TICK_S)
    )
    await asyncio.sleep(0.5 * _TICK_S)
    stop.set()
    await task
    assert status["air_sensor"]["state"] == "unavailable"
    assert status["air_sensor"]["accepts"] == []


# -- spawn param --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ({"enabled": False}, None),
        ("yes", None),
        ({"enabled": True}, {"enabled": True, "latency_ms": None}),
        ({"enabled": True, "latency_ms": 203.4}, {"enabled": True, "latency_ms": 203.4}),
        ({"enabled": True, "latency_ms": 9000}, {"enabled": True, "latency_ms": 2000.0}),
        ({"enabled": True, "latency_ms": -5}, {"enabled": True, "latency_ms": 0.0}),
        ({"enabled": True, "latency_ms": "x"}, {"enabled": True, "latency_ms": None}),
        ({"enabled": True, "latency_ms": True}, {"enabled": True, "latency_ms": None}),
        ({"enabled": True, "latency_ms": float("nan")}, {"enabled": True, "latency_ms": None}),
    ],
)
def test_the_spawn_param_is_validated(raw, expected):
    assert daemon_process._air_sensor_args(raw) == expected


@pytest.mark.asyncio
async def test_run_passes_the_air_sensor_param_into_daemon_args(monkeypatch):
    captured: dict = {}

    class _PastDaemonArgs(BaseException):
        pass

    class _ProbeArgs(SimpleNamespace):
        def __init__(self, **kwargs):
            captured.update(kwargs)
            super().__init__(**kwargs)

    def _stop_after_args(*_args, **_kwargs):
        raise _PastDaemonArgs

    import sendspin_bridge.services.ipc.bridge_daemon as bridge_daemon_mod

    monkeypatch.setattr(bridge_daemon_mod, "DaemonArgs", _ProbeArgs)
    monkeypatch.setattr(bridge_daemon_mod, "BridgeDaemon", _stop_after_args)
    with pytest.raises(_PastDaemonArgs):
        await daemon_process._run(
            {
                "player_name": "TestSpeaker",
                "client_id": "test",
                "listen_port": 8927,
                "playout": {"mode": "measure"},
                "air_sensor": {"enabled": True, "latency_ms": 12.0},
            }
        )
    assert captured["air_sensor"] == {"enabled": True, "latency_ms": 12.0}


def test_air_sensor_is_built_from_daemon_args_only(make_daemon):
    d = make_daemon(playout_mode="measure", air_sensor=ON)
    assert isinstance(d._air_sensor, AirSensor)


# -- parent: spawn param and status allowlist ---------------------------------------------

_BT_PLAYER = {"id": "p1", "name": "Speaker", "backend": {"type": "bluetooth_a2dp"}}


async def _spawned(monkeypatch, config: dict) -> dict:
    import json
    from unittest.mock import patch

    from sendspin_bridge.bridge.client import SendspinClient

    monkeypatch.setattr("sendspin_bridge.bridge.client.find_available_bind_port", lambda *a, **kw: 8930)
    monkeypatch.setattr("sendspin_bridge.bridge.client.load_config", lambda: json.loads(json.dumps(config)))
    client = SendspinClient("Speaker", "localhost", 9000)
    client.config_id = "p1"
    client._start_sendspin_lock = asyncio.Lock()
    client.bluetooth_sink_name = "bluez_sink.6C_5C_3D_35_17_99.a2dp_sink"
    spawned: list[dict] = []

    async def _fake_exec(*args, **_kw):
        spawned.append(json.loads(args[3]))
        raise RuntimeError("intentional short-circuit")

    with (
        patch("sendspin_bridge.bridge.client.asyncio.create_subprocess_exec", side_effect=_fake_exec),
        patch.object(client, "stop_sendspin", return_value=None),
    ):
        await client.start_sendspin()
    return spawned[0]


@pytest.mark.asyncio
async def test_an_effective_sensor_is_asked_for_at_spawn(monkeypatch):
    config = {"PLAYOUT_SYNC": "measure", "EXPERIMENTAL_AIR_SENSOR": True, "players": [_BT_PLAYER]}
    params = await _spawned(monkeypatch, config)
    assert params["air_sensor"] == {"enabled": True, "latency_ms": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "config",
    [
        {"PLAYOUT_SYNC": "measure", "EXPERIMENTAL_AIR_SENSOR": False, "players": [_BT_PLAYER]},
        {"PLAYOUT_SYNC": "measure", "players": [_BT_PLAYER]},
        {"PLAYOUT_SYNC": "loop", "EXPERIMENTAL_AIR_SENSOR": True, "players": [_BT_PLAYER]},
        {"PLAYOUT_SYNC": "off", "EXPERIMENTAL_AIR_SENSOR": True, "players": [_BT_PLAYER]},
        {
            "PLAYOUT_SYNC": "measure",
            "EXPERIMENTAL_AIR_SENSOR": True,
            "players": [{**_BT_PLAYER, "backend": {"type": "local_sink"}}],
        },
    ],
)
async def test_a_sensor_that_is_not_effective_adds_no_spawn_param(monkeypatch, config):
    params = await _spawned(monkeypatch, config)
    assert "air_sensor" not in params


def test_the_daemon_air_status_passes_the_ipc_allowlist():
    from sendspin_bridge.bridge.client import _IPC_ALLOWED_KEYS

    assert "air_sensor" in _IPC_ALLOWED_KEYS
