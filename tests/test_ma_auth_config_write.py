"""Tests: ``_save_ma_token_and_rediscover`` returns an actionable
JSON 500 (not raises) when ``$CONFIG_DIR`` write fails — instead of
letting Flask fall back to the default "Internal Server Error" HTML
page that issue #190 reported.

After the fix, every MA OAuth handler can do::

    err = _save_ma_token_and_rediscover(ma_url, token, ...)
    if err is not None:
        return err   # actionable 500 with chown remediation

Single-site exception handling means all 6 call sites (login,
ha_silent_auth, ha_login×3, future ones) benefit identically.
"""

from __future__ import annotations

import errno
import json

import pytest
from flask import Flask

from routes import ma_auth as ma_auth_module


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def app():
    application = Flask(__name__)
    application.secret_key = "test-secret"
    application.config["TESTING"] = True
    return application


def test_save_returns_none_on_happy_path(monkeypatch, app):
    """Contract: helper returns ``None`` when the save succeeds, so
    callers can write the natural ``if err: return err`` pattern."""
    monkeypatch.setattr(ma_auth_module, "update_config", lambda _mut: None)
    monkeypatch.setattr(ma_auth_module, "set_ma_api_credentials", lambda *_a: None)
    monkeypatch.setattr(ma_auth_module, "get_main_loop", lambda: None)

    with app.test_request_context():
        result = ma_auth_module._save_ma_token_and_rediscover("http://x", "tok", "u", "builtin")

    assert result is None


def test_save_returns_actionable_500_on_permission_denied(monkeypatch, app):
    """Issue #190 scenario: /config bind-mount left as root:root,
    update_config raises PermissionError.  Helper returns
    ``(response, 500)`` with structured JSON carrying the chown
    remediation hint, so caller propagates it verbatim instead of
    Flask 500-ing on an uncaught exception."""

    def _denied(_mutator):
        raise PermissionError(errno.EACCES, "Permission denied", "/config/config.json")

    monkeypatch.setattr(ma_auth_module, "update_config", _denied)
    monkeypatch.setattr(ma_auth_module, "set_ma_api_credentials", lambda *_a: None)
    monkeypatch.setattr(ma_auth_module, "get_main_loop", lambda: None)

    with app.test_request_context():
        result = ma_auth_module._save_ma_token_and_rediscover("http://x", "tok", "u", "builtin")

    assert result is not None, "must return error tuple instead of raising"
    response, status = result
    assert status == 500
    payload = response.get_json()
    assert payload["success"] is False
    assert "remediation" in payload
    assert "chown" in payload["remediation"]["fix"].lower()
    # Caller's intent ("save MA token") must show in the error so the
    # frontend toast reads naturally, not just "config not writable".
    assert "ma" in payload["error"].lower() or "token" in payload["error"].lower()


def test_save_returns_500_on_read_only_filesystem(monkeypatch, app):
    """EROFS gets the read-only-fs remediation, not chown."""

    def _ro(_mutator):
        raise OSError(errno.EROFS, "Read-only file system", "/config/config.json")

    monkeypatch.setattr(ma_auth_module, "update_config", _ro)
    monkeypatch.setattr(ma_auth_module, "set_ma_api_credentials", lambda *_a: None)
    monkeypatch.setattr(ma_auth_module, "get_main_loop", lambda: None)

    with app.test_request_context():
        result = ma_auth_module._save_ma_token_and_rediscover("http://x", "tok", "u", "builtin")

    assert result is not None
    _response, status = result
    assert status == 500
    payload = result[0].get_json()
    assert "read-only" in payload["error"].lower()


def test_save_does_not_swallow_non_oserror(monkeypatch, app):
    """Defensive: only OSError-class exceptions get the structured
    response.  A bug elsewhere (e.g. KeyError in the mutator) must
    propagate so the test suite catches it instead of silently
    returning a 500 that hides the real defect."""

    def _bug(_mutator):
        raise ValueError("unexpected")

    monkeypatch.setattr(ma_auth_module, "update_config", _bug)
    monkeypatch.setattr(ma_auth_module, "set_ma_api_credentials", lambda *_a: None)
    monkeypatch.setattr(ma_auth_module, "get_main_loop", lambda: None)

    with app.test_request_context(), pytest.raises(ValueError):
        ma_auth_module._save_ma_token_and_rediscover("http://x", "tok", "u", "builtin")
