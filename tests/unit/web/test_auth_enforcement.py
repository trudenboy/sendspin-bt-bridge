"""Auth enforcement regression tests.

Verify that protected API endpoints return 401 when authentication is enabled
but no session cookie is provided.  Uses the real web_interface Flask app with
auth forced on.
"""

import json
import sys

import pytest


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"AUTH_ENABLED": True}))


@pytest.fixture()
def auth_client(monkeypatch):
    """Flask test client with auth enforcement enabled."""
    from flask import Flask, jsonify, redirect, request, session, url_for

    # Remove cached module stubs from other test files
    _stashed = {}
    for mod_name in [
        "sendspin_bridge.web.routes.api",
        "sendspin_bridge.web.routes.api_bt",
        "sendspin_bridge.web.routes.api_config",
        "sendspin_bridge.web.routes.api_ma",
        "sendspin_bridge.web.routes.api_status",
        "sendspin_bridge.web.routes.auth",
        "sendspin_bridge.web.routes.views",
        "sendspin_bridge.web.routes",
    ]:
        cached = sys.modules.get(mod_name)
        if cached is not None and getattr(cached, "__file__", None) is None:
            _stashed[mod_name] = sys.modules.pop(mod_name)

    from sendspin_bridge.web.routes.api import api_bp
    from sendspin_bridge.web.routes.api_bt import bt_bp
    from sendspin_bridge.web.routes.api_config import config_bp
    from sendspin_bridge.web.routes.api_ma import ma_bp
    from sendspin_bridge.web.routes.api_status import status_bp
    from sendspin_bridge.web.routes.auth import auth_bp

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.config["AUTH_ENABLED"] = True
    app.register_blueprint(api_bp)
    app.register_blueprint(bt_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(ma_bp)
    app.register_blueprint(status_bp)
    app.register_blueprint(auth_bp)

    # Mirror the live ``_PUBLIC_PATHS`` from ``web_interface.py`` — keep
    # in lockstep.  Adding a path here without updating the source module
    # creates a false-positive test, so the test below also asserts the
    # set is identical to the live module's.
    from sendspin_bridge.web.interface import _PUBLIC_PATHS as _LIVE_PUBLIC_PATHS

    _PUBLIC_PATHS = set(_LIVE_PUBLIC_PATHS)

    @app.before_request
    def _check_auth():
        session.permanent = True
        if request.path.startswith("/static/"):
            return
        if request.path in _PUBLIC_PATHS:
            return
        if session.get("authenticated"):
            return
        if request.path.startswith("/api/"):
            return jsonify({"error": "Unauthorized"}), 401
        return redirect(url_for("auth.login", next=request.path))

    yield app.test_client()

    for mod_name, mod in _stashed.items():
        sys.modules.setdefault(mod_name, mod)


# ---------------------------------------------------------------------------
# Protected endpoints must return 401 without a session
# ---------------------------------------------------------------------------


_PROTECTED_GET_ENDPOINTS = [
    "/api/status",
    "/api/config",
    "/api/diagnostics",
    "/api/version",
    "/api/logs",
    "/api/startup-progress",
    "/api/runtime-info",
]

_PROTECTED_POST_ENDPOINTS = [
    "/api/bt/scan",
    "/api/volume",
    "/api/pause_all",
    "/api/restart",
    "/api/config",
]


@pytest.mark.parametrize("path", _PROTECTED_GET_ENDPOINTS)
def test_protected_get_returns_401(auth_client, path):
    resp = auth_client.get(path)
    assert resp.status_code == 401
    data = resp.get_json()
    assert data["error"] == "Unauthorized"


@pytest.mark.parametrize("path", _PROTECTED_POST_ENDPOINTS)
def test_protected_post_returns_401(auth_client, path):
    resp = auth_client.post(path, data=json.dumps({}), content_type="application/json")
    assert resp.status_code == 401
    data = resp.get_json()
    assert data["error"] == "Unauthorized"


# ---------------------------------------------------------------------------
# Public endpoints must remain accessible
# ---------------------------------------------------------------------------


def test_health_is_public(auth_client):
    resp = auth_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {"ok": True}


def test_preflight_is_public(auth_client):
    resp = auth_client.get("/api/preflight")
    assert resp.status_code == 200


def test_ha_pair_is_public_pre_auth(auth_client):
    """``/api/auth/ha-pair`` MUST bypass the bearer-or-session gate so
    the HA custom_component on HAOS can mint its first token before any
    token exists.  The route itself enforces Supervisor-IP +
    ``X-Ingress-Path`` as the real gate (see ``routes/auth.py``).

    Caught by Copilot review on PR #214.
    """
    # No session, no bearer — the route's own IP/header check should
    # produce 403, NOT a 401 from the auth middleware.  Receiving 401
    # here means the middleware swallowed the request before the route
    # could run, which would have made HAOS pairing unreachable.
    resp = auth_client.post("/api/auth/ha-pair", environ_base={"REMOTE_ADDR": "192.168.1.50"})
    assert resp.status_code != 401, "Auth middleware blocked the bootstrap pair endpoint"


def test_public_paths_set_includes_ha_pair():
    """Lockstep guard: the live ``_PUBLIC_PATHS`` must list
    ``/api/auth/ha-pair`` alongside login/logout/health/preflight."""
    from sendspin_bridge.web.interface import _PUBLIC_PATHS

    assert "/api/auth/ha-pair" in _PUBLIC_PATHS


# ---------------------------------------------------------------------------
# Authenticated session should pass through
# ---------------------------------------------------------------------------


def test_authenticated_session_passes(auth_client):
    with auth_client.session_transaction() as sess:
        sess["authenticated"] = True
    resp = auth_client.get("/api/status")
    assert resp.status_code != 401
