"""Regression tests for the ingress-auth branch of ``_check_auth``.

The ``X-Ingress-Path`` + trusted-peer branch grants a session without a
password.  It must only fire in HA-addon mode: in standalone Docker/LXC a
loopback-origin request (a local process or an SSRF/proxy pivot on the
host) that sets the header itself must NOT be auto-authenticated.

It must also match trusted peers by CIDR (the whole hassio ``172.30.32.0/23``
network), not by exact string, so it stays consistent with the auth
rate-limiter's trust set.
"""

from __future__ import annotations

import json

import pytest


@pytest.fixture(autouse=True)
def _mock_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


def _call_check_auth(monkeypatch, *, is_ha_addon, remote_addr, ingress=True):
    """Invoke the real ``_check_auth`` in a request context; return
    ``(result, authenticated)`` where ``result`` is None when the request
    is allowed through, or a Flask response/tuple when blocked."""
    from flask import session

    import sendspin_bridge.web.interface as I

    monkeypatch.setattr(I, "_auth_enabled", True)
    monkeypatch.setattr(I, "_is_ha_addon", is_ha_addon)

    headers = {"X-Ingress-Path": "/api/hassio_ingress/abc"} if ingress else {}
    with I.app.test_request_context("/api/version", headers=headers, environ_base={"REMOTE_ADDR": remote_addr}):
        result = I._check_auth()
        authenticated = bool(session.get("authenticated"))
    return result, authenticated


def _is_blocked(result) -> bool:
    """True when ``_check_auth`` returned a 401/redirect rather than None."""
    if result is None:
        return False
    # API paths return a ``(json, 401)`` tuple.
    if isinstance(result, tuple):
        return result[1] == 401
    return True  # a redirect response object


def test_standalone_ingress_header_from_loopback_is_rejected(monkeypatch):
    result, authenticated = _call_check_auth(monkeypatch, is_ha_addon=False, remote_addr="127.0.0.1")
    assert _is_blocked(result), "standalone mode must not honor X-Ingress-Path"
    assert not authenticated


def test_addon_ingress_header_from_supervisor_authenticates(monkeypatch):
    result, authenticated = _call_check_auth(monkeypatch, is_ha_addon=True, remote_addr="172.30.32.2")
    assert not _is_blocked(result)
    assert authenticated


def test_addon_ingress_trusts_hassio_cidr_not_just_literals(monkeypatch):
    # 172.30.33.5 is inside 172.30.32.0/23 but absent from the old literal
    # set {172.30.32.1, 172.30.32.2}; CIDR matching must accept it.
    result, authenticated = _call_check_auth(monkeypatch, is_ha_addon=True, remote_addr="172.30.33.5")
    assert not _is_blocked(result)
    assert authenticated
