"""Tests for key routes/api.py endpoints.

All modules imported by routes.api (state, config, services.pulse, services.bluetooth)
use ``from __future__ import annotations`` and/or graceful fallbacks, so they
import cleanly on Python 3.9.  No module-level sys.modules manipulation needed.
"""

import json
import threading
from types import SimpleNamespace

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

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.register_blueprint(api_bp)
    app.register_blueprint(bt_bp)
    app.register_blueprint(config_bp)
    app.register_blueprint(ma_bp)
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


def test_api_config_get_includes_security_and_monitor_defaults(client):
    """GET /api/config returns merged defaults for new security and MA monitor settings."""
    resp = client.get("/api/config")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["SESSION_TIMEOUT_HOURS"] == 24
    assert data["BRUTE_FORCE_PROTECTION"] is True
    assert data["BRUTE_FORCE_MAX_ATTEMPTS"] == 5
    assert data["BRUTE_FORCE_WINDOW_MINUTES"] == 1
    assert data["BRUTE_FORCE_LOCKOUT_MINUTES"] == 5
    assert data["MA_WEBSOCKET_MONITOR"] is True


def test_api_config_post_accepts_security_and_monitor_settings(client, tmp_path, monkeypatch):
    """POST /api/config persists new security and MA monitor settings."""
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    payload = {
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": 9000,
        "BRIDGE_NAME": "",
        "BLUETOOTH_DEVICES": [],
        "BLUETOOTH_ADAPTERS": [
            {"id": "hci0", "mac": "AA:BB:CC:DD:EE:FF", "name": "Living room"},
            {"id": "hci1", "mac": "11:22:33:44:55:66"},
        ],
        "TZ": "UTC",
        "PULSE_LATENCY_MSEC": 200,
        "PREFER_SBC_CODEC": False,
        "BT_CHECK_INTERVAL": 10,
        "BT_MAX_RECONNECT_FAILS": 0,
        "AUTH_ENABLED": False,
        "SESSION_TIMEOUT_HOURS": 12,
        "BRUTE_FORCE_PROTECTION": True,
        "BRUTE_FORCE_MAX_ATTEMPTS": 4,
        "BRUTE_FORCE_WINDOW_MINUTES": 2,
        "BRUTE_FORCE_LOCKOUT_MINUTES": 10,
        "LOG_LEVEL": "INFO",
        "MA_API_URL": "",
        "MA_API_TOKEN": "",
        "MA_USERNAME": "",
        "MA_WEBSOCKET_MONITOR": False,
        "VOLUME_VIA_MA": True,
        "MUTE_VIA_MA": False,
        "SMOOTH_RESTART": True,
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }
    resp = client.post(
        "/api/config",
        data=json.dumps(payload),
        content_type="application/json",
    )
    assert resp.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["SESSION_TIMEOUT_HOURS"] == 12
    assert saved["BRUTE_FORCE_PROTECTION"] is True
    assert saved["BRUTE_FORCE_MAX_ATTEMPTS"] == 4
    assert saved["BRUTE_FORCE_WINDOW_MINUTES"] == 2
    assert saved["BRUTE_FORCE_LOCKOUT_MINUTES"] == 10
    assert saved["MA_WEBSOCKET_MONITOR"] is False
    assert saved["BLUETOOTH_ADAPTERS"][0]["name"] == "Living room"


def test_api_config_post_normalizes_numeric_strings(client, tmp_path, monkeypatch):
    """POST /api/config should coerce known numeric fields to ints before saving."""
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    payload = {
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": "9001",
        "BRIDGE_NAME": "Bridge",
        "BLUETOOTH_DEVICES": [
            {
                "mac": "AA:BB:CC:DD:EE:FF",
                "player_name": "Kitchen",
                "listen_port": "8930",
                "keepalive_interval": "60",
            }
        ],
        "BLUETOOTH_ADAPTERS": [],
        "TZ": "UTC",
        "PULSE_LATENCY_MSEC": "250",
        "PREFER_SBC_CODEC": False,
        "BT_CHECK_INTERVAL": "15",
        "BT_MAX_RECONNECT_FAILS": "3",
        "AUTH_ENABLED": False,
        "SESSION_TIMEOUT_HOURS": "12",
        "BRUTE_FORCE_PROTECTION": True,
        "BRUTE_FORCE_MAX_ATTEMPTS": "4",
        "BRUTE_FORCE_WINDOW_MINUTES": "2",
        "BRUTE_FORCE_LOCKOUT_MINUTES": "10",
        "LOG_LEVEL": "INFO",
        "MA_API_URL": "",
        "MA_API_TOKEN": "",
        "MA_USERNAME": "",
        "MA_WEBSOCKET_MONITOR": False,
        "VOLUME_VIA_MA": True,
        "MUTE_VIA_MA": False,
        "SMOOTH_RESTART": True,
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.post("/api/config", data=json.dumps(payload), content_type="application/json")

    assert resp.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["SENDSPIN_PORT"] == 9001
    assert saved["PULSE_LATENCY_MSEC"] == 250
    assert saved["BT_CHECK_INTERVAL"] == 15
    assert saved["BT_MAX_RECONNECT_FAILS"] == 3
    assert saved["SESSION_TIMEOUT_HOURS"] == 12
    assert saved["BRUTE_FORCE_MAX_ATTEMPTS"] == 4
    assert saved["BLUETOOTH_DEVICES"][0]["listen_port"] == 8930
    assert saved["BLUETOOTH_DEVICES"][0]["keepalive_interval"] == 60


def test_api_config_post_prunes_last_volumes_for_removed_devices(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "BLUETOOTH_DEVICES": [
                    {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Kitchen"},
                    {"mac": "11:22:33:44:55:66", "player_name": "Office"},
                ],
                "LAST_VOLUMES": {
                    "AA:BB:CC:DD:EE:FF": 60,
                    "11:22:33:44:55:66": 40,
                },
            }
        )
    )
    payload = {
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": 9001,
        "BRIDGE_NAME": "Bridge",
        "BLUETOOTH_DEVICES": [{"mac": "aa:bb:cc:dd:ee:ff", "player_name": "Kitchen"}],
        "BLUETOOTH_ADAPTERS": [],
        "TZ": "UTC",
        "PULSE_LATENCY_MSEC": 250,
        "PREFER_SBC_CODEC": False,
        "BT_CHECK_INTERVAL": 15,
        "BT_MAX_RECONNECT_FAILS": 3,
        "AUTH_ENABLED": False,
        "SESSION_TIMEOUT_HOURS": 12,
        "BRUTE_FORCE_PROTECTION": True,
        "BRUTE_FORCE_MAX_ATTEMPTS": 4,
        "BRUTE_FORCE_WINDOW_MINUTES": 2,
        "BRUTE_FORCE_LOCKOUT_MINUTES": 10,
        "LOG_LEVEL": "INFO",
        "MA_API_URL": "",
        "MA_API_TOKEN": "",
        "MA_USERNAME": "",
        "MA_WEBSOCKET_MONITOR": False,
        "VOLUME_VIA_MA": True,
        "MUTE_VIA_MA": False,
        "SMOOTH_RESTART": True,
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.post("/api/config", data=json.dumps(payload), content_type="application/json")

    assert resp.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["BLUETOOTH_DEVICES"][0]["mac"] == "AA:BB:CC:DD:EE:FF"
    assert saved["LAST_VOLUMES"] == {"AA:BB:CC:DD:EE:FF": 60}


def test_api_config_download_redacts_sensitive_tokens(client, tmp_path, monkeypatch):
    """GET /api/config/download must not leak secrets in the exported JSON."""
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    cfg = {
        "BRIDGE_NAME": "Kitchen",
        "MA_API_URL": "http://ma:8095",
        "MA_API_TOKEN": "super-secret-token",
        "MA_ACCESS_TOKEN": "oauth-access",
        "MA_REFRESH_TOKEN": "oauth-refresh",
        "AUTH_PASSWORD_HASH": "hashed-password",
        "SECRET_KEY": "very-secret",
    }
    (tmp_path / "config.json").write_text(json.dumps(cfg))

    resp = client.get("/api/config/download")
    assert resp.status_code == 200
    exported = json.loads(resp.get_data(as_text=True))
    assert exported["MA_API_URL"] == "http://ma:8095"
    for key in (
        "MA_API_TOKEN",
        "MA_ACCESS_TOKEN",
        "MA_REFRESH_TOKEN",
        "AUTH_PASSWORD_HASH",
        "SECRET_KEY",
    ):
        assert key not in exported


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


def test_status_includes_ma_syncgroup_id(client, monkeypatch):
    """GET /api/status exposes the MA syncgroup player_id for grouped devices."""
    import routes.api_status as api_status
    import state

    fake_client = SimpleNamespace(
        status={
            "server_connected": True,
            "bluetooth_connected": True,
            "bluetooth_available": True,
            "playing": False,
            "group_id": "8e0f23da-3db6-4cc2-902b-cc61241ecf02",
            "group_name": None,
        },
        _status_lock=threading.Lock(),
        player_name="Yandex mini 2 @ LXC",
        player_id="sendspin-yandex-mini-2---lxc",
        listen_port=8932,
        server_host=None,
        server_port=None,
        static_delay_ms=-500.0,
        connected_server_url="",
        bt_manager=None,
        bluetooth_sink_name="bluez_sink.2C_D2_6B_B8_EC_5B.a2dp_sink",
        bt_management_enabled=True,
        is_running=lambda: True,
    )

    monkeypatch.setattr(api_status, "_clients", [fake_client])
    state.set_ma_groups(
        {"sendspin-yandex-mini-2---lxc": {"id": "syncgroup_5zr8ss8g", "name": "Semdspin BT"}},
        [{"id": "syncgroup_5zr8ss8g", "name": "Semdspin BT", "members": []}],
    )
    state.set_ma_api_credentials("http://192.168.10.10:8095", "token")
    try:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["group_name"] == "Semdspin BT"
        assert data["ma_syncgroup_id"] == "syncgroup_5zr8ss8g"
    finally:
        state.set_ma_groups({}, [])
        state.set_ma_api_credentials("", "")


def test_api_status_parse_helpers_are_defensive():
    """Diagnostics parsers must return None instead of raising on malformed input."""
    from routes.api_status import (
        _parse_audio_server_name,
        _parse_bluetoothctl_adapter,
        _parse_memtotal_mb,
        _parse_sink_input_id,
    )

    assert _parse_sink_input_id("Sink Input #42") == "42"
    assert _parse_sink_input_id("Sink Input") is None

    assert _parse_audio_server_name("Server Name: PulseAudio") == "PulseAudio"
    assert _parse_audio_server_name("Server Name") is None

    assert _parse_bluetoothctl_adapter("Controller AA:BB:CC:DD:EE:FF adapter") == "AA:BB:CC:DD:EE:FF"
    assert _parse_bluetoothctl_adapter("Controller") is None

    assert _parse_memtotal_mb("MemTotal: 2048000 kB") == 2000
    assert _parse_memtotal_mb("MemTotal:") is None
    assert _parse_memtotal_mb("MemTotal: nope kB") is None


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


# ---------------------------------------------------------------------------
#  XSS protection - /api/ma/ha-auth-page
# ---------------------------------------------------------------------------


def test_ha_auth_page_escapes_xss_payload(client):
    """XSS payload in ma_url must be safely quoted in the rendered page."""
    resp = client.get("/api/ma/ha-auth-page?ma_url=';alert(1)//")
    assert resp.status_code == 200
    body = resp.data.decode()
    # The payload must be inside a JSON-quoted string (double quotes),
    # not raw inside single-quoted JS where it could break out.
    assert '"\';alert(1)//"' in body
    # The old vulnerable pattern must NOT appear.
    assert "= '';alert(1)//';" not in body


def test_ha_auth_page_rejects_javascript_scheme(client):
    """javascript: scheme in ma_url must be rejected with 400."""
    resp = client.get("/api/ma/ha-auth-page?ma_url=javascript:alert(1)")
    assert resp.status_code == 400


def test_ha_auth_page_accepts_http_url(client):
    """Normal http URL should be accepted and present in the page."""
    url = "http://192.168.1.100:8123"
    resp = client.get(f"/api/ma/ha-auth-page?ma_url={url}")
    assert resp.status_code == 200
    assert url.encode() in resp.data


def test_ha_auth_page_accepts_empty_url(client):
    """Empty ma_url should be accepted."""
    resp = client.get("/api/ma/ha-auth-page?ma_url=")
    assert resp.status_code == 200


def test_ma_artwork_proxy_fetches_same_origin_ma_artwork(client):
    import state

    class _FakeHeaders:
        def get(self, key, default=None):
            if key.lower() == "content-type":
                return "image/jpeg"
            return default

    class _FakeResponse:
        headers = _FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"jpeg-bytes"

    state.set_ma_api_credentials("http://ma:8095", "token123")
    try:
        with pytest.MonkeyPatch.context() as mp:
            import routes.api_ma as api_ma_mod

            opened = {}

            def _fake_urlopen(req, timeout=0):
                opened["url"] = req.full_url
                opened["auth"] = req.headers.get("Authorization")
                opened["accept"] = req.headers.get("Accept")
                opened["timeout"] = timeout
                return _FakeResponse()

            mp.setattr(api_ma_mod._ur, "urlopen", _fake_urlopen)
            resp = client.get("/api/ma/artwork?url=%2Fapi%2Fimage%2F123")

        assert resp.status_code == 200
        assert resp.data == b"jpeg-bytes"
        assert resp.headers["Content-Type"].startswith("image/jpeg")
        assert opened["url"] == "http://ma:8095/api/image/123"
        assert opened["auth"] == "Bearer token123"
        assert opened["accept"] == "image/*"
        assert opened["timeout"] == 15
    finally:
        state.set_ma_api_credentials("", "")


def test_ma_artwork_proxy_rejects_external_origin(client):
    import state

    state.set_ma_api_credentials("http://ma:8095", "token123")
    try:
        resp = client.get("/api/ma/artwork?url=http%3A%2F%2Fevil.example%2Fimage.jpg")
        assert resp.status_code == 400
        assert "configured MA origin" in resp.get_data(as_text=True)
    finally:
        state.set_ma_api_credentials("", "")
