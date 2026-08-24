"""What to do when a speaker's daemon dies.

Restarting is not unconditional: a daemon that dies because Bluetooth went
away must wait for Bluetooth rather than spin, a daemon that cannot bind its
port must stop trying, and one that dies at the same moment on every attempt
is hitting a deterministic timeout the operator can act on.

All of that lived inside a 120-line branch of the status loop, tangled with
the status writes and the log lines it produced, so none of it could be
asked a question without a running loop and a subprocess.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from sendspin_bridge.bridge.daemon_supervisor import (
    DaemonSupervisor,
    Halted,
    RestartAfter,
    SpawnRecord,
    WaitForBluetooth,
)

UTC = timezone.utc


def _record(*, pid: int, lifetime_s: float | None, unexpected: bool = True) -> SpawnRecord:
    spawn_at = datetime(2026, 8, 24, 12, 0, 0, tzinfo=UTC)
    record = SpawnRecord(pid=pid, spawn_at=spawn_at)
    if lifetime_s is not None:
        record.exit_at = spawn_at + timedelta(seconds=lifetime_s)
        record.lifetime_s = lifetime_s
        record.exit_code = 1
        record.unexpected = unexpected
    return record


# ── backoff ──────────────────────────────────────────────────────────────


def test_the_first_restart_is_immediate_enough_to_feel_instant():
    supervisor = DaemonSupervisor()

    assert supervisor.on_death(bt_connected=True) == RestartAfter(delay_s=1.0)


def test_repeated_deaths_back_off_exponentially():
    supervisor = DaemonSupervisor()

    delays = [supervisor.on_death(bt_connected=True).delay_s for _ in range(5)]

    assert delays == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_backoff_is_capped():
    supervisor = DaemonSupervisor()
    for _ in range(20):
        supervisor.on_death(bt_connected=True)

    assert supervisor.on_death(bt_connected=True).delay_s == 30.0


def test_a_daemon_that_stays_up_resets_the_backoff():
    supervisor = DaemonSupervisor()
    for _ in range(4):
        supervisor.on_death(bt_connected=True)

    supervisor.on_alive()

    assert supervisor.on_death(bt_connected=True) == RestartAfter(delay_s=1.0)


# ── who drives the restart ───────────────────────────────────────────────


def test_a_disconnected_speaker_waits_for_bluetooth_instead_of_spinning():
    supervisor = DaemonSupervisor()

    assert supervisor.on_death(bt_connected=False) == WaitForBluetooth()


def test_waiting_for_bluetooth_resets_the_backoff():
    """Bluetooth will drive the restart, so the next one starts fresh."""
    supervisor = DaemonSupervisor()
    for _ in range(4):
        supervisor.on_death(bt_connected=True)

    supervisor.on_death(bt_connected=False)

    assert supervisor.on_death(bt_connected=True) == RestartAfter(delay_s=1.0)


def test_a_bridge_without_bluetooth_always_restarts():
    """Nothing else will do it — there is no Bluetooth monitor to wait for."""
    supervisor = DaemonSupervisor()

    assert supervisor.on_death(bt_connected=None) == RestartAfter(delay_s=1.0)


def test_a_halted_supervisor_waits_without_respawning():
    supervisor = DaemonSupervisor()
    supervisor.halt()

    decision = supervisor.on_death(bt_connected=True)

    assert isinstance(decision, Halted)
    assert decision.delay_s == 1.0


def test_a_daemon_that_comes_up_clears_the_halt():
    supervisor = DaemonSupervisor()
    supervisor.halt()

    supervisor.on_alive()

    assert supervisor.on_death(bt_connected=True) == RestartAfter(delay_s=1.0)


# ── the deterministic-timeout signal ─────────────────────────────────────


def test_three_deaths_at_the_same_moment_are_reported_as_a_pattern():
    supervisor = DaemonSupervisor()
    for pid, lifetime in [(1, 10.0), (2, 10.4), (3, 9.7)]:
        supervisor.record(_record(pid=pid, lifetime_s=lifetime))

    assert supervisor.repeating_lifetime() == pytest.approx(10.033, abs=0.01)


def test_scattered_lifetimes_are_not_a_pattern():
    supervisor = DaemonSupervisor()
    for pid, lifetime in [(1, 2.0), (2, 30.0), (3, 9.0)]:
        supervisor.record(_record(pid=pid, lifetime_s=lifetime))

    assert supervisor.repeating_lifetime() is None


def test_two_deaths_are_not_yet_a_pattern():
    supervisor = DaemonSupervisor()
    for pid, lifetime in [(1, 10.0), (2, 10.0)]:
        supervisor.record(_record(pid=pid, lifetime_s=lifetime))

    assert supervisor.repeating_lifetime() is None


def test_deliberate_stops_do_not_count_towards_the_pattern():
    """We stopped it; that says nothing about a handshake timeout."""
    supervisor = DaemonSupervisor()
    for pid in (1, 2, 3):
        supervisor.record(_record(pid=pid, lifetime_s=10.0, unexpected=False))

    assert supervisor.repeating_lifetime() is None


# ── the record window ────────────────────────────────────────────────────


def test_the_history_is_bounded():
    supervisor = DaemonSupervisor(history=3)
    for pid in range(6):
        supervisor.record(_record(pid=pid, lifetime_s=1.0))

    assert [r.pid for r in supervisor.records()] == [3, 4, 5]


def test_recent_records_are_serialisable_oldest_first():
    supervisor = DaemonSupervisor()
    for pid in (1, 2, 3):
        supervisor.record(_record(pid=pid, lifetime_s=float(pid)))

    rows = supervisor.recent(2)

    assert [row["pid"] for row in rows] == [2, 3]
    assert rows[0]["lifetime_s"] == 2.0
    assert rows[0]["spawn_at"].endswith("+00:00")
