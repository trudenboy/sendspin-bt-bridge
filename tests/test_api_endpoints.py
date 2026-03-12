"""Tests for key routes/api.py endpoints.

All modules imported by routes.api (state, config, services.pulse, services.bluetooth)
use ``from __future__ import annotations`` and/or graceful fallbacks, so they
import cleanly on Python 3.9.  No module-level sys.modules manipulation needed.
"""

import json

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory so the web app can start."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def client():
    """Return a Flask test client with the api blueprint registered."""
    import sys

    from flask import Flask

    # test_ingress_middleware.py stubs routes.api at module level during pytest
    # collection (before any tests run).  Remove the stub so we get the real
    # module with actual route definitions.
    _stashed = {}
    for mod_name in [
        "routes.api",
        "routes.api_bt",
        "routes.api_config",
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
    from routes.api_status import status_bp

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.register_blueprint(api_bp)
    app.register_blueprint(bt_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(status_bp)

    yield app.test_client()

    # Restore stubs so test_ingress_middleware.py is unaffected
    for mod_name, mod in _stashed.items():
        sys.modules.setdefault(mod_name, mod)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health_endpoint(client):
    """GET /api/health returns {"ok": true} with status 200."""
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {"ok": True}


def test_set_volume_empty_body(client):
    """POST /api/volume with an empty JSON object must not return 500."""
    resp = client.post(
        "/api/volume",
        data=json.dumps({}),
        content_type="application/json",
    )
    # With no clients available the response is 503 ("No clients available"),
    # but it must never be an unhandled 500.
    assert resp.status_code != 500


def test_set_volume_with_invalid_player_names(client):
    """POST /api/volume with player_names as a string (not list) returns 400."""
    resp = client.post(
        "/api/volume",
        data=json.dumps({"volume": 50, "player_names": "string_not_list"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert "player_names" in data.get("error", "").lower()


def test_set_password_with_json(client, tmp_path):
    """POST /api/set-password with proper JSON sets password successfully."""
    resp = client.post(
        "/api/set-password",
        data=json.dumps({"password": "mysecretpassword"}),
        content_type="application/json",
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("success") is True

    # Verify hash was persisted
    cfg = json.loads((tmp_path / "config.json").read_text())
    assert "AUTH_PASSWORD_HASH" in cfg
    assert cfg["AUTH_PASSWORD_HASH"] != "mysecretpassword"  # stored as hash


def test_set_password_too_short(client):
    """POST /api/set-password with short password returns 400."""
    resp = client.post(
        "/api/set-password",
        data=json.dumps({"password": "short"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    assert "8 characters" in resp.get_json().get("error", "")


def test_error_response_no_leak(client):
    """Error responses must not expose Python tracebacks or file paths."""
    # Trigger a volume error with an impossible scenario — no clients available
    resp = client.post(
        "/api/volume",
        data=json.dumps({"volume": 50}),
        content_type="application/json",
    )
    body = resp.get_data(as_text=True)
    # Must not contain Python traceback markers or filesystem paths
    assert "Traceback" not in body
    assert 'File "/' not in body
    assert '.py"' not in body


# ---------------------------------------------------------------------------
# Disabled devices
# ---------------------------------------------------------------------------


def test_status_includes_disabled_devices(client):
    """GET /api/status includes disabled_devices list."""
    import state

    state.set_disabled_devices(
        [
            {"player_name": "Off Speaker", "mac": "AA:BB:CC:DD:EE:FF", "enabled": False},
        ]
    )
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "disabled_devices" in data
    assert len(data["disabled_devices"]) == 1
    assert data["disabled_devices"][0]["player_name"] == "Off Speaker"
    # cleanup
    state.set_disabled_devices([])


def test_device_enabled_toggle(client, tmp_path):
    """POST /api/device/enabled returns success with restart_required."""
    import services.bluetooth as _bt_mod

    # Seed config with a device and patch _CONFIG_FILE for persist
    cfg = {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Test", "enabled": True}]}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    _orig = _bt_mod._CONFIG_FILE
    _bt_mod._CONFIG_FILE = tmp_path / "config.json"
    try:
        resp = client.post(
            "/api/device/enabled",
            data=json.dumps({"player_name": "Test", "enabled": False}),
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["restart_required"] is True
        assert data["enabled"] is False

        # Verify config was updated
        saved = json.loads((tmp_path / "config.json").read_text())
        dev = saved["BLUETOOTH_DEVICES"][0]
        assert dev["enabled"] is False
    finally:
        _bt_mod._CONFIG_FILE = _orig


def test_device_enabled_missing_fields(client):
    """POST /api/device/enabled without required fields returns 400."""
    resp = client.post(
        "/api/device/enabled",
        data=json.dumps({}),
        content_type="application/json",
    )
    assert resp.status_code == 400
