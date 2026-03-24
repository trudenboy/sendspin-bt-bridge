"""Tests for the 10-second BT scan cooldown behaviour in routes/api_bt.py."""

import importlib
import json
from unittest.mock import patch

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
    """Return a Flask test client with the bt_bp blueprint registered."""
    import sys

    from flask import Flask

    route_modules = [
        "routes.api",
        "routes.api_bt",
        "routes.api_config",
        "routes.api_status",
        "routes.auth",
        "routes.views",
        "routes",
    ]
    _stashed = {}
    for mod_name in route_modules:
        cached = sys.modules.pop(mod_name, None)
        if cached is not None:
            _stashed[mod_name] = cached

    from routes.api_bt import bt_bp

    app = Flask(__name__)
    app.secret_key = "testing"
    app.config["TESTING"] = True
    app.register_blueprint(bt_bp)

    yield app.test_client()

    for mod_name in route_modules:
        sys.modules.pop(mod_name, None)
    for mod_name, mod in _stashed.items():
        sys.modules[mod_name] = mod


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_scan_returns_429_during_cooldown(client):
    """POST /api/bt/scan during cooldown returns 429."""
    with patch("routes.api_bt.is_scan_running", return_value=False), patch("routes.api_bt.time") as mock_time:
        # Simulate a scan that completed 5 seconds ago (within 10 s cooldown)
        mock_time.monotonic.return_value = 100.0
        _mod = importlib.import_module("routes.api_bt")

        _mod._last_scan_completed = 95.0  # 5 s ago

        resp = client.post("/api/bt/scan")
        assert resp.status_code == 429
        assert "cooldown" in resp.get_json()["error"].lower()


def test_scan_allowed_after_cooldown_expires(client):
    """POST /api/bt/scan succeeds when cooldown has elapsed."""
    with (
        patch("routes.api_bt.is_scan_running", return_value=False),
        patch("routes.api_bt.time") as mock_time,
        patch("routes.api_bt.list_bt_adapters", return_value=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"]),
        patch("routes.api_bt.threading.Thread") as mock_thread,
    ):
        mock_time.monotonic.return_value = 100.0
        _mod = importlib.import_module("routes.api_bt")

        _mod._last_scan_completed = 80.0  # 20 s ago — past the 10 s cooldown

        mock_thread.return_value.start = lambda: None

        resp = client.post("/api/bt/scan")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "job_id" in data
        assert data["scan_options"]["audio_only"] is True
        assert data["scan_options"]["adapter_scope"] == "all"
        assert data["expected_duration"] == 17


def test_concurrent_scan_returns_409(client):
    """POST /api/bt/scan while another scan is running returns 409."""
    with patch("routes.api_bt.is_scan_running", return_value=True):
        resp = client.post("/api/bt/scan")
        assert resp.status_code == 409
        assert "already in progress" in resp.get_json()["error"].lower()


def test_scan_accepts_selected_adapter_and_audio_filter(client):
    """POST /api/bt/scan forwards selected adapter and audio-only options."""
    with (
        patch("routes.api_bt.is_scan_running", return_value=False),
        patch("routes.api_bt.time") as mock_time,
        patch("routes.api_bt.list_bt_adapters", return_value=["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"]),
        patch("routes.api_bt._run_bt_scan") as run_bt_scan,
        patch("routes.api_bt._release_bt_operation") as release_bt_operation,
        patch("routes.api_bt.threading.Thread") as mock_thread,
    ):
        mock_time.monotonic.return_value = 100.0
        _mod = importlib.import_module("routes.api_bt")
        _mod._last_scan_completed = 80.0
        mock_thread.return_value.start = lambda: None

        resp = client.post("/api/bt/scan", json={"adapter": "hci1", "audio_only": False})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["scan_options"] == {
            "adapter": "hci1",
            "audio_only": False,
            "adapter_scope": "selected",
            "adapter_count": 1,
        }
        assert data["expected_duration"] == 15
        target = mock_thread.call_args.kwargs["target"]
        assert callable(target)
        target()
        run_bt_scan.assert_called_once_with(data["job_id"], "hci1", False)
        release_bt_operation.assert_called_once()


def test_scan_rejects_invalid_adapter_identifier(client):
    """POST /api/bt/scan rejects malformed adapter values."""
    with patch("routes.api_bt.is_scan_running", return_value=False):
        resp = client.post("/api/bt/scan", json={"adapter": "hciX"})
        assert resp.status_code == 400
        assert "invalid adapter" in resp.get_json()["error"].lower()


def test_scan_result_running_includes_metadata(client):
    """GET /api/bt/scan/result exposes running scan metadata for the modal."""
    with patch(
        "routes.api_bt.get_scan_job",
        return_value={
            "status": "running",
            "scan_options": {"adapter": "", "audio_only": True, "adapter_scope": "all", "adapter_count": 2},
            "expected_duration": 17,
            "started_at": 123.0,
        },
    ):
        resp = client.get("/api/bt/scan/result/test-job")
        assert resp.status_code == 200
        assert resp.get_json() == {
            "status": "running",
            "scan_options": {"adapter": "", "audio_only": True, "adapter_scope": "all", "adapter_count": 2},
            "expected_duration": 17,
            "started_at": 123.0,
        }


def test_cooldown_timestamp_updated_after_scan(client):
    """A completed scan immediately activates the cooldown for the next scan request."""
    _mod = importlib.import_module("routes.api_bt")

    _mod._last_scan_completed = 0.0

    fake_time = 500.0
    with (
        patch("routes.api_bt.time") as mock_time,
        patch("routes.api_bt.is_scan_running", return_value=False),
        patch("routes.api_bt.list_bt_adapters", return_value=[]),
        patch("routes.api_bt._run_bluetoothctl_scan", return_value=""),
        patch("routes.api_bt._parse_scan_output", return_value=(set(), {}, {}, set())),
        patch("routes.api_bt._resolve_unnamed_devices"),
        patch("routes.api_bt.finish_scan_job"),
    ):
        mock_time.monotonic.return_value = fake_time

        _mod._run_bt_scan("test-job-id")

        resp = client.post("/api/bt/scan")
        assert resp.status_code == 429
        assert "cooldown" in resp.get_json()["error"].lower()
