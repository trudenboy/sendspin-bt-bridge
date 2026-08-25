"""Brute-force lockout: two windows that were being measured from one clock.

An entry recorded the time of the *first* failure.  `_record_failure` used
that to decide whether the attempt window had rolled over, and
`_check_rate_limit` used the same timestamp to decide whether the lockout had
expired — so the lockout was measured from the first failure rather than from
the moment it started.  With the defaults (5 attempts in 1 minute, 5 minute
lockout) a user who took 50 seconds to burn their attempts served 4m10s
instead of 5 minutes.

The state was a module-level dict, so the only way to test any of this was to
mutate `auth._failed` and patch `load_config` seventeen times.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.web.login_rate_limiter import LockoutSettings, LoginRateLimiter

ALICE = "10.0.0.1"
BOB = "10.0.0.2"


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


def _limiter(clock, **kw):
    settings = LockoutSettings(
        enabled=kw.get("enabled", True),
        max_attempts=kw.get("max_attempts", 5),
        window_s=kw.get("window_s", 60),
        lockout_s=kw.get("lockout_s", 300),
    )
    return LoginRateLimiter(settings_provider=lambda: settings, clock=clock)


def _fail(limiter, client_id, times=1):
    for _ in range(times):
        limiter.record_failure(client_id)


# ── the threshold ────────────────────────────────────────────────────────


def test_a_client_with_no_failures_is_not_locked_out(clock):
    assert _limiter(clock).is_locked_out(ALICE) is False


def test_failures_below_the_threshold_do_not_lock_out(clock):
    limiter = _limiter(clock)

    _fail(limiter, ALICE, 4)

    assert limiter.is_locked_out(ALICE) is False


def test_reaching_the_threshold_locks_out(clock):
    limiter = _limiter(clock)

    _fail(limiter, ALICE, 5)

    assert limiter.is_locked_out(ALICE) is True


def test_clients_are_counted_separately(clock):
    limiter = _limiter(clock)

    _fail(limiter, ALICE, 5)

    assert limiter.is_locked_out(BOB) is False


def test_a_success_clears_the_record(clock):
    limiter = _limiter(clock)
    _fail(limiter, ALICE, 5)

    limiter.clear(ALICE)

    assert limiter.is_locked_out(ALICE) is False


def test_the_limiter_can_be_switched_off(clock):
    limiter = _limiter(clock, enabled=False)

    _fail(limiter, ALICE, 50)

    assert limiter.is_locked_out(ALICE) is False


# ── the attempt window ───────────────────────────────────────────────────


def test_failures_spread_beyond_the_window_do_not_accumulate(clock):
    limiter = _limiter(clock, window_s=60)

    for _ in range(4):
        limiter.record_failure(ALICE)
    clock.advance(61)
    limiter.record_failure(ALICE)

    assert limiter.is_locked_out(ALICE) is False, "an expired window must start counting again"


# ── the lockout window, measured from the lockout ────────────────────────


def test_the_lockout_runs_from_when_it_started_not_from_the_first_failure(clock):
    """The bug: a slow attacker used to serve a shorter sentence."""
    limiter = _limiter(clock, max_attempts=5, window_s=60, lockout_s=300)

    for _ in range(4):
        limiter.record_failure(ALICE)
        clock.advance(12)  # 48s spent on the first four
    limiter.record_failure(ALICE)  # locked out now, 48s after the first failure

    clock.advance(299)
    assert limiter.is_locked_out(ALICE) is True, "released early — the lockout was measured from the first failure"

    clock.advance(2)
    assert limiter.is_locked_out(ALICE) is False


def test_the_lockout_expires_after_its_full_duration(clock):
    limiter = _limiter(clock, lockout_s=300)
    _fail(limiter, ALICE, 5)

    clock.advance(301)

    assert limiter.is_locked_out(ALICE) is False


def test_a_failure_during_a_lockout_does_not_extend_it_indefinitely(clock):
    """Otherwise a bot hammering the door keeps a real user shut out forever."""
    limiter = _limiter(clock, lockout_s=300)
    _fail(limiter, ALICE, 5)

    for _ in range(10):
        clock.advance(20)
        limiter.record_failure(ALICE)

    clock.advance(120)  # 320s since the lockout began
    assert limiter.is_locked_out(ALICE) is False


# ── how long the caller should say to wait ───────────────────────────────


def test_the_remaining_time_counts_down(clock):
    limiter = _limiter(clock, lockout_s=300)
    _fail(limiter, ALICE, 5)

    clock.advance(100)

    assert limiter.retry_after(ALICE) == pytest.approx(200)


def test_a_client_who_is_not_locked_out_has_nothing_to_wait_for(clock):
    assert _limiter(clock).retry_after(ALICE) == 0


# ── the record store does not grow without bound ─────────────────────────


def test_expired_records_are_swept(clock):
    limiter = _limiter(clock, window_s=60, lockout_s=300)
    for i in range(250):
        limiter.record_failure(f"client-{i}")

    clock.advance(400)
    limiter.record_failure("someone-new")

    assert limiter.tracked() < 250


def test_the_store_is_capped_even_when_nothing_has_expired(clock):
    limiter = _limiter(clock, window_s=60, lockout_s=300)

    for i in range(1500):
        limiter.record_failure(f"client-{i}")

    assert limiter.tracked() <= 1000


# ── settings are read live ───────────────────────────────────────────────


def test_settings_are_re_read_rather_than_captured(clock):
    settings = {"value": LockoutSettings(enabled=True, max_attempts=5, window_s=60, lockout_s=300)}
    limiter = LoginRateLimiter(settings_provider=lambda: settings["value"], clock=clock)
    _fail(limiter, ALICE, 3)

    settings["value"] = LockoutSettings(enabled=True, max_attempts=3, window_s=60, lockout_s=300)

    assert limiter.is_locked_out(ALICE) is True
