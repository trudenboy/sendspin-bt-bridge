"""Configuration, diagnostics and bug-report endpoints of API v1."""

import io
import json
import sys
import threading
from types import SimpleNamespace
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory so the web app can start."""
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))


@pytest.fixture()
def client():
    from tests.support.api_client import make_client

    return make_client()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_health_endpoint(client):
    """GET /api/v1/health returns {"ok": true} with status 200."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data == {"ok": True}


def test_config_upload_returns_structured_validation_errors(client):
    resp = client.post(
        "/api/v1/config/import",
        files={
            "file": (
                "config.json",
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
            )
        },
    )

    assert resp.status_code == 400
    data = resp.json()
    assert data["detail"] == "Duplicate MAC address: AA:BB:CC:DD:EE:FF"
    assert data["errors"][0]["field"] == "BLUETOOTH_DEVICES[1].mac"


def test_config_upload_rejects_duplicate_effective_listen_ports(client):
    resp = client.post(
        "/api/v1/config/import",
        files={
            "file": (
                "config.json",
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
            )
        },
    )

    assert resp.status_code == 400
    data = resp.json()
    assert data["detail"].startswith("Duplicate effective listen_port 8928")
    assert data["errors"][0]["field"] == "BLUETOOTH_DEVICES[1].listen_port"


def test_config_upload_returns_validation_warnings_on_success(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")

    resp = client.post(
        "/api/v1/config/import",
        files={
            "file": (
                "config.json",
                io.BytesIO(
                    json.dumps(
                        {
                            "SENDSPIN_PORT": "9000",
                            "BLUETOOTH_DEVICES": [{"mac": "aa:bb:cc:dd:ee:ff"}],
                        }
                    ).encode()
                ),
            )
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["warnings"][0]["field"] == "CONFIG_SCHEMA_VERSION"


def test_config_validate_returns_normalized_preview(client):
    resp = client.post(
        "/api/v1/config/validate",
        json={
            "SENDSPIN_PORT": "9000",
            "WEB_PORT": "18080",
            "BASE_LISTEN_PORT": "19000",
            "BLUETOOTH_DEVICES": [{"mac": "aa:bb:cc:dd:ee:ff"}],
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is True
    assert data["warnings"][0]["field"] == "CONFIG_SCHEMA_VERSION"
    assert data["normalized_config"]["SENDSPIN_PORT"] == 9000
    assert data["normalized_config"]["WEB_PORT"] == 18080
    assert data["normalized_config"]["BASE_LISTEN_PORT"] == 19000
    assert data["normalized_config"]["BLUETOOTH_DEVICES"][0]["mac"] == "AA:BB:CC:DD:EE:FF"


def test_config_validate_warns_when_new_mac_already_exists_in_ma(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod
    from sendspin_bridge.config import _player_id_from_mac

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))
    mac = "AA:BB:CC:DD:EE:FF"
    monkeypatch.setattr(
        api_config_mod,
        "fetch_all_players_snapshot",
        lambda ma_url, ma_token: [{"player_id": _player_id_from_mac(mac), "display_name": "Kitchen @ Other Bridge"}],
    )

    resp = client.post(
        "/api/v1/config/validate",
        json={
            "MA_API_URL": "http://ma:8095",
            "MA_API_TOKEN": "token",
            "BLUETOOTH_DEVICES": [{"mac": mac}],
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    messages = [warning["message"] for warning in data["warnings"]]
    assert any("may belong to another bridge" in message for message in messages)
    assert any(warning["field"] == "BLUETOOTH_DEVICES[0].mac" for warning in data["warnings"])


def test_config_validate_does_not_warn_for_existing_mac_on_same_bridge(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod
    from sendspin_bridge.config import _player_id_from_mac

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    mac = "AA:BB:CC:DD:EE:FF"
    (tmp_path / "config.json").write_text(json.dumps({"BLUETOOTH_DEVICES": [{"mac": mac}]}))
    monkeypatch.setattr(
        api_config_mod,
        "fetch_all_players_snapshot",
        lambda ma_url, ma_token: [{"player_id": _player_id_from_mac(mac), "display_name": "Kitchen @ This Bridge"}],
    )

    resp = client.post(
        "/api/v1/config/validate",
        json={
            "MA_API_URL": "http://ma:8095",
            "MA_API_TOKEN": "token",
            "BLUETOOTH_DEVICES": [{"mac": mac}],
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    messages = [warning["message"] for warning in data["warnings"]]
    assert not any("may belong to another bridge" in message for message in messages)


# ---------------------------------------------------------------------------
# Worker-thread startup failures must not wedge the Bluetooth operation lock.
# Every endpoint below takes the lock in the request thread and hands the
# release to a worker; if the thread never starts, the release never runs and
# every later Bluetooth operation answers 409 until the process restarts.
# ---------------------------------------------------------------------------


def test_config_validate_returns_errors_for_invalid_payload(client):
    resp = client.post(
        "/api/v1/config/validate",
        json={"BLUETOOTH_DEVICES": [{"mac": "not-a-mac"}]},
    )

    # Validation answered: the request succeeded, the config did not.
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert data["errors"][0]["field"] == "BLUETOOTH_DEVICES[0].mac"


def test_config_validate_rejects_duplicate_effective_listen_ports(client):
    resp = client.post(
        "/api/v1/config/validate",
        json={
            "CONFIG_SCHEMA_VERSION": 1,
            "BASE_LISTEN_PORT": 8928,
            "BLUETOOTH_DEVICES": [
                {"mac": "AA:BB:CC:DD:EE:01", "player_name": "Kitchen"},
                {"mac": "AA:BB:CC:DD:EE:02", "player_name": "Office", "listen_port": 8928},
            ],
        },
    )

    # Validation answered: the request succeeded, the config did not.
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert data["errors"][0]["field"] == "BLUETOOTH_DEVICES[1].listen_port"
    assert "Duplicate effective listen_port 8928" in data["errors"][0]["message"]


def test_config_validate_rejects_future_schema_version(client):
    resp = client.post(
        "/api/v1/config/validate",
        json={"CONFIG_SCHEMA_VERSION": 999, "BLUETOOTH_DEVICES": []},
    )

    # Validation answered: the request succeeded, the config did not.
    assert resp.status_code == 200
    data = resp.json()
    assert data["valid"] is False
    assert data["errors"][0]["field"] == "CONFIG_SCHEMA_VERSION"


def test_read_log_lines_docker_uses_root_logger_ring_buffer(monkeypatch):
    """Ring buffer fallback must find the handler via sys.modules['__main__'],
    not via ``from sendspin_client import _ring_log_handler`` (which
    would create a second empty instance when __main__ != module name)."""
    import subprocess
    from collections import deque

    import sendspin_bridge.application.config as mod
    import sendspin_bridge.bridge.client as client_mod

    monkeypatch.setattr(mod, "_detect_runtime", lambda: "docker")

    def _no_docker(*a, **kw):
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", _no_docker)

    class _FakeRing:
        def __init__(self):
            self.records = deque(["line-1", "line-2", "line-3"], maxlen=100)

    monkeypatch.setattr(client_mod, "_ring_log_handler", _FakeRing())
    lines = mod._read_log_lines("docker", 10)
    assert lines == ["line-1", "line-2", "line-3"]


def test_api_bugreport_uses_issue_worthy_logs_in_summary(client, monkeypatch):
    import sendspin_bridge.application.diagnostics as api_status_mod

    monkeypatch.setattr(
        api_status_mod,
        "diagnostics",
        lambda **_kw: (
            {
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

    resp = client.get("/api/v1/diagnostics/bug-report")

    assert resp.status_code == 200
    data = resp.json()
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
    import sendspin_bridge.application.diagnostics as api_status_mod

    monkeypatch.setattr(
        api_status_mod,
        "diagnostics",
        lambda **_kw: (
            {
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

    resp = client.get("/api/v1/diagnostics/bug-report")

    assert resp.status_code == 200
    data = resp.json()
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
    import sendspin_bridge.application.diagnostics as api_status_mod

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
    import sendspin_bridge.application.config as api_config_mod
    from sendspin_bridge.config import CONFIG_SCHEMA_VERSION
    from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION

    monkeypatch.setattr(
        api_config_mod,
        "get_runtime_dependency_versions",
        lambda: {"sendspin": "5.3.1", "aiosendspin": "4.3.0", "av": "15.0.0"},
    )

    resp = client.get("/api/v1/diagnostics/version")

    assert resp.status_code == 200
    data = resp.json()
    assert data["dependencies"]["sendspin"] == "5.3.1"
    assert data["dependencies"]["aiosendspin"] == "4.3.0"
    assert data["config_schema_version"] == CONFIG_SCHEMA_VERSION
    assert data["ipc_protocol_version"] == IPC_PROTOCOL_VERSION


def test_api_config_get_includes_security_and_monitor_defaults(client):
    """GET /api/v1/config returns merged defaults for new security and MA monitor settings."""
    resp = client.get("/api/v1/config")
    assert resp.status_code == 200
    data = resp.json()
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
    import sendspin_bridge.application.config as api_config_mod

    monkeypatch.setattr(api_config_mod, "_detect_runtime", lambda: "ha_addon")
    monkeypatch.setattr(api_config_mod, "resolve_web_port", lambda: 8081)
    monkeypatch.setattr(api_config_mod, "resolve_base_listen_port", lambda: 9028)
    monkeypatch.setattr(api_config_mod, "detect_ha_addon_channel", lambda: "rc")
    monkeypatch.setattr(api_config_mod, "load_config", lambda: {"WEB_PORT": 18080, "BASE_LISTEN_PORT": 19000})

    resp = client.get("/api/v1/config")

    assert resp.status_code == 200
    data = resp.json()
    assert data["WEB_PORT"] is None
    assert data["_effective_web_port"] == 8081
    assert data["_effective_base_listen_port"] == 9028
    assert data["_delivery_channel"] == "rc"


def test_api_config_get_enriches_devices_from_registry_snapshot(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod
    from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

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

    resp = client.get("/api/v1/config")

    assert resp.status_code == 200
    data = resp.json()
    assert data["BLUETOOTH_DEVICES"][0]["listen_port"] == 8930
    assert data["BLUETOOTH_DEVICES"][0]["listen_host"] == "bridge.local"


def test_api_config_get_uses_snapshot_ip_address_for_listen_host_fallback(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod
    from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

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

    resp = client.get("/api/v1/config")

    assert resp.status_code == 200
    data = resp.json()
    assert data["BLUETOOTH_DEVICES"][0]["listen_host"] == "192.168.10.20"


def test_api_config_post_accepts_security_and_monitor_settings(client, tmp_path, monkeypatch):
    """PUT /api/v1/config persists new security and MA monitor settings."""
    import sendspin_bridge.application.config as api_config_mod

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
        "SMOOTH_RESTART": True,
        "UPDATE_CHANNEL": "beta",
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }
    resp = client.put(
        "/api/v1/config",
        json=payload,
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
    import sendspin_bridge.application.config as api_config_mod

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
        "SMOOTH_RESTART": True,
        "UPDATE_CHANNEL": "beta",
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.put("/api/v1/config", json=payload)

    assert resp.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["WEB_PORT"] is None
    assert saved["UPDATE_CHANNEL"] == "rc"


def test_api_config_post_normalizes_numeric_strings(client, tmp_path, monkeypatch):
    """PUT /api/v1/config should coerce known numeric fields to ints before saving."""
    import sendspin_bridge.application.config as api_config_mod

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
        "SMOOTH_RESTART": True,
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.put("/api/v1/config", json=payload)

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
    from sendspin_bridge.config import CONFIG_SCHEMA_VERSION

    assert saved["CONFIG_SCHEMA_VERSION"] == CONFIG_SCHEMA_VERSION
    assert saved["BLUETOOTH_DEVICES"][0]["listen_port"] == 8930
    assert saved["BLUETOOTH_DEVICES"][0]["keepalive_interval"] == 60
    assert saved["BLUETOOTH_DEVICES"][0]["room_name"] == "Living Room"
    assert saved["BLUETOOTH_DEVICES"][0]["room_id"] == "living-room"


def test_api_config_post_accepts_empty_manual_port_overrides(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod

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
        "SMOOTH_RESTART": True,
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.put("/api/v1/config", json=payload)

    assert resp.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["WEB_PORT"] is None
    assert saved["BASE_LISTEN_PORT"] is None


def test_config_post_preserves_zero_reconnect_threshold(client, tmp_path, monkeypatch):
    """#332: saving BT_MAX_RECONNECT_FAILS=0 from the settings form must stick.

    The one-shot 0→5 migration must not re-fire just because the form payload
    omits CONFIG_SCHEMA_VERSION — the handler carries the on-disk schema version
    into the payload so migration sees the already-upgraded config.
    """
    import sendspin_bridge.application.config as api_config_mod
    from sendspin_bridge.config import CONFIG_SCHEMA_VERSION

    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", cfg_file)
    # Existing config is already schema-stamped (migration long since ran).
    cfg_file.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": CONFIG_SCHEMA_VERSION,
                "BT_MAX_RECONNECT_FAILS": 5,
                "BLUETOOTH_DEVICES": [],
            }
        )
    )
    payload = {
        "SENDSPIN_SERVER": "auto",
        "BLUETOOTH_DEVICES": [],
        "BLUETOOTH_ADAPTERS": [],
        "TZ": "UTC",
        "BT_MAX_RECONNECT_FAILS": 0,  # operator opts out of auto-disable
        "AUTH_ENABLED": False,
    }
    resp = client.put("/api/v1/config", json=payload)

    assert resp.status_code == 200
    saved = json.loads(cfg_file.read_text())
    assert saved["BT_MAX_RECONNECT_FAILS"] == 0
    assert saved["CONFIG_SCHEMA_VERSION"] == CONFIG_SCHEMA_VERSION


def test_sync_ha_options_omits_manual_ports_when_unset(monkeypatch):
    import sendspin_bridge.application.config as api_config_mod

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
    import sendspin_bridge.application.config as api_config_mod

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
    import sendspin_bridge.application.config as api_config_mod

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
        "/api/v1/ha-integration/areas",
        json={
            "ha_token": "token",
            "include_devices": True,
            "adapters": [{"id": "hci0", "mac": "AA:BB:CC:DD:EE:FF"}],
        },
    )

    assert resp.status_code == 200
    payload = resp.json()
    assert payload["bridge_name_suggestions"][0]["value"] == "Living Room"
    assert payload["adapter_matches"][0]["matched_area_id"] == "living-room"


def test_api_ha_areas_returns_helper_error(client, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod
    from sendspin_bridge.services.ha.ha_core_api import HaCoreApiError

    monkeypatch.setattr(
        api_config_mod,
        "fetch_ha_area_catalog",
        lambda ha_token, include_devices, adapters: (_ for _ in ()).throw(HaCoreApiError("HA unavailable")),
    )

    resp = client.post(
        "/api/v1/ha-integration/areas",
        json={"ha_token": "token", "include_devices": True, "adapters": []},
    )

    assert resp.status_code == 502
    assert resp.json()["detail"] == "HA unavailable"


def test_api_config_post_persists_ha_adapter_area_map(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod

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
        "SMOOTH_RESTART": True,
        "UPDATE_CHANNEL": "stable",
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.put("/api/v1/config", json=payload)

    assert resp.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["HA_AREA_NAME_ASSIST_ENABLED"] is False
    assert saved["HA_ADAPTER_AREA_MAP"] == {"AA:BB:CC:DD:EE:FF": {"area_id": "living-room", "area_name": "Living Room"}}


def test_api_config_post_returns_structured_validation_errors(client):
    payload = {
        "BLUETOOTH_DEVICES": [
            {"mac": "AA:BB:CC:DD:EE:FF"},
            {"mac": "aa:bb:cc:dd:ee:ff"},
        ]
    }

    resp = client.put("/api/v1/config", json=payload)

    assert resp.status_code == 400
    data = resp.json()
    assert data["detail"] == "Duplicate MAC address: AA:BB:CC:DD:EE:FF"
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
        "SMOOTH_RESTART": True,
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.put("/api/v1/config", json=payload)

    assert resp.status_code == 400
    data = resp.json()
    assert data["detail"].startswith("Duplicate effective listen_port 8928")
    assert data["errors"][0]["field"] == "BLUETOOTH_DEVICES[1].listen_port"


def test_api_config_post_uses_registry_snapshot_for_adapter_removal(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod
    from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

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
        "SMOOTH_RESTART": True,
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.put("/api/v1/config", json=payload)

    assert resp.status_code == 200
    assert removed == [("AA:BB:CC:DD:EE:FF", "C0:FB:F9:62:D6:9D")]


def test_api_config_post_does_not_remove_device_for_default_adapter_equivalence(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod

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
        "SMOOTH_RESTART": True,
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.put("/api/v1/config", json=payload)

    assert resp.status_code == 200
    assert removed == []


def test_api_config_post_prunes_last_volumes_for_removed_devices(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod

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
        "SMOOTH_RESTART": True,
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.put("/api/v1/config", json=payload)

    assert resp.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["BLUETOOTH_DEVICES"][0]["mac"] == "AA:BB:CC:DD:EE:FF"
    assert saved["LAST_VOLUMES"] == {"AA:BB:CC:DD:EE:FF": 60}


def test_api_config_post_marks_only_unconfigured_new_device_for_initial_delay(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod

    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps(
            {
                "BLUETOOTH_DEVICES": [
                    {
                        "mac": "AA:BB:CC:DD:EE:FF",
                        "player_name": "Existing",
                        "static_delay_ms": 210,
                        "static_delay_source": "manual",
                    }
                ]
            }
        )
    )
    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", config_file)
    monkeypatch.setattr(api_config_mod, "_sync_ha_options", lambda _config: None)
    payload = {
        "BLUETOOTH_DEVICES": [
            {
                "mac": "AA:BB:CC:DD:EE:FF",
                "player_name": "Existing",
                "static_delay_ms": 210,
                "static_delay_source": "manual",
            },
            {"mac": "11:22:33:44:55:66", "player_name": "Automatic"},
            {"mac": "22:33:44:55:66:77", "player_name": "Explicit", "static_delay_ms": 90},
        ]
    }

    response = client.put("/api/v1/config", json=payload)

    assert response.status_code == 200
    devices = {device["mac"]: device for device in json.loads(config_file.read_text())["BLUETOOTH_DEVICES"]}
    assert devices["AA:BB:CC:DD:EE:FF"]["static_delay_source"] == "manual"
    assert devices["11:22:33:44:55:66"]["static_delay_ms"] == 0
    assert devices["11:22:33:44:55:66"]["static_delay_source"] == "auto_pending"
    assert devices["22:33:44:55:66:77"]["static_delay_ms"] == 90
    assert devices["22:33:44:55:66:77"]["static_delay_source"] == "manual"


def test_api_config_post_returns_validation_warnings(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod

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
        "SMOOTH_RESTART": True,
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.put("/api/v1/config", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    assert data["warnings"][0]["field"] == "CONFIG_SCHEMA_VERSION"


def test_api_config_post_includes_ma_duplicate_warning(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod
    from sendspin_bridge.config import _player_id_from_mac

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
        "SMOOTH_RESTART": True,
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.put("/api/v1/config", json=payload)

    assert resp.status_code == 200
    data = resp.json()
    messages = [warning["message"] for warning in data["warnings"]]
    assert any("may belong to another bridge" in message for message in messages)


def test_api_config_post_preserves_ma_token_metadata(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod

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
        "SMOOTH_RESTART": True,
        "AUTO_UPDATE": False,
        "CHECK_UPDATES": True,
    }

    resp = client.put("/api/v1/config", json=payload)

    assert resp.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["MA_TOKEN_INSTANCE_HOSTNAME"] == "bridge-host"
    assert saved["MA_TOKEN_LABEL"] == "Sendspin BT Bridge (bridge-host)"


def test_config_upload_includes_ma_duplicate_warning(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod
    from sendspin_bridge.config import _player_id_from_mac

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({}))
    mac = "AA:BB:CC:DD:EE:FF"
    monkeypatch.setattr(
        api_config_mod,
        "fetch_all_players_snapshot",
        lambda ma_url, ma_token: [{"player_id": _player_id_from_mac(mac), "display_name": "Kitchen @ Other Bridge"}],
    )

    resp = client.post(
        "/api/v1/config/import",
        files={
            "file": (
                "config.json",
                io.BytesIO(
                    json.dumps(
                        {
                            "MA_API_URL": "http://ma:8095",
                            "MA_API_TOKEN": "token",
                            "BLUETOOTH_DEVICES": [{"mac": mac}],
                        }
                    ).encode()
                ),
            )
        },
    )

    assert resp.status_code == 200
    data = resp.json()
    messages = [warning["message"] for warning in data["warnings"]]
    assert any("may belong to another bridge" in message for message in messages)


def test_config_upload_preserves_ma_token_metadata(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod

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
        "/api/v1/config/import",
        files={
            "file": (
                "config.json",
                io.BytesIO(
                    json.dumps(
                        {
                            "MA_API_URL": "http://ma:8095",
                            "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}],
                        }
                    ).encode()
                ),
            )
        },
    )

    assert resp.status_code == 200
    saved = json.loads((tmp_path / "config.json").read_text())
    assert saved["MA_TOKEN_INSTANCE_HOSTNAME"] == "bridge-host"
    assert saved["MA_TOKEN_LABEL"] == "Sendspin BT Bridge (bridge-host)"


def test_api_config_download_redacts_sensitive_tokens(client, tmp_path, monkeypatch):
    """GET /api/v1/config/export must not leak secrets in the exported JSON."""
    import sendspin_bridge.application.config as api_config_mod

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

    resp = client.get("/api/v1/config/export")
    assert resp.status_code == 200
    exported = json.loads(resp.text)
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
    import sendspin_bridge.application.config as api_config_mod

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

    resp = client.get("/api/v1/config")

    assert resp.status_code == 200
    data = resp.json()
    assert data["_password_set"] is True
    assert "AUTH_PASSWORD_HASH" not in data
    assert "SECRET_KEY" not in data
    assert "MA_ACCESS_TOKEN" not in data
    assert "MA_REFRESH_TOKEN" not in data


def test_api_config_download_returns_error_for_invalid_json(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.config as api_config_mod

    monkeypatch.setattr(api_config_mod, "CONFIG_FILE", tmp_path / "config.json")
    (tmp_path / "config.json").write_text("{bad")

    resp = client.get("/api/v1/config/export")

    assert resp.status_code == 500
    assert resp.json()["detail"] == "Could not read config file"


def test_error_response_no_leak(client):
    """Error responses must not expose Python tracebacks or file paths."""
    # Trigger a volume error with an impossible scenario — no clients available
    resp = client.put("/api/v1/devices/ghost/volume", json={"level": 50})
    body = resp.text
    # Must not contain Python traceback markers or filesystem paths
    assert "Traceback" not in body
    assert 'File "/' not in body
    assert '.py"' not in body


# ---------------------------------------------------------------------------
# Disabled devices
# ---------------------------------------------------------------------------


def test_api_bridge_telemetry_includes_resource_and_hook_data(client, monkeypatch):
    import sendspin_bridge.application.diagnostics as api_status
    from sendspin_bridge.services.diagnostics.event_hooks import EventHookRegistry, get_event_hook_registry

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
        resp = client.get("/api/v1/bridge/telemetry")
        assert resp.status_code == 200
        data = resp.json()
        assert data["bridge"]["process_rss_mb"] == 42.5
        assert data["subprocesses"][0]["process_rss_mb"] == 12.3
        assert data["event_hooks"]["summary"]["registered_hooks"] == 1
    finally:
        registry.clear()


def test_onboarding_assistant_endpoint_returns_guidance(client, monkeypatch):
    import sendspin_bridge.application.diagnostics as api_status
    from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

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

    resp = client.get("/api/v1/diagnostics/onboarding")

    assert resp.status_code == 200
    data = resp.json()
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
    import sendspin_bridge.application.diagnostics as api_status

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

    resp = client.get("/api/v1/diagnostics/recovery")

    assert resp.status_code == 200
    data = resp.json()
    assert data["summary"]["headline"] == "Kitchen is disconnected"
    assert data["issues"][0]["primary_action"]["key"] == "reconnect_device"
    assert data["issues"][0]["recommended_action"]["key"] == "reconnect_device"
    assert data["issues"][0]["secondary_actions"][0]["key"] == "open_diagnostics"
    assert data["safe_actions"][0]["key"] == "refresh_diagnostics"
    assert data["timeline"]["summary"]["entry_count"] == 1


def test_rerun_safe_check_endpoint_returns_runner_payload(client, monkeypatch):
    import sendspin_bridge.application.diagnostics as api_status

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

    resp = client.post("/api/v1/diagnostics/checks/sink_verification/run", json={"device_names": ["Kitchen"]})

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["check_key"] == "sink_verification"
    assert data["device_results"][0]["device_name"] == "Kitchen"


def test_recovery_timeline_download_returns_csv(client, monkeypatch):
    import sendspin_bridge.application.diagnostics as api_status

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

    resp = client.get("/api/v1/diagnostics/timeline.csv")

    assert resp.status_code == 200
    assert resp.headers["Content-Type"].startswith("text/csv")
    assert "Kitchen" in resp.text
    assert "sink_missing" in resp.text


def test_latency_apply_persists_config(client, tmp_path, monkeypatch):
    import sendspin_bridge.application.diagnostics as api_status
    import sendspin_bridge.config as config

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

    resp = client.post("/api/v1/latency/recommendations/apply", json={"pulse_latency_msec": 600})

    assert resp.status_code == 200
    data = resp.json()
    assert data["restart_required"] is True
    assert data["pulse_latency_msec"] == 600


def test_operator_guidance_endpoint_returns_unified_payload(client, monkeypatch):
    import sendspin_bridge.application.diagnostics as api_status

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

    resp = client.get("/api/v1/diagnostics/guidance")

    assert resp.status_code == 200
    data = resp.json()
    assert data["mode"] == "attention"
    assert data["banner"]["primary_action"]["key"] == "reconnect_devices"
    assert data["issue_groups"][0]["count"] == 2


def test_api_status_parse_helpers_are_defensive():
    """Diagnostics parsers must return None instead of raising on malformed input."""
    from sendspin_bridge.application.diagnostics import (
        _parse_audio_server_name,
        _parse_memtotal_mb,
        _parse_sink_input_id,
    )

    assert _parse_sink_input_id("Sink Input #42") == "42"
    assert _parse_sink_input_id("Sink Input") is None

    assert _parse_audio_server_name("Server Name: PulseAudio") == "PulseAudio"
    assert _parse_audio_server_name("Server Name") is None

    assert _parse_memtotal_mb("MemTotal: 2048000 kB") == 2000
    assert _parse_memtotal_mb("MemTotal:") is None
    assert _parse_memtotal_mb("MemTotal: nope kB") is None


def test_api_diagnostics_includes_playing_and_sink_input_metadata(client, monkeypatch, installed_bluez):
    """GET /api/v1/diagnostics should expose playing state and parsed sink-input metadata."""
    import sendspin_bridge.application.diagnostics as api_status
    import sendspin_bridge.bridge.state as state
    from sendspin_bridge.config import CONFIG_SCHEMA_VERSION
    from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot
    from sendspin_bridge.services.diagnostics.event_hooks import EventHookRegistry, get_event_hook_registry
    from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION

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
        # bluetoothctl no longer flows through subprocess here — the
        # diagnostics collectors use the installed_bluez transport.
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
    # The host probe reads both from one PulseAudio session.
    monkeypatch.setattr(
        api_status,
        "get_audio_server_snapshot",
        lambda: ("pulseaudio 16.1", [{"name": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"}]),
    )
    monkeypatch.setattr(
        api_status,
        "list_cards",
        lambda: [
            {
                "name": "bluez_card.AA_BB_CC_DD_EE_FF",
                "driver": "module-bluez5-device.c",
                "active_profile": "a2dp_sink",
                "profiles": ["off", "a2dp_sink", "headset_head_unit"],
            }
        ],
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
        resp = client.get("/api/v1/diagnostics")
        assert resp.status_code == 200
        data = resp.json()
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
        assert data["collections_status"]["cards"]["status"] == "ok"
        assert data["cards"][0]["name"] == "bluez_card.AA_BB_CC_DD_EE_FF"
        assert data["cards"][0]["active_profile"] == "a2dp_sink"
        assert "headset_head_unit" in data["cards"][0]["profiles"]
    finally:
        sys.modules.pop("sendspin.audio", None)
        sys.modules.pop("sendspin.audio_devices", None)
        state.set_ma_groups({}, [])
        state.set_ma_api_credentials("", "")
        hook_registry.clear()


def test_collect_preflight_status_surfaces_audio_probe_failure(monkeypatch):
    import subprocess

    import sendspin_bridge.application.diagnostics as api_status

    # The probe reads the server name and sinks from one PulseAudio session.
    monkeypatch.setattr(
        api_status,
        "get_audio_server_snapshot",
        lambda: (_ for _ in ()).throw(subprocess.TimeoutExpired("pactl info", 5)),
    )
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


def test_api_diagnostics_reports_failed_collections_for_sink_input_timeout(client, monkeypatch, installed_bluez):
    import subprocess

    import sendspin_bridge.application.diagnostics as api_status
    import sendspin_bridge.bridge.state as state
    from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

    fake_client = SimpleNamespace(
        player_name="Kitchen",
        status={"bluetooth_connected": True, "playing": False, "server_connected": True},
        bt_management_enabled=True,
        bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
        bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
        player_id="sendspin-kitchen",
    )

    def fake_run(cmd, capture_output=True, text=True, timeout=5):
        # bluetoothctl no longer flows through subprocess here — the
        # diagnostics collectors use the installed_bluez transport.
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
    # The host probe reads both from one PulseAudio session.
    monkeypatch.setattr(
        api_status,
        "get_audio_server_snapshot",
        lambda: ("pulseaudio 16.1", [{"name": "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"}]),
    )
    monkeypatch.setattr(api_status, "list_cards", lambda: [])
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

    resp = client.get("/api/v1/diagnostics")

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "degraded"
    assert "sink_inputs" in data["failed_collections"]
    assert data["collections_status"]["sink_inputs"]["status"] == "error"
    assert data["collections_status"]["sink_inputs"]["error"]["code"] == "timeout"
    assert data["sink_inputs"][0]["error"] == "Failed to list sink inputs"


def test_device_enabled_toggle(client, tmp_path, monkeypatch):
    """Disabling a speaker that is not running is written to the config, restart required."""
    import sendspin_bridge.application.devices as devices
    import sendspin_bridge.config as _config

    # Seed config with a device; the persist helpers read the live path.
    cfg = {"BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF", "player_name": "Test", "enabled": True}]}
    (tmp_path / "config.json").write_text(json.dumps(cfg))
    monkeypatch.setattr(_config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr(devices, "_sync_ha_options_later", lambda: None)
    device_id = _config._player_id_from_mac("AA:BB:CC:DD:EE:FF")
    resp = client.patch(f"/api/v1/devices/{device_id}", json={"enabled": False})
    assert resp.status_code == 200
    data = resp.json()
    assert data["restart_required"] is True
    assert data["enabled"] is False

    # Verify config was updated
    saved = json.loads((tmp_path / "config.json").read_text())
    dev = saved["BLUETOOTH_DEVICES"][0]
    assert dev["enabled"] is False


def test_device_enabled_missing_fields(client):
    """A PATCH without ``enabled`` is refused by the contract."""
    resp = client.patch("/api/v1/devices/anything", json={})
    assert resp.status_code == 422
    assert resp.json()["code"] == "invalid_request"


# ---------------------------------------------------------------------------
#  XSS protection - the HA auth popup page
# ---------------------------------------------------------------------------


def test_ha_auth_page_escapes_xss_payload(client, monkeypatch):
    """XSS payload inside a valid URL must be safely quoted in the rendered page.

    The outer URL-safety check rejects obviously-malformed inputs; this test
    focuses on the defence-in-depth JS string escaping for values that do get
    through.
    """
    from sendspin_bridge.application.music_assistant import auth as _ma_auth

    monkeypatch.setattr(_ma_auth, "is_safe_external_url", lambda _u: True)
    resp = client.get("/api/v1/music-assistant/session/ha-auth-page?ma_url=';alert(1)//")
    assert resp.status_code == 200
    body = resp.content.decode()
    assert '"\';alert(1)//"' in body
    assert "= '';alert(1)//';" not in body


def test_ha_auth_page_rejects_javascript_scheme(client):
    """javascript: scheme in ma_url must be rejected with 400."""
    resp = client.get("/api/v1/music-assistant/session/ha-auth-page?ma_url=javascript:alert(1)")
    assert resp.status_code == 400
    body = resp.text
    assert "Invalid" in body


def test_ha_auth_page_accepts_http_url(client, monkeypatch):
    """Normal http URL should be accepted and present in the page."""
    from sendspin_bridge.application.music_assistant import auth as _ma_auth

    monkeypatch.setattr(_ma_auth, "is_safe_external_url", lambda _u: True)
    url = "http://example.com"
    resp = client.get(f"/api/v1/music-assistant/session/ha-auth-page?ma_url={url}")
    assert resp.status_code == 200
    assert url.encode() in resp.content


def test_ha_auth_page_accepts_empty_url(client):
    """Empty ma_url should be accepted."""
    resp = client.get("/api/v1/music-assistant/session/ha-auth-page?ma_url=")
    assert resp.status_code == 200
    assert resp.content  # non-empty HTML response


def test_ma_artwork_proxy_fetches_same_origin_ma_artwork(client):
    import sendspin_bridge.bridge.state as state
    from sendspin_bridge.services.music_assistant.ma_artwork import sign_artwork_url

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
            import sendspin_bridge.application.music_assistant.playback as ma_playback_mod

            opened = {}
            raw_url = "/api/image/123"
            signature = sign_artwork_url(raw_url)

            class _FakeOpener:
                def open(self, req, timeout=0):
                    opened["url"] = req.full_url
                    opened["auth"] = req.headers.get("Authorization")
                    opened["accept"] = req.headers.get("Accept")
                    opened["timeout"] = timeout
                    return _FakeResponse()

            # The proxy fetches through ``safe_build_opener`` (SSRF re-check
            # per hop + a redirect handler that strips the MA bearer token
            # when the origin changes), so the seam is the opener, not
            # ``urllib.request.urlopen``.
            mp.setattr(ma_playback_mod, "safe_build_opener", lambda *h, **kw: _FakeOpener())
            resp = client.get(f"/api/v1/music-assistant/artwork?url=%2Fapi%2Fimage%2F123&sig={signature}")

        assert resp.status_code == 200
        assert resp.content == b"jpeg-bytes"
        assert resp.headers["Content-Type"].startswith("image/jpeg")
        assert opened["url"] == "http://ma:8095/api/image/123"
        assert opened["auth"] == "Bearer token123"
        assert opened["accept"] == "image/*"
        assert opened["timeout"] == 15
    finally:
        state.set_ma_api_credentials("", "")


def test_ma_artwork_proxy_rejects_unsupported_scheme(client):
    import sendspin_bridge.bridge.state as state
    from sendspin_bridge.services.music_assistant.ma_artwork import sign_artwork_url

    state.set_ma_api_credentials("http://ma:8095", "token123")
    try:
        raw_url = "ftp://evil.example/image.jpg"
        resp = client.get(
            f"/api/v1/music-assistant/artwork?url=ftp%3A%2F%2Fevil.example%2Fimage.jpg&sig={sign_artwork_url(raw_url)}"
        )
        assert resp.status_code == 400
        assert "Unsupported artwork URL scheme" in resp.text
    finally:
        state.set_ma_api_credentials("", "")


def test_ma_artwork_proxy_rejects_unsigned_external_provider_artwork(client):
    import sendspin_bridge.bridge.state as state

    state.set_ma_api_credentials("http://ma:8095", "token123")
    try:
        resp = client.get(
            "/api/v1/music-assistant/artwork?url="
            "https%3A%2F%2Favatars.yandex.net%2Fget-music-content%2F49876%2Fab027f9c.a.37173-2%2F1000x1000"
        )
        assert resp.status_code == 400
        assert "Invalid artwork signature" in resp.text
    finally:
        state.set_ma_api_credentials("", "")


def test_ma_artwork_proxy_fetches_signed_external_provider_artwork_without_ma_token(client):
    """External (non-MA-origin) artwork URLs with valid sig are proxied without Bearer token."""
    import sendspin_bridge.bridge.state as state
    from sendspin_bridge.services.music_assistant.ma_artwork import sign_artwork_url

    raw_url = "https://avatars.yandex.net/get-music-content/49876/ab027f9c.a.37173-2/1000x1000"
    state.set_ma_api_credentials("http://ma:8095", "token123")
    try:
        fake_resp = io.BytesIO(b"\x89PNG\r\n\x1a\n")
        fake_resp.headers = {"Content-Type": "image/png", "Content-Length": "8"}
        opened_reqs = []

        class _FakeOpener:
            def open(self, req, timeout=0):
                opened_reqs.append(req)
                return fake_resp

        with patch(
            "sendspin_bridge.application.music_assistant.playback.safe_build_opener", lambda *h, **kw: _FakeOpener()
        ):
            resp = client.get(
                "/api/v1/music-assistant/artwork?url="
                "https%3A%2F%2Favatars.yandex.net%2Fget-music-content%2F49876%2Fab027f9c.a.37173-2%2F1000x1000"
                f"&sig={sign_artwork_url(raw_url)}"
            )
            assert resp.status_code == 200
            # Should NOT include Authorization header for external URLs
            called_req = opened_reqs[0]
            assert "Authorization" not in called_req.headers
    finally:
        state.set_ma_api_credentials("", "")


def test_ma_artwork_proxy_rejects_invalid_signature(client):
    import sendspin_bridge.bridge.state as state

    state.set_ma_api_credentials("http://ma:8095", "token123")
    try:
        resp = client.get("/api/v1/music-assistant/artwork?url=%2Fapi%2Fimage%2F123&sig=bad")
        assert resp.status_code == 400
        assert "Invalid artwork signature" in resp.text
    finally:
        state.set_ma_api_credentials("", "")


# ---------------------------------------------------------------------------
# Pair-time adapter quiesce (issue #168)
# ---------------------------------------------------------------------------


def test_latency_endpoint_rejects_stale_recommendation(client, monkeypatch):
    """Applying a suggestion computed for an older state of the device is refused."""
    import sendspin_bridge.application.status as status

    fake_client = SimpleNamespace(player_id="player-1", status={"latency_suggestion_revision": "new"})
    monkeypatch.setattr(status, "get_device_registry_snapshot", lambda: SimpleNamespace(active_clients=[fake_client]))

    response = client.put(
        "/api/v1/devices/player-1/latency",
        json={"value": 125, "source": "recommendation", "recommendation_revision": "old"},
    )

    assert response.status_code == 409
