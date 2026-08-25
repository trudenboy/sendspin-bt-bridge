"""The cost of deriving guidance must not follow the rate of status changes.

Every SSE tick rebuilt the whole status payload, and the derived half of that
payload probes the host: two `bluetoothctl` invocations plus the audio and
D-Bus checks.  Measured on a live bridge, that half costs ~55 ms of the ~60 ms
a status build takes, and an idle bridge with one speaker ticks about every
six seconds — per connected client.  A speaker that is actually playing ticks
far more often, and every listening browser tab multiplies it.

Runtime state changes many times a second and is read from memory.  What the
host looks like changes on the order of seconds and costs subprocesses.  They
belong on different clocks.
"""

from __future__ import annotations

import threading

import pytest

from sendspin_bridge.services.diagnostics.status_derivation import StatusDerivation


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _counting_build(results: list[str] | None = None):
    calls: list[int] = []

    def _build():
        calls.append(len(calls) + 1)
        return (results or ["value"] * 99)[len(calls) - 1]

    return _build, calls


# ── the window ───────────────────────────────────────────────────────────


def test_the_first_call_builds():
    build, calls = _counting_build()

    derivation = StatusDerivation(build, min_interval_s=2.0, clock=_Clock())

    assert derivation.current() == "value"
    assert len(calls) == 1


def test_a_second_call_inside_the_window_reuses_the_first():
    build, calls = _counting_build()
    clock = _Clock()
    derivation = StatusDerivation(build, min_interval_s=2.0, clock=clock)

    derivation.current()
    clock.advance(1.9)
    derivation.current()

    assert len(calls) == 1


def test_a_call_after_the_window_builds_again():
    build, calls = _counting_build()
    clock = _Clock()
    derivation = StatusDerivation(build, min_interval_s=2.0, clock=clock)

    derivation.current()
    clock.advance(2.1)
    derivation.current()

    assert len(calls) == 2


# ── events that cannot wait ──────────────────────────────────────────────


def test_an_invalidation_rebuilds_inside_the_window():
    """A saved config or an added speaker must show up at once."""
    build, calls = _counting_build()
    clock = _Clock()
    derivation = StatusDerivation(build, min_interval_s=60.0, clock=clock)

    derivation.current()
    derivation.invalidate()
    derivation.current()

    assert len(calls) == 2


def test_an_invalidation_without_a_reader_costs_nothing():
    build, calls = _counting_build()
    derivation = StatusDerivation(build, min_interval_s=60.0, clock=_Clock())

    derivation.invalidate()
    derivation.invalidate()

    assert calls == []


# ── many readers, one probe ──────────────────────────────────────────────


def test_concurrent_readers_do_not_each_probe_the_host():
    """Every browser tab holds a stream; they must not multiply the probes."""
    started = threading.Event()
    release = threading.Event()
    calls: list[int] = []

    def _slow_build():
        calls.append(1)
        started.set()
        release.wait(timeout=5)
        return "value"

    # A window wide enough that a reader waking after the build finds the
    # fresh value rather than a due rebuild — the subject here is what happens
    # *during* one build.
    derivation = StatusDerivation(_slow_build, min_interval_s=60.0, clock=_Clock())
    first = threading.Thread(target=derivation.current)
    first.start()
    started.wait(timeout=5)

    others = [threading.Thread(target=derivation.current) for _ in range(4)]
    for thread in others:
        thread.start()
    for thread in others:
        thread.join(timeout=5)

    assert len(calls) == 1, "a reader started its own probe while one was already running"

    release.set()
    first.join(timeout=5)


def test_a_reader_arriving_during_a_build_gets_the_previous_value():
    """Waiting on the probe would put its cost back on the tick."""
    clock = _Clock()
    release = threading.Event()
    started = threading.Event()
    values = iter(["first", "second"])

    def _build():
        value = next(values)
        if value == "second":
            started.set()
            release.wait(timeout=5)
        return value

    derivation = StatusDerivation(_build, min_interval_s=0.0, clock=clock)
    assert derivation.current() == "first"

    slow = threading.Thread(target=derivation.current)
    slow.start()
    started.wait(timeout=5)

    assert derivation.current() == "first"

    release.set()
    slow.join(timeout=5)
    assert derivation.current() == "second"


# ── failure ──────────────────────────────────────────────────────────────


def test_the_first_build_reports_its_failure():
    """With nothing cached there is nothing to serve instead."""

    def _build():
        raise RuntimeError("pactl is missing")

    derivation = StatusDerivation(_build, min_interval_s=2.0, clock=_Clock())

    with pytest.raises(RuntimeError):
        derivation.current()


def test_a_later_failure_keeps_serving_what_was_last_true():
    """One failed probe must not blank the operator's screen."""
    clock = _Clock()
    calls: list[int] = []

    def _build():
        calls.append(len(calls) + 1)
        if len(calls) > 1:
            raise RuntimeError("bluetoothctl vanished")
        return "value"

    derivation = StatusDerivation(_build, min_interval_s=2.0, clock=clock)
    assert derivation.current() == "value"

    clock.advance(3.0)

    assert derivation.current() == "value"
    assert len(calls) == 2


def test_a_failure_does_not_pin_the_window_open():
    """The next window must retry rather than serve the stale value forever."""
    clock = _Clock()
    calls: list[int] = []

    def _build():
        calls.append(len(calls) + 1)
        if len(calls) == 2:
            raise RuntimeError("transient")
        return f"value-{len(calls)}"

    derivation = StatusDerivation(_build, min_interval_s=2.0, clock=clock)
    derivation.current()
    clock.advance(3.0)
    derivation.current()
    clock.advance(3.0)

    assert derivation.current() == "value-3"
