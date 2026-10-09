"""Bridge-wide settings outside the config document: log level, local password, the service log."""

from __future__ import annotations

import json
import logging
import time

import pytest

import sendspin_bridge.config as config_module
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot
from sendspin_bridge.services.ipc.commands import SetLogLevel


@pytest.fixture(autouse=True)
def _standalone(monkeypatch, tmp_config):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)


class _Daemon:
    def __init__(self, name: str, running: bool):
        self.player_name = name
        self._running = running
        self.sent: list = []

    def is_running(self) -> bool:
        return self._running

    async def _send_subprocess_command(self, command) -> None:
        self.sent.append(command)


@pytest.fixture
def restore_root_level():
    root = logging.getLogger()
    level = root.level
    yield root
    root.setLevel(level)


# -- log level ------------------------------------------------------------------------------------


def test_log_level_is_saved_applied_and_pushed_to_running_daemons(
    api_client, tmp_config, monkeypatch, bridge_loop, restore_root_level
):
    import sendspin_bridge.services.bluetooth.device_registry as registry

    kitchen, bedroom = _Daemon("Kitchen", True), _Daemon("Bedroom", False)
    monkeypatch.setattr(
        registry, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=[kitchen, bedroom])
    )

    resp = api_client.put("/api/v1/bridge/log-level", json={"level": "DEBUG"})

    assert resp.status_code == 200
    assert resp.json() == {"level": "DEBUG"}
    assert json.loads(tmp_config.read_text())["LOG_LEVEL"] == "DEBUG"
    assert restore_root_level.level == logging.DEBUG
    deadline = time.monotonic() + 2
    while not kitchen.sent and time.monotonic() < deadline:
        time.sleep(0.01)
    assert kitchen.sent == [SetLogLevel(level="DEBUG")]
    assert bedroom.sent == []


def test_unknown_log_level_is_422(api_client):
    assert api_client.put("/api/v1/bridge/log-level", json={"level": "TRACE"}).status_code == 422


def test_a_log_level_that_cannot_be_saved_changes_nothing(api_client, monkeypatch, restore_root_level):
    before = restore_root_level.level

    def _disk_full(_mutator):
        raise OSError("disk full")

    monkeypatch.setattr(config_module, "update_config", _disk_full)

    resp = api_client.put("/api/v1/bridge/log-level", json={"level": "DEBUG"})

    assert resp.status_code == 500
    problem = resp.json()
    assert "log level" in problem["detail"].lower()
    assert "remediation" in problem
    assert restore_root_level.level == before


# -- local password ----------------------------------------------------------------------------------


def test_first_password_is_stored_as_a_hash(api_client, tmp_config):
    resp = api_client.put("/api/v1/auth/password", json={"password": "mysecretpassword"})

    assert resp.status_code == 204
    stored = json.loads(tmp_config.read_text())["AUTH_PASSWORD_HASH"]
    assert stored != "mysecretpassword"
    assert config_module.check_password("mysecretpassword", stored)


def test_short_password_is_422(api_client):
    resp = api_client.put("/api/v1/auth/password", json={"password": "short"})
    assert resp.status_code == 422
    assert resp.json()["errors"][0]["loc"][-1] == "password"


def test_a_password_that_cannot_be_saved_says_how_to_fix_it(api_client, monkeypatch):
    def _disk_full(_mutator):
        raise OSError("disk full")

    monkeypatch.setattr(config_module, "update_config", _disk_full)

    resp = api_client.put("/api/v1/auth/password", json={"password": "verysecurepassword"})

    assert resp.status_code == 500
    assert "save password" in resp.json()["detail"].lower()
    assert "remediation" in resp.json()


def test_the_addon_leaves_users_to_home_assistant(api_client, monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
    from tests.support.api_client import make_client

    resp = make_client(peer="172.30.32.2").put(
        "/api/v1/auth/password",
        json={"password": "verysecurepassword"},
        headers={"X-Ingress-Path": "/api/hassio_ingress/x", "X-CSRF-Token": "x"},
    )
    assert resp.status_code in (400, 403)


# -- service log ---------------------------------------------------------------------------------------


def test_service_log_summarises_recent_issues(api_client, monkeypatch):
    import sendspin_bridge.application.config as config_uc

    monkeypatch.setattr(config_uc, "_detect_runtime", lambda: "systemd")
    monkeypatch.setattr(
        config_uc,
        "_read_log_lines",
        lambda runtime, lines: [
            "2026-03-17 18:00:00,000 - root - INFO - startup complete",
            "2026-03-17 18:00:01,000 - root - WARNING - daemon stderr: ALSA setup failed",
            "2026-03-17 18:00:02,000 - root - ERROR - daemon crashed",
        ],
    )

    data = api_client.get("/api/v1/diagnostics/logs", params={"lines": 50}).json()

    assert data["has_recent_issues"] is True
    assert data["recent_issue_count"] == 2
    assert data["recent_issue_level"] == "error"


def test_service_log_line_count_is_clamped(api_client, monkeypatch):
    import sendspin_bridge.application.config as config_uc

    asked: list[int] = []
    monkeypatch.setattr(config_uc, "_read_log_lines", lambda runtime, lines: asked.append(lines) or [])
    api_client.get("/api/v1/diagnostics/logs", params={"lines": 100000})
    api_client.get("/api/v1/diagnostics/logs", params={"lines": -3})
    assert asked == [500, 1]
