"""``/audio`` commands: the five safe writes and their problem responses."""

from __future__ import annotations

import pytest

from sendspin_bridge.application import audio as audio_uc
from sendspin_bridge.services.audio.sink_inventory import LocalSink
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


def _read(*, audio_server: str = "PulseAudio") -> InventoryRead:
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
        audio=build_audio_facts([], [SINK], [], CaptureFacts()),
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
