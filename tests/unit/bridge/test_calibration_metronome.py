"""The calibration metronome as its own resource.

It has a subprocess, a feed task and a termination ladder, and it shared none
of that with the rest of the client — only the sink name to play on, the
declared delay to phase-align against, and a flag to publish.  Living inside
`SendspinClient` meant its process and task lifecycle could only be exercised
by standing up a client.
"""

from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.bridge.calibration_metronome import CalibrationMetronome


class _FakeStdin:
    def __init__(self):
        self.written = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.extend(data)

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def close(self) -> None:
        self.closed = True


class _FakeProc:
    def __init__(self, *, exits_on_terminate: bool = True):
        self.stdin = _FakeStdin()
        self.returncode: int | None = None
        self._exits_on_terminate = exits_on_terminate
        self.signals: list[str] = []

    def terminate(self) -> None:
        self.signals.append("terminate")
        if self._exits_on_terminate:
            self.returncode = 0

    def kill(self) -> None:
        self.signals.append("kill")
        self.returncode = -9

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0.01)
        return self.returncode


def _metronome(proc, *, sink="bluez_sink.AA_BB", flags=None, **kwargs):
    spawned: list[tuple] = []

    async def _spawn(args):
        spawned.append(args)
        return proc

    metronome = CalibrationMetronome(
        sink_name=sink,
        player_name="TestSpeaker",
        static_delay_ms=lambda: 0.0,
        on_active_change=(flags.append if flags is not None else lambda _v: None),
        spawn=_spawn,
        **kwargs,
    )
    metronome._spawned = spawned  # type: ignore[attr-defined]
    return metronome


def test_start_publishes_active_and_feeds_the_sink():
    proc = _FakeProc()
    flags: list[bool] = []
    metronome = _metronome(proc, flags=flags)

    async def _run():
        assert await metronome.start() is True
        await asyncio.sleep(0.05)
        await metronome.stop()

    asyncio.run(_run())

    assert flags == [True, False]
    assert proc.stdin.written, "the metronome never wrote audio to the sink"
    assert metronome._spawned and "bluez_sink.AA_BB" in " ".join(metronome._spawned[0])


def test_starting_twice_keeps_the_running_process():
    proc = _FakeProc()
    metronome = _metronome(proc)

    async def _run():
        await metronome.start()
        assert await metronome.start() is True
        await metronome.stop()

    asyncio.run(_run())

    assert len(metronome._spawned) == 1


def test_stop_terminates_the_process_and_clears_the_flag():
    proc = _FakeProc()
    flags: list[bool] = []
    metronome = _metronome(proc, flags=flags)

    async def _run():
        await metronome.start()
        await metronome.stop()

    asyncio.run(_run())

    assert proc.signals == ["terminate"]
    assert proc.stdin.closed
    assert metronome.active is False
    assert flags[-1] is False


def test_a_process_that_ignores_terminate_is_killed():
    proc = _FakeProc(exits_on_terminate=False)
    metronome = _metronome(proc, terminate_grace_s=0.05)

    async def _run():
        await metronome.start()
        await metronome.stop()

    asyncio.run(_run())

    assert proc.signals == ["terminate", "kill"]


def test_stop_without_start_is_a_no_op():
    metronome = _metronome(_FakeProc())

    asyncio.run(metronome.stop())

    assert metronome.active is False


def test_a_spawn_failure_is_reported_not_raised():
    async def _boom(_args):
        raise FileNotFoundError("paplay")

    metronome = CalibrationMetronome(
        sink_name="bluez_sink.AA_BB",
        player_name="TestSpeaker",
        static_delay_ms=lambda: 0.0,
        on_active_change=lambda _v: None,
        spawn=_boom,
    )

    assert asyncio.run(metronome.start()) is False
    assert metronome.active is False


def test_no_sink_means_no_metronome():
    metronome = _metronome(_FakeProc(), sink="")

    assert asyncio.run(metronome.start()) is False
    assert metronome._spawned == []


@pytest.mark.parametrize("delay_ms", [0.0, 180.0])
def test_the_declared_delay_shifts_the_phase(delay_ms):
    """Phase alignment is what makes two speakers click together."""
    proc = _FakeProc()
    spawned: list[tuple] = []

    async def _spawn(args):
        spawned.append(args)
        return proc

    metronome = CalibrationMetronome(
        sink_name="bluez_sink.AA_BB",
        player_name="TestSpeaker",
        static_delay_ms=lambda: delay_ms,
        on_active_change=lambda _v: None,
        spawn=_spawn,
    )

    async def _run():
        await metronome.start()
        await asyncio.sleep(0.02)
        await metronome.stop()

    asyncio.run(_run())

    assert proc.stdin.written


# ── player selection and buffering, moved here with the code they describe ──


def test_the_pulse_player_asks_for_a_small_deterministic_buffer():
    from sendspin_bridge.bridge.calibration_metronome import _calibration_metronome_paplay_args

    args = _calibration_metronome_paplay_args("bluez_sink.test")

    assert "--latency-msec=20" in args
    assert "--process-time-msec=5" in args


def test_a_pipewire_sink_uses_the_native_pipewire_player(monkeypatch):
    import sendspin_bridge.bridge.calibration_metronome as mod

    monkeypatch.setattr(mod.shutil, "which", lambda name: "/usr/bin/pw-play" if name == "pw-play" else None)

    args = mod._calibration_metronome_player_args("bluez_output.test.1")

    assert args[0] == "/usr/bin/pw-play"
    assert "--target=bluez_output.test.1" in args
    assert "--latency=20ms" in args


def test_a_pulseaudio_sink_keeps_paplay_even_when_pw_play_exists(monkeypatch):
    import sendspin_bridge.bridge.calibration_metronome as mod

    monkeypatch.setattr(mod.shutil, "which", lambda _name: "/usr/bin/pw-play")

    args = mod._calibration_metronome_player_args("bluez_sink.test.a2dp_sink")

    assert args[0] == "paplay"
    assert "--device=bluez_sink.test.a2dp_sink" in args


def test_the_train_keeps_playing_until_it_is_told_to_stop():
    """It must not stop itself after the lead-in; that is the whole point."""
    proc = _FakeProc()
    metronome = _metronome(proc)

    async def _run():
        await metronome.start()
        await asyncio.sleep(0.05)
        still_running = metronome.active
        await metronome.stop()
        return still_running

    assert asyncio.run(_run()) is True
    assert metronome.active is False


def test_the_declared_delay_shifts_the_phase_epoch(monkeypatch):
    """Two speakers click together only if each shifts by its own delay."""
    import sendspin_bridge.bridge.calibration_metronome as mod

    epochs: list[float] = []

    def _capture(_started_at, **kwargs):
        epochs.append(kwargs["epoch_seconds"])
        return 1

    monkeypatch.setattr(mod, "calculate_metronome_lead_frames", _capture)

    async def _run(delay_ms):
        proc = _FakeProc()
        metronome = _metronome(proc)
        metronome._static_delay_ms = lambda: delay_ms
        await metronome.start()
        await metronome.stop()

    asyncio.run(_run(0.0))
    asyncio.run(_run(180.0))

    assert epochs[0] - epochs[1] == pytest.approx(0.180, abs=1e-6)
