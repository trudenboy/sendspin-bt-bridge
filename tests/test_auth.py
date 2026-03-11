"""Unit tests for brute-force protection and URL validation in routes/auth.py."""

import time
from unittest.mock import patch

import pytest
from flask import Flask

from routes.auth import (
    _LOCKOUT_DURATION_SECS,
    _LOCKOUT_WINDOW_SECS,
    _check_rate_limit,
    _clear_failures,
    _detect_auth_methods,
    _failed,
    _ma_validate_credentials,
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


# ── _detect_auth_methods ─────────────────────────────────────────────────


def test_detect_no_methods():
    """No config keys set → password always present."""
    with patch("routes.auth.load_config", return_value={}), patch.dict("os.environ", {}, clear=True):
        methods = _detect_auth_methods()
    assert methods == ["password"]


def test_detect_ma_method():
    """MA_API_URL + MA_API_TOKEN present → 'ma' in methods."""
    cfg = {"MA_API_URL": "http://ma:8095", "MA_API_TOKEN": "tok123"}
    with patch("routes.auth.load_config", return_value=cfg), patch.dict("os.environ", {}, clear=True):
        methods = _detect_auth_methods()
    assert "ma" in methods


def test_detect_ha_method():
    """SUPERVISOR_TOKEN env → 'ha' in methods."""
    with (
        patch("routes.auth.load_config", return_value={}),
        patch.dict("os.environ", {"SUPERVISOR_TOKEN": "abc"}, clear=False),
    ):
        methods = _detect_auth_methods()
    assert "ha" in methods


def test_detect_password_always_present():
    """'password' is always in methods, even without hash."""
    with patch("routes.auth.load_config", return_value={}), patch.dict("os.environ", {}, clear=True):
        methods = _detect_auth_methods()
    assert "password" in methods


def test_detect_multiple_methods():
    """In standalone mode all three methods can be available."""
    cfg = {
        "MA_API_URL": "http://ma:8095",
        "MA_API_TOKEN": "tok",
    }
    with (
        patch("routes.auth.load_config", return_value=cfg),
        patch.dict("os.environ", {}, clear=True),
    ):
        methods = _detect_auth_methods()
    assert methods == ["ma", "password"]


def test_detect_addon_mode_only_ha():
    """In addon mode only HA auth is offered."""
    cfg = {
        "MA_API_URL": "http://ma:8095",
        "MA_API_TOKEN": "tok",
    }
    with (
        patch("routes.auth.load_config", return_value=cfg),
        patch.dict("os.environ", {"SUPERVISOR_TOKEN": "x"}, clear=False),
    ):
        methods = _detect_auth_methods()
    assert methods == ["ha"]


def test_detect_ha_via_ma():
    """MA_AUTH_PROVIDER == 'ha' → 'ha_via_ma' instead of 'ma'."""
    cfg = {"MA_API_URL": "http://ma:8095", "MA_API_TOKEN": "tok", "MA_AUTH_PROVIDER": "ha"}
    with patch("routes.auth.load_config", return_value=cfg), patch.dict("os.environ", {}, clear=True):
        methods = _detect_auth_methods()
    assert "ha_via_ma" in methods
    assert "ma" not in methods


def test_detect_ma_requires_both_url_and_token():
    """MA_API_URL without token → no 'ma' method."""
    cfg = {"MA_API_URL": "http://ma:8095"}
    with patch("routes.auth.load_config", return_value=cfg), patch.dict("os.environ", {}, clear=True):
        methods = _detect_auth_methods()
    assert "ma" not in methods


# ── _ma_validate_credentials ─────────────────────────────────────────────


def test_ma_validate_no_url():
    """No MA_API_URL → fails with descriptive message."""
    with patch("routes.auth.load_config", return_value={}):
        ok, msg = _ma_validate_credentials("user", "pass")
    assert not ok
    assert "not connected" in msg.lower()


def test_ma_validate_success():
    """Successful MA login → True."""
    cfg = {"MA_API_URL": "http://ma:8095"}
    with (
        patch("routes.auth.load_config", return_value=cfg),
        patch("routes.api_ma._ma_http_login", return_value="token123"),
    ):
        ok, msg = _ma_validate_credentials("user", "pass")
    assert ok
    assert msg == ""


def test_ma_validate_bad_credentials():
    """RuntimeError from _ma_http_login → invalid credentials."""
    cfg = {"MA_API_URL": "http://ma:8095"}
    with (
        patch("routes.auth.load_config", return_value=cfg),
        patch("routes.api_ma._ma_http_login", side_effect=RuntimeError("Invalid username or password")),
    ):
        ok, msg = _ma_validate_credentials("user", "wrong")
    assert not ok
    assert "invalid" in msg.lower()


def test_ma_validate_unreachable():
    """ConnectionError → server unreachable message."""
    cfg = {"MA_API_URL": "http://ma:8095"}
    with (
        patch("routes.auth.load_config", return_value=cfg),
        patch("routes.api_ma._ma_http_login", side_effect=ConnectionError("refused")),
    ):
        ok, msg = _ma_validate_credentials("user", "pass")
    assert not ok
    assert "unreachable" in msg.lower()
