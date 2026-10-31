"""``/hardware`` — the aggregate read and the two detail routes."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from sendspin_bridge.services.hardware import inventory
from tests.support.api_client import make_client

ADAPTER = "C0:FB:F9:62:D7:D6"
DEVICE = "6C:5C:3D:35:17:99"
UNKNOWN = "AA:BB:CC:DD:EE:FF"

INFO = (
    f"Device {DEVICE} (public)\n"
    "\tName: ENEBY Portable\n"
    "\tAlias: ENEBY Portable\n"
    "\tPaired: yes\n"
    "\tBonded: yes\n"
    "\tTrusted: yes\n"
    "\tBlocked: no\n"
    "\tConnected: no\n"
    "\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)\n"
    "\tModalias: bluetooth:v0059p0001d0001\n"
)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch, tmp_config):
    inventory.clear_cache()
    monkeypatch.setattr(inventory, "probe_audio_server", lambda: "PulseAudio (on PipeWire 1.0.5)")
    monkeypatch.setattr(inventory, "probe_capture", lambda: True)
    tmp_config.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {
                        "id": "eneby",
                        "name": "ENEBY",
                        "backend": {"type": "bluetooth_a2dp", "mac": DEVICE, "adapter": "hci0"},
                    }
                ],
                "HA_ADAPTER_AREA_MAP": {ADAPTER: {"area_id": "living", "area_name": "Living room"}},
            }
        )
    )
    yield
    inventory.clear_cache()


def test_snapshot_lists_controllers_and_devices(installed_bluez):
    installed_bluez.on(lambda text: f"info {DEVICE}" in text, stdout=INFO)

    response = make_client().get("/api/v1/hardware")

    assert response.status_code == 200
    body = response.json()
    assert body["stale"] is False
    assert body["stale_after_s"] == 2
    assert datetime.fromisoformat(body["as_of"]).tzinfo is not None
    assert body["capabilities"] == {
        "bluetooth": True,
        "dbus": False,
        "audio_server": "pipewire",
        "capture": True,
        "default_sink": True,
        "reasons": {"dbus": "dbus_unavailable"},
    }
    controller = body["controllers"][0]
    assert controller["id"] == "hci0"
    assert controller["id_synthetic"] is True
    assert controller["area"] == {"id": "living", "name": "Living room"}
    assert controller["device_count"] == 2
    by_mac = {device["mac"]: device for device in body["bluetooth_devices"]}
    assert set(by_mac) == {DEVICE, "30:21:0E:0A:AE:5A"}
    assert by_mac[DEVICE]["player_id"] == "eneby"
    assert by_mac[DEVICE]["source_phone"] is False
    assert by_mac[DEVICE]["roles"] == []


def test_a_degraded_section_is_a_200_with_a_code(installed_bluez):
    response = make_client().get("/api/v1/hardware")

    assert response.status_code == 200
    degraded = response.json()["degraded"]
    assert {"section": "controllers", "code": "dbus_unavailable"}.items() <= next(
        item for item in degraded if item["section"] == "controllers"
    ).items()


def test_no_audio_server_degrades_the_audio_section(installed_bluez, monkeypatch):
    monkeypatch.setattr(inventory, "probe_audio_server", lambda: "not available")

    body = make_client().get("/api/v1/hardware").json()

    assert body["capabilities"]["audio_server"] == "none"
    assert body["capabilities"]["default_sink"] is False
    assert any(section["section"] == "audio" and section["code"] == "pulse_unavailable" for section in body["degraded"])


def test_unknown_controller_is_entity_not_found(installed_bluez):
    response = make_client().get("/api/v1/hardware/controllers/hci9")

    assert response.status_code == 404
    assert response.json()["code"] == "entity_not_found"


def test_unknown_bluetooth_device_is_entity_not_found(installed_bluez):
    response = make_client().get(f"/api/v1/hardware/bluetooth/{UNKNOWN}")

    assert response.status_code == 404
    assert response.json()["code"] == "entity_not_found"


def test_a_host_with_no_bluetooth_stack_is_hardware_unavailable(installed_bluez):
    installed_bluez.fail("list")
    installed_bluez.fail("--version")

    response = make_client().get("/api/v1/hardware")

    assert response.status_code == 503
    assert response.json()["code"] == "hardware_unavailable"


def test_controller_detail_lists_devices(installed_bluez):
    response = make_client().get("/api/v1/hardware/controllers/hci0")

    assert response.status_code == 200
    body = response.json()
    assert body["address_forms"] == {
        "colons": ADAPTER,
        "underscores": "C0_FB_F9_62_D7_D6",
        "bare": "C0FBF962D7D6",
    }
    assert {device["mac"] for device in body["devices"]} == {DEVICE, "30:21:0E:0A:AE:5A"}


def test_bluetooth_detail_has_uuids_and_manufacturer(installed_bluez):
    installed_bluez.on(lambda text: f"info {DEVICE}" in text, stdout=INFO)

    response = make_client().get(f"/api/v1/hardware/bluetooth/{DEVICE.lower()}")

    assert response.status_code == 200
    body = response.json()
    assert body["mac"] == DEVICE
    assert body["uuids"] == ["0000110b-0000-1000-8000-00805f9b34fb"]
    assert body["manufacturer"] == "Nordic Semiconductor"
    assert body["bonds"] == [ADAPTER]
