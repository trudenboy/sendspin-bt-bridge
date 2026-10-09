"""Tests for the ``config_write_error`` use-case error.

When any handler tries to persist into ``$CONFIG_DIR`` and hits
``PermissionError`` / ``OSError``, the server used to fall back to a
generic 500 — operators saw a failure with no signal pointing at file
ownership (issue #190).

``config_write_error`` builds a structured 500 problem with a chown
remediation hint so the next operator gets one-glance diagnosis.
"""

from __future__ import annotations

import errno
import os

from fastapi import FastAPI
from fastapi.testclient import TestClient

from sendspin_bridge.api.errors import install_error_handlers
from sendspin_bridge.application.errors import config_write_error


def config_write_error_response(exc: OSError, context: str | None = None) -> tuple[dict, int]:
    """Render the error the way the API does: the problem body and its status."""
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/save")
    def _save():
        raise config_write_error(exc, context)

    response = TestClient(app).get("/save")
    return response.json(), response.status_code


def test_returns_500_with_success_false_and_actionable_error():
    """Default contract: status 500, a problem body with a stable ``code``,
    a human-readable ``detail`` mentioning "not writable", and a
    structured ``remediation`` block carrying the exact chown command."""
    exc = PermissionError(errno.EACCES, "Permission denied", "/config/config.json")
    payload, status = config_write_error_response(exc)

    assert status == 500
    assert payload["code"] == "config_not_writable"
    assert "not writable" in payload["detail"].lower() or "permission" in payload["detail"].lower()
    assert "remediation" in payload
    assert "chown" in payload["remediation"]["fix"].lower()


def test_remediation_carries_runtime_uid_when_known():
    """The chown command in the remediation hint must include the
    process's actual UID — operators copy it verbatim, so a wrong
    UID would have them chown to the wrong owner and the next
    write would fail again."""
    exc = PermissionError(errno.EACCES, "Permission denied", "/config/config.json")
    payload, _ = config_write_error_response(exc)

    expected_uid = str(os.getuid())
    assert expected_uid in payload["remediation"]["fix"]


def test_distinguishes_read_only_filesystem():
    """``EROFS`` (read-only filesystem) is a different remediation
    from ``EACCES`` (wrong ownership) — the operator can't fix the
    former with chown.  The hint must reflect that so we don't send
    them on a wild goose chase."""
    exc = OSError(errno.EROFS, "Read-only file system", "/config/config.json")
    payload, status = config_write_error_response(exc)

    assert status == 500
    assert "read-only" in payload["detail"].lower()
    # The remediation block exists but mentions remount, not chown
    assert "chown" not in payload["remediation"]["fix"].lower()
    assert "remount" in payload["remediation"]["fix"].lower() or "rw" in payload["remediation"]["fix"].lower()


def test_unknown_oserror_falls_back_to_generic_500():
    """An unrecognised errno (e.g. ENOSPC, EIO) shouldn't pretend
    to know the fix — return a structured 500 so the frontend can
    still render it cleanly, but with a generic remediation pointing
    at the logs."""
    exc = OSError(errno.ENOSPC, "No space left on device", "/config/config.json")
    payload, status = config_write_error_response(exc)

    assert status == 500
    assert payload["detail"]
    # Generic — no false chown promise
    assert "chown" not in payload.get("remediation", {}).get("fix", "").lower()


def test_includes_optional_context_in_error_message():
    """Callers can pass an extra ``context`` string (e.g. "Cannot
    save MA token") to prefix the error with what the user was
    trying to do.  Helps the frontend toast read naturally instead
    of just "config not writable" with no operation context."""
    exc = PermissionError(errno.EACCES, "Permission denied", "/config/config.json")
    payload, _ = config_write_error_response(exc, context="Cannot save MA token")

    assert payload["detail"].startswith("Cannot save MA token")
