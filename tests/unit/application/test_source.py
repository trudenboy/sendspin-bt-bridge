"""The Sendspin source as a use case: start/stop, players, pairing and status."""

from __future__ import annotations

import json

import pytest

from sendspin_bridge.application import source as source_uc
from sendspin_bridge.application import status as status_uc
from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.services.source import manager, pairing, relay
from sendspin_bridge.services.source import state as source_state

PHONE_NODE = "bluez_input.34_C7_E9_25_A3_A3.2"
BLUEZ_SINK = "bluez_sink.6C_5C_3D_35_17_99.a2dp_sink"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch):
    source_state.reset()
    monkeypatch.setattr(relay, "armed_devices", lambda: [])
    yield
    source_state.reset()


class _Client:
    """A running speaker, as the registry exposes it."""

    def __init__(self, sink: str | None = None, *, legacy: str | None = None):
        if legacy is not None:
            self.sink_name = legacy
        else:
            self.bluetooth_sink_name = sink


def _client(monkeypatch, client: _Client) -> None:
    monkeypatch.setattr(status_uc, "find_client", lambda device_id: client)


# -- status ---------------------------------------------------------------------------------------


def test_status_reads_the_typed_snapshot_and_the_armed_relays(monkeypatch):
    source_state.publish(enabled=True, port=8940, phone=PHONE_NODE, players=2, streaming=True)
    monkeypatch.setattr(relay, "armed_devices", lambda: ["eneby"])

    view = source_uc.status({"SENDSPIN_SOURCE": True, "SENDSPIN_SOURCE_PORT": 9000})

    assert view.as_status_dict() == {
        "enabled": True,
        "port": 8940,
        "phone": PHONE_NODE,
        "players": 2,
        "streaming": True,
        "error": None,
        "pairing_seconds_left": 0,
        "pairing_adapters": [],
        "attached_devices": ["eneby"],
    }


def test_status_falls_back_to_the_configured_port_before_the_source_runs():
    view = source_uc.status({"SENDSPIN_SOURCE": True, "SENDSPIN_SOURCE_PORT": 9000})

    assert view.port == 9000
    assert (view.enabled, view.streaming) == (False, False)


def test_status_keeps_every_key_the_spa_reads():
    source_state.publish(error="port_in_use")
    view = source_uc.status({"SENDSPIN_SOURCE": True})

    assert set(view.as_status_dict()) == {
        "enabled",
        "port",
        "phone",
        "players",
        "streaming",
        "error",
        "pairing_seconds_left",
        "pairing_adapters",
        "attached_devices",
    }
    assert view.error == "port_in_use"


# -- start / stop ---------------------------------------------------------------------------------


async def test_start_hands_the_loaded_config_and_its_directory_to_the_manager(monkeypatch, tmp_config):
    seen: list[tuple[dict, object]] = []

    async def fake_start(config, config_dir):
        seen.append((config, config_dir))
        return {"started": True, "port": 8940}

    monkeypatch.setattr(manager, "start", fake_start)
    monkeypatch.setattr("sendspin_bridge.bridge.orchestrator._resolve_config_dir", lambda: tmp_config.parent)
    tmp_config.write_text(json.dumps({"SENDSPIN_SOURCE": True, "SENDSPIN_SOURCE_PORT": 8940}))

    result = await source_uc.start()

    assert result == {"started": True, "port": 8940}
    assert len(seen) == 1
    assert seen[0][0]["SENDSPIN_SOURCE_PORT"] == 8940
    assert seen[0][1] == tmp_config.parent


async def test_stop_delegates_to_the_manager(monkeypatch):
    async def fake_stop():
        return {"started": False, "already": True}

    monkeypatch.setattr(manager, "stop", fake_stop)

    assert await source_uc.stop() == {"started": False, "already": True}


# -- players --------------------------------------------------------------------------------------


async def test_set_players_arms_the_speakers_audio_sink(monkeypatch):
    _client(monkeypatch, _Client(BLUEZ_SINK))
    armed: list[tuple[str, str]] = []
    monkeypatch.setattr(relay, "arm", lambda device_id, sink: armed.append((device_id, sink)))

    result = await source_uc.set_players("eneby", True)

    assert result == {"device_id": "eneby", "playing_source": True}
    assert armed == [("eneby", BLUEZ_SINK)]


async def test_set_players_reads_the_legacy_sink_name_fallback(monkeypatch):
    _client(monkeypatch, _Client(legacy="alsa_output.usb.dac"))
    armed: list[tuple[str, str]] = []
    monkeypatch.setattr(relay, "arm", lambda device_id, sink: armed.append((device_id, sink)))

    await source_uc.set_players("dac", True)

    assert armed == [("dac", "alsa_output.usb.dac")]


async def test_set_players_disarms_the_device(monkeypatch):
    _client(monkeypatch, _Client())
    disarmed: list[str] = []
    monkeypatch.setattr(relay, "disarm", lambda device_id: disarmed.append(device_id))

    result = await source_uc.set_players("eneby", False)

    assert result == {"device_id": "eneby", "playing_source": False}
    assert disarmed == ["eneby"]


async def test_a_speaker_with_no_audio_output_is_a_409(monkeypatch):
    _client(monkeypatch, _Client())

    with pytest.raises(UseCaseError) as exc:
        await source_uc.set_players("eneby", True)

    assert exc.value.status == 409
    assert exc.value.code == "source_sink_missing"


async def test_an_unknown_device_is_still_a_404_even_when_disarming():
    with pytest.raises(UseCaseError) as exc:
        await source_uc.set_players("no.such.player", False)

    assert exc.value.status == 404
    assert exc.value.code == "unknown_device"


# -- pairing window -------------------------------------------------------------------------------


async def test_open_pairing_window_passes_the_requested_seconds(monkeypatch):
    seen: list[int] = []

    async def fake_open(seconds):
        seen.append(seconds)
        return {"pairing_seconds_left": seconds}

    monkeypatch.setattr(pairing, "open_window", fake_open)

    assert await source_uc.open_pairing_window(45) == {"pairing_seconds_left": 45}
    assert seen == [45]


async def test_open_pairing_window_defaults_to_two_minutes(monkeypatch):
    seen: list[int] = []

    async def fake_open(seconds):
        seen.append(seconds)
        return {"pairing_seconds_left": seconds}

    monkeypatch.setattr(pairing, "open_window", fake_open)

    await source_uc.open_pairing_window()

    assert seen == [120]


async def test_close_pairing_window_closes_the_window(monkeypatch):
    called: list[int] = []

    async def fake_close():
        called.append(1)

    monkeypatch.setattr(pairing, "close_window", fake_close)

    await source_uc.close_pairing_window()

    assert called == [1]
