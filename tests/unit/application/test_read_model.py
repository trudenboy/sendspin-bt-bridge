"""One state, one answer: every surface that reports the bridge's health agrees.

The header pill (``/status``), the guidance endpoint and the diagnostics
bundle each used to run the snapshot → state model → onboarding → recovery →
guidance chain themselves, with different inputs.  The diagnostics bundle
dropped the disabled speakers, the guidance endpoint read them from a
different list, and the operator saw "All devices disabled" above a
Diagnostics page that disagreed.  These tests hold the surfaces to one build.
"""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import sendspin_bridge.application.diagnostics as diagnostics
import sendspin_bridge.bridge.state as state
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot
from tests.support.config_v6 import v6_config

HEALTHY_HOST = {
    "status": "ok",
    "failed_collections": [],
    "collections_status": {},
    "platform": "test",
    "audio": {"system": "pulseaudio", "socket_exists": True, "socket_reachable": True, "sinks": 1, "last_error": None},
    "bluetooth": {"controller": True, "adapter": "C0:FB:F9:62:D6:9D", "paired_devices": 2, "daemon": "active"},
    "dbus": True,
    "memory_mb": 512,
    "config_writable": {"status": "ok", "writable": True, "remediation": None},
}


def _runtime_client(player_id: str, name: str, mac: str, **status_fields):
    return SimpleNamespace(
        status={
            "server_connected": True,
            "bluetooth_connected": True,
            "bluetooth_available": True,
            "bluetooth_mac": mac,
            **status_fields,
        },
        _status_lock=threading.Lock(),
        player_name=name,
        player_id=player_id,
        listen_port=8928,
        server_host="music-assistant.local",
        server_port=9000,
        static_delay_ms=0.0,
        connected_server_url="",
        bt_manager=None,
        bluetooth_sink_name="bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
        bt_management_enabled=True,
        is_running=lambda: True,
    )


@pytest.fixture
def bridge(monkeypatch, tmp_config):
    """A bridge with a pinned healthy host and a registry the test fills."""
    # Another test on this worker may have left a boot in progress. These cases describe an
    # idle, already configured bridge; resetting only after the test left its first run order-dependent.
    state.reset_startup_progress()
    state.set_disabled_devices([])
    state.set_ma_groups({}, [])
    state.set_ma_api_credentials("", "")
    state.set_runtime_mode_info(None)
    clients: list = []

    def _registry():
        return DeviceRegistrySnapshot(active_clients=list(clients), disabled_devices=state.get_disabled_devices())

    import sendspin_bridge.application.status as status_uc

    monkeypatch.setattr(status_uc, "get_device_registry_snapshot", _registry)
    monkeypatch.setattr(diagnostics, "get_device_registry_snapshot", _registry)
    monkeypatch.setattr(diagnostics, "_collect_preflight_status", lambda: dict(HEALTHY_HOST))
    diagnostics.reset_preflight_probe()
    yield clients
    diagnostics.reset_preflight_probe()
    state.set_disabled_devices([])
    state.set_ma_groups({}, [])
    state.set_ma_api_credentials("", "")
    state.reset_startup_progress()
    state.set_runtime_mode_info(None)


def _use_config(monkeypatch, config: dict) -> None:
    import sendspin_bridge.application.status as status_uc

    monkeypatch.setattr(diagnostics, "load_config", lambda: config)
    monkeypatch.setattr(status_uc, "load_config", lambda: config)


def _three_views(api_client) -> dict[str, dict]:
    status = api_client.get("/api/v1/status").json()
    guidance = api_client.get("/api/v1/diagnostics/guidance").json()
    bundle = api_client.get("/api/v1/diagnostics").json()
    return {
        "status": status["bridge"]["guidance"],
        "guidance endpoint": guidance,
        "diagnostics bundle": bundle["operator_guidance"],
    }


def _assert_agree(views: dict[str, dict]) -> None:
    headers = {name: (view.get("header_status") or {}).get("label") for name, view in views.items()}
    assert len(set(headers.values())) == 1, f"the surfaces disagree on the header: {headers}"
    groups = {name: [g.get("key") for g in view.get("issue_groups") or []] for name, view in views.items()}
    assert len({tuple(v) for v in groups.values()}) == 1, f"the surfaces disagree on the issues: {groups}"


def test_all_disabled_reads_the_same_everywhere(api_client, bridge, monkeypatch):
    disabled = [
        {"player_name": "Kitchen", "mac": "AA:AA:AA:AA:AA:01", "enabled": False},
        {"player_name": "Office", "mac": "AA:AA:AA:AA:AA:02", "enabled": False},
    ]
    _use_config(monkeypatch, v6_config(disabled, BLUETOOTH_ADAPTERS=[{"id": "hci0"}]))
    state.set_disabled_devices(disabled)

    views = _three_views(api_client)

    assert views["status"]["header_status"]["label"] == "All devices disabled"
    _assert_agree(views)


def test_one_playing_one_disabled_reads_the_same_everywhere(api_client, bridge, monkeypatch):
    devices = [
        {"player_name": "Kitchen", "mac": "AA:AA:AA:AA:AA:01"},
        {"player_name": "Office", "mac": "AA:AA:AA:AA:AA:02", "enabled": False},
    ]
    _use_config(monkeypatch, v6_config(devices, BLUETOOTH_ADAPTERS=[{"id": "hci0"}]))
    state.set_disabled_devices([devices[1]])
    bridge.append(_runtime_client("sendspin-kitchen", "Kitchen", "AA:AA:AA:AA:AA:01", playing=True))

    _assert_agree(_three_views(api_client))


def test_the_bundle_verdict_is_the_header_verdict(api_client, bridge, monkeypatch):
    """With every collection healthy, the bundle's status is the bridge verdict, renamed."""
    disabled = [{"player_name": "Kitchen", "mac": "AA:AA:AA:AA:AA:01", "enabled": False}]
    _use_config(monkeypatch, v6_config(disabled, BLUETOOTH_ADAPTERS=[{"id": "hci0"}]))
    state.set_disabled_devices(disabled)

    from sendspin_bridge.application import read_model

    view = read_model.build(read_model.inputs_now())
    verdict = api_client.get("/api/v1/status").json()["bridge"]["verdict"]

    assert (view.verdict.level, view.verdict.count) == (verdict["level"], verdict["count"])
