"""Tests for key routes/api.py endpoints.

All modules imported by routes.api (state, config, services.pulse, services.bluetooth)
use ``from __future__ import annotations`` and/or graceful fallbacks, so they
import cleanly on Python 3.9.  No module-level sys.modules manipulation needed.
"""

import json
import sys
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


def test_api_logs_returns_recent_issue_metadata(client, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "_detect_runtime", lambda: "systemd")
    monkeypatch.setattr(
        api_config_mod,
        "_read_log_lines",
        lambda runtime, lines: [
            "2026-03-17 18:00:00,000 - root - INFO - startup complete",
            "2026-03-17 18:00:01,000 - root - WARNING - daemon stderr: ALSA setup failed",
            "2026-03-17 18:00:02,000 - root - ERROR - daemon crashed",
        ],
    )

    resp = client.get("/api/logs?lines=50")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["has_recent_issues"] is True
    assert data["recent_issue_count"] == 2
    assert data["recent_issue_level"] == "error"


def test_api_bugreport_uses_issue_worthy_logs_in_summary(client, monkeypatch):
    import routes.api_status as api_status_mod

    monkeypatch.setattr(
        api_status_mod,
        "api_diagnostics",
        lambda: SimpleNamespace(
            get_json=lambda: {
                "devices": [],
                "ma_integration": {},
                "sinks": [],
                "sink_inputs": [],
                "dbus_available": True,
                "bluetooth_daemon": "active",
                "subprocesses": [],
            }
        ),
    )
    monkeypatch.setattr(
        api_status_mod,
        "_collect_environment",
        lambda: {
            "python": "3.12.0 test",
            "platform": "Linux-test",
            "arch": "x86_64",
            "bluez": "5.72",
            "audio_server": "PulseAudio",
            "process_rss_mb": 42,
        },
    )
    monkeypatch.setattr(api_status_mod, "_collect_subprocess_info", lambda: [])
    monkeypatch.setattr(api_status_mod, "_sanitized_config", lambda: {})
    monkeypatch.setattr(api_status_mod, "_collect_bt_device_info", lambda: [])
    monkeypatch.setattr(
        api_status_mod,
        "_collect_recent_logs",
        lambda n=100: [
            "2026-03-17 18:00:00,000 - root - WARNING - reconnecting to bluetooth speaker",
            "2026-03-17 18:00:01,000 - root - WARNING - daemon stderr: ALSA setup failed",
            "2026-03-17 18:00:02,000 - root - ERROR - daemon crashed",
        ],
    )

    resp = client.get("/api/bugreport")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "Recent issue logs" in data["markdown_short"]
    assert "ALSA setup failed" in data["markdown_short"]
    assert "daemon crashed" in data["markdown_short"]
    assert "reconnecting to bluetooth speaker" not in data["markdown_short"]
    assert data["report"]["recent_issue_logs"] == [
        "2026-03-17 18:00:01,000 - root - WARNING - daemon stderr: ALSA setup failed",
        "2026-03-17 18:00:02,000 - root - ERROR - daemon crashed",
    ]


def test_api_version_includes_runtime_dependency_versions(client, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(
        api_config_mod,
        "get_runtime_dependency_versions",
        lambda: {"sendspin": "5.3.1", "aiosendspin": "4.3.0", "av": "15.0.0"},
    )

    resp = client.get("/api/version")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["dependencies"]["sendspin"] == "5.3.1"
    assert data["dependencies"]["aiosendspin"] == "4.3.0"


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


def test_status_includes_health_summary_and_recent_events(client, monkeypatch):
    """GET /api/status exposes health_summary and recent_events from snapshots."""
    import routes.api_status as api_status
    import state

    fake_client = SimpleNamespace(
        status={
            "server_connected": True,
            "bluetooth_connected": True,
            "bluetooth_available": True,
            "playing": True,
            "audio_streaming": False,
            "last_error": "Route degraded",
            "last_error_at": "2026-03-18T00:00:00+00:00",
        },
        _status_lock=threading.Lock(),
        player_name="Kitchen",
        player_id="sendspin-kitchen",
        listen_port=8928,
        server_host="music-assistant.local",
        server_port=9000,
        static_delay_ms=-500.0,
        connected_server_url="",
        bt_manager=None,
        bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
        bt_management_enabled=True,
        is_running=lambda: True,
    )

    monkeypatch.setattr(api_status, "_clients", [fake_client])
    state.clear_device_events("sendspin-kitchen")
    state.record_device_event("sendspin-kitchen", "runtime-error", level="error", message="Route degraded")
    try:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["health_summary"]["state"] == "degraded"
        assert data["health_summary"]["severity"] == "error"
        assert data["recent_events"][0]["event_type"] == "runtime-error"
    finally:
        state.clear_device_events("sendspin-kitchen")


def test_status_and_startup_progress_endpoint_include_startup_progress(client):
    """Startup progress is exposed both directly and via the main status payload."""
    import state

    state.reset_startup_progress(4, message="Booting")
    state.update_startup_progress("web", "Starting web interface", current_step=3, details={"active_clients": 2})
    try:
        resp = client.get("/api/startup-progress")
        assert resp.status_code == 200
        progress = resp.get_json()
        assert progress["phase"] == "web"
        assert progress["percent"] == 75
        assert progress["details"]["active_clients"] == 2

        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        status_data = status_resp.get_json()
        assert status_data["startup_progress"]["phase"] == "web"
        assert status_data["startup_progress"]["percent"] == 75
    finally:
        state.reset_startup_progress()


def test_runtime_info_endpoint_and_status_include_mock_runtime(client):
    """Runtime explainability is exposed directly and via the status payload."""
    import state

    state.set_runtime_mode_info(
        {
            "mode": "demo",
            "is_mocked": True,
            "simulator_active": True,
            "fixture_devices": 3,
            "mocked_layers": [{"layer": "Music Assistant", "summary": "Fixture-backed"}],
        }
    )
    try:
        resp = client.get("/api/runtime-info")
        assert resp.status_code == 200
        runtime_info = resp.get_json()
        assert runtime_info["mode"] == "demo"
        assert runtime_info["is_mocked"] is True
        assert runtime_info["mocked_layers"][0]["layer"] == "Music Assistant"

        status_resp = client.get("/api/status")
        assert status_resp.status_code == 200
        status_data = status_resp.get_json()
        assert status_data["runtime_mode"] == "demo"
        assert status_data["mock_runtime"]["fixture_devices"] == 3
    finally:
        state.set_runtime_mode_info(None)


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


def test_api_diagnostics_includes_playing_and_sink_input_metadata(client, monkeypatch):
    """GET /api/diagnostics should expose playing state and parsed sink-input metadata."""
    import routes.api_status as api_status
    import state

    fake_client = SimpleNamespace(
        player_name="Kitchen",
        status={
            "bluetooth_connected": True,
            "playing": True,
            "last_error": "Route degraded",
            "server_connected": True,
        },
        bt_management_enabled=True,
        bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
        bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
        player_id="sendspin-kitchen",
    )

    def fake_run(cmd, capture_output=True, text=True, timeout=5):
        if cmd == ["bluetoothctl", "list"]:
            return SimpleNamespace(
                returncode=0,
                stdout="Controller AA:BB:CC:DD:EE:FF Test [default]\n",
                stderr="",
            )
        if cmd == ["pactl", "list", "sink-inputs"]:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "Sink Input #42\n"
                    "Sink: 1\n"
                    "State: RUNNING\n"
                    "application.name = Sendspin Bridge\n"
                    "media.name = Quiet Woods\n"
                ),
                stderr="",
            )
        pytest.fail(f"Unexpected subprocess call: {cmd}")

    monkeypatch.setattr(api_status, "_clients", [fake_client], raising=False)
    monkeypatch.setattr(api_status, "get_server_name", lambda: "pulseaudio 16.1")
    monkeypatch.setattr(
        api_status,
        "list_sinks",
        lambda: [{"name": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"}],
    )
    monkeypatch.setattr(api_status, "_collect_environment", lambda: {"audio_server": "pulseaudio 16.1"})
    monkeypatch.setattr(api_status, "_collect_subprocess_info", lambda: [])
    monkeypatch.setattr(api_status.subprocess, "run", fake_run)

    state.set_ma_api_credentials("", "")
    state.set_ma_groups({}, [])
    try:
        resp = client.get("/api/diagnostics")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["devices"][0]["playing"] is True
        assert data["devices"][0]["last_error"] == "Route degraded"
        assert data["sink_inputs"][0]["id"] == "42"
        assert data["sink_inputs"][0]["state"] == "RUNNING"
        assert data["sink_inputs"][0]["application_name"] == "Sendspin Bridge"
        assert data["sink_inputs"][0]["media_name"] == "Quiet Woods"
    finally:
        sys.modules.pop("sendspin.audio", None)
        state.set_ma_groups({}, [])
        state.set_ma_api_credentials("", "")


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
    from services.ma_artwork import sign_artwork_url

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
            raw_url = "/api/image/123"
            signature = sign_artwork_url(raw_url)

            def _fake_urlopen(req, timeout=0):
                opened["url"] = req.full_url
                opened["auth"] = req.headers.get("Authorization")
                opened["accept"] = req.headers.get("Accept")
                opened["timeout"] = timeout
                return _FakeResponse()

            mp.setattr(api_ma_mod._ur, "urlopen", _fake_urlopen)
            resp = client.get(f"/api/ma/artwork?url=%2Fapi%2Fimage%2F123&sig={signature}")

        assert resp.status_code == 200
        assert resp.data == b"jpeg-bytes"
        assert resp.headers["Content-Type"].startswith("image/jpeg")
        assert opened["url"] == "http://ma:8095/api/image/123"
        assert opened["auth"] == "Bearer token123"
        assert opened["accept"] == "image/*"
        assert opened["timeout"] == 15
    finally:
        state.set_ma_api_credentials("", "")


def test_ma_artwork_proxy_rejects_unsupported_scheme(client):
    import state
    from services.ma_artwork import sign_artwork_url

    state.set_ma_api_credentials("http://ma:8095", "token123")
    try:
        raw_url = "ftp://evil.example/image.jpg"
        resp = client.get(f"/api/ma/artwork?url=ftp%3A%2F%2Fevil.example%2Fimage.jpg&sig={sign_artwork_url(raw_url)}")
        assert resp.status_code == 400
        assert "Unsupported artwork URL scheme" in resp.get_data(as_text=True)
    finally:
        state.set_ma_api_credentials("", "")


def test_ma_artwork_proxy_rejects_unsigned_external_provider_artwork(client):
    import state

    state.set_ma_api_credentials("http://ma:8095", "token123")
    try:
        resp = client.get(
            "/api/ma/artwork?url="
            "https%3A%2F%2Favatars.yandex.net%2Fget-music-content%2F49876%2Fab027f9c.a.37173-2%2F1000x1000"
        )
        assert resp.status_code == 400
        assert "Invalid artwork signature" in resp.get_data(as_text=True)
    finally:
        state.set_ma_api_credentials("", "")


def test_ma_artwork_proxy_fetches_signed_external_provider_artwork_without_ma_token(client):
    import state
    from services.ma_artwork import sign_artwork_url

    class _FakeHeaders:
        def get(self, key, default=None):
            if key.lower() == "content-type":
                return "image/png"
            return default

    class _FakeResponse:
        headers = _FakeHeaders()

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"png-bytes"

    raw_url = "https://avatars.yandex.net/get-music-content/49876/ab027f9c.a.37173-2/1000x1000"
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
            resp = client.get(
                "/api/ma/artwork?url="
                "https%3A%2F%2Favatars.yandex.net%2Fget-music-content%2F49876%2Fab027f9c.a.37173-2%2F1000x1000"
                f"&sig={sign_artwork_url(raw_url)}"
            )

        assert resp.status_code == 200
        assert resp.data == b"png-bytes"
        assert resp.headers["Content-Type"].startswith("image/png")
        assert opened["url"] == raw_url
        assert opened["auth"] is None
        assert opened["accept"] == "image/*"
        assert opened["timeout"] == 15
    finally:
        state.set_ma_api_credentials("", "")


def test_ma_artwork_proxy_rejects_invalid_signature(client):
    import state

    state.set_ma_api_credentials("http://ma:8095", "token123")
    try:
        resp = client.get("/api/ma/artwork?url=%2Fapi%2Fimage%2F123&sig=bad")
        assert resp.status_code == 400
        assert "Invalid artwork signature" in resp.get_data(as_text=True)
    finally:
        state.set_ma_api_credentials("", "")


def test_ma_queue_cmd_returns_structured_predicted_state(client, monkeypatch):
    import routes.api_ma as api_ma
    import services.ma_monitor as ma_monitor
    import state

    class _FakeMonitor:
        def is_connected(self):
            return True

    class _DoneFuture:
        def result(self, timeout=None):
            return {
                "accepted": True,
                "queue_id": "syncgroup_1",
                "ack_latency_ms": 42,
                "accepted_at": 123.45,
            }

    async def _fake_send_queue_cmd(action, value, syncgroup_id):
        return {
            "accepted": True,
            "queue_id": syncgroup_id,
            "ack_latency_ms": 42,
            "accepted_at": 123.45,
        }

    async def _fake_request_queue_refresh(syncgroup_id):
        return True

    def _fake_run_coroutine_threadsafe(coro, loop):
        coro.close()
        return _DoneFuture()

    state.set_ma_connected(True)
    state.set_ma_groups({}, [{"id": "syncgroup_1", "name": "Kitchen", "members": []}])
    state.set_ma_now_playing_for_group(
        "syncgroup_1",
        {"syncgroup_id": "syncgroup_1", "shuffle": False, "connected": True},
    )
    try:
        monkeypatch.setattr(state, "get_main_loop", lambda: object())
        monkeypatch.setattr(ma_monitor, "send_queue_cmd", _fake_send_queue_cmd)
        monkeypatch.setattr(ma_monitor, "request_queue_refresh", _fake_request_queue_refresh)
        monkeypatch.setattr(ma_monitor, "get_monitor", lambda: _FakeMonitor())
        monkeypatch.setattr(api_ma.asyncio, "run_coroutine_threadsafe", _fake_run_coroutine_threadsafe)

        resp = client.post(
            "/api/ma/queue/cmd",
            data=json.dumps({"action": "shuffle", "value": True, "syncgroup_id": "syncgroup_1"}),
            content_type="application/json",
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["success"] is True
        assert data["accepted"] is True
        assert data["accepted_at"] == 123.45
        assert data["ack_latency_ms"] == 42
        assert data["confirmed"] is False
        assert data["pending"] is True
        assert data["syncgroup_id"] == "syncgroup_1"
        assert data["ma_now_playing"]["shuffle"] is True
        assert data["ma_now_playing"]["_sync_meta"]["pending"] is True
        assert data["ma_now_playing"]["_sync_meta"]["last_accepted_at"] == 123.45
        assert data["ma_now_playing"]["_sync_meta"]["pending_ops"][0]["action"] == "shuffle"
        assert data["op_id"]
    finally:
        state.clear_ma_now_playing()
        state.set_ma_groups({}, [])
        state.set_ma_connected(False)


def test_ma_queue_cmd_prefers_player_queue_over_stale_group_id(client, monkeypatch):
    import routes.api_ma as api_ma
    import services.ma_monitor as ma_monitor
    import state

    class _FakeMonitor:
        def is_connected(self):
            return True

    captured = {}

    async def _fake_send_queue_cmd(action, value, syncgroup_id):
        captured["action"] = action
        captured["value"] = value
        captured["syncgroup_id"] = syncgroup_id
        return {
            "accepted": True,
            "queue_id": syncgroup_id,
            "ack_latency_ms": 7,
            "accepted_at": 456.78,
        }

    async def _fake_request_queue_refresh(syncgroup_id):
        captured["refresh_syncgroup_id"] = syncgroup_id
        return True

    class _DoneFuture:
        def __init__(self, result):
            self._result = result

        def result(self, timeout=None):
            return self._result

    def _fake_run_coroutine_threadsafe(coro, loop):
        import asyncio

        temp_loop = asyncio.new_event_loop()
        try:
            return _DoneFuture(temp_loop.run_until_complete(coro))
        finally:
            temp_loop.close()

    state.set_ma_connected(True)
    state.set_ma_groups({}, [{"id": "4fd07f70-5da7-4bbb-8d0a-d6fb1478e798", "name": "Living Room", "members": []}])
    state.set_ma_now_playing_for_group(
        "4fd07f70-5da7-4bbb-8d0a-d6fb1478e798",
        {"syncgroup_id": "4fd07f70-5da7-4bbb-8d0a-d6fb1478e798", "shuffle": False, "connected": True},
    )
    try:
        monkeypatch.setattr(state, "get_main_loop", lambda: object())
        monkeypatch.setattr(ma_monitor, "send_queue_cmd", _fake_send_queue_cmd)
        monkeypatch.setattr(ma_monitor, "request_queue_refresh", _fake_request_queue_refresh)
        monkeypatch.setattr(ma_monitor, "get_monitor", lambda: _FakeMonitor())
        monkeypatch.setattr(api_ma.asyncio, "run_coroutine_threadsafe", _fake_run_coroutine_threadsafe)

        resp = client.post(
            "/api/ma/queue/cmd",
            data=json.dumps(
                {
                    "action": "repeat",
                    "value": "all",
                    "syncgroup_id": "sendspin-yandex-mini-2---lxc",
                    "group_id": "4fd07f70-5da7-4bbb-8d0a-d6fb1478e798",
                    "player_id": "sendspin-yandex-mini-2---lxc",
                }
            ),
            content_type="application/json",
        )

        assert resp.status_code == 200
        data = resp.get_json()
        assert captured["syncgroup_id"] == "upsendspinyandexmini2lxc"
        assert captured["refresh_syncgroup_id"] == "upsendspinyandexmini2lxc"
        assert data["syncgroup_id"] == "sendspin-yandex-mini-2---lxc"
        assert data["queue_id"] == "upsendspinyandexmini2lxc"
    finally:
        state.clear_ma_now_playing()
        state.set_ma_groups({}, [])
        state.set_ma_connected(False)


def test_resolve_target_queue_uses_universal_player_queue_for_solo_sendspin_player():
    import routes.api_ma as api_ma
    import state

    state.set_ma_groups({}, [])
    try:
        state_key, queue_id = api_ma._resolve_target_queue(
            "4fd07f70-5da7-4bbb-8d0a-d6fb1478e798",
            "sendspin-yandex-mini-2---lxc",
            "4fd07f70-5da7-4bbb-8d0a-d6fb1478e798",
        )
    finally:
        state.set_ma_groups({}, [])

    assert state_key == "sendspin-yandex-mini-2---lxc"
    assert queue_id == "upsendspinyandexmini2lxc"


def test_resolve_target_queue_uses_ma_group_mapping_for_grouped_player():
    import routes.api_ma as api_ma
    import state

    state.set_ma_groups(
        {"sendspin-yandex-mini-2---lxc": {"id": "syncgroup_5zr8ss8g", "name": "Semdspin BT"}},
        [{"id": "syncgroup_5zr8ss8g", "name": "Semdspin BT", "members": []}],
    )
    try:
        state_key, queue_id = api_ma._resolve_target_queue(
            None,
            "sendspin-yandex-mini-2---lxc",
            None,
        )
    finally:
        state.set_ma_groups({}, [])

    assert state_key == "syncgroup_5zr8ss8g"
    assert queue_id == "syncgroup_5zr8ss8g"


def test_resolve_target_queue_ignores_stale_syncgroup_id_for_solo_player():
    import routes.api_ma as api_ma
    import state

    state.set_ma_groups(
        {},
        [
            {
                "id": "syncgroup_5zr8ss8g",
                "name": "Semdspin BT",
                "members": [{"id": "upsendspinlencols500haos", "name": "Lenco LS-500 @ HAOS"}],
            }
        ],
    )
    try:
        state_key, queue_id = api_ma._resolve_target_queue(
            "syncgroup_5zr8ss8g",
            "sendspin-yandex-mini-2---lxc",
            None,
        )
    finally:
        state.set_ma_groups({}, [])

    assert state_key == "sendspin-yandex-mini-2---lxc"
    assert queue_id == "upsendspinyandexmini2lxc"


def test_resolve_target_queue_infers_single_active_player_for_stale_page(monkeypatch):
    import routes.api_ma as api_ma
    import state

    fake_client = SimpleNamespace(
        player_id="sendspin-yandex-mini-2---lxc",
        status={"server_connected": True},
        is_running=lambda: True,
    )

    state.set_ma_groups(
        {},
        [
            {
                "id": "syncgroup_5zr8ss8g",
                "name": "Semdspin BT",
                "members": [{"id": "upsendspinlencols500haos", "name": "Lenco LS-500 @ HAOS"}],
            }
        ],
    )
    monkeypatch.setattr(state, "get_clients_snapshot", lambda: [fake_client])
    try:
        state_key, queue_id = api_ma._resolve_target_queue("syncgroup_5zr8ss8g", None, None)
    finally:
        state.set_ma_groups({}, [])

    assert state_key == "sendspin-yandex-mini-2---lxc"
    assert queue_id == "upsendspinyandexmini2lxc"


def test_ma_queue_cmd_returns_503_when_monitor_unavailable(client, monkeypatch):
    import services.ma_monitor as ma_monitor
    import state

    class _FakeMonitor:
        def is_connected(self):
            return False

    state.set_ma_connected(True)
    state.set_ma_groups({}, [{"id": "syncgroup_1", "name": "Kitchen", "members": []}])
    try:
        monkeypatch.setattr(state, "get_main_loop", lambda: object())
        monkeypatch.setattr(ma_monitor, "get_monitor", lambda: _FakeMonitor())
        resp = client.post(
            "/api/ma/queue/cmd",
            data=json.dumps({"action": "next", "syncgroup_id": "syncgroup_1"}),
            content_type="application/json",
        )
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "monitor_unavailable"
    finally:
        state.set_ma_groups({}, [])
        state.set_ma_connected(False)


def test_ma_queue_cmd_returns_503_when_queue_unavailable(client):
    import state

    state.set_ma_connected(True)
    state.set_ma_groups({}, [])
    try:
        resp = client.post(
            "/api/ma/queue/cmd",
            data=json.dumps({"action": "next"}),
            content_type="application/json",
        )
        assert resp.status_code == 503
        data = resp.get_json()
        assert data["success"] is False
        assert data["error_code"] == "queue_unavailable"
    finally:
        state.set_ma_connected(False)
