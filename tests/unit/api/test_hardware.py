"""``/hardware`` — the aggregate read and the two detail routes."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime

import pytest

from sendspin_bridge.services.hardware import inventory
from sendspin_bridge.services.source import state as source_state
from tests.support import hardware_audio
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
    monkeypatch.setattr(inventory, "probe_capture", lambda _name=None: True)
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


@pytest.fixture(autouse=True)
def _fresh_source_state():
    source_state.reset()
    yield
    source_state.reset()


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
        "default_sink": False,
        "reasons": {"dbus": "dbus_unavailable", "default_sink": "pulse_unavailable"},
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


# ---------------------------------------------------------------------------
# The audio half
# ---------------------------------------------------------------------------


def _use_audio(monkeypatch) -> None:
    async def fake_audio(server_name: str | None = None):
        del server_name
        return hardware_audio.facts()

    monkeypatch.setattr(inventory, "_read_audio_async", fake_audio)


def test_snapshot_has_the_audio_section_with_owner_shapes(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    body = make_client().get("/api/v1/hardware").json()

    audio = body["audio"]
    assert {card["name"] for card in audio["cards"]} == {
        hardware_audio.ANALOG_CARD,
        hardware_audio.BLUEZ_CARD,
        hardware_audio.USB_CARD,
    }
    by_name = {sink["name"]: sink for sink in audio["sinks"]}
    assert by_name[hardware_audio.ANALOG_SINK]["owner"] == {
        "card": hardware_audio.ANALOG_CARD,
        "bluetooth": None,
        "internal": None,
    }
    assert by_name[hardware_audio.BLUEZ_SINK]["owner"] == {
        "card": None,
        "bluetooth": hardware_audio.SPEAKER_MAC,
        "internal": None,
    }
    assert by_name[hardware_audio.INTERNAL_SINK]["owner"] == {
        "card": None,
        "bluetooth": None,
        "internal": True,
    }
    by_name = {source["name"]: source for source in audio["sources"]}
    assert by_name[hardware_audio.BLUEZ_SOURCE]["owner"]["bluetooth"] == hardware_audio.SPEAKER_MAC


def test_audio_card_route_returns_profiles_ports_and_endpoints(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    response = make_client().get(f"/api/v1/hardware/audio/cards/{hardware_audio.ANALOG_CARD}")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == hardware_audio.ANALOG_CARD
    assert next(profile["name"] for profile in body["profiles"]) == "output:analog-stereo"
    assert next(port for port in body["ports"] if port["name"] == "analog-output-headphones")["available"] is False
    assert {endpoint["name"] for endpoint in body["endpoints"]} == {
        hardware_audio.ANALOG_SINK,
        hardware_audio.ANALOG_SOURCE,
        hardware_audio.MONITOR_SOURCE,
    }


def test_audio_sink_route_returns_identifiers_and_card_ports(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    response = make_client().get(f"/api/v1/hardware/audio/sinks/{hardware_audio.ANALOG_SINK}")

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "sink"
    assert body["identifiers"]["bus_path"] == "pci-0000:00:1f.3"
    assert body["owner"]["card"] == hardware_audio.ANALOG_CARD
    assert {port["name"] for port in body["ports"]} == {
        "analog-output-speaker",
        "analog-output-headphones",
        "analog-input-mic",
    }


def test_audio_source_route_names_the_monitored_sink(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    response = make_client().get(f"/api/v1/hardware/audio/sources/{hardware_audio.MONITOR_SOURCE}")

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "source"
    assert body["monitor_of"] == hardware_audio.ANALOG_SINK


def test_unknown_audio_entities_are_404(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    for kind in ("cards", "sinks", "sources"):
        response = make_client().get(f"/api/v1/hardware/audio/{kind}/no.such.entity")
        assert response.status_code == 404
        assert response.json()["code"] == "entity_not_found"


def test_a_name_outside_the_grammar_is_400(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    for kind in ("cards", "sinks", "sources"):
        response = make_client().get(f"/api/v1/hardware/audio/{kind}/not%20a%20name")
        assert response.status_code == 400
        assert response.json()["code"] == "invalid_audio_name"


def test_an_unusual_but_legal_name_is_not_a_400(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    response = make_client().get("/api/v1/hardware/audio/sinks/bluez_output.FC:58:FA:EB:08:6C.1")

    assert response.status_code == 404
    assert response.json()["code"] == "entity_not_found"


def test_a_slash_in_a_name_never_reaches_the_validation_400(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    response = make_client().get("/api/v1/hardware/audio/sinks/not/a/name")

    assert response.status_code == 404
    assert response.json().get("code") != "invalid_audio_name"


def test_no_audio_server_gives_an_empty_audio_section(installed_bluez, monkeypatch):
    monkeypatch.setattr(inventory, "probe_audio_server", lambda: "not available")

    body = make_client().get("/api/v1/hardware").json()

    assert body["audio"] == {"cards": [], "sinks": [], "sources": []}


# ---------------------------------------------------------------------------
# The default sink and the source feed
# ---------------------------------------------------------------------------


def test_the_default_sink_is_a_flag_on_the_endpoints(installed_bluez, monkeypatch):
    async def fake_audio(server_name: str | None = None):
        del server_name
        return replace(hardware_audio.facts(), default_sink=hardware_audio.USB_SINK)

    monkeypatch.setattr(inventory, "_read_audio_async", fake_audio)

    body = make_client().get("/api/v1/hardware").json()

    assert [sink["name"] for sink in body["audio"]["sinks"] if sink["is_default"]] == [hardware_audio.USB_SINK]
    assert all(source["is_default"] is False for source in body["audio"]["sources"])


def test_the_default_sink_capability_follows_a_successful_read(installed_bluez, monkeypatch):
    async def fake_audio(server_name: str | None = None):
        del server_name
        return replace(hardware_audio.facts(), default_sink=hardware_audio.USB_SINK)

    monkeypatch.setattr(inventory, "_read_audio_async", fake_audio)

    body = make_client().get("/api/v1/hardware").json()

    assert body["capabilities"]["default_sink"] is True


def test_no_source_key_when_the_feature_is_off(installed_bluez):
    body = make_client().get("/api/v1/hardware").json()

    assert body["source"] is None


def test_the_source_feed_has_the_contract_shape(installed_bluez, monkeypatch, tmp_config):
    _use_audio(monkeypatch)
    source_state.publish(
        enabled=True,
        state="waiting",
        phone="bluez_input.6C_5C_3D_35_17_99.1",
        phone_mac=DEVICE,
        server="ws://ma:8927/sendspin",
        ma_connected=True,
        ma_paired=True,
    )
    tmp_config.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "SENDSPIN_SOURCE": True,
                "players": [
                    {
                        "id": "eneby",
                        "name": "ENEBY",
                        "backend": {"type": "bluetooth_a2dp", "mac": DEVICE, "adapter": "hci0"},
                    }
                ],
            }
        )
    )

    body = make_client().get("/api/v1/hardware").json()

    assert body["source"] == {
        "enabled": True,
        "state": "waiting",
        "phone_mac": DEVICE,
        "paired_phone_mac": None,
        "capture": {"kind": "pipewire_node", "node": hardware_audio.CAPTURE_NODE},
        "ma": {
            "server": "ws://ma:8927/sendspin",
            "connected": True,
            "paired": True,
            "pairing_required": False,
            "pairing_code": None,
            "pairing_seconds_left": 0,
            "error": None,
        },
        "error": None,
    }
    phone = next(device for device in body["bluetooth_devices"] if device["mac"] == DEVICE)
    assert phone["source_phone"] is True
    assert {"role": "input", "evidence": f"source_feed:{DEVICE}"} in phone["roles"]
