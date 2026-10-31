"""``/bridge/source`` keeps its paths and response shapes after the E3 extraction."""

from __future__ import annotations

import pytest

from sendspin_bridge.application import source as source_uc
from sendspin_bridge.application import status as status_uc


@pytest.fixture(autouse=True)
def _clean_state():
    from sendspin_bridge.services.source import state

    state.reset()
    yield
    state.reset()


def test_starting_the_source_answers_exactly_as_before(api_client, monkeypatch):
    async def fake_start():
        return {"started": True, "port": 8940}

    monkeypatch.setattr(source_uc, "start", fake_start)

    response = api_client.post("/api/v1/bridge/source", json={"enabled": True})

    assert response.status_code == 200
    assert response.json() == {"started": True, "port": 8940}


def test_stopping_the_source_answers_exactly_as_before(api_client, monkeypatch):
    async def fake_stop():
        return {"started": False, "already": True}

    monkeypatch.setattr(source_uc, "stop", fake_stop)

    response = api_client.post("/api/v1/bridge/source", json={"enabled": False})

    assert response.status_code == 200
    assert response.json() == {"started": False, "already": True}


def test_arming_a_player_keeps_its_two_keys(api_client, monkeypatch):
    seen: list[tuple[str, bool]] = []

    async def fake_set_players(device_id, enabled):
        seen.append((device_id, enabled))
        return {"device_id": device_id, "playing_source": enabled}

    monkeypatch.setattr(source_uc, "set_players", fake_set_players)

    response = api_client.post("/api/v1/bridge/source/players", json={"device_id": "eneby", "enabled": True})

    assert response.status_code == 200
    assert response.json() == {"device_id": "eneby", "playing_source": True}
    assert seen == [("eneby", True)]


def test_disarming_a_player_keeps_its_two_keys(api_client, monkeypatch):
    async def fake_set_players(device_id, enabled):
        return {"device_id": device_id, "playing_source": enabled}

    monkeypatch.setattr(source_uc, "set_players", fake_set_players)

    response = api_client.post("/api/v1/bridge/source/players", json={"device_id": "eneby", "enabled": False})

    assert response.status_code == 200
    assert response.json() == {"device_id": "eneby", "playing_source": False}


def test_a_player_without_a_sink_is_the_same_409(api_client, monkeypatch):
    class _Client:
        bluetooth_sink_name = None
        sink_name = None

    monkeypatch.setattr(status_uc, "find_client", lambda device_id: _Client())

    response = api_client.post("/api/v1/bridge/source/players", json={"device_id": "eneby", "enabled": True})

    assert response.status_code == 409
    assert response.json()["code"] == "source_sink_missing"


def test_opening_the_pairing_window_passes_its_seconds(api_client, monkeypatch):
    seen: list[int] = []

    async def fake_open(seconds):
        seen.append(seconds)
        return {"pairing_seconds_left": seconds, "pairing_adapters": ["AA:BB:CC:DD:EE:FF"], "error": None}

    monkeypatch.setattr(source_uc, "open_pairing_window", fake_open)

    response = api_client.post("/api/v1/bridge/source/pairing", json={"seconds": 45})

    assert response.status_code == 200
    assert set(response.json()) == {"pairing_seconds_left", "pairing_adapters", "error"}
    assert seen == [45]


def test_opening_the_pairing_window_without_a_body_uses_the_default(api_client, monkeypatch):
    seen: list[int] = []

    async def fake_open(seconds):
        seen.append(seconds)
        return {"pairing_seconds_left": seconds, "pairing_adapters": [], "error": None}

    monkeypatch.setattr(source_uc, "open_pairing_window", fake_open)

    response = api_client.post("/api/v1/bridge/source/pairing")

    assert response.status_code == 200
    assert seen == [120]


def test_closing_the_pairing_window_is_still_a_204_without_a_body(api_client, monkeypatch):
    called: list[int] = []

    async def fake_close():
        called.append(1)

    monkeypatch.setattr(source_uc, "close_pairing_window", fake_close)

    response = api_client.delete("/api/v1/bridge/source/pairing")

    assert response.status_code == 204
    assert response.content == b""
    assert called == [1]


def test_starting_the_source_through_the_real_use_case_reaches_the_manager(api_client, monkeypatch):
    from sendspin_bridge.services.source import manager

    seen: list[dict] = []

    async def fake_start(config, config_dir):
        seen.append(config)
        return {"started": True, "port": 8940}

    async def capture_available():
        return True

    monkeypatch.setattr(source_uc, "_capture_available", capture_available)
    monkeypatch.setattr(manager, "start", fake_start)
    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: {"SENDSPIN_SOURCE": True})
    monkeypatch.setattr("sendspin_bridge.bridge.orchestrator._resolve_config_dir", lambda: None)

    response = api_client.post("/api/v1/bridge/source", json={"enabled": True})

    assert response.status_code == 200
    assert response.json() == {"started": True, "port": 8940}
    assert seen == [{"SENDSPIN_SOURCE": True}]
