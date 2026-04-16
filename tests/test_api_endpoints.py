"""Tests for key routes/api.py endpoints.

All modules imported by routes.api (state, config, services.pulse, services.bluetooth)
use ``from __future__ import annotations`` and/or graceful fallbacks, so they
import cleanly on Python 3.9.  No module-level sys.modules manipulation needed.
"""

import asyncio
import io
import json
import sys
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return ""


class _FakeStdin:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def flush(self):
        return None


class _FakeProc:
    def __init__(self, stdout_lines, tail=""):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(stdout_lines)
        self._tail = tail
        self._returncode = None

    def poll(self):
        return self._returncode

    def communicate(self, timeout=None):
        self._returncode = 0
        return self._tail, ""

    def kill(self):
        self._returncode = -9

    def wait(self, timeout=None):
        return 0


class _FakeSelector:
    def __init__(self, stdout):
        self._stdout = stdout

    def register(self, *_args, **_kwargs):
        return None

    def select(self, timeout=None):
        return [object()] if self._stdout._lines else []

    def close(self):
        return None


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


def _cancel_api_volume_timers() -> None:
    api_mod = sys.modules.get("routes.api")
    if api_mod is None:
        return
    timers = getattr(api_mod, "_volume_timers", None)
    lock = getattr(api_mod, "_volume_timers_lock", None)
    if timers is None or lock is None:
        return
    with lock:
        pending = list(timers.values())
        timers.clear()
    for timer in pending:
        timer.cancel()


@pytest.fixture(autouse=True)
def _clear_volume_persist_timers():
    _cancel_api_volume_timers()
    yield
    _cancel_api_volume_timers()


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


def test_config_upload_returns_structured_validation_errors(client):
    resp = client.post(
        "/api/config/upload",
        data={
            "file": (
                io.BytesIO(
                    json.dumps(
                        {
                            "CONFIG_SCHEMA_VERSION": 1,
                            "BLUETOOTH_DEVICES": [
                                {"mac": "AA:BB:CC:DD:EE:FF"},
                                {"mac": "aa:bb:cc:dd:ee:ff"},
                            ],
                        }
                    ).encode()
                ),
                "config.json",
            )
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "Duplicate MAC address: AA:BB:CC:DD:EE:FF"
    assert data["errors"][0]["field"] == "BLUETOOTH_DEVICES[1].mac"


def test_config_upload_rejects_duplicate_effective_listen_ports(client):
    resp = client.post(
        "/api/config/upload",
        data={
            "file": (
                io.BytesIO(
                    json.dumps(
                        {
                            "CONFIG_SCHEMA_VERSION": 1,
                            "BASE_LISTEN_PORT": 8928,
                            "BLUETOOTH_DEVICES": [
                                {"mac": "AA:BB:CC:DD:EE:01", "player_name": "Kitchen"},
                                {"mac": "AA:BB:CC:DD:EE:02", "player_name": "Office", "listen_port": 8928},
                            ],
                        }
                    ).encode()
                ),
                "config.json",
            )
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"].startswith("Duplicate effective listen_port 8928")
    assert data["errors"][0]["field"] == "BLUETOOTH_DEVICES[1].listen_port"


def test_config_upload_returns_validation_warnings_on_success(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")

    resp = client.post(
        "/api/config/upload",
        data={
            "file": (
                io.BytesIO(
                    json.dumps(
                        {
                            "SENDSPIN_PORT": "9000",
                            "BLUETOOTH_DEVICES": [{"mac": "aa:bb:cc:dd:ee:ff"}],
                        }
                    ).encode()
                ),
                "config.json",
            )
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["validation"]["warnings"][0]["field"] == "CONFIG_SCHEMA_VERSION"


def test_config_validate_returns_normalized_preview(client):
    resp = client.post(
        "/api/config/validate",
        data=json.dumps(
            {
                "SENDSPIN_PORT": "9000",
                "WEB_PORT": "18080",
                "BASE_LISTEN_PORT": "19000",
                "BLUETOOTH_DEVICES": [{"mac": "aa:bb:cc:dd:ee:ff"}],
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["valid"] is True
    assert data["warnings"][0]["field"] == "CONFIG_SCHEMA_VERSION"
    assert data["normalized_config"]["SENDSPIN_PORT"] == 9000
    assert data["normalized_config"]["WEB_PORT"] == 18080
    assert data["normalized_config"]["BASE_LISTEN_PORT"] == 19000
    assert data["normalized_config"]["BLUETOOTH_DEVICES"][0]["mac"] == "AA:BB:CC:DD:EE:FF"


def test_config_validate_warns_when_new_mac_already_exists_in_ma(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod
    from config import _player_id_from_mac

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))
    mac = "AA:BB:CC:DD:EE:FF"
    monkeypatch.setattr(
        api_config_mod,
        "fetch_all_players_snapshot",
        lambda ma_url, ma_token: [{"player_id": _player_id_from_mac(mac), "display_name": "Kitchen @ Other Bridge"}],
    )

    resp = client.post(
        "/api/config/validate",
        data=json.dumps(
            {
                "MA_API_URL": "http://ma:8095",
                "MA_API_TOKEN": "token",
                "BLUETOOTH_DEVICES": [{"mac": mac}],
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    messages = [warning["message"] for warning in data["warnings"]]
    assert any("may belong to another bridge" in message for message in messages)
    assert any(warning["field"] == "BLUETOOTH_DEVICES[0].mac" for warning in data["warnings"])


def test_config_validate_does_not_warn_for_existing_mac_on_same_bridge(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod
    from config import _player_id_from_mac

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    mac = "AA:BB:CC:DD:EE:FF"
    (tmp_path / "config.json").write_text(json.dumps({"BLUETOOTH_DEVICES": [{"mac": mac}]}))
    monkeypatch.setattr(
        api_config_mod,
        "fetch_all_players_snapshot",
        lambda ma_url, ma_token: [{"player_id": _player_id_from_mac(mac), "display_name": "Kitchen @ This Bridge"}],
    )

    resp = client.post(
        "/api/config/validate",
        data=json.dumps(
            {
                "MA_API_URL": "http://ma:8095",
                "MA_API_TOKEN": "token",
                "BLUETOOTH_DEVICES": [{"mac": mac}],
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    messages = [warning["message"] for warning in data["warnings"]]
    assert not any("may belong to another bridge" in message for message in messages)


def test_run_standalone_pair_cleans_stale_device_before_trusting(monkeypatch):
    import routes.api_bt as api_bt_mod

    fake_proc = _FakeProc(stdout_lines=["Pairing successful\n"], tail="Paired: yes\nTrusted: yes\n")
    cleanup_run = MagicMock()
    finish_job = MagicMock()

    monkeypatch.setattr(api_bt_mod.subprocess, "run", cleanup_run)
    monkeypatch.setattr(api_bt_mod.subprocess, "Popen", lambda *args, **kwargs: fake_proc)
    monkeypatch.setattr(api_bt_mod, "finish_scan_job", finish_job)
    monkeypatch.setattr(api_bt_mod.time, "sleep", lambda _seconds: None)

    with patch("selectors.DefaultSelector", side_effect=lambda: _FakeSelector(fake_proc.stdout)):
        api_bt_mod._run_standalone_pair("job-1", "AA:BB:CC:DD:EE:FF", "hci1")

    cleanup_input = cleanup_run.call_args.kwargs["input"]
    assert cleanup_input == "select hci1\nremove AA:BB:CC:DD:EE:FF\n"
    assert fake_proc.stdin.writes[0].endswith("scan on\n")
    assert fake_proc.stdin.writes[1] == "pair AA:BB:CC:DD:EE:FF\n"
    assert fake_proc.stdin.writes[2].startswith("trust AA:BB:CC:DD:EE:FF\n")
    finish_job.assert_called_once_with("job-1", {"success": True, "mac": "AA:BB:CC:DD:EE:FF"})


def test_bt_pair_new_returns_409_when_bt_operation_busy(client, monkeypatch):
    import routes.api_bt as api_bt_mod

    monkeypatch.setattr(api_bt_mod, "_try_acquire_bt_operation", lambda: False)

    resp = client.post("/api/bt/pair_new", json={"mac": "AA:BB:CC:DD:EE:FF"})

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "Another Bluetooth operation is already in progress"


def test_bt_scan_returns_409_when_bt_operation_busy(client, monkeypatch):
    import routes.api_bt as api_bt_mod

    monkeypatch.setattr(api_bt_mod, "is_scan_running", lambda: False)
    monkeypatch.setattr(api_bt_mod, "_try_acquire_bt_operation", lambda: False)
    monkeypatch.setattr(api_bt_mod, "_last_scan_completed", 0.0)
    monkeypatch.setattr(api_bt_mod.time, "monotonic", lambda: 1000.0)

    resp = client.post("/api/bt/scan", json={})

    assert resp.status_code == 409
    assert resp.get_json()["error"] == "Another Bluetooth operation is already in progress"


def test_config_validate_returns_errors_for_invalid_payload(client):
    resp = client.post(
        "/api/config/validate",
        data=json.dumps({"BLUETOOTH_DEVICES": [{"mac": "not-a-mac"}]}),
        content_type="application/json",
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["valid"] is False
    assert data["errors"][0]["field"] == "BLUETOOTH_DEVICES[0].mac"


def test_config_validate_rejects_duplicate_effective_listen_ports(client):
    resp = client.post(
        "/api/config/validate",
        data=json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 1,
                "BASE_LISTEN_PORT": 8928,
                "BLUETOOTH_DEVICES": [
                    {"mac": "AA:BB:CC:DD:EE:01", "player_name": "Kitchen"},
                    {"mac": "AA:BB:CC:DD:EE:02", "player_name": "Office", "listen_port": 8928},
                ],
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["valid"] is False
    assert data["errors"][0]["field"] == "BLUETOOTH_DEVICES[1].listen_port"
    assert "Duplicate effective listen_port 8928" in data["errors"][0]["message"]


def test_config_validate_rejects_future_schema_version(client):
    resp = client.post(
        "/api/config/validate",
        data=json.dumps({"CONFIG_SCHEMA_VERSION": 999, "BLUETOOTH_DEVICES": []}),
        content_type="application/json",
    )

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["valid"] is False
    assert data["errors"][0]["field"] == "CONFIG_SCHEMA_VERSION"


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
    data = resp.get_json()
    assert data is not None
    assert "success" in data or "error" in data


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


def test_set_volume_uses_registry_snapshot_for_player_lookup(client, monkeypatch):
    import routes.api as api_mod
    from services.device_registry import DeviceRegistrySnapshot

    updates = []
    fake_client = SimpleNamespace(
        player_name="Kitchen",
        status={},
        bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
        bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
        _update_status=lambda payload: updates.append(payload),
    )
    monkeypatch.setattr(
        api_mod,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[fake_client]),
    )
    monkeypatch.setattr(api_mod, "get_volume_via_ma", lambda: False)
    monkeypatch.setattr(api_mod, "is_ma_connected", lambda: False)
    monkeypatch.setattr(api_mod, "set_sink_volume", lambda sink, volume: True)
    monkeypatch.setattr(api_mod, "get_main_loop", lambda: None)

    resp = client.post(
        "/api/volume",
        data=json.dumps({"player_name": "Kitchen", "volume": 33}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["results"][0]["player"] == "Kitchen"
    assert updates == [{"volume": 33}]


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


def test_read_log_lines_docker_uses_root_logger_ring_buffer(monkeypatch):
    """Ring buffer fallback must find the handler via sys.modules['__main__'],
    not via ``from sendspin_client import _ring_log_handler`` (which
    would create a second empty instance when __main__ != module name)."""
    import logging
    import subprocess
    import sys
    from collections import deque

    import routes.api_config as mod

    monkeypatch.setattr(mod, "_detect_runtime", lambda: "docker")

    def _no_docker(*a, **kw):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", _no_docker)

    class _FakeRing(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = deque(["line-1", "line-2", "line-3"], maxlen=100)

        def emit(self, record):
            pass

    handler = _FakeRing()
    main_mod = sys.modules["__main__"]
    old = getattr(main_mod, "_ring_log_handler", None)
    main_mod._ring_log_handler = handler
    try:
        lines = mod._read_log_lines("docker", 10)
        assert lines == ["line-1", "line-2", "line-3"]
    finally:
        if old is None:
            delattr(main_mod, "_ring_log_handler")
        else:
            main_mod._ring_log_handler = old


def test_api_group_pause_uses_registry_snapshot_for_group_lookup(client, monkeypatch):
    import routes.api as api_mod
    from services.device_registry import DeviceRegistrySnapshot

    sent = []

    class _DoneFuture:
        def result(self, timeout=None):
            return None

    class _FakeClient:
        player_name = "Kitchen"
        player_id = "sendspin-kitchen"
        status = {"group_id": "group-1", "group_name": "Kitchen Group"}

        def is_running(self):
            return True

        async def _send_subprocess_command(self, payload):
            sent.append(payload)

    def _run_coroutine_threadsafe(coro, loop):
        temp_loop = asyncio.new_event_loop()
        try:
            temp_loop.run_until_complete(coro)
        finally:
            temp_loop.close()
        return _DoneFuture()

    monkeypatch.setattr(
        api_mod,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[_FakeClient()]),
    )
    monkeypatch.setattr(api_mod, "get_main_loop", lambda: object())
    monkeypatch.setattr(api_mod.asyncio, "run_coroutine_threadsafe", _run_coroutine_threadsafe)

    resp = client.post(
        "/api/group/pause",
        data=json.dumps({"group_id": "group-1", "action": "pause"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["group_name"] == "Kitchen Group"
    assert sent == [{"cmd": "pause"}]


def test_api_pause_uses_registry_snapshot_for_player_lookup(client, monkeypatch):
    import routes.api as api_mod
    from services.device_registry import DeviceRegistrySnapshot

    sent = []

    class _DoneFuture:
        def result(self, timeout=None):
            return None

    class _FakeClient:
        player_name = "Kitchen"

        def is_running(self):
            return True

        async def _send_subprocess_command(self, payload):
            sent.append(payload)

    def _run_coroutine_threadsafe(coro, loop):
        temp_loop = asyncio.new_event_loop()
        try:
            temp_loop.run_until_complete(coro)
        finally:
            temp_loop.close()
        return _DoneFuture()

    monkeypatch.setattr(
        api_mod,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[_FakeClient()]),
    )
    monkeypatch.setattr(api_mod, "get_main_loop", lambda: object())
    monkeypatch.setattr(api_mod.asyncio, "run_coroutine_threadsafe", _run_coroutine_threadsafe)

    resp = client.post(
        "/api/pause",
        data=json.dumps({"player_name": "Kitchen", "action": "play"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["count"] == 1
    assert sent == [{"cmd": "play"}]


def test_api_pause_does_not_require_future_result(client, monkeypatch):
    import routes.api as api_mod
    from services.device_registry import DeviceRegistrySnapshot

    sent = []

    class _NoWaitFuture:
        def add_done_callback(self, callback):
            callback(self)

    class _FakeClient:
        player_name = "Kitchen"

        def is_running(self):
            return True

        async def _send_subprocess_command(self, payload):
            sent.append(payload)

    def _run_coroutine_threadsafe(coro, loop):
        temp_loop = asyncio.new_event_loop()
        try:
            temp_loop.run_until_complete(coro)
        finally:
            temp_loop.close()
        return _NoWaitFuture()

    monkeypatch.setattr(
        api_mod,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[_FakeClient()]),
    )
    monkeypatch.setattr(api_mod, "get_main_loop", lambda: object())
    monkeypatch.setattr(api_mod.asyncio, "run_coroutine_threadsafe", _run_coroutine_threadsafe)

    resp = client.post(
        "/api/pause",
        data=json.dumps({"player_name": "Kitchen", "action": "pause"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["count"] == 1
    assert sent == [{"cmd": "pause"}]


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
                "onboarding_assistant": {
                    "checks": [
                        {"key": "ma_auth", "status": "warning", "summary": "Music Assistant is not configured."}
                    ],
                    "next_steps": ["Configure MA_API_URL before using MA sync features."],
                },
                "recovery_assistant": {
                    "summary": {
                        "headline": "Kitchen is disconnected",
                        "summary": "Power on the speaker or trigger a reconnect.",
                    },
                    "issues": [
                        {
                            "severity": "warning",
                            "title": "Kitchen is disconnected",
                            "summary": "Power on the speaker or trigger a reconnect.",
                        }
                    ],
                    "traces": [{"label": "Bridge startup", "summary": "Waiting for devices to stabilize."}],
                    "latency_assistant": {"summary": "Multi-device setup detected without per-device static delays."},
                    "timeline": {
                        "summary": {
                            "entry_count": 2,
                            "error_count": 1,
                            "warning_count": 1,
                            "latest_at": "2026-03-17T18:00:02+00:00",
                        },
                        "entries": [
                            {
                                "at": "2026-03-17T18:00:01+00:00",
                                "level": "warning",
                                "source": "Kitchen",
                                "label": "reconnect",
                                "summary": "Bridge requested a reconnect after sink loss.",
                            },
                            {
                                "at": "2026-03-17T18:00:02+00:00",
                                "level": "error",
                                "source": "Kitchen",
                                "label": "daemon_crash",
                                "summary": "Device daemon exited unexpectedly.",
                            },
                        ],
                    },
                },
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
    assert "ONBOARDING ASSISTANT" in data["text_full"]
    assert "RECOVERY ASSISTANT" in data["text_full"]
    assert "RECOVERY TIMELINE" in data["text_full"]
    assert "Device daemon exited unexpectedly." in data["text_full"]
    assert "Configure MA_API_URL before using MA sync features." in data["text_full"]
    assert "Kitchen is disconnected" in data["text_full"]
    assert data["report"]["recent_issue_logs"] == [
        "2026-03-17 18:00:01,000 - root - WARNING - daemon stderr: ALSA setup failed",
        "2026-03-17 18:00:02,000 - root - ERROR - daemon crashed",
    ]
    assert "### Diagnostics summary" in data["suggested_description"]
    assert "Recent logs show: daemon stderr: ALSA setup failed; daemon crashed." in data["suggested_description"]
    assert "error from Kitchen: Device daemon exited unexpectedly." in data["suggested_description"]
    assert "Music Assistant is configured but not currently connected." not in data["suggested_description"]


def test_api_bugreport_suggested_description_uses_runtime_health_signals(client, monkeypatch):
    import routes.api_status as api_status_mod

    monkeypatch.setattr(
        api_status_mod,
        "api_diagnostics",
        lambda: SimpleNamespace(
            get_json=lambda: {
                "devices": [
                    {
                        "name": "Kitchen",
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "connected": False,
                        "last_error": "sink missing",
                    },
                    {
                        "name": "Office",
                        "mac": "11:22:33:44:55:66",
                        "connected": True,
                        "last_error": None,
                    },
                ],
                "ma_integration": {
                    "configured": True,
                    "connected": False,
                    "version": "2.7.0",
                    "syncgroups": [],
                },
                "sinks": [],
                "sink_inputs": [],
                "dbus_available": False,
                "bluetooth_daemon": "inactive",
                "onboarding_assistant": {},
                "recovery_assistant": {
                    "issues": [
                        {
                            "severity": "warning",
                            "title": "Kitchen is disconnected",
                        }
                    ],
                    "timeline": {
                        "summary": {"entry_count": 1, "error_count": 0, "warning_count": 1},
                        "entries": [
                            {
                                "at": "2026-03-17T18:00:03+00:00",
                                "level": "warning",
                                "source": "Kitchen",
                                "label": "sink_missing",
                                "summary": "Sink is still missing after reconnect.",
                            }
                        ],
                    },
                },
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
    monkeypatch.setattr(
        api_status_mod,
        "_collect_subprocess_info",
        lambda: [
            {"name": "Kitchen", "alive": False, "reconnecting": True},
            {"name": "Office", "alive": True, "reconnecting": False},
        ],
    )
    monkeypatch.setattr(api_status_mod, "_sanitized_config", lambda: {})
    monkeypatch.setattr(api_status_mod, "_collect_bt_device_info", lambda: [])
    monkeypatch.setattr(
        api_status_mod,
        "_collect_recent_logs",
        lambda n=100: [
            "2026-03-17 18:00:02,000 - root - ERROR - daemon crashed",
        ],
    )

    resp = client.get("/api/bugreport")

    assert resp.status_code == 200
    data = resp.get_json()
    assert "Bluetooth health is degraded: 1/2 configured devices are connected." in data["suggested_description"]
    assert "Bridge subprocess health is degraded: 1/2 device daemons are alive." in data["suggested_description"]
    assert "Devices currently reconnecting: Kitchen." in data["suggested_description"]
    assert "D-Bus is unavailable" in data["suggested_description"]
    assert "bluetooth daemon reports status `inactive`." in data["suggested_description"]
    assert "Music Assistant is configured but not currently connected." in data["suggested_description"]
    assert "Recovery guidance highlights: Kitchen is disconnected." in data["suggested_description"]
    assert (
        "Recovery timeline shows: warning from Kitchen: Sink is still missing after reconnect."
        in data["suggested_description"]
    )


def test_api_bugreport_redacts_oauth_tokens_and_runtime_state(client, monkeypatch):
    import routes.api_status as api_status_mod

    monkeypatch.setattr(
        api_status_mod,
        "load_config",
        lambda: {
            "MA_ACCESS_TOKEN": "oauth-access",
            "MA_REFRESH_TOKEN": "oauth-refresh",
            "MA_API_TOKEN": "legacy-token",
            "AUTH_PASSWORD_HASH": "hashed",
            "SECRET_KEY": "secret",
            "LAST_VOLUMES": {"AA:BB:CC:DD:EE:FF": 20},
            "LAST_SINKS": {"AA:BB:CC:DD:EE:FF": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"},
            "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}],
        },
    )

    sanitized = api_status_mod._sanitized_config()

    assert sanitized["MA_ACCESS_TOKEN"] == "***"
    assert sanitized["MA_REFRESH_TOKEN"] == "***"
    assert sanitized["MA_API_TOKEN"] == "***"
    assert sanitized["AUTH_PASSWORD_HASH"] == "***"
    assert sanitized["SECRET_KEY"] == "***"
    assert sanitized["LAST_VOLUMES"] == "***"
    assert sanitized["LAST_SINKS"] == "***"


def test_api_version_includes_runtime_dependency_versions(client, monkeypatch):
    import routes.api_config as api_config_mod
    from config import CONFIG_SCHEMA_VERSION
    from services.ipc_protocol import IPC_PROTOCOL_VERSION

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
    assert data["config_schema_version"] == CONFIG_SCHEMA_VERSION
    assert data["ipc_protocol_version"] == IPC_PROTOCOL_VERSION


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
    assert data["STARTUP_BANNER_GRACE_SECONDS"] == 5
    assert data["RECOVERY_BANNER_GRACE_SECONDS"] == 15
    assert data["MA_AUTO_SILENT_AUTH"] is True
    assert data["MA_WEBSOCKET_MONITOR"] is True


def test_api_config_get_reports_fixed_ha_ingress_web_port(client, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "_detect_runtime", lambda: "ha_addon")
    monkeypatch.setattr(api_config_mod, "resolve_web_port", lambda: 8081)
    monkeypatch.setattr(api_config_mod, "resolve_base_listen_port", lambda: 9028)
    monkeypatch.setattr(api_config_mod, "detect_ha_addon_channel", lambda: "rc")
    monkeypatch.setattr(api_config_mod, "load_config", lambda: {"WEB_PORT": 18080, "BASE_LISTEN_PORT": 19000})

    resp = client.get("/api/config")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["WEB_PORT"] is None
    assert data["_effective_web_port"] == 8081
    assert data["_effective_base_listen_port"] == 9028
    assert data["_delivery_channel"] == "rc"


def test_api_config_get_enriches_devices_from_registry_snapshot(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod
    from services.device_registry import DeviceRegistrySnapshot

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "BLUETOOTH_DEVICES": [
                    {
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "player_name": "Kitchen",
                    }
                ]
            }
        )
    )
    fake_client = SimpleNamespace(
        player_name="Kitchen",
        listen_port=8930,
        listen_host="bridge.local",
        status={"ip_address": "192.168.10.20"},
        bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
    )
    monkeypatch.setattr(
        api_config_mod,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[fake_client]),
    )

    resp = client.get("/api/config")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["BLUETOOTH_DEVICES"][0]["listen_port"] == 8930
    assert data["BLUETOOTH_DEVICES"][0]["listen_host"] == "bridge.local"


def test_api_config_get_uses_snapshot_ip_address_for_listen_host_fallback(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod
    from services.device_registry import DeviceRegistrySnapshot

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "BLUETOOTH_DEVICES": [
                    {
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "player_name": "Kitchen",
                    }
                ]
            }
        )
    )
    fake_client = SimpleNamespace(
        player_name="Kitchen",
        listen_port=8930,
        listen_host=None,
        status={"ip_address": "192.168.10.20"},
        bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
    )
    monkeypatch.setattr(
        api_config_mod,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[fake_client]),
    )

    resp = client.get("/api/config")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["BLUETOOTH_DEVICES"][0]["listen_host"] == "192.168.10.20"


def test_api_config_post_accepts_security_and_monitor_settings(client, tmp_path, monkeypatch):
    """POST /api/config persists new security and MA monitor settings."""
    import routes.api_config as api_config_mod

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
        "STARTUP_BANNER_GRACE_SECONDS": 7,
        "RECOVERY_BANNER_GRACE_SECONDS": 12,
        "LOG_LEVEL": "INFO",
        "MA_API_URL": "",
        "MA_API_TOKEN": "",
        "MA_USERNAME": "",
        "MA_AUTO_SILENT_AUTH": False,
        "MA_WEBSOCKET_MONITOR": False,
        "VOLUME_VIA_MA": True,
        "MUTE_VIA_MA": False,
        "SMOOTH_RESTART": True,
        "UPDATE_CHANNEL": "beta",
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
    assert saved["STARTUP_BANNER_GRACE_SECONDS"] == 7
    assert saved["RECOVERY_BANNER_GRACE_SECONDS"] == 12
    assert saved["MA_AUTO_SILENT_AUTH"] is False
    assert saved["MA_WEBSOCKET_MONITOR"] is False
    assert saved["UPDATE_CHANNEL"] == "beta"
    assert saved["BLUETOOTH_ADAPTERS"][0]["name"] == "Living room"


def test_api_config_post_uses_installed_addon_channel_in_ha_runtime(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(api_config_mod, "_detect_runtime", lambda: "ha_addon")
    monkeypatch.setattr(api_config_mod, "get_self_delivery_channel", lambda: "rc")
    payload = {
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": 9000,
        "BRIDGE_NAME": "",
        "BLUETOOTH_DEVICES": [],
        "BLUETOOTH_ADAPTERS": [],
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
        "MA_AUTO_SILENT_AUTH": False,
        "MA_WEBSOCKET_MONITOR": False,
        "VOLUME_VIA_MA": True,
        "MUTE_VIA_MA": False,
        "SMOOTH_RESTART": True,
        "UPDATE_CHANNEL": "beta",
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.post("/api/config", data=json.dumps(payload), content_type="application/json")

    assert resp.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["WEB_PORT"] is None
    assert saved["UPDATE_CHANNEL"] == "rc"


def test_api_config_post_normalizes_numeric_strings(client, tmp_path, monkeypatch):
    """POST /api/config should coerce known numeric fields to ints before saving."""
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    payload = {
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": "9001",
        "WEB_PORT": "18080",
        "BASE_LISTEN_PORT": "19000",
        "BRIDGE_NAME": "Bridge",
        "BLUETOOTH_DEVICES": [
            {
                "mac": "AA:BB:CC:DD:EE:FF",
                "player_name": "Kitchen",
                "listen_port": "8930",
                "keepalive_interval": "60",
                "room_name": "Living Room",
                "room_id": "living-room",
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
        "STARTUP_BANNER_GRACE_SECONDS": "0",
        "RECOVERY_BANNER_GRACE_SECONDS": "15",
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
    assert saved["WEB_PORT"] == 18080
    assert saved["BASE_LISTEN_PORT"] == 19000
    assert saved["PULSE_LATENCY_MSEC"] == 250
    assert saved["BT_CHECK_INTERVAL"] == 15
    assert saved["BT_MAX_RECONNECT_FAILS"] == 3
    assert saved["SESSION_TIMEOUT_HOURS"] == 12
    assert saved["BRUTE_FORCE_MAX_ATTEMPTS"] == 4
    assert saved["STARTUP_BANNER_GRACE_SECONDS"] == 0
    assert saved["RECOVERY_BANNER_GRACE_SECONDS"] == 15
    from config import CONFIG_SCHEMA_VERSION

    assert saved["CONFIG_SCHEMA_VERSION"] == CONFIG_SCHEMA_VERSION
    assert saved["BLUETOOTH_DEVICES"][0]["listen_port"] == 8930
    assert saved["BLUETOOTH_DEVICES"][0]["keepalive_interval"] == 60
    assert saved["BLUETOOTH_DEVICES"][0]["room_name"] == "Living Room"
    assert saved["BLUETOOTH_DEVICES"][0]["room_id"] == "living-room"


def test_api_config_post_accepts_empty_manual_port_overrides(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    payload = {
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": 9000,
        "WEB_PORT": "",
        "BASE_LISTEN_PORT": "",
        "BRIDGE_NAME": "",
        "BLUETOOTH_DEVICES": [],
        "BLUETOOTH_ADAPTERS": [],
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

    resp = client.post("/api/config", data=json.dumps(payload), content_type="application/json")

    assert resp.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["WEB_PORT"] is None
    assert saved["BASE_LISTEN_PORT"] is None


def test_sync_ha_options_omits_manual_ports_when_unset(monkeypatch):
    import routes.api_config as api_config_mod

    captured = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout):
        captured["payload"] = json.loads(req.data.decode())
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(api_config_mod, "_detect_runtime", lambda: "ha_addon")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "token")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    api_config_mod._sync_ha_options(
        {
            "SENDSPIN_SERVER": "auto",
            "SENDSPIN_PORT": 9000,
            "WEB_PORT": None,
            "BASE_LISTEN_PORT": None,
            "MA_AUTO_SILENT_AUTH": True,
            "STARTUP_BANNER_GRACE_SECONDS": 10,
            "RECOVERY_BANNER_GRACE_SECONDS": 25,
            "BLUETOOTH_DEVICES": [],
            "BLUETOOTH_ADAPTERS": [],
        }
    )

    options = captured["payload"]["options"]
    assert "web_port" not in options
    assert "base_listen_port" not in options
    assert "update_channel" not in options
    assert options["ma_auto_silent_auth"] is True
    assert options["startup_banner_grace_seconds"] == 10
    assert options["recovery_banner_grace_seconds"] == 25


def test_sync_ha_options_includes_manual_ports_when_set(monkeypatch):
    import routes.api_config as api_config_mod

    captured = {}

    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    def fake_urlopen(req, timeout):
        captured["payload"] = json.loads(req.data.decode())
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(api_config_mod, "_detect_runtime", lambda: "ha_addon")
    monkeypatch.setenv("SUPERVISOR_TOKEN", "token")
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    api_config_mod._sync_ha_options(
        {
            "SENDSPIN_SERVER": "auto",
            "SENDSPIN_PORT": 9000,
            "WEB_PORT": 18080,
            "BASE_LISTEN_PORT": 19000,
            "MA_AUTO_SILENT_AUTH": False,
            "STARTUP_BANNER_GRACE_SECONDS": 0,
            "RECOVERY_BANNER_GRACE_SECONDS": 8,
            "BLUETOOTH_DEVICES": [],
            "BLUETOOTH_ADAPTERS": [],
        }
    )

    options = captured["payload"]["options"]
    assert "web_port" not in options
    assert options["base_listen_port"] == 19000
    assert "update_channel" not in options
    assert options["ma_auto_silent_auth"] is False
    assert options["startup_banner_grace_seconds"] == 0
    assert options["recovery_banner_grace_seconds"] == 8


def test_api_ha_areas_returns_bridge_suggestions_and_adapter_matches(client, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(
        api_config_mod,
        "fetch_ha_area_catalog",
        lambda ha_token, include_devices, adapters: {
            "source": "ingress_token",
            "areas": [{"area_id": "living-room", "name": "Living Room"}],
            "bridge_name_suggestions": [{"area_id": "living-room", "label": "Living Room", "value": "Living Room"}],
            "adapter_matches": [
                {
                    "adapter_id": "hci0",
                    "adapter_mac": "AA:BB:CC:DD:EE:FF",
                    "matched_area_id": "living-room",
                    "matched_area_name": "Living Room",
                    "match_source": "device_registry_mac",
                    "match_confidence": "high",
                }
            ],
        },
    )

    resp = client.post(
        "/api/ha/areas",
        data=json.dumps(
            {
                "ha_token": "token",
                "include_devices": True,
                "adapters": [{"id": "hci0", "mac": "AA:BB:CC:DD:EE:FF"}],
            }
        ),
        content_type="application/json",
    )

    assert resp.status_code == 200
    payload = resp.get_json()
    assert payload["success"] is True
    assert payload["bridge_name_suggestions"][0]["value"] == "Living Room"
    assert payload["adapter_matches"][0]["matched_area_id"] == "living-room"


def test_api_ha_areas_returns_helper_error(client, monkeypatch):
    import routes.api_config as api_config_mod
    from services.ha_core_api import HaCoreApiError

    monkeypatch.setattr(
        api_config_mod,
        "fetch_ha_area_catalog",
        lambda ha_token, include_devices, adapters: (_ for _ in ()).throw(HaCoreApiError("HA unavailable")),
    )

    resp = client.post(
        "/api/ha/areas",
        data=json.dumps({"ha_token": "token", "include_devices": True, "adapters": []}),
        content_type="application/json",
    )

    assert resp.status_code == 502
    payload = resp.get_json()
    assert payload["success"] is False
    assert payload["error"] == "HA unavailable"


def test_api_config_post_persists_ha_adapter_area_map(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    payload = {
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": 9000,
        "BRIDGE_NAME": "",
        "HA_AREA_NAME_ASSIST_ENABLED": False,
        "BLUETOOTH_DEVICES": [],
        "BLUETOOTH_ADAPTERS": [{"id": "hci0", "mac": "AA:BB:CC:DD:EE:FF"}],
        "HA_ADAPTER_AREA_MAP": {"aa:bb:cc:dd:ee:ff": {"area_id": "living-room", "area_name": "Living Room"}},
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
        "MA_AUTO_SILENT_AUTH": False,
        "MA_WEBSOCKET_MONITOR": False,
        "VOLUME_VIA_MA": True,
        "MUTE_VIA_MA": False,
        "SMOOTH_RESTART": True,
        "UPDATE_CHANNEL": "stable",
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.post("/api/config", data=json.dumps(payload), content_type="application/json")

    assert resp.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["HA_AREA_NAME_ASSIST_ENABLED"] is False
    assert saved["HA_ADAPTER_AREA_MAP"] == {"AA:BB:CC:DD:EE:FF": {"area_id": "living-room", "area_name": "Living Room"}}


def test_api_ma_discover_reports_invalid_saved_token(client, monkeypatch):
    import routes.api_ma as api_ma
    import routes.ma_groups as ma_groups
    import services.ma_discovery as ma_discovery

    class _ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None, name=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    async def _fake_validate_ma_url(_url):
        return {
            "url": "http://localhost:8095",
            "version": "2.0.0",
            "homeassistant_addon": True,
        }

    class _DoneFuture:
        def __init__(self, result):
            self._result = result

        def result(self, timeout=None):
            return self._result

    def _run_coroutine_threadsafe(coro, loop):
        tmp_loop = asyncio.new_event_loop()
        try:
            return _DoneFuture(tmp_loop.run_until_complete(coro))
        finally:
            tmp_loop.close()

    monkeypatch.setattr(ma_groups, "get_main_loop", lambda: object())
    monkeypatch.setattr(ma_groups, "_detect_runtime", lambda: "ha_addon")
    monkeypatch.setattr(
        ma_groups,
        "get_ma_addon_discovery_candidates",
        lambda: [
            {
                "url": "http://ma-addon:8095",
                "source": "ha_addon_hostname",
                "summary": "Home Assistant Supervisor reported a running Music Assistant add-on.",
            }
        ],
    )
    monkeypatch.setattr(api_ma.asyncio, "run_coroutine_threadsafe", _run_coroutine_threadsafe)
    monkeypatch.setattr(ma_groups.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(ma_discovery, "validate_ma_url", _fake_validate_ma_url)
    monkeypatch.setattr(
        api_ma,
        "load_config",
        lambda: {
            "MA_API_URL": "http://localhost:8095",
            "MA_API_TOKEN": "expired-token",
            "MA_USERNAME": "admin",
            "MA_AUTH_PROVIDER": "ha",
        },
    )
    monkeypatch.setattr(api_ma, "_validate_ma_token", lambda ma_url, token: False)

    resp = client.get("/api/ma/discover")

    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]
    result = client.get(f"/api/ma/discover/result/{job_id}")
    assert result.status_code == 200
    data = result.get_json()
    assert data["servers"][0]["url"] == "http://localhost:8095"
    assert data["servers"][0]["discovery_source"] == "ha_addon_hostname"
    assert "Supervisor" in data["servers"][0]["discovery_summary"]
    assert data["integration"]["token_configured"] is True
    assert data["integration"]["token_valid"] is False
    assert data["integration"]["connected"] is False
    assert data["integration"]["matches_discovered_server"] is True


def test_api_ma_discover_reports_connected_runtime_when_saved_token_validation_fails(client, monkeypatch):
    import routes.api_ma as api_ma
    import routes.ma_groups as ma_groups
    import services.ma_discovery as ma_discovery

    class _ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None, name=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    async def _fake_validate_ma_url(_url):
        return {
            "url": "http://localhost:8095",
            "version": "2.0.0",
            "homeassistant_addon": False,
        }

    class _DoneFuture:
        def __init__(self, result):
            self._result = result

        def result(self, timeout=None):
            return self._result

    def _run_coroutine_threadsafe(coro, loop):
        tmp_loop = asyncio.new_event_loop()
        try:
            return _DoneFuture(tmp_loop.run_until_complete(coro))
        finally:
            tmp_loop.close()

    monkeypatch.setattr(ma_groups, "get_main_loop", lambda: object())
    monkeypatch.setattr(api_ma, "is_ma_connected", lambda: True)
    monkeypatch.setattr(ma_groups, "_detect_runtime", lambda: "docker")
    monkeypatch.setattr(ma_groups, "get_ma_api_credentials", lambda: ("http://localhost:8095", "working-token"))
    monkeypatch.setattr(api_ma.asyncio, "run_coroutine_threadsafe", _run_coroutine_threadsafe)
    monkeypatch.setattr(ma_groups.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(ma_discovery, "validate_ma_url", _fake_validate_ma_url)
    monkeypatch.setattr(
        api_ma,
        "load_config",
        lambda: {
            "MA_API_URL": "http://localhost:8095",
            "MA_API_TOKEN": "working-token",
            "MA_USERNAME": "admin",
            "MA_AUTH_PROVIDER": "ha",
        },
    )
    monkeypatch.setattr(api_ma, "_validate_ma_token", lambda ma_url, token: False)

    resp = client.get("/api/ma/discover")

    assert resp.status_code == 202
    job_id = resp.get_json()["job_id"]
    result = client.get(f"/api/ma/discover/result/{job_id}")
    assert result.status_code == 200
    data = result.get_json()
    assert data["servers"][0]["discovery_source"] == "saved_config"
    assert "saved bridge configuration" in data["servers"][0]["discovery_summary"]
    assert data["integration"]["token_configured"] is True
    assert data["integration"]["token_valid"] is False
    assert data["integration"]["connected"] is True


def test_api_config_post_returns_structured_validation_errors(client):
    payload = {
        "BLUETOOTH_DEVICES": [
            {"mac": "AA:BB:CC:DD:EE:FF"},
            {"mac": "aa:bb:cc:dd:ee:ff"},
        ]
    }

    resp = client.post("/api/config", data=json.dumps(payload), content_type="application/json")

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"] == "Duplicate MAC address: AA:BB:CC:DD:EE:FF"
    assert data["errors"][0]["field"] == "BLUETOOTH_DEVICES[1].mac"


def test_api_config_post_rejects_duplicate_effective_listen_ports(client):
    payload = {
        "CONFIG_SCHEMA_VERSION": 1,
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": 9000,
        "WEB_PORT": 18080,
        "BASE_LISTEN_PORT": 8928,
        "BRIDGE_NAME": "Bridge",
        "BLUETOOTH_DEVICES": [
            {"mac": "AA:BB:CC:DD:EE:01", "player_name": "Kitchen"},
            {"mac": "AA:BB:CC:DD:EE:02", "player_name": "Office", "listen_port": 8928},
        ],
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

    assert resp.status_code == 400
    data = resp.get_json()
    assert data["error"].startswith("Duplicate effective listen_port 8928")
    assert data["errors"][0]["field"] == "BLUETOOTH_DEVICES[1].listen_port"


def test_api_config_post_uses_registry_snapshot_for_adapter_removal(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod
    from services.device_registry import DeviceRegistrySnapshot

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "BLUETOOTH_DEVICES": [
                    {
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "player_name": "Kitchen",
                        "adapter": "hci0",
                    }
                ]
            }
        )
    )
    removed = []
    fake_client = SimpleNamespace(
        bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF", _adapter_select="C0:FB:F9:62:D6:9D")
    )
    monkeypatch.setattr(
        api_config_mod,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[fake_client]),
    )
    monkeypatch.setattr(api_config_mod, "_bt_remove_device", lambda mac, adapter: removed.append((mac, adapter)))
    payload = {
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": 9001,
        "BRIDGE_NAME": "Bridge",
        "BLUETOOTH_DEVICES": [
            {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Kitchen", "adapter": "hci1"},
        ],
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
    assert removed == [("AA:BB:CC:DD:EE:FF", "C0:FB:F9:62:D6:9D")]


def test_api_config_post_does_not_remove_device_for_default_adapter_equivalence(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "BLUETOOTH_DEVICES": [
                    {
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "player_name": "Kitchen",
                    }
                ]
            }
        )
    )
    removed = []
    monkeypatch.setattr(api_config_mod, "_bt_remove_device", lambda mac, adapter: removed.append((mac, adapter)))
    payload = {
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": 9001,
        "BRIDGE_NAME": "Bridge",
        "BLUETOOTH_DEVICES": [
            {"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Kitchen", "adapter": ""},
        ],
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
    assert removed == []


def test_api_config_post_prunes_last_volumes_for_removed_devices(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod

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


def test_api_config_post_returns_validation_warnings(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))
    payload = {
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": "9001",
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
    data = resp.get_json()
    assert data["validation"]["warnings"][0]["field"] == "CONFIG_SCHEMA_VERSION"


def test_api_config_post_includes_ma_duplicate_warning(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod
    from config import _player_id_from_mac

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))
    mac = "AA:BB:CC:DD:EE:FF"
    monkeypatch.setattr(
        api_config_mod,
        "fetch_all_players_snapshot",
        lambda ma_url, ma_token: [{"player_id": _player_id_from_mac(mac), "display_name": "Kitchen @ Other Bridge"}],
    )
    payload = {
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": "9001",
        "BRIDGE_NAME": "Bridge",
        "BLUETOOTH_DEVICES": [{"mac": mac, "player_name": "Kitchen"}],
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
        "MA_API_URL": "http://ma:8095",
        "MA_API_TOKEN": "token",
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
    data = resp.get_json()
    messages = [warning["message"] for warning in data["validation"]["warnings"]]
    assert any("may belong to another bridge" in message for message in messages)


def test_api_config_post_preserves_ma_token_metadata(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "MA_TOKEN_INSTANCE_HOSTNAME": "bridge-host",
                "MA_TOKEN_LABEL": "Sendspin BT Bridge (bridge-host)",
            }
        )
    )
    payload = {
        "SENDSPIN_SERVER": "auto",
        "SENDSPIN_PORT": "9001",
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
    assert saved["MA_TOKEN_INSTANCE_HOSTNAME"] == "bridge-host"
    assert saved["MA_TOKEN_LABEL"] == "Sendspin BT Bridge (bridge-host)"


def test_config_upload_includes_ma_duplicate_warning(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod
    from config import _player_id_from_mac

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))
    mac = "AA:BB:CC:DD:EE:FF"
    monkeypatch.setattr(
        api_config_mod,
        "fetch_all_players_snapshot",
        lambda ma_url, ma_token: [{"player_id": _player_id_from_mac(mac), "display_name": "Kitchen @ Other Bridge"}],
    )

    resp = client.post(
        "/api/config/upload",
        data={
            "file": (
                io.BytesIO(
                    json.dumps(
                        {
                            "MA_API_URL": "http://ma:8095",
                            "MA_API_TOKEN": "token",
                            "BLUETOOTH_DEVICES": [{"mac": mac}],
                        }
                    ).encode()
                ),
                "config.json",
            )
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    data = resp.get_json()
    messages = [warning["message"] for warning in data["validation"]["warnings"]]
    assert any("may belong to another bridge" in message for message in messages)


def test_config_upload_preserves_ma_token_metadata(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "MA_TOKEN_INSTANCE_HOSTNAME": "bridge-host",
                "MA_TOKEN_LABEL": "Sendspin BT Bridge (bridge-host)",
            }
        )
    )

    resp = client.post(
        "/api/config/upload",
        data={
            "file": (
                io.BytesIO(
                    json.dumps(
                        {
                            "MA_API_URL": "http://ma:8095",
                            "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}],
                        }
                    ).encode()
                ),
                "config.json",
            )
        },
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["MA_TOKEN_INSTANCE_HOSTNAME"] == "bridge-host"
    assert saved["MA_TOKEN_LABEL"] == "Sendspin BT Bridge (bridge-host)"


def test_api_set_log_level_propagates_via_registry_snapshot(client, monkeypatch):
    import routes.api_config as api_config_mod
    from services.device_registry import DeviceRegistrySnapshot

    sent = []

    class _DoneFuture:
        def result(self, timeout=None):
            return None

    class _FakeClient:
        def __init__(self, name: str, running: bool):
            self.player_name = name
            self._running = running

        def is_running(self):
            return self._running

        async def _send_subprocess_command(self, cmd):
            sent.append((self.player_name, cmd))

    monkeypatch.setattr(api_config_mod, "get_main_loop", lambda: object())
    monkeypatch.setattr(api_config_mod, "update_config", lambda updater: None)
    monkeypatch.setattr(
        api_config_mod,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[_FakeClient("Kitchen", True), _FakeClient("Bedroom", False)]),
    )

    def _run_coroutine_threadsafe(coro, loop):
        tmp_loop = asyncio.new_event_loop()
        try:
            tmp_loop.run_until_complete(coro)
        finally:
            tmp_loop.close()
        return _DoneFuture()

    monkeypatch.setattr(api_config_mod.asyncio, "run_coroutine_threadsafe", _run_coroutine_threadsafe)

    resp = client.post(
        "/api/settings/log_level",
        data=json.dumps({"level": "debug"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["level"] == "DEBUG"
    assert sent == [("Kitchen", {"cmd": "set_log_level", "level": "DEBUG"})]


def test_api_set_log_level_does_not_require_future_result(client, monkeypatch):
    import routes.api_config as api_config_mod
    from services.device_registry import DeviceRegistrySnapshot

    sent = []

    class _NoWaitFuture:
        def add_done_callback(self, callback):
            callback(self)

    class _FakeClient:
        player_name = "Kitchen"

        def is_running(self):
            return True

        async def _send_subprocess_command(self, cmd):
            sent.append(cmd)

    monkeypatch.setattr(api_config_mod, "get_main_loop", lambda: object())
    monkeypatch.setattr(api_config_mod, "update_config", lambda updater: None)
    monkeypatch.setattr(
        api_config_mod,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[_FakeClient()]),
    )

    def _run_coroutine_threadsafe(coro, loop):
        tmp_loop = asyncio.new_event_loop()
        try:
            tmp_loop.run_until_complete(coro)
        finally:
            tmp_loop.close()
        return _NoWaitFuture()

    monkeypatch.setattr(api_config_mod.asyncio, "run_coroutine_threadsafe", _run_coroutine_threadsafe)

    resp = client.post(
        "/api/settings/log_level",
        data=json.dumps({"level": "debug"}),
        content_type="application/json",
    )

    assert resp.status_code == 200
    assert resp.get_json()["level"] == "DEBUG"
    assert sent == [{"cmd": "set_log_level", "level": "DEBUG"}]


def test_api_set_password_returns_error_when_config_persist_fails(client, monkeypatch):
    import routes.api_config as api_config_mod

    def _raise_update_error(_updater):
        raise OSError("disk full")

    monkeypatch.setattr(api_config_mod, "update_config", _raise_update_error)

    resp = client.post(
        "/api/set-password",
        data=json.dumps({"password": "verysecurepassword"}),
        content_type="application/json",
    )

    assert resp.status_code == 500
    assert resp.get_json() == {"success": False, "error": "Could not save password"}


def test_api_set_log_level_returns_error_when_config_persist_fails(client, monkeypatch):
    import routes.api_config as api_config_mod

    root_logger = api_config_mod.logging.getLogger()
    original_level = root_logger.level
    monkeypatch.delenv("LOG_LEVEL", raising=False)

    def _raise_update_error(_updater):
        raise OSError("disk full")

    monkeypatch.setattr(api_config_mod, "update_config", _raise_update_error)
    monkeypatch.setattr(
        api_config_mod.asyncio,
        "run_coroutine_threadsafe",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("unexpected propagation")),
    )

    try:
        resp = client.post(
            "/api/settings/log_level",
            data=json.dumps({"level": "debug"}),
            content_type="application/json",
        )
        assert resp.status_code == 500
        assert resp.get_json() == {"success": False, "error": "Could not persist log level"}
        assert root_logger.level == original_level
        assert "LOG_LEVEL" not in api_config_mod.os.environ
    finally:
        root_logger.setLevel(original_level)


def test_api_config_download_redacts_sensitive_tokens(client, tmp_path, monkeypatch):
    """GET /api/config/download must not leak secrets in the exported JSON."""
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    cfg = {
        "BRIDGE_NAME": "Kitchen",
        "MA_API_URL": "http://ma:8095",
        "MA_API_TOKEN": "super-secret-token",
        "MA_ACCESS_TOKEN": "oauth-access",
        "MA_REFRESH_TOKEN": "oauth-refresh",
        "MA_TOKEN_INSTANCE_HOSTNAME": "bridge-host",
        "MA_TOKEN_LABEL": "Sendspin BT Bridge (bridge-host)",
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
        "MA_TOKEN_INSTANCE_HOSTNAME",
        "MA_TOKEN_LABEL",
        "AUTH_PASSWORD_HASH",
        "SECRET_KEY",
    ):
        assert key not in exported


def test_api_config_get_redacts_oauth_tokens(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(
        api_config_mod,
        "load_config",
        lambda: {
            "AUTH_PASSWORD_HASH": "hashed-password",
            "SECRET_KEY": "secret",
            "MA_ACCESS_TOKEN": "oauth-access",
            "MA_REFRESH_TOKEN": "oauth-refresh",
            "BLUETOOTH_DEVICES": [],
            "WEB_PORT": None,
        },
    )
    monkeypatch.setattr(api_config_mod, "_detect_runtime", lambda: "docker")
    monkeypatch.setattr(api_config_mod, "resolve_base_listen_port", lambda: 8928)

    resp = client.get("/api/config")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["_password_set"] is True
    assert "AUTH_PASSWORD_HASH" not in data
    assert "SECRET_KEY" not in data
    assert "MA_ACCESS_TOKEN" not in data
    assert "MA_REFRESH_TOKEN" not in data


def test_api_config_download_returns_error_for_invalid_json(client, tmp_path, monkeypatch):
    import routes.api_config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text("{bad")

    resp = client.get("/api/config/download")

    assert resp.status_code == 500
    assert resp.get_json() == {"error": "Could not read config file"}


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


def test_status_reports_all_devices_disabled_header_status(client, monkeypatch):
    """GET /api/status reports a dedicated neutral header when all configured devices are disabled."""
    import routes.api_status as api_status
    import state

    monkeypatch.setattr(
        api_status,
        "load_config",
        lambda: {
            "BLUETOOTH_ADAPTERS": [{"id": "hci0"}],
            "BLUETOOTH_DEVICES": [
                {"player_name": "Kitchen", "mac": "AA", "enabled": False},
                {"player_name": "Office", "mac": "BB", "enabled": False},
            ],
        },
    )
    monkeypatch.setattr(
        api_status,
        "_build_onboarding_assistant_payload",
        lambda **_: {
            "checks": [{"key": "bluetooth", "status": "ok", "summary": "Bluetooth access is ready."}],
            "checklist": {
                "overall_status": "warning",
                "progress_percent": 60,
                "headline": "Next recommended step: Attach your first speaker",
                "summary": "Devices are configured, but none are currently connected over Bluetooth.",
                "current_step_key": "sink_verification",
                "current_step_title": "Attach your first speaker",
                "primary_action": {"key": "open_devices_settings", "label": "Open device settings"},
                "checkpoints": [],
                "steps": [
                    {"key": "bluetooth", "title": "Check Bluetooth access", "status": "ok", "stage": "complete"},
                    {"key": "audio", "title": "Verify audio backend", "status": "ok", "stage": "complete"},
                    {
                        "key": "sink_verification",
                        "title": "Attach your first speaker",
                        "status": "warning",
                        "stage": "current",
                        "summary": "Devices are configured, but none are currently connected over Bluetooth.",
                    },
                ],
            },
            "counts": {"configured_devices": 2, "connected_devices": 0, "sink_ready_devices": 0},
        },
    )
    state.set_disabled_devices(
        [
            {"player_name": "Kitchen", "mac": "AA", "enabled": False},
            {"player_name": "Office", "mac": "BB", "enabled": False},
        ]
    )
    try:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["operator_guidance"]["mode"] == "healthy"
        assert data["operator_guidance"]["header_status"]["label"] == "All devices disabled"
    finally:
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

    from services.device_registry import DeviceRegistrySnapshot

    monkeypatch.setattr(
        api_status,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[fake_client]),
    )
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

    from services.device_registry import DeviceRegistrySnapshot

    monkeypatch.setattr(
        api_status,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[fake_client]),
    )
    state.clear_device_events("sendspin-kitchen")
    state.record_device_event("sendspin-kitchen", "runtime-error", level="error", message="Route degraded")
    try:
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["health_summary"]["state"] == "degraded"
        assert data["health_summary"]["severity"] == "error"
        assert data["recent_events"][0]["event_type"] == "runtime-error"
        assert data["capabilities"]["domains"]["playback"]["currently_available"] is True
        assert data["capabilities"]["actions"]["queue_control"]["currently_available"] is False
        assert (
            data["capabilities"]["actions"]["queue_control"]["blocked_reason"]
            == "Music Assistant API is not connected."
        )
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


def test_status_includes_operator_guidance(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "operator_guidance" in data
    assert data["operator_guidance"]["visibility_keys"]["onboarding"] == "sendspin-ui:show-onboarding-guidance"
    assert "header_status" in data["operator_guidance"]


def test_status_includes_normalized_state_model_and_assistant_payloads(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "preflight" in data
    assert "state_model" in data
    assert "onboarding_assistant" in data
    assert "recovery_assistant" in data
    assert "runtime_substrate" in data["state_model"]
    assert "configuration" in data["state_model"]
    assert isinstance(data["state_model"]["devices"], list)


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


def test_api_bridge_telemetry_includes_resource_and_hook_data(client, monkeypatch):
    import routes.api_status as api_status
    from services.event_hooks import EventHookRegistry, get_event_hook_registry

    registry = get_event_hook_registry()
    registry.clear()
    monkeypatch.setattr(
        EventHookRegistry,
        "_resolve_host_addresses",
        staticmethod(lambda hostname, port, scheme: {"93.184.216.34"}),
    )
    registry.register(url="https://example.com/hook", categories=["bridge_event"])
    monkeypatch.setattr(
        api_status,
        "_collect_environment",
        lambda: {
            "process_rss_mb": 42.5,
            "python": "3.12.0",
            "platform": "Linux-test",
            "arch": "x86_64",
            "kernel": "6.8.0",
            "audio_server": "pulseaudio 16.1",
            "bluez": "5.72",
        },
    )
    monkeypatch.setattr(
        api_status,
        "_collect_subprocess_info",
        lambda: [{"name": "Kitchen", "pid": 1234, "process_rss_mb": 12.3}],
    )
    try:
        resp = client.get("/api/bridge/telemetry")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["bridge"]["process_rss_mb"] == 42.5
        assert data["subprocesses"][0]["process_rss_mb"] == 12.3
        assert data["event_hooks"]["summary"]["registered_hooks"] == 1
    finally:
        registry.clear()


def test_api_hooks_register_list_and_delete(client, monkeypatch):
    from services.event_hooks import EventHookRegistry, get_event_hook_registry

    registry = get_event_hook_registry()
    registry.clear()
    monkeypatch.setattr(
        EventHookRegistry,
        "_resolve_host_addresses",
        staticmethod(lambda hostname, port, scheme: {"93.184.216.34"}),
    )
    try:
        create_resp = client.post(
            "/api/hooks",
            data=json.dumps({"url": "https://example.com/hook", "categories": ["bridge_event"]}),
            content_type="application/json",
        )
        assert create_resp.status_code == 201
        hook = create_resp.get_json()["hook"]

        list_resp = client.get("/api/hooks")
        assert list_resp.status_code == 200
        list_data = list_resp.get_json()
        assert list_data["summary"]["registered_hooks"] == 1
        assert list_data["hooks"][0]["id"] == hook["id"]

        delete_resp = client.delete(f"/api/hooks/{hook['id']}")
        assert delete_resp.status_code == 200
        assert delete_resp.get_json() == {"success": True}
    finally:
        registry.clear()


def test_api_hooks_reject_invalid_url(client):
    resp = client.post(
        "/api/hooks",
        data=json.dumps({"url": "/relative/path"}),
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "url must be an absolute http:// or https:// URL"}


def test_api_hooks_reject_private_network_targets(client, monkeypatch):
    from services.event_hooks import EventHookRegistry

    monkeypatch.setattr(
        EventHookRegistry,
        "_resolve_host_addresses",
        staticmethod(lambda hostname, port, scheme: {"127.0.0.1"}),
    )

    resp = client.post(
        "/api/hooks",
        data=json.dumps({"url": "http://example.com/hook"}),
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "url must not target loopback, local, or private network hosts"}


def test_api_hooks_reject_non_numeric_timeout_values(client):
    resp = client.post(
        "/api/hooks",
        data=json.dumps({"url": "https://example.com/hook", "timeout_sec": {"seconds": 5}}),
        content_type="application/json",
    )

    assert resp.status_code == 400
    assert resp.get_json() == {"error": "Invalid timeout_sec: must be a number"}


def test_onboarding_assistant_endpoint_returns_guidance(client, monkeypatch):
    import routes.api_status as api_status
    from services.device_registry import DeviceRegistrySnapshot

    monkeypatch.setattr(
        api_status,
        "_collect_preflight_status",
        lambda: {
            "audio": {"system": "pulseaudio", "sinks": 1},
            "bluetooth": {"controller": True, "paired_devices": 1},
        },
    )
    monkeypatch.setattr(
        api_status,
        "load_config",
        lambda: {
            "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}],
            "PULSE_LATENCY_MSEC": 200,
            "MA_API_URL": "",
        },
    )
    monkeypatch.setattr(
        api_status,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(
            active_clients=[
                SimpleNamespace(
                    status={"bluetooth_connected": True},
                    _status_lock=threading.Lock(),
                    bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
                    player_name="Kitchen",
                    listen_port=8928,
                    server_host="music-assistant.local",
                    server_port=9000,
                    static_delay_ms=0.0,
                    connected_server_url="",
                    bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
                    bt_management_enabled=True,
                    is_running=lambda: True,
                )
            ]
        ),
    )
    monkeypatch.setattr(api_status, "is_ma_connected", lambda: False)
    monkeypatch.setattr(api_status, "build_mock_runtime_snapshot", lambda: SimpleNamespace(mode="production"))

    resp = client.get("/api/onboarding/assistant")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["runtime_mode"] == "production"
    assert data["counts"]["configured_devices"] == 1
    checks = {check["key"]: check for check in data["checks"]}
    assert checks["sink_verification"]["status"] == "ok"
    assert checks["ma_auth"]["status"] == "warning"
    assert checks["ma_auth"]["details"]["auto_discovery_available"] is True
    assert data["checklist"]["current_step_key"] == "ma_auth"
    assert data["checklist"]["primary_action"]["key"] == "retry_ma_discovery"
    ma_step = next(step for step in data["checklist"]["steps"] if step["key"] == "ma_auth")
    assert ma_step["recommended_action"]["key"] == "retry_ma_discovery"
    assert data["checklist"]["checkpoints"][2]["reached"] is True
    assert data["next_steps"]


def test_recovery_assistant_endpoint_returns_guidance(client, monkeypatch):
    import routes.api_status as api_status

    monkeypatch.setattr(
        api_status,
        "_build_recovery_assistant_payload",
        lambda **kwargs: {
            "summary": {
                "open_issue_count": 1,
                "highest_severity": "warning",
                "headline": "Kitchen is disconnected",
                "summary": "Power on the speaker or reconnect it.",
            },
            "issues": [
                {
                    "key": "disconnected",
                    "severity": "warning",
                    "title": "Kitchen is disconnected",
                    "summary": "Power on the speaker or reconnect it.",
                    "primary_action": {"key": "reconnect_device", "label": "Reconnect speaker"},
                    "recommended_action": {"key": "reconnect_device", "label": "Reconnect speaker"},
                    "secondary_actions": [{"key": "open_diagnostics", "label": "Open diagnostics"}],
                }
            ],
            "safe_actions": [{"key": "refresh_diagnostics", "label": "Rerun checks"}],
            "timeline": {"summary": {"entry_count": 1}, "entries": [{"source": "Bridge startup"}]},
        },
    )

    resp = client.get("/api/recovery/assistant")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["summary"]["headline"] == "Kitchen is disconnected"
    assert data["issues"][0]["primary_action"]["key"] == "reconnect_device"
    assert data["issues"][0]["recommended_action"]["key"] == "reconnect_device"
    assert data["issues"][0]["secondary_actions"][0]["key"] == "open_diagnostics"
    assert data["safe_actions"][0]["key"] == "refresh_diagnostics"
    assert data["timeline"]["summary"]["entry_count"] == 1


def test_rerun_safe_check_endpoint_returns_runner_payload(client, monkeypatch):
    import routes.api_status as api_status

    monkeypatch.setattr(
        api_status,
        "run_safe_check",
        lambda check_key, device_names=None, config=None: {
            "status": "ok",
            "check_key": check_key,
            "summary": "Bluetooth sinks verified.",
            "device_results": [{"device_name": "Kitchen", "status": "ok"}],
        },
    )
    monkeypatch.setattr(api_status, "load_config", lambda: {"BLUETOOTH_DEVICES": []})

    resp = client.post("/api/checks/rerun", json={"check_key": "sink_verification", "device_names": ["Kitchen"]})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["check_key"] == "sink_verification"
    assert data["device_results"][0]["device_name"] == "Kitchen"


def test_recovery_timeline_download_returns_csv(client, monkeypatch):
    import routes.api_status as api_status

    monkeypatch.setattr(
        api_status,
        "_build_recovery_assistant_payload",
        lambda **kwargs: {
            "timeline": {
                "summary": {"entry_count": 1},
                "entries": [
                    {
                        "at": "2026-03-20T10:00:00+00:00",
                        "level": "warning",
                        "source_type": "device",
                        "source": "Kitchen",
                        "label": "sink_missing",
                        "summary": "No sink after reconnect",
                    }
                ],
            }
        },
    )

    resp = client.get("/api/recovery/timeline/download")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/csv")
    assert "Kitchen" in resp.get_data(as_text=True)
    assert "sink_missing" in resp.get_data(as_text=True)


def test_latency_apply_persists_config(client, tmp_path, monkeypatch):
    import config
    import routes.api_status as api_status

    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"PULSE_LATENCY_MSEC": 300}))
    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", config_path)
    monkeypatch.setattr(api_status, "load_config", lambda: json.loads(config_path.read_text()))
    monkeypatch.setattr(
        api_status,
        "_build_recovery_assistant_payload",
        lambda **kwargs: {"latency_assistant": {"recommended_pulse_latency_msec": 600}},
    )

    resp = client.post("/api/latency/apply", json={"pulse_latency_msec": 600})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["restart_required"] is True
    assert data["pulse_latency_msec"] == 600


def test_operator_guidance_endpoint_returns_unified_payload(client, monkeypatch):
    import routes.api_status as api_status

    monkeypatch.setattr(
        api_status,
        "_build_operator_guidance_payload",
        lambda **kwargs: {
            "mode": "attention",
            "visibility_keys": {
                "onboarding": "sendspin-ui:show-onboarding-guidance",
                "recovery": "sendspin-ui:show-recovery-guidance",
            },
            "header_status": {
                "tone": "warning",
                "label": "2 issues need attention",
                "summary": "Reconnect affected devices.",
            },
            "banner": {
                "tone": "warning",
                "headline": "2 devices are disconnected",
                "summary": "Reconnect Kitchen and Office.",
                "dismissible": True,
                "preference_key": "sendspin-ui:show-recovery-guidance",
                "primary_action": {
                    "key": "reconnect_devices",
                    "label": "Reconnect 2 devices",
                    "device_names": ["Kitchen", "Office"],
                },
                "secondary_actions": [{"key": "open_diagnostics", "label": "Open diagnostics"}],
                "issue_count": 1,
            },
            "issue_groups": [
                {
                    "key": "disconnected",
                    "severity": "warning",
                    "title": "2 devices are disconnected",
                    "summary": "Reconnect Kitchen and Office.",
                    "count": 2,
                    "device_names": ["Kitchen", "Office"],
                    "primary_action": {
                        "key": "reconnect_devices",
                        "label": "Reconnect 2 devices",
                        "device_names": ["Kitchen", "Office"],
                    },
                    "secondary_actions": [{"key": "open_diagnostics", "label": "Open diagnostics"}],
                }
            ],
        },
    )

    resp = client.get("/api/operator/guidance")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["mode"] == "attention"
    assert data["banner"]["primary_action"]["key"] == "reconnect_devices"
    assert data["issue_groups"][0]["count"] == 2


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
    from config import CONFIG_SCHEMA_VERSION
    from services.device_registry import DeviceRegistrySnapshot
    from services.event_hooks import EventHookRegistry, get_event_hook_registry
    from services.ipc_protocol import IPC_PROTOCOL_VERSION

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

    monkeypatch.setattr(
        api_status,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[fake_client]),
    )
    monkeypatch.setattr(api_status, "get_server_name", lambda: "pulseaudio 16.1")
    monkeypatch.setattr(
        api_status,
        "list_sinks",
        lambda: [{"name": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"}],
    )
    monkeypatch.setattr(api_status, "_collect_environment", lambda: {"audio_server": "pulseaudio 16.1"})
    monkeypatch.setattr(api_status, "_collect_subprocess_info", lambda: [])
    monkeypatch.setattr(api_status, "_collect_portaudio_device_diagnostics", lambda: [])
    monkeypatch.setattr(
        api_status,
        "_build_onboarding_assistant_payload",
        lambda **kwargs: {
            "checks": [{"key": "sink_verification", "status": "ok", "summary": "All sinks look good."}],
            "next_steps": [],
        },
    )
    monkeypatch.setattr(
        api_status,
        "_build_recovery_assistant_payload",
        lambda **kwargs: {
            "summary": {"headline": "No active recovery issues", "open_issue_count": 0},
            "issues": [],
            "traces": [{"label": "Bridge startup", "summary": "Startup complete."}],
        },
    )
    monkeypatch.setattr(
        api_status,
        "_build_operator_guidance_payload",
        lambda **kwargs: {
            "mode": "healthy",
            "visibility_keys": {
                "onboarding": "sendspin-ui:show-onboarding-guidance",
                "recovery": "sendspin-ui:show-recovery-guidance",
            },
            "header_status": {"tone": "success", "label": "1/1 devices ready", "summary": "Healthy."},
            "issue_groups": [],
        },
    )
    monkeypatch.setattr(api_status.subprocess, "run", fake_run)

    state.set_ma_api_credentials("", "")
    state.set_ma_groups({}, [])
    hook_registry = get_event_hook_registry()
    hook_registry.clear()
    monkeypatch.setattr(
        EventHookRegistry,
        "_resolve_host_addresses",
        staticmethod(lambda hostname, port, scheme: {"93.184.216.34"}),
    )
    hook_registry.register(url="https://example.com/hook", categories=["device_event"])
    try:
        resp = client.get("/api/diagnostics")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"
        assert data["failed_collections"] == []
        assert data["collections_status"]["sink_inputs"]["status"] == "ok"
        assert data["contract_versions"]["config_schema_version"] == CONFIG_SCHEMA_VERSION
        assert data["contract_versions"]["ipc_protocol_version"] == IPC_PROTOCOL_VERSION
        assert data["devices"][0]["playing"] is True
        assert data["devices"][0]["last_error"] == "Route degraded"
        assert data["sink_inputs"][0]["id"] == "42"
        assert data["sink_inputs"][0]["state"] == "RUNNING"
        assert data["sink_inputs"][0]["application_name"] == "Sendspin Bridge"
        assert data["sink_inputs"][0]["media_name"] == "Quiet Woods"
        assert data["event_hooks"]["summary"]["registered_hooks"] == 1
        assert data["telemetry"]["event_hooks"]["summary"]["registered_hooks"] == 1
        assert data["onboarding_assistant"]["checks"][0]["key"] == "sink_verification"
        assert data["recovery_assistant"]["summary"]["headline"] == "No active recovery issues"
        assert data["recovery_assistant"]["traces"][0]["label"] == "Bridge startup"
        assert data["operator_guidance"]["mode"] == "healthy"
        assert data["operator_guidance"]["header_status"]["label"] == "1/1 devices ready"
    finally:
        sys.modules.pop("sendspin.audio", None)
        sys.modules.pop("sendspin.audio_devices", None)
        state.set_ma_groups({}, [])
        state.set_ma_api_credentials("", "")
        hook_registry.clear()


def test_collect_preflight_status_surfaces_audio_probe_failure(monkeypatch):
    import subprocess

    import routes.api_status as api_status

    monkeypatch.setattr(
        api_status,
        "get_server_name",
        lambda: (_ for _ in ()).throw(subprocess.TimeoutExpired("pactl info", 5)),
    )
    monkeypatch.setattr(api_status, "list_sinks", lambda: [{"name": "bluez_sink.demo"}])
    monkeypatch.setattr(
        api_status.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="", stderr=""),
    )

    payload = api_status._collect_preflight_status()

    assert payload["status"] == "degraded"
    assert "audio" in payload["failed_collections"]
    assert payload["collections_status"]["audio"]["status"] == "error"
    assert payload["collections_status"]["audio"]["error"]["code"] == "timeout"
    assert payload["audio"]["system"] == "unknown"


def test_api_diagnostics_reports_failed_collections_for_sink_input_timeout(client, monkeypatch):
    import subprocess

    import routes.api_status as api_status
    import state
    from services.device_registry import DeviceRegistrySnapshot

    fake_client = SimpleNamespace(
        player_name="Kitchen",
        status={"bluetooth_connected": True, "playing": False, "server_connected": True},
        bt_management_enabled=True,
        bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
        bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
        player_id="sendspin-kitchen",
    )

    def fake_run(cmd, capture_output=True, text=True, timeout=5):
        if cmd == ["bluetoothctl", "list"]:
            return SimpleNamespace(returncode=0, stdout="Controller AA:BB:CC:DD:EE:FF Test [default]\n", stderr="")
        if cmd == ["systemctl", "is-active", "bluetooth"]:
            return SimpleNamespace(returncode=0, stdout="active\n", stderr="")
        if cmd == ["pactl", "list", "sink-inputs"]:
            raise subprocess.TimeoutExpired(cmd, timeout)
        pytest.fail(f"Unexpected subprocess call: {cmd}")

    monkeypatch.setattr(
        api_status,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[fake_client]),
    )
    monkeypatch.setattr(api_status, "get_server_name", lambda: "pulseaudio 16.1")
    monkeypatch.setattr(api_status, "list_sinks", lambda: [{"name": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"}])
    monkeypatch.setattr(api_status, "_collect_environment", lambda: {"audio_server": "pulseaudio 16.1"})
    monkeypatch.setattr(api_status, "_collect_subprocess_info", lambda: [])
    monkeypatch.setattr(api_status, "_collect_portaudio_device_diagnostics", lambda: [])
    monkeypatch.setattr(api_status, "_build_onboarding_assistant_payload", lambda **kwargs: {"checks": []})
    monkeypatch.setattr(
        api_status, "_build_recovery_assistant_payload", lambda **kwargs: {"summary": {"headline": "OK"}}
    )
    monkeypatch.setattr(api_status, "_build_operator_guidance_payload", lambda **kwargs: {"mode": "healthy"})
    monkeypatch.setattr(api_status.subprocess, "run", fake_run)

    state.set_ma_api_credentials("", "")
    state.set_ma_groups({}, [])

    resp = client.get("/api/diagnostics")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "degraded"
    assert "sink_inputs" in data["failed_collections"]
    assert data["collections_status"]["sink_inputs"]["status"] == "error"
    assert data["collections_status"]["sink_inputs"]["error"]["code"] == "timeout"
    assert data["sink_inputs"][0]["error"] == "Failed to list sink inputs"


def test_device_enabled_toggle(client, tmp_path, monkeypatch):
    """POST /api/device/enabled returns success with restart_required."""
    import routes.api_bt as api_bt_mod
    import services.bluetooth as _bt_mod

    # Seed config with a device and patch _CONFIG_FILE for persist
    cfg = {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Test", "enabled": True}]}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    monkeypatch.setattr(api_bt_mod, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(api_bt_mod, "get_client_or_error", lambda player_name: (None, None))
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
    data = resp.get_json()
    assert data is not None
    assert "error" in data


# ---------------------------------------------------------------------------
#  XSS protection - /api/ma/ha-auth-page
# ---------------------------------------------------------------------------


def test_ha_auth_page_escapes_xss_payload(client, monkeypatch):
    """XSS payload inside a valid URL must be safely quoted in the rendered page.

    The outer URL-safety check rejects obviously-malformed inputs; this test
    focuses on the defence-in-depth JS string escaping for values that do get
    through.
    """
    from routes import ma_auth as _ma_auth

    monkeypatch.setattr(_ma_auth, "is_safe_external_url", lambda _u: True)
    resp = client.get("/api/ma/ha-auth-page?ma_url=';alert(1)//")
    assert resp.status_code == 200
    body = resp.data.decode()
    assert '"\';alert(1)//"' in body
    assert "= '';alert(1)//';" not in body


def test_ha_auth_page_rejects_javascript_scheme(client):
    """javascript: scheme in ma_url must be rejected with 400."""
    resp = client.get("/api/ma/ha-auth-page?ma_url=javascript:alert(1)")
    assert resp.status_code == 400
    body = resp.get_data(as_text=True)
    assert "Invalid" in body


def test_ha_auth_page_accepts_http_url(client, monkeypatch):
    """Normal http URL should be accepted and present in the page."""
    from routes import ma_auth as _ma_auth

    monkeypatch.setattr(_ma_auth, "is_safe_external_url", lambda _u: True)
    url = "http://192.168.1.100:8123"
    resp = client.get(f"/api/ma/ha-auth-page?ma_url={url}")
    assert resp.status_code == 200
    assert url.encode() in resp.data


def test_ha_auth_page_accepts_empty_url(client):
    """Empty ma_url should be accepted."""
    resp = client.get("/api/ma/ha-auth-page?ma_url=")
    assert resp.status_code == 200
    assert resp.data  # non-empty HTML response


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

        def read(self, size=-1):
            return b"jpeg-bytes"

    state.set_ma_api_credentials("http://ma:8095", "token123")
    try:
        with pytest.MonkeyPatch.context() as mp:
            import routes.ma_playback as ma_playback_mod

            opened = {}
            raw_url = "/api/image/123"
            signature = sign_artwork_url(raw_url)

            def _fake_urlopen(req, timeout=0):
                opened["url"] = req.full_url
                opened["auth"] = req.headers.get("Authorization")
                opened["accept"] = req.headers.get("Accept")
                opened["timeout"] = timeout
                return _FakeResponse()

            mp.setattr(ma_playback_mod._ur, "urlopen", _fake_urlopen)
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
    """External (non-MA-origin) artwork URLs with valid sig are proxied without Bearer token."""
    import state
    from services.ma_artwork import sign_artwork_url

    raw_url = "https://avatars.yandex.net/get-music-content/49876/ab027f9c.a.37173-2/1000x1000"
    state.set_ma_api_credentials("http://ma:8095", "token123")
    try:
        fake_resp = io.BytesIO(b"\x89PNG\r\n\x1a\n")
        fake_resp.headers = {"Content-Type": "image/png", "Content-Length": "8"}
        with patch("routes.ma_playback._ur.urlopen", return_value=fake_resp) as mock_open:
            resp = client.get(
                "/api/ma/artwork?url="
                "https%3A%2F%2Favatars.yandex.net%2Fget-music-content%2F49876%2Fab027f9c.a.37173-2%2F1000x1000"
                f"&sig={sign_artwork_url(raw_url)}"
            )
            assert resp.status_code == 200
            # Should NOT include Authorization header for external URLs
            called_req = mock_open.call_args[0][0]
            assert "Authorization" not in called_req.headers
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


def test_ma_host_from_sendspin_clients_uses_registry_snapshot(monkeypatch):
    import routes.api_ma as api_ma
    from services.device_registry import DeviceRegistrySnapshot

    snapshot = DeviceRegistrySnapshot(
        active_clients=[
            SimpleNamespace(server_host="auto", connected_server_url="192.168.10.10:9000"),
            SimpleNamespace(server_host="music-assistant.local", connected_server_url=""),
        ]
    )
    monkeypatch.setattr(api_ma, "get_device_registry_snapshot", lambda: snapshot)

    assert api_ma._ma_host_from_sendspin_clients() == "music-assistant.local"


def test_api_ma_rediscover_uses_registry_snapshot_player_payload(client, tmp_path, monkeypatch):
    import routes.api_ma as api_ma
    import routes.ma_groups as ma_groups
    import services.ma_client as ma_client
    import state
    from services.device_registry import DeviceRegistrySnapshot

    (tmp_path / "config.json").write_text(json.dumps({"MA_API_URL": "http://ma.local:8095", "MA_API_TOKEN": "token"}))
    captured = {}

    class _ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None, name=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    async def _fake_discover(ma_url, ma_token, player_info):
        captured["ma_url"] = ma_url
        captured["ma_token"] = ma_token
        captured["player_info"] = player_info
        return (
            {"sendspin-kitchen": {"id": "syncgroup_1", "name": "Kitchen"}},
            [{"id": "syncgroup_1", "name": "Kitchen"}],
        )

    class _DoneFuture:
        def __init__(self, result):
            self._result = result

        def result(self, timeout=None):
            return self._result

    def _run_coroutine_threadsafe(coro, loop):
        tmp_loop = asyncio.new_event_loop()
        try:
            return _DoneFuture(tmp_loop.run_until_complete(coro))
        finally:
            tmp_loop.close()

    monkeypatch.setattr(ma_groups, "get_main_loop", lambda: object())
    monkeypatch.setattr(ma_client, "discover_ma_groups", _fake_discover)
    monkeypatch.setattr(api_ma.asyncio, "run_coroutine_threadsafe", _run_coroutine_threadsafe)
    monkeypatch.setattr(ma_groups.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        api_ma,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(
            active_clients=[SimpleNamespace(player_id="sendspin-kitchen", player_name="Kitchen", status={})]
        ),
    )

    try:
        resp = client.post("/api/ma/rediscover")

        assert resp.status_code == 202
        job_id = resp.get_json()["job_id"]
        result = client.get(f"/api/ma/rediscover/result/{job_id}")
        assert result.status_code == 200
        assert result.get_json()["success"] is True
        assert captured["ma_url"] == "http://ma.local:8095"
        assert captured["ma_token"] == "token"
        assert captured["player_info"] == [{"player_id": "sendspin-kitchen", "player_name": "Kitchen"}]
    finally:
        state.set_ma_api_credentials("", "")
        state.set_ma_groups({}, [])


def test_api_ma_reload_restarts_monitor_and_rediscover(client, tmp_path, monkeypatch):
    import routes.api_ma as api_ma
    import routes.ma_groups as ma_groups
    import services.ma_client as ma_client
    import state
    from services.device_registry import DeviceRegistrySnapshot

    (tmp_path / "config.json").write_text(json.dumps({"MA_API_URL": "http://ma.local:8095", "MA_API_TOKEN": "token"}))
    captured = {}

    class _ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None, name=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    async def _fake_discover(ma_url, ma_token, player_info):
        captured["ma_url"] = ma_url
        captured["ma_token"] = ma_token
        captured["player_info"] = player_info
        return (
            {"sendspin-kitchen": {"id": "syncgroup_1", "name": "Kitchen"}},
            [{"id": "syncgroup_1", "name": "Kitchen"}],
        )

    class _DoneFuture:
        def __init__(self, result):
            self._result = result

        def result(self, timeout=None):
            return self._result

    def _run_coroutine_threadsafe(coro, loop):
        tmp_loop = asyncio.new_event_loop()
        try:
            return _DoneFuture(tmp_loop.run_until_complete(coro))
        finally:
            tmp_loop.close()

    monkeypatch.setattr(
        ma_groups, "load_config", lambda: {"MA_API_URL": "http://ma.local:8095", "MA_API_TOKEN": "token"}
    )
    monkeypatch.setattr(ma_groups, "get_main_loop", lambda: object())
    monkeypatch.setattr(
        ma_groups,
        "reload_monitor_credentials",
        lambda loop, ma_url, ma_token: captured.update({"reloaded": (ma_url, ma_token)}) or True,
    )
    monkeypatch.setattr(ma_client, "discover_ma_groups", _fake_discover)
    monkeypatch.setattr(api_ma.asyncio, "run_coroutine_threadsafe", _run_coroutine_threadsafe)
    monkeypatch.setattr(ma_groups.threading, "Thread", _ImmediateThread)
    monkeypatch.setattr(
        api_ma,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(
            active_clients=[SimpleNamespace(player_id="sendspin-kitchen", player_name="Kitchen", status={})]
        ),
    )

    try:
        resp = client.post("/api/ma/reload")

        assert resp.status_code == 202
        payload = resp.get_json()
        assert payload["monitor_reloaded"] is True
        job_id = payload["job_id"]
        result = client.get(f"/api/ma/rediscover/result/{job_id}")
        assert result.status_code == 200
        assert result.get_json()["success"] is True
        assert captured["reloaded"] == ("http://ma.local:8095", "token")
        assert captured["ma_url"] == "http://ma.local:8095"
        assert captured["ma_token"] == "token"
        assert captured["player_info"] == [{"player_id": "sendspin-kitchen", "player_name": "Kitchen"}]
    finally:
        state.set_ma_api_credentials("", "")
        state.set_ma_groups({}, [])


def test_api_debug_ma_uses_registry_snapshot(client, monkeypatch):
    import routes.api_ma as api_ma
    import state
    from services.device_registry import DeviceRegistrySnapshot

    state.clear_ma_now_playing()
    state.set_ma_groups({}, [])
    try:
        monkeypatch.setattr(
            api_ma,
            "get_device_registry_snapshot",
            lambda: DeviceRegistrySnapshot(
                active_clients=[
                    SimpleNamespace(
                        player_name="Kitchen", player_id="sendspin-kitchen", status={"group_id": "syncgroup_1"}
                    )
                ]
            ),
        )

        resp = client.get("/api/debug/ma")

        assert resp.status_code == 200
        data = resp.get_json()
        assert data["clients"] == [
            {
                "player_name": "Kitchen",
                "player_id": "sendspin-kitchen",
                "group_id": "syncgroup_1",
            }
        ]
    finally:
        state.clear_ma_now_playing()
        state.set_ma_groups({}, [])


def test_ma_queue_cmd_returns_structured_predicted_state(client, monkeypatch):
    import routes.api_ma as api_ma
    import routes.ma_playback as ma_playback
    import services.ma_monitor as ma_monitor
    import state

    class _FakeMonitor:
        def is_connected(self):
            return True

    class _ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None, name=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    async def _fake_send_queue_cmd(action, value, syncgroup_id, player_id=None):
        return {
            "accepted": True,
            "queue_id": syncgroup_id,
            "ack_latency_ms": 42,
            "accepted_at": 123.45,
        }

    async def _fake_request_queue_refresh(syncgroup_id):
        return True

    class _DoneFuture:
        def __init__(self, result):
            self._result = result

        def result(self, timeout=None):
            return self._result

    def _fake_run_coroutine_threadsafe(coro, loop):
        temp_loop = asyncio.new_event_loop()
        try:
            return _DoneFuture(temp_loop.run_until_complete(coro))
        finally:
            temp_loop.close()

    state.set_ma_connected(True)
    state.set_ma_groups({}, [{"id": "syncgroup_1", "name": "Kitchen", "members": []}])
    state.set_ma_now_playing_for_group(
        "syncgroup_1",
        {"syncgroup_id": "syncgroup_1", "shuffle": False, "connected": True},
    )
    try:
        monkeypatch.setattr(ma_playback, "get_main_loop", lambda: object())
        monkeypatch.setattr(ma_monitor, "send_queue_cmd", _fake_send_queue_cmd)
        monkeypatch.setattr(ma_monitor, "request_queue_refresh", _fake_request_queue_refresh)
        monkeypatch.setattr(ma_monitor, "get_monitor", lambda: _FakeMonitor())
        monkeypatch.setattr(api_ma.asyncio, "run_coroutine_threadsafe", _fake_run_coroutine_threadsafe)
        monkeypatch.setattr(ma_playback.threading, "Thread", _ImmediateThread)

        resp = client.post(
            "/api/ma/queue/cmd",
            data=json.dumps({"action": "shuffle", "value": True, "syncgroup_id": "syncgroup_1"}),
            content_type="application/json",
        )

        assert resp.status_code == 202
        data = resp.get_json()
        assert data["success"] is True
        assert data["accepted"] is False
        assert data["accepted_at"] is None
        assert data["ack_latency_ms"] is None
        assert data["confirmed"] is False
        assert data["pending"] is True
        assert data["syncgroup_id"] == "syncgroup_1"
        assert data["ma_now_playing"]["shuffle"] is True
        assert data["ma_now_playing"]["_sync_meta"]["pending"] is True
        assert data["ma_now_playing"]["_sync_meta"]["pending_ops"][0]["action"] == "shuffle"
        assert data["op_id"]
        assert data["job_id"]

        result = client.get(f"/api/ma/queue/cmd/result/{data['job_id']}")
        assert result.status_code == 200
        result_data = result.get_json()
        assert result_data["success"] is True
        assert result_data["accepted"] is True
        assert result_data["accepted_at"] == 123.45
        assert result_data["ack_latency_ms"] == 42
        assert result_data["ma_now_playing"]["_sync_meta"]["last_accepted_at"] == 123.45
    finally:
        state.clear_ma_now_playing()
        state.set_ma_groups({}, [])
        state.set_ma_connected(False)


def test_ma_queue_cmd_prefers_player_queue_over_stale_group_id(client, monkeypatch):
    import routes.api_ma as api_ma
    import routes.ma_playback as ma_playback
    import services.ma_monitor as ma_monitor
    import state

    class _FakeMonitor:
        def is_connected(self):
            return True

    class _ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None, name=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    captured = {}

    async def _fake_send_queue_cmd(action, value, syncgroup_id, player_id=None):
        captured["action"] = action
        captured["value"] = value
        captured["syncgroup_id"] = syncgroup_id
        captured["player_id"] = player_id
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
        monkeypatch.setattr(ma_playback, "get_main_loop", lambda: object())
        monkeypatch.setattr(ma_monitor, "send_queue_cmd", _fake_send_queue_cmd)
        monkeypatch.setattr(ma_monitor, "request_queue_refresh", _fake_request_queue_refresh)
        monkeypatch.setattr(ma_monitor, "get_monitor", lambda: _FakeMonitor())
        monkeypatch.setattr(api_ma.asyncio, "run_coroutine_threadsafe", _fake_run_coroutine_threadsafe)
        monkeypatch.setattr(ma_playback.threading, "Thread", _ImmediateThread)

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

        assert resp.status_code == 202
        data = resp.get_json()
        assert captured["syncgroup_id"] == "sendspin-yandex-mini-2---lxc"
        assert captured["player_id"] == "sendspin-yandex-mini-2---lxc"
        assert captured["refresh_syncgroup_id"] == "sendspin-yandex-mini-2---lxc"
        assert data["syncgroup_id"] == "sendspin-yandex-mini-2---lxc"
        assert data["queue_id"] == "sendspin-yandex-mini-2---lxc"
        result = client.get(f"/api/ma/queue/cmd/result/{data['job_id']}")
        assert result.status_code == 200
        assert result.get_json()["success"] is True
    finally:
        state.clear_ma_now_playing()
        state.set_ma_groups({}, [])
        state.set_ma_connected(False)


def test_ma_queue_cmd_refreshes_actual_accepted_solo_queue(client, monkeypatch):
    import routes.api_ma as api_ma
    import routes.ma_playback as ma_playback
    import services.ma_monitor as ma_monitor
    import state

    class _FakeMonitor:
        def is_connected(self):
            return True

    class _ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon=None, name=None):
            self._target = target
            self._args = args
            self._kwargs = kwargs or {}

        def start(self):
            self._target(*self._args, **self._kwargs)

    captured = {}

    async def _fake_send_queue_cmd(action, value, syncgroup_id, player_id=None):
        captured["action"] = action
        captured["value"] = value
        captured["syncgroup_id"] = syncgroup_id
        captured["player_id"] = player_id
        return {
            "accepted": True,
            "queue_id": "upsendspinyandexmini2lxc",
            "ack_latency_ms": 9,
            "accepted_at": 789.01,
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
    state.set_ma_groups({}, [])
    state.set_ma_now_playing_for_group(
        "sendspin-yandex-mini-2---lxc",
        {"syncgroup_id": "sendspin-yandex-mini-2---lxc", "shuffle": False, "connected": True},
    )
    try:
        monkeypatch.setattr(ma_playback, "get_main_loop", lambda: object())
        monkeypatch.setattr(ma_monitor, "send_queue_cmd", _fake_send_queue_cmd)
        monkeypatch.setattr(ma_monitor, "request_queue_refresh", _fake_request_queue_refresh)
        monkeypatch.setattr(ma_monitor, "get_monitor", lambda: _FakeMonitor())
        monkeypatch.setattr(api_ma.asyncio, "run_coroutine_threadsafe", _fake_run_coroutine_threadsafe)
        monkeypatch.setattr(ma_playback.threading, "Thread", _ImmediateThread)

        resp = client.post(
            "/api/ma/queue/cmd",
            data=json.dumps(
                {
                    "action": "shuffle",
                    "value": True,
                    "syncgroup_id": "sendspin-yandex-mini-2---lxc",
                    "player_id": "sendspin-yandex-mini-2---lxc",
                }
            ),
            content_type="application/json",
        )

        assert resp.status_code == 202
        data = resp.get_json()
        assert captured["syncgroup_id"] == "sendspin-yandex-mini-2---lxc"
        assert captured["player_id"] == "sendspin-yandex-mini-2---lxc"
        assert captured["refresh_syncgroup_id"] == "upsendspinyandexmini2lxc"
        assert data["queue_id"] == "sendspin-yandex-mini-2---lxc"
        result = client.get(f"/api/ma/queue/cmd/result/{data['job_id']}")
        assert result.status_code == 200
        result_data = result.get_json()
        assert result_data["success"] is True
        assert result_data["queue_id"] == "upsendspinyandexmini2lxc"
    finally:
        state.clear_ma_now_playing()
        state.set_ma_groups({}, [])
        state.set_ma_connected(False)


def test_resolve_target_queue_uses_player_id_queue_for_solo_sendspin_player():
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
    assert queue_id == "sendspin-yandex-mini-2---lxc"


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
    assert queue_id == "sendspin-yandex-mini-2---lxc"


def test_resolve_target_queue_infers_single_active_player_for_stale_page(monkeypatch):
    import routes.api_ma as api_ma
    import routes.ma_playback as ma_playback
    import state
    from services.device_registry import DeviceRegistrySnapshot

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
    monkeypatch.setattr(
        ma_playback, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=[fake_client])
    )
    try:
        state_key, queue_id = api_ma._resolve_target_queue("syncgroup_5zr8ss8g", None, None)
    finally:
        state.set_ma_groups({}, [])

    assert state_key == "sendspin-yandex-mini-2---lxc"
    assert queue_id == "sendspin-yandex-mini-2---lxc"


def test_resolve_target_queue_keeps_legacy_universal_queue_for_uuid_player_id():
    import routes.api_ma as api_ma
    import state

    state.set_ma_groups({}, [])
    try:
        state_key, queue_id = api_ma._resolve_target_queue(
            None,
            "d3002d0d-db47-51e2-b3a2-00f79b7fc683",
            None,
        )
    finally:
        state.set_ma_groups({}, [])

    assert state_key == "d3002d0d-db47-51e2-b3a2-00f79b7fc683"
    assert queue_id == "upd3002d0ddb4751e2b3a200f79b7fc683"


def test_ma_queue_cmd_returns_503_when_monitor_unavailable(client, monkeypatch):
    import routes.ma_playback as ma_playback
    import services.ma_monitor as ma_monitor
    import state

    class _FakeMonitor:
        def is_connected(self):
            return False

    state.set_ma_connected(True)
    state.set_ma_groups({}, [{"id": "syncgroup_1", "name": "Kitchen", "members": []}])
    try:
        monkeypatch.setattr(ma_playback, "get_main_loop", lambda: object())
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
