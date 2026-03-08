"""Unit tests for brute-force protection and URL validation in routes/auth.py."""

import time

import pytest
from flask import Flask

from routes.auth import (
    _LOCKOUT_DURATION_SECS,
    _LOCKOUT_WINDOW_SECS,
    _check_rate_limit,
    _clear_failures,
    _failed,
    _record_failure,
    _safe_next_url,
)


@pytest.fixture(autouse=True)
def _reset_failed():
    _failed.clear()
    yield
    _failed.clear()


# ── Brute-force protection ───────────────────────────────────────────────


def test_no_failures_not_locked():
    assert _check_rate_limit("10.0.0.1") is False


def test_under_limit_not_locked():
    for _ in range(4):
        _record_failure("10.0.0.2")
    assert _check_rate_limit("10.0.0.2") is False


def test_at_limit_locks():
    for _ in range(5):
        _record_failure("10.0.0.3")
    assert _check_rate_limit("10.0.0.3") is True


def test_clear_failures_unlocks():
    for _ in range(5):
        _record_failure("10.0.0.4")
    assert _check_rate_limit("10.0.0.4") is True
    _clear_failures("10.0.0.4")
    assert _check_rate_limit("10.0.0.4") is False


def test_lockout_expires(monkeypatch):
    fake_time = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    for _ in range(5):
        _record_failure("10.0.0.5")
    assert _check_rate_limit("10.0.0.5") is True

    # Advance past lockout duration
    fake_time[0] = 1000.0 + _LOCKOUT_DURATION_SECS + 1
    assert _check_rate_limit("10.0.0.5") is False


def test_window_reset(monkeypatch):
    fake_time = [1000.0]
    monkeypatch.setattr(time, "monotonic", lambda: fake_time[0])

    for _ in range(4):
        _record_failure("10.0.0.6")

    # Jump past the window — next failure starts a fresh count
    fake_time[0] = 1000.0 + _LOCKOUT_WINDOW_SECS + 1
    _record_failure("10.0.0.6")

    # Only 1 failure in the new window → not locked
    assert _check_rate_limit("10.0.0.6") is False


# ── _safe_next_url ───────────────────────────────────────────────────────

_app = Flask(__name__)
_app.secret_key = "test"


def test_safe_local_path():
    with _app.test_request_context("/?next=/settings"):
        assert _safe_next_url() == "/settings"


def test_rejects_absolute_url():
    with _app.test_request_context("/?next=http://evil.com"):
        assert _safe_next_url() == "/"


def test_rejects_protocol_relative():
    with _app.test_request_context("/?next=//evil.com"):
        assert _safe_next_url() == "/"


def test_rejects_no_leading_slash():
    with _app.test_request_context("/?next=evil.com"):
        assert _safe_next_url() == "/"


def test_default_is_root():
    with _app.test_request_context("/"):
        assert _safe_next_url() == "/"
