"""Tests for the ``config_write_error_response`` helper.

When any handler tries to persist into ``$CONFIG_DIR`` and hits
``PermissionError`` / ``OSError``, Flask used to fall back to the
default 500 ``Internal Server Error`` HTML page — operators saw a
generic failure with no signal pointing at file ownership (issue
#190).

The helper builds a structured JSON 500 with a chown remediation
hint so the next operator who hits this gets one-glance diagnosis.
"""

from __future__ import annotations

import errno
import os

from flask import Flask

from routes._helpers import config_write_error_response


def _make_app():
    app = Flask(__name__)
    return app


def test_returns_500_with_success_false_and_actionable_error():
    """Default contract: status 500, JSON body with ``success: false``,
    a human-readable ``error`` mentioning "not writable", and a
    structured ``remediation`` block carrying the exact chown command."""
    app = _make_app()
    with app.app_context():
        exc = PermissionError(errno.EACCES, "Permission denied", "/config/config.json")
        response, status = config_write_error_response(exc)

    assert status == 500
    payload = response.get_json()
    assert payload["success"] is False
    assert "not writable" in payload["error"].lower() or "permission" in payload["error"].lower()
    assert "remediation" in payload
    assert "chown" in payload["remediation"]["fix"].lower()


def test_remediation_carries_runtime_uid_when_known():
    """The chown command in the remediation hint must include the
    process's actual UID — operators copy it verbatim, so a wrong
    UID would have them chown to the wrong owner and the next
    write would fail again."""
    app = _make_app()
    with app.app_context():
        exc = PermissionError(errno.EACCES, "Permission denied", "/config/config.json")
        response, _ = config_write_error_response(exc)

    payload = response.get_json()
    expected_uid = str(os.getuid())
    assert expected_uid in payload["remediation"]["fix"]


def test_distinguishes_read_only_filesystem():
    """``EROFS`` (read-only filesystem) is a different remediation
    from ``EACCES`` (wrong ownership) — the operator can't fix the
    former with chown.  The hint must reflect that so we don't send
    them on a wild goose chase."""
    app = _make_app()
    with app.app_context():
        exc = OSError(errno.EROFS, "Read-only file system", "/config/config.json")
        response, status = config_write_error_response(exc)

    assert status == 500
    payload = response.get_json()
    assert "read-only" in payload["error"].lower()
    # The remediation block exists but mentions remount, not chown
    assert "chown" not in payload["remediation"]["fix"].lower()
    assert "remount" in payload["remediation"]["fix"].lower() or "rw" in payload["remediation"]["fix"].lower()


def test_unknown_oserror_falls_back_to_generic_500():
    """An unrecognised errno (e.g. ENOSPC, EIO) shouldn't pretend
    to know the fix — return a structured 500 so the frontend can
    still render it cleanly, but with a generic remediation pointing
    at the logs."""
    app = _make_app()
    with app.app_context():
        exc = OSError(errno.ENOSPC, "No space left on device", "/config/config.json")
        response, status = config_write_error_response(exc)

    assert status == 500
    payload = response.get_json()
    assert payload["success"] is False
    assert "error" in payload
    # Generic — no false chown promise
    assert "chown" not in payload.get("remediation", {}).get("fix", "").lower()


def test_includes_optional_context_in_error_message():
    """Callers can pass an extra ``context`` string (e.g. "Cannot
    save MA token") to prefix the error with what the user was
    trying to do.  Helps the frontend toast read naturally instead
    of just "config not writable" with no operation context."""
    app = _make_app()
    with app.app_context():
        exc = PermissionError(errno.EACCES, "Permission denied", "/config/config.json")
        response, _ = config_write_error_response(exc, context="Cannot save MA token")

    payload = response.get_json()
    assert payload["error"].startswith("Cannot save MA token")
