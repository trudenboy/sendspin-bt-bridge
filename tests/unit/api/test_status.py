"""``GET /status`` and its parts: the dashboard's one read."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

import sendspin_bridge.application.diagnostics as diagnostics
import sendspin_bridge.bridge.state as state
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot


def _runtime_client(player_id: str, name: str, **status_fields):
    return SimpleNamespace(
        status={"server_connected": True, "bluetooth_connected": True, "bluetooth_available": True, **status_fields},
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
def registry(monkeypatch, tmp_config):
    clients: list = []
    snapshot = lambda: DeviceRegistrySnapshot(active_clients=clients)  # noqa: E731
    import sendspin_bridge.application.status as status_uc

    monkeypatch.setattr(status_uc, "get_device_registry_snapshot", snapshot)
    monkeypatch.setattr(diagnostics, "get_device_registry_snapshot", snapshot)
    yield clients
    state.set_disabled_devices([])
    state.set_ma_groups({}, [])
    state.set_ma_api_credentials("", "")
    state.clear_device_events("sendspin-kitchen")
    state.reset_startup_progress()
    state.set_runtime_mode_info(None)


def test_status_has_bridge_devices_and_groups(api_client, registry):
    registry.append(_runtime_client("sendspin-kitchen", "Kitchen", playing=True))

    data = api_client.get("/api/v1/status").json()

    assert set(data) == {"bridge", "devices", "groups"}
    assert data["bridge"]["device_count"] == 1
    device = data["devices"][0]
    assert (device["id"], device["name"]) == ("sendspin-kitchen", "Kitchen")
    assert device["playback"]["playing"] is True
    assert device["audio"]["sink_name"] == "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"
    assert api_client.get("/api/v1/devices").json() == data["devices"]
    assert api_client.get("/api/v1/devices/sendspin-kitchen").json() == device
    bridge = api_client.get("/api/v1/bridge").json()
    assert (bridge["version"], bridge["device_count"]) == (data["bridge"]["version"], 1)


def test_disabled_devices_are_listed_on_the_bridge(api_client, registry):
    state.set_disabled_devices([{"player_name": "Off Speaker", "mac": "AA:BB:CC:DD:EE:FF", "enabled": False}])
    disabled = api_client.get("/api/v1/status").json()["bridge"]["disabled_devices"]
    assert [d["player_name"] for d in disabled] == ["Off Speaker"]


def test_all_devices_disabled_gets_its_own_neutral_header(api_client, registry, monkeypatch):
    monkeypatch.setattr(
        diagnostics,
        "load_config",
        lambda: {
            "BLUETOOTH_ADAPTERS": [{"id": "hci0"}],
            "BLUETOOTH_DEVICES": [
                {"player_name": "Kitchen", "mac": "AA", "enabled": False},
                {"player_name": "Office", "mac": "BB", "enabled": False},
            ],
        },
    )
    import sendspin_bridge.application.status as status_uc

    monkeypatch.setattr(status_uc, "load_config", diagnostics.load_config)
    # The onboarding checks probe the host's Bluetooth and audio; pin them
    # healthy so the header depends only on the disabled devices.
    monkeypatch.setattr(
        diagnostics,
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

    guidance = api_client.get("/api/v1/status").json()["bridge"]["guidance"]

    assert guidance["mode"] == "healthy"
    assert guidance["header_status"]["label"] == "All devices disabled"


def test_a_grouped_device_carries_its_ma_syncgroup(api_client, registry):
    registry.append(_runtime_client("sendspin-yandex", "Yandex", group_id="8e0f23da-3db6-4cc2-902b-cc61241ecf02"))
    state.set_ma_groups(
        {"sendspin-yandex": {"id": "syncgroup_5zr8ss8g", "name": "Semdspin BT"}},
        [{"id": "syncgroup_5zr8ss8g", "name": "Semdspin BT", "members": []}],
    )
    state.set_ma_api_credentials("http://192.168.10.10:8095", "token")

    device = api_client.get("/api/v1/status").json()["devices"][0]

    assert device["music_assistant"]["syncgroup_id"] == "syncgroup_5zr8ss8g"
    assert device["playback"]["group"]["name"] == "Semdspin BT"


def test_health_events_and_capabilities_are_per_device(api_client, registry):
    registry.append(
        _runtime_client(
            "sendspin-kitchen",
            "Kitchen",
            playing=True,
            audio_streaming=False,
            last_error="Route degraded",
            last_error_at="2026-03-18T00:00:00+00:00",
        )
    )
    state.clear_device_events("sendspin-kitchen")
    state.record_device_event("sendspin-kitchen", "runtime-error", level="error", message="Route degraded")

    device = api_client.get("/api/v1/status").json()["devices"][0]

    assert device["health"]["state"] == "degraded"
    assert device["health"]["severity"] == "error"
    assert device["recent_events"][0]["event_type"] == "runtime-error"
    assert device["capabilities"]["domains"]["playback"]["currently_available"] is True
    queue = device["capabilities"]["actions"]["queue_control"]
    assert queue["currently_available"] is False
    assert queue["blocked_reason"] == "Music Assistant API is not connected."


def test_startup_progress_is_its_own_resource_and_part_of_the_bridge(api_client, registry):
    state.reset_startup_progress(4, message="Booting")
    state.update_startup_progress("web", "Starting web interface", current_step=3, details={"active_clients": 2})

    progress = api_client.get("/api/v1/bridge/startup").json()
    assert (progress["phase"], progress["percent"], progress["details"]["active_clients"]) == ("web", 75, 2)

    startup = api_client.get("/api/v1/status").json()["bridge"]["startup"]
    assert (startup["phase"], startup["percent"]) == ("web", 75)


def test_guidance_state_model_and_assistants_ride_along(api_client, registry):
    bridge = api_client.get("/api/v1/status").json()["bridge"]
    assert bridge["guidance"]["visibility_keys"]["onboarding"] == "sendspin-ui:show-onboarding-guidance"
    assert "header_status" in bridge["guidance"]
    assert "runtime_substrate" in bridge["state_model"]
    assert "configuration" in bridge["state_model"]
    assert isinstance(bridge["state_model"]["devices"], list)
    assert bridge["preflight"] is not None
    assert bridge["onboarding"] is not None
    assert bridge["recovery"] is not None


def test_a_mocked_runtime_says_so(api_client, registry):
    state.set_runtime_mode_info(
        {
            "mode": "demo",
            "is_mocked": True,
            "simulator_active": True,
            "fixture_devices": 3,
            "mocked_layers": [{"layer": "Music Assistant", "summary": "Fixture-backed"}],
        }
    )

    runtime = api_client.get("/api/v1/bridge/runtime").json()
    assert (runtime["mode"], runtime["is_mocked"]) == ("demo", True)
    assert runtime["mocked_layers"][0]["layer"] == "Music Assistant"

    bridge = api_client.get("/api/v1/status").json()["bridge"]
    assert bridge["runtime_mode"] == "demo"
    assert bridge["mock_runtime"]["fixture_devices"] == 3
