"""Reconnect decisions, separated from the machinery that carries them out.

Backoff, churn detection, the never-paired auto-disable and the auto-reclaim
quiet period are arithmetic over counters and a clock.  They used to be
reachable only through a live ``BluetoothManager``, which is why the monitor's
tests carry 182 mocks; here they are table tests with an injected clock.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.reconnect_policy import (
    Churned,
    DisableNeverPaired,
    KeepTrying,
    ReconnectPolicy,
    ReleaseManagement,
    TryAdapterRecovery,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture()
def clock():
    return _Clock()


def _policy(clock, **kwargs):
    params = {
        "check_interval": 10,
        "max_fails": 5,
        "churn_threshold": 0,
        "churn_window": 300.0,
        "clock": clock,
    }
    params.update(kwargs)
    return ReconnectPolicy(**params)


# ── backoff ──────────────────────────────────────────────────────────────


def test_first_three_attempts_use_the_check_interval(clock):
    policy = _policy(clock)

    assert policy.delay_for(1) == 10
    assert policy.delay_for(2) == 10
    assert policy.delay_for(3) == 10


def test_backoff_doubles_after_the_third_attempt(clock):
    policy = _policy(clock)

    assert policy.delay_for(4) == 20
    assert policy.delay_for(5) == 40


def test_backoff_is_capped_at_five_minutes(clock):
    policy = _policy(clock)

    assert policy.delay_for(50) == 300.0


# ── churn ────────────────────────────────────────────────────────────────


def test_churn_is_disabled_at_threshold_zero(clock):
    policy = _policy(clock, churn_threshold=0)
    for _ in range(20):
        policy.record_reconnect()

    assert isinstance(policy.on_failure(1, paired=True, ever_paired=True), KeepTrying)


def test_churn_fires_once_the_threshold_is_reached_inside_the_window(clock):
    policy = _policy(clock, churn_threshold=3, churn_window=300.0)
    for _ in range(3):
        policy.record_reconnect()
        clock.advance(10)

    decision = policy.on_failure(1, paired=True, ever_paired=True)

    assert isinstance(decision, Churned)
    assert decision.reconnect_count == 3
    assert decision.window_s == 300.0


def test_reconnects_outside_the_window_do_not_count(clock):
    policy = _policy(clock, churn_threshold=3, churn_window=300.0)
    for _ in range(3):
        policy.record_reconnect()
    clock.advance(301)
    policy.record_reconnect()

    assert isinstance(policy.on_failure(1, paired=True, ever_paired=True), KeepTrying)


# ── failure ladder ───────────────────────────────────────────────────────


def test_failures_below_the_threshold_keep_trying(clock):
    policy = _policy(clock, max_fails=5)

    assert isinstance(policy.on_failure(4, paired=True, ever_paired=True), KeepTrying)


def test_a_zero_threshold_never_releases(clock):
    policy = _policy(clock, max_fails=0)

    assert isinstance(policy.on_failure(99, paired=True, ever_paired=True), KeepTrying)


def test_adapter_recovery_is_offered_before_releasing(clock):
    policy = _policy(clock, max_fails=5)

    decision = policy.on_failure(5, paired=True, ever_paired=True, recovery_available=True)

    assert isinstance(decision, TryAdapterRecovery)
    assert decision.attempt == 5


def test_recovery_is_offered_only_once_per_ladder(clock):
    policy = _policy(clock, max_fails=5)

    decision = policy.on_failure(5, paired=True, ever_paired=True, recovery_available=True, recovery_attempted=True)

    assert isinstance(decision, ReleaseManagement)


def test_a_device_bluez_never_knew_is_disabled_rather_than_released(clock):
    policy = _policy(clock, max_fails=5)

    decision = policy.on_failure(5, paired=None, ever_paired=False)

    assert isinstance(decision, DisableNeverPaired)
    assert decision.attempt == 5


def test_a_device_that_paired_this_session_is_released_not_disabled(clock):
    policy = _policy(clock, max_fails=5)

    decision = policy.on_failure(5, paired=None, ever_paired=True)

    assert isinstance(decision, ReleaseManagement)
    assert decision.attempt == 5
    assert decision.threshold == 5


# ── auto-reclaim quiet period ────────────────────────────────────────────


def test_reclaim_waits_out_the_quiet_period(clock):
    policy = _policy(clock)
    policy.mark_released()

    clock.advance(59)
    assert policy.may_reclaim(connected=True) is False

    clock.advance(2)
    assert policy.may_reclaim(connected=True) is True


def test_reclaim_needs_an_established_link(clock):
    policy = _policy(clock)
    policy.mark_released()
    clock.advance(120)

    assert policy.may_reclaim(connected=False) is False
    assert policy.may_reclaim(connected=None) is False


def test_reclaim_clears_the_churn_history(clock):
    policy = _policy(clock, churn_threshold=3)
    for _ in range(3):
        policy.record_reconnect()
    policy.mark_released()
    clock.advance(120)

    assert policy.may_reclaim(connected=True) is True
    policy.mark_reclaimed()

    assert isinstance(policy.on_failure(1, paired=True, ever_paired=True), KeepTrying)


def test_a_release_carried_over_from_an_earlier_run_reclaims_at_once(clock):
    """No quiet period to observe: the churn ended with the previous process."""
    policy = _policy(clock)

    assert policy.may_reclaim(connected=True) is True
    assert policy.may_reclaim(connected=False) is False
