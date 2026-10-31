"""``/audio`` commands: the five safe writes and their problem responses."""

from __future__ import annotations

import pytest

from sendspin_bridge.application import audio as audio_uc
from sendspin_bridge.services.audio.sink_inventory import CardPort, CardProfile, LocalCard, LocalSink
from sendspin_bridge.services.hardware.capabilities import build_capabilities
from sendspin_bridge.services.hardware.inventory import (
    CaptureFacts,
    InventoryRead,
    build_audio_facts,
)

SINK = LocalSink(
    name="alsa_output.usb-dac.analog-stereo",
    description="USB DAC",
    state="idle",
    sample_spec="s16le 2ch 48000Hz",
    muted=False,
    volume=40,
    active_port="analog-output",
    active_port_available="available",
)

CARD = LocalCard(
    name="alsa_card.pci-0000_00_1f.3",
    driver="module-alsa-card.c",
    active_profile="output:analog-stereo",
    profiles=(
        CardProfile("output:analog-stereo", "Analog Stereo Output", True),
        CardProfile("output:analog-stereo+input:analog-stereo", "Analog Stereo Duplex", True),
        CardProfile("off", "Off", True),
    ),
    ports=(
        CardPort("analog-output", "Analog Output", True, True, ("output:analog-stereo",)),
        CardPort("analog-output-headphones", "Headphones", False, False, ("output:analog-stereo",)),
    ),
    sink_names=(SINK.name,),
)


def _read(*, audio_server: str = "PulseAudio", cards: tuple[LocalCard, ...] = (CARD,)) -> InventoryRead:
    return InventoryRead(
        as_of="2026-10-31T00:00:00+00:00",
        stale=False,
        stale_after_s=2,
        capabilities=build_capabilities(
            bluetooth=True,
            dbus=True,
            server_name=audio_server,
            capture=True,
            default_sink=True,
        ),
        controllers=(),
        devices=(),
        degraded=(),
        audio=build_audio_facts(list(cards), [SINK], [], CaptureFacts()),
    )


def _pulse():
    """The live pulse module (another test file re-imports it during collection)."""
    import sendspin_bridge.services.audio.pulse as pulse

    return pulse


@pytest.fixture(autouse=True)
def _host(monkeypatch):
    pulse = _pulse()
    monkeypatch.setattr(audio_uc, "read_inventory", lambda: _read())
    monkeypatch.setattr(pulse, "set_sink_volume", lambda _name, _volume: True)
    monkeypatch.setattr(pulse, "set_sink_mute", lambda _name, _muted: True)
    monkeypatch.setattr(pulse, "set_default_sink", lambda _name: True)
    monkeypatch.setattr(pulse, "suspend_sink", lambda _name, _suspend: True)
    monkeypatch.setattr(pulse, "set_card_profile", lambda _name, _profile: True)
    monkeypatch.setattr(pulse, "set_sink_port", lambda _name, _port: True)


def test_set_volume_round_trips(api_client):
    response = api_client.put(f"/api/v1/audio/sinks/{SINK.name}/volume", json={"volume": 37})

    assert response.status_code == 200
    assert response.json() == {"name": SINK.name, "volume": 37, "muted": False, "state": "idle"}


def test_set_volume_out_of_range_is_400_invalid_volume(api_client):
    response = api_client.put(f"/api/v1/audio/sinks/{SINK.name}/volume", json={"volume": 101})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_volume"


def test_set_mute_round_trips(api_client):
    response = api_client.put(f"/api/v1/audio/sinks/{SINK.name}/mute", json={"muted": True})

    assert response.status_code == 200
    assert response.json()["muted"] is True


def test_set_default_sink_round_trips(api_client):
    response = api_client.put("/api/v1/audio/default-sink", json={"name": SINK.name})

    assert response.status_code == 200
    assert response.json()["name"] == SINK.name


def test_suspend_and_resume_round_trip(api_client):
    suspended = api_client.post(f"/api/v1/audio/sinks/{SINK.name}/suspend")
    resumed = api_client.post(f"/api/v1/audio/sinks/{SINK.name}/resume")

    assert suspended.status_code == 200
    assert suspended.json()["state"] == "suspended"
    assert resumed.status_code == 200
    assert resumed.json()["state"] == "idle"


def test_an_unknown_sink_is_404(api_client):
    response = api_client.put("/api/v1/audio/sinks/no.such.sink/volume", json={"volume": 20})

    assert response.status_code == 404
    assert response.json()["code"] == "entity_not_found"


def test_a_name_outside_the_grammar_is_400(api_client):
    response = api_client.put("/api/v1/audio/sinks/not%20a%20name/volume", json={"volume": 20})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_audio_name"


def test_no_audio_server_is_409_not_supported_for_backend(api_client, monkeypatch):
    monkeypatch.setattr(audio_uc, "read_inventory", lambda: _read(audio_server="not available"))

    response = api_client.put(f"/api/v1/audio/sinks/{SINK.name}/volume", json={"volume": 20})

    assert response.status_code == 409
    assert response.json()["code"] == "not_supported_for_backend"


# ---------------------------------------------------------------------------
# Card profiles and sink ports
# ---------------------------------------------------------------------------


def test_set_card_profile_returns_the_updated_card_detail(api_client):
    response = api_client.put(
        f"/api/v1/audio/cards/{CARD.name}/profile", json={"profile": "output:analog-stereo+input:analog-stereo"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == CARD.name
    assert body["active_profile"] == "output:analog-stereo+input:analog-stereo"
    assert [profile["name"] for profile in body["profiles"]] == [profile.name for profile in CARD.profiles]
    assert [endpoint["name"] for endpoint in body["endpoints"]] == [SINK.name]


def test_set_card_profile_a_profile_the_card_does_not_offer_is_400_invalid_profile(api_client):
    response = api_client.put(f"/api/v1/audio/cards/{CARD.name}/profile", json={"profile": "a2dp_sink"})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_profile"


def test_set_card_profile_an_unknown_card_is_404_entity_not_found(api_client):
    response = api_client.put("/api/v1/audio/cards/no.such.card/profile", json={"profile": "off"})

    assert response.status_code == 404
    assert response.json()["code"] == "entity_not_found"


def test_set_card_profile_without_a_server_is_409(api_client, monkeypatch):
    monkeypatch.setattr(audio_uc, "read_inventory", lambda: _read(audio_server="not available"))

    response = api_client.put(f"/api/v1/audio/cards/{CARD.name}/profile", json={"profile": "off"})

    assert response.status_code == 409
    assert response.json()["code"] == "not_supported_for_backend"


def test_set_card_profile_a_refused_command_is_409(api_client, monkeypatch):
    monkeypatch.setattr(_pulse(), "set_card_profile", lambda _name, _profile: False)

    response = api_client.put(f"/api/v1/audio/cards/{CARD.name}/profile", json={"profile": "off"})

    assert response.status_code == 409
    assert response.json()["code"] == "not_supported_for_backend"


def test_set_card_profile_a_name_outside_the_grammar_is_400(api_client):
    response = api_client.put("/api/v1/audio/cards/not%20a%20name/profile", json={"profile": "off"})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_audio_name"


def test_set_sink_port_returns_the_updated_endpoint_detail(api_client):
    response = api_client.put(f"/api/v1/audio/sinks/{SINK.name}/port", json={"port": "analog-output-headphones"})

    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "sink"
    assert body["name"] == SINK.name
    assert body["active_port"] == "analog-output-headphones"
    assert {port["name"] for port in body["ports"]} == {"analog-output", "analog-output-headphones"}


def test_set_sink_port_a_port_not_on_the_sinks_card_is_400_invalid_port(api_client):
    response = api_client.put(f"/api/v1/audio/sinks/{SINK.name}/port", json={"port": "hdmi-output-0"})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_port"


def test_set_sink_port_an_unknown_sink_is_404_entity_not_found(api_client):
    response = api_client.put("/api/v1/audio/sinks/no.such.sink/port", json={"port": "analog-output"})

    assert response.status_code == 404
    assert response.json()["code"] == "entity_not_found"


def test_set_sink_port_without_a_server_is_409(api_client, monkeypatch):
    monkeypatch.setattr(audio_uc, "read_inventory", lambda: _read(audio_server="not available"))

    response = api_client.put(f"/api/v1/audio/sinks/{SINK.name}/port", json={"port": "analog-output"})

    assert response.status_code == 409
    assert response.json()["code"] == "not_supported_for_backend"


def test_set_sink_port_a_refused_command_is_409(api_client, monkeypatch):
    monkeypatch.setattr(_pulse(), "set_sink_port", lambda _name, _port: False)

    response = api_client.put(f"/api/v1/audio/sinks/{SINK.name}/port", json={"port": "analog-output"})

    assert response.status_code == 409
    assert response.json()["code"] == "not_supported_for_backend"


def test_set_sink_port_a_name_outside_the_grammar_is_400(api_client):
    response = api_client.put("/api/v1/audio/sinks/not%20a%20name/port", json={"port": "analog-output"})

    assert response.status_code == 400
    assert response.json()["code"] == "invalid_audio_name"
