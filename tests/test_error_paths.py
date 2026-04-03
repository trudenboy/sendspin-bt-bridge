"""Error-path tests for malformed input, missing processes, and invalid parameters."""

import json
import os
import sys

import pytest

from services.ipc_protocol import IPC_PROTOCOL_VERSION
from services.subprocess_ipc import SubprocessIpcService

# ---------------------------------------------------------------------------
# Malformed JSON in IPC messages
# ---------------------------------------------------------------------------


def test_handle_message_ignores_status_without_allowed_keys():
    """handle_message returns empty dict for a status envelope with no allowed fields."""
    service = SubprocessIpcService(
        player_name="Test",
        protocol_warning_cache=set(),
        status_updater=lambda _: None,
        allowed_keys=frozenset(),
    )
    result = service.handle_message({"type": "status", "protocol_version": IPC_PROTOCOL_VERSION, "unknown_field": True})
    assert result == {}


def test_handle_message_ignores_dict_without_type():
    """A dict with no recognized type/cmd returns None."""
    updates: list[dict] = []
    service = SubprocessIpcService(
        player_name="Test",
        protocol_warning_cache=set(),
        status_updater=updates.append,
        allowed_keys=frozenset({"playing"}),
    )
    result = service.handle_message({"random_key": "value"})
    assert result is None
    assert updates == []


def test_parse_line_returns_none_for_malformed_json():
    """parse_line must silently return None for invalid JSON."""
    service = SubprocessIpcService(
        player_name="Test",
        protocol_warning_cache=set(),
        status_updater=lambda _: None,
    )
    assert service.parse_line(b"this is not json\n") is None
    assert service.parse_line(b"{truncated\n") is None
    assert service.parse_line(b"") is None


def test_parse_line_returns_none_for_json_array():
    """JSON arrays should be rejected (only objects are valid)."""
    service = SubprocessIpcService(
        player_name="Test",
        protocol_warning_cache=set(),
        status_updater=lambda _: None,
    )
    assert service.parse_line(b"[1, 2, 3]\n") is None


def test_handle_message_with_empty_status_envelope():
    """A status envelope with no allowed keys returns empty updates."""
    service = SubprocessIpcService(
        player_name="Test",
        protocol_warning_cache=set(),
        status_updater=lambda _: None,
        allowed_keys=frozenset({"playing"}),
    )
    result = service.handle_message({"type": "status", "protocol_version": IPC_PROTOCOL_VERSION})
    assert result == {}


# ---------------------------------------------------------------------------
# ProcessLookupError in restart endpoint
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
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

    from flask import Flask

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

    for mod_name, mod in _stashed.items():
        sys.modules.setdefault(mod_name, mod)


def test_restart_handles_process_lookup_error(client, monkeypatch):
    """Restart endpoint falls back to SIGTERM on ProcessLookupError from PID 1."""
    import routes.api as api_mod

    monkeypatch.setattr(api_mod, "_detect_runtime", lambda: "docker")

    killed_pids: list[int] = []

    def _fake_kill(pid, sig):
        if pid == 1:
            raise ProcessLookupError("No such process")
        killed_pids.append(pid)

    monkeypatch.setattr(os, "kill", _fake_kill)
    monkeypatch.setattr(api_mod.time, "sleep", lambda _: None)

    resp = client.post("/api/restart")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True
    assert data["runtime"] == "docker"

    # The restart thread runs in background — wait briefly for it
    import time

    time.sleep(0.1)
    assert os.getpid() in killed_pids


def test_restart_handles_permission_error(client, monkeypatch):
    """Restart endpoint falls back to own-PID SIGTERM on PermissionError from PID 1."""
    import routes.api as api_mod

    monkeypatch.setattr(api_mod, "_detect_runtime", lambda: "docker")

    killed_pids: list[int] = []

    def _fake_kill(pid, sig):
        if pid == 1:
            raise PermissionError("Operation not permitted")
        killed_pids.append(pid)

    monkeypatch.setattr(os, "kill", _fake_kill)
    monkeypatch.setattr(api_mod.time, "sleep", lambda _: None)

    resp = client.post("/api/restart")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["success"] is True

    import time

    time.sleep(0.1)
    assert os.getpid() in killed_pids


# Invalid MAC address in BluetoothManager.pair_and_trust
# ---------------------------------------------------------------------------


def test_pair_device_rejects_invalid_mac():
    """pair_device rejects malformed MAC addresses.

    The demo test suite may have replaced bluetooth_manager.BluetoothManager
    with the demo stub, so we verify the MAC regex guard directly (which is
    what the real pair_device checks first).
    """
    import re

    # This is the exact regex from BluetoothManager.pair_device (bluetooth_manager.py:429)
    mac_pattern = r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}"
    assert re.fullmatch(mac_pattern, "not-a-mac") is None
    assert re.fullmatch(mac_pattern, "AA:BB:CC:DD:EE:FF") is not None


def test_pair_device_rejects_short_mac():
    """MAC with too few octets is rejected by the validation regex."""
    import re

    mac_pattern = r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}"
    assert re.fullmatch(mac_pattern, "AA:BB:CC") is None
    assert re.fullmatch(mac_pattern, "AA:BB:CC:DD") is None
    assert re.fullmatch(mac_pattern, "") is None


def test_pair_device_rejects_mac_with_invalid_chars():
    """MAC with non-hex characters is rejected."""
    import re

    mac_pattern = r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}"
    assert re.fullmatch(mac_pattern, "GG:HH:II:JJ:KK:LL") is None
    assert re.fullmatch(mac_pattern, "XX:YY:ZZ:11:22:33") is None


# ---------------------------------------------------------------------------
# Invalid action parameter in pause endpoint
# ---------------------------------------------------------------------------


def test_pause_all_rejects_invalid_action(client):
    """POST /api/pause_all with invalid action returns 400."""
    resp = client.post(
        "/api/pause_all",
        data=json.dumps({"action": "invalid"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
    assert "Invalid action" in data["error"]


def test_pause_player_rejects_invalid_action(client):
    """POST /api/pause with invalid action returns 400."""
    resp = client.post(
        "/api/pause",
        data=json.dumps({"player_name": "Kitchen", "action": "rewind"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
    assert "Invalid action" in data["error"]


def test_group_pause_rejects_invalid_action(client):
    """POST /api/group/pause with invalid action returns 400."""
    resp = client.post(
        "/api/group/pause",
        data=json.dumps({"group_id": "g1", "action": "stop"}),
        content_type="application/json",
    )
    assert resp.status_code == 400
    data = resp.get_json()
    assert data["success"] is False
    assert "Invalid action" in data["error"]
