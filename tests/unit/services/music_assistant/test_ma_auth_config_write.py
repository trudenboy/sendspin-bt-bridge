"""Tests: ``_save_ma_token_and_rediscover`` turns a failed ``$CONFIG_DIR``
write into an actionable 500 instead of a generic "Internal Server Error"
(issue #190).

The single raise site means every MA sign-in path (password, HA silent,
HA login with MFA) answers with the same chown remediation.
"""

from __future__ import annotations

import errno
import json

import pytest

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.music_assistant import auth as ma_auth_module


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture(autouse=True)
def _quiet_runtime(monkeypatch):
    monkeypatch.setattr(ma_auth_module, "set_ma_api_credentials", lambda *_a: None)
    monkeypatch.setattr(ma_auth_module, "get_main_loop", lambda: None)


def test_save_returns_none_on_happy_path(monkeypatch):
    monkeypatch.setattr(ma_auth_module, "update_config", lambda _mut: None)

    assert ma_auth_module._save_ma_token_and_rediscover("http://x", "tok", "u", "builtin") is None


def test_save_raises_actionable_500_on_permission_denied(monkeypatch):
    """Issue #190 scenario: /config bind-mount left as root:root,
    update_config raises PermissionError.  The helper raises a 500 use-case
    error carrying the chown remediation, which the API renders verbatim."""

    def _denied(_mutator):
        raise PermissionError(errno.EACCES, "Permission denied", "/config/config.json")

    monkeypatch.setattr(ma_auth_module, "update_config", _denied)

    with pytest.raises(UseCaseError) as exc:
        ma_auth_module._save_ma_token_and_rediscover("http://x", "tok", "u", "builtin")

    assert exc.value.status == 500
    assert "chown" in exc.value.extra["remediation"]["fix"].lower()
    # Caller's intent ("save MA token") must show in the error so the
    # frontend toast reads naturally, not just "config not writable".
    assert "ma" in exc.value.detail.lower() or "token" in exc.value.detail.lower()


def test_save_raises_500_on_read_only_filesystem(monkeypatch):
    """EROFS gets the read-only-fs remediation, not chown."""

    def _ro(_mutator):
        raise OSError(errno.EROFS, "Read-only file system", "/config/config.json")

    monkeypatch.setattr(ma_auth_module, "update_config", _ro)

    with pytest.raises(UseCaseError) as exc:
        ma_auth_module._save_ma_token_and_rediscover("http://x", "tok", "u", "builtin")

    assert exc.value.status == 500
    assert "read-only" in exc.value.detail.lower()


def test_save_does_not_swallow_non_oserror(monkeypatch):
    """Only OSError-class exceptions get the structured error; a bug elsewhere
    (e.g. ValueError in the mutator) must propagate unchanged."""

    def _bug(_mutator):
        raise ValueError("unexpected")

    monkeypatch.setattr(ma_auth_module, "update_config", _bug)

    with pytest.raises(ValueError):
        ma_auth_module._save_ma_token_and_rediscover("http://x", "tok", "u", "builtin")
