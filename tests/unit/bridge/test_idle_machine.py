"""When a speaker should be let go, and when it should be woken.

The four idle modes were a set of branches spread across `_update_status`,
two sink callbacks and three timer starters, each reading `self.status` and
firing side effects inline.  Deciding anything meant standing up a client
with a Bluetooth manager and a sink monitor; the tests for it built clients
through `SendspinClient.__new__` and wired six private attributes by hand.

The decisions are arithmetic over five flags and a mode.  Here they are the
whole module, and what to *do* about them stays with the client.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bridge.idle_machine import (
    ArmIdleTimer,
    ArmPowerSaveTimer,
    ArmSinkMuteWatchdog,
    CancelIdleTimer,
    CancelPowerSaveTimer,
    CancelSinkMuteWatchdog,
    ExitPowerSave,
    IdleMachine,
    PlaybackState,
)


def _state(**kw) -> PlaybackState:
    base = {
        "playing": False,
        "audio_streaming": False,
        "server_connected": False,
        "bt_standby": False,
        "bt_power_save": False,
        "sink_muted": False,
        "muted": False,
    }
    base.update(kw)
    return PlaybackState(**base)


def _machine(mode="auto_disconnect", *, sink_monitor_active=False, **kw):
    return IdleMachine(
        mode=mode,
        idle_disconnect_minutes=kw.get("idle_disconnect_minutes", 10),
        power_save_delay_minutes=kw.get("power_save_delay_minutes", 5),
        sink_monitor_active=lambda: sink_monitor_active,
    )


# ── default mode does nothing ────────────────────────────────────────────


@pytest.mark.parametrize("mode", ["default", "keep_alive"])
def test_modes_that_never_release_the_speaker_arm_nothing(mode):
    machine = _machine(mode)

    actions = machine.on_playback_change(
        previous=_state(playing=True, audio_streaming=True),
        current=_state(),
    )

    assert actions == []


# ── audio stopping arms the right timer ──────────────────────────────────


def test_audio_stopping_arms_the_idle_timer():
    machine = _machine("auto_disconnect", idle_disconnect_minutes=7)

    actions = machine.on_playback_change(
        previous=_state(playing=True),
        current=_state(),
    )

    assert actions == [ArmIdleTimer(delay_s=7 * 60)]


def test_audio_stopping_arms_the_power_save_timer():
    machine = _machine("power_save", power_save_delay_minutes=3)

    actions = machine.on_playback_change(
        previous=_state(audio_streaming=True),
        current=_state(),
    )

    assert actions == [ArmPowerSaveTimer(delay_s=3 * 60)]


def test_a_speaker_already_in_standby_is_left_alone():
    machine = _machine("auto_disconnect")

    actions = machine.on_playback_change(
        previous=_state(playing=True),
        current=_state(bt_standby=True),
    )

    assert actions == []


# ── audio starting cancels, and wakes from power save ────────────────────


def test_audio_starting_cancels_the_idle_timer():
    machine = _machine("auto_disconnect")

    actions = machine.on_playback_change(previous=_state(), current=_state(playing=True))

    assert actions == [CancelIdleTimer()]


def test_audio_starting_leaves_power_save_when_the_sink_was_suspended():
    machine = _machine("power_save")

    actions = machine.on_playback_change(
        previous=_state(bt_power_save=True),
        current=_state(playing=True, bt_power_save=True),
    )

    assert actions == [CancelIdleTimer(), CancelPowerSaveTimer(), ExitPowerSave()]


def test_audio_starting_without_power_save_does_not_try_to_leave_it():
    machine = _machine("power_save")

    actions = machine.on_playback_change(previous=_state(), current=_state(playing=True))

    assert ExitPowerSave() not in actions


def test_playback_that_did_not_change_decides_nothing():
    machine = _machine("auto_disconnect")

    assert machine.on_playback_change(previous=_state(playing=True), current=_state(playing=True)) == []


# ── the server-connected fallback ────────────────────────────────────────


def test_an_idle_connection_arms_the_timer_when_no_sink_monitor_is_running():
    """Without PulseAudio events, a connected-but-silent speaker still counts."""
    machine = _machine("auto_disconnect", sink_monitor_active=False)

    actions = machine.on_server_connected(_state(server_connected=True))

    assert actions == [ArmIdleTimer(delay_s=600)]


def test_the_sink_monitor_owns_the_decision_when_it_is_running():
    machine = _machine("auto_disconnect", sink_monitor_active=True)

    assert machine.on_server_connected(_state(server_connected=True)) == []


def test_a_connection_with_audio_flowing_arms_nothing():
    machine = _machine("auto_disconnect", sink_monitor_active=False)

    assert machine.on_server_connected(_state(server_connected=True, playing=True)) == []


# ── sink monitor callbacks ───────────────────────────────────────────────


def test_a_running_sink_cancels_the_timer_and_leaves_power_save():
    machine = _machine("power_save")

    assert machine.on_sink_active(_state(bt_power_save=True)) == [CancelPowerSaveTimer(), ExitPowerSave()]


def test_an_idle_sink_arms_the_timer():
    machine = _machine("power_save", power_save_delay_minutes=2)

    assert machine.on_sink_idle(_state()) == [ArmPowerSaveTimer(delay_s=120)]


def test_an_idle_sink_is_ignored_in_standby():
    machine = _machine("auto_disconnect")

    assert machine.on_sink_idle(_state(bt_standby=True)) == []


# ── the sink-mute watchdog ───────────────────────────────────────────────


def test_a_sink_muted_behind_our_back_arms_the_watchdog():
    machine = _machine("default")

    assert machine.on_sink_mute_change(_state(sink_muted=True)) == [ArmSinkMuteWatchdog()]


def test_a_deliberate_mute_does_not_arm_the_watchdog():
    """The operator muted it; that is not a sink drifting out from under us."""
    machine = _machine("default")

    assert machine.on_sink_mute_change(_state(sink_muted=True, muted=True)) == [CancelSinkMuteWatchdog()]


def test_an_unmuted_sink_disarms_the_watchdog():
    machine = _machine("default")

    assert machine.on_sink_mute_change(_state()) == [CancelSinkMuteWatchdog()]


# ── zero delays ──────────────────────────────────────────────────────────
#
# `client.py` calls `idle_disconnect_minutes` "0 = disabled", but the code has
# never honoured that: it arms a zero-second timer, so a speaker configured
# that way is released the moment audio stops.  Migration only ever assigns
# `auto_disconnect` when the value is above zero, which is presumably why it
# has not been noticed.  These tests pin what the bridge *does* — changing it
# is a decision for its own commit, not a refactor's side effect.


def test_a_zero_idle_delay_arms_immediately_despite_the_docstring():
    machine = _machine("auto_disconnect", idle_disconnect_minutes=0)

    assert machine.on_playback_change(previous=_state(playing=True), current=_state()) == [ArmIdleTimer(delay_s=0)]


def test_a_zero_power_save_delay_arms_immediately():
    machine = _machine("power_save", power_save_delay_minutes=0)

    assert machine.on_playback_change(previous=_state(playing=True), current=_state()) == [ArmPowerSaveTimer(delay_s=0)]
