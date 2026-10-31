"""DEMO_MODE feeds the hardware page from the demo fixtures, not the host."""

from __future__ import annotations

import pytest

from demo.fixtures import DEMO_ADAPTERS, DEMO_BT_DEVICE_INFO
from sendspin_bridge.services.hardware import inventory
from tests.support.api_client import make_client


@pytest.fixture(autouse=True)
def _fresh_inventory(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    from sendspin_bridge.services.audio import pulse

    originals = {
        (pulse, name): getattr(pulse, name)
        for name in (
            "list_sinks",
            "get_sink_description",
            "get_server_name",
            "set_sink_volume",
            "get_sink_volume",
            "set_sink_mute",
            "get_sink_mute",
            "suspend_sink",
            "set_default_sink",
        )
    }
    original_refresh = inventory._refresh
    inventory.clear_cache()
    yield
    inventory._refresh = original_refresh
    for (module, name), value in originals.items():
        setattr(module, name, value)
    inventory.clear_cache()


def test_demo_mode_lists_the_controllers_and_paired_devices_the_page_needs():
    from demo import install_inventory

    install_inventory()

    response = make_client().get("/api/v1/hardware")

    assert response.status_code == 200
    body = response.json()
    assert {controller["mac"] for controller in body["controllers"]} == {adapter["mac"] for adapter in DEMO_ADAPTERS}
    assert {device["mac"] for device in body["bluetooth_devices"]} == {
        str(device["mac"]) for device in DEMO_BT_DEVICE_INFO
    }
    assert body["capabilities"]["bluetooth"] is True
    assert body["capabilities"]["dbus"] is True
    assert body["degraded"] == []


def test_demo_mode_shows_audio_and_a_command_round_trips():
    from demo import install_inventory

    install_inventory()
    client = make_client()

    audio = client.get("/api/v1/hardware").json()["audio"]
    assert len(audio["sinks"]) >= 2
    assert len(audio["sources"]) >= 1
    card = next(item for item in audio["cards"] if item["profile_count"] and item["port_count"])
    detail = client.get(f"/api/v1/hardware/audio/cards/{card['name']}").json()
    assert detail["profiles"] and detail["ports"]

    sink = audio["sinks"][0]
    response = client.put(f"/api/v1/audio/sinks/{sink['name']}/volume", json={"volume": 23})

    assert response.status_code == 200
    assert response.json()["volume"] == 23

    flipped = not sink["muted"]
    muted = client.put(f"/api/v1/audio/sinks/{sink['name']}/mute", json={"muted": flipped})
    suspended = client.post(f"/api/v1/audio/sinks/{sink['name']}/suspend")

    assert muted.status_code == 200 and muted.json()["muted"] is flipped
    assert suspended.status_code == 200 and suspended.json()["state"] == "suspended"

    refreshed = client.get("/api/v1/hardware").json()["audio"]
    stored = next(item for item in refreshed["sinks"] if item["name"] == sink["name"])
    assert stored["volume"] == 23
    assert stored["muted"] is flipped
    assert stored["state"] == "suspended"
