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
    import config

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
        "routes.api",
        "routes.api_bt",
        "routes.api_config",
        "routes.api_ma",
        "routes.api_status",
        "routes.auth",
        "routes.views",
        "routes",
    ]:
        cached = sys.modules.get(mod_name)
        if cached is not None and getattr(cached, "__file__", None) is None:
            _stashed[mod_name] = sys.modules.pop(mod_name)

    from routes.api import api_bp
    from routes.api_bt import bt_bp
    from routes.api_config import config_bp
    from routes.api_ma import ma_bp
    from routes.api_status import status_bp
    from routes.auth import auth_bp

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

    _PUBLIC_PATHS = {"/login", "/logout", "/api/health", "/api/preflight"}

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


# ---------------------------------------------------------------------------
# Authenticated session should pass through
# ---------------------------------------------------------------------------


def test_authenticated_session_passes(auth_client):
    with auth_client.session_transaction() as sess:
        sess["authenticated"] = True
    resp = auth_client.get("/api/status")
    assert resp.status_code != 401
