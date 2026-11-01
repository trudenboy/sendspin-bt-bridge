"""``/bridge/source``: the source client, its Music Assistant pairing and the phone window."""

from __future__ import annotations

import pytest

from sendspin_bridge.application import source as source_uc
from sendspin_bridge.application.errors import UseCaseError


@pytest.fixture(autouse=True)
def _clean_state():
    from sendspin_bridge.services.source import state

    state.reset()
    yield
    state.reset()


def test_enabling_the_source_answers_with_the_start_result(api_client, monkeypatch):
    async def fake_start(**kwargs):
        assert kwargs == {}
        return {"started": True, "server": "ws://ma:8927/sendspin"}

    monkeypatch.setattr(source_uc, "start", fake_start)

    response = api_client.post("/api/v1/bridge/source", json={"enabled": True})

    assert response.status_code == 200
    assert response.json() == {"started": True, "server": "ws://ma:8927/sendspin"}


def test_disabling_the_source_answers_with_the_stop_result(api_client, monkeypatch):
    async def fake_stop():
        return {"started": False, "already": False}

    monkeypatch.setattr(source_uc, "stop", fake_stop)

    response = api_client.post("/api/v1/bridge/source", json={"enabled": False})

    assert response.status_code == 200
    assert response.json() == {"started": False, "already": False}


def test_the_music_assistant_pairing_code_is_returned(api_client, monkeypatch):
    async def fake_open():
        return {"code": "12345678", "seconds_left": 300, "client_id": "peer"}

    monkeypatch.setattr(source_uc, "open_ma_pairing", fake_open)

    response = api_client.post("/api/v1/bridge/source/ma-pairing")

    assert response.status_code == 200
    assert response.json() == {"code": "12345678", "seconds_left": 300, "client_id": "peer"}


def test_forgetting_the_pairing_answers_204_and_passes_forget(api_client, monkeypatch):
    seen: list[bool] = []

    async def fake_cancel(*, forget):
        seen.append(forget)

    monkeypatch.setattr(source_uc, "cancel_ma_pairing", fake_cancel)

    response = api_client.delete("/api/v1/bridge/source/ma-pairing")

    assert response.status_code == 204
    assert seen == [True]


def test_the_player_relay_route_is_gone(api_client):
    response = api_client.post("/api/v1/bridge/source/players", json={"device_id": "eneby", "enabled": True})

    assert response.status_code == 404


def test_the_phone_pairing_window_is_still_available(api_client, monkeypatch):
    async def fake_open(seconds):
        return {"pairing_seconds_left": seconds, "pairing_adapters": ["AA:BB:CC:DD:EE:FF"], "error": None}

    monkeypatch.setattr(source_uc, "open_pairing_window", fake_open)

    response = api_client.post("/api/v1/bridge/source/pairing", json={"seconds": 30})

    assert response.status_code == 200
    assert response.json()["pairing_seconds_left"] == 30


def test_a_pairing_command_without_a_source_is_a_problem(api_client, monkeypatch):
    async def fake_open():
        raise UseCaseError(409, "source_off", "The source is off")

    monkeypatch.setattr(source_uc, "open_ma_pairing", fake_open)

    response = api_client.post("/api/v1/bridge/source/ma-pairing")

    assert response.status_code == 409
    assert response.json()["code"] == "source_off"
