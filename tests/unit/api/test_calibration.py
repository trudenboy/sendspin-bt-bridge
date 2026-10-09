"""``/calibration``: click track, metronome, and microphone sessions for relative delay."""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

import sendspin_bridge.application.status as status


class _Speaker:
    player_id = "player-1"
    player_name = "Kitchen"

    def __init__(self, *, sink: bool = True):
        self.sink = sink
        self.actions: list[str] = []

    async def play_calibration_tone(self):
        self.actions.append("tone")
        return self.sink

    async def start_calibration_metronome(self):
        self.actions.append("start")
        return self.sink

    async def stop_calibration_metronome(self):
        self.actions.append("stop")


@pytest.fixture
def speaker(monkeypatch):
    found = _Speaker()
    monkeypatch.setattr(status, "get_device_registry_snapshot", lambda: SimpleNamespace(active_clients=[found]))
    return found


def test_the_click_track_is_a_wav(api_client):
    resp = api_client.get("/api/v1/calibration/tone.wav")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/wav"
    assert resp.content.startswith(b"RIFF")


def test_play_targets_the_selected_device(api_client, speaker, bridge_loop):
    assert api_client.post("/api/v1/calibration/play", json={"device_id": "player-1"}).status_code == 204
    assert speaker.actions == ["tone"]


def test_play_without_a_sink_is_409(api_client, speaker, bridge_loop):
    speaker.sink = False
    resp = api_client.post("/api/v1/calibration/play", json={"device_id": "player-1"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "no_sink"


def test_metronome_starts_and_stops(api_client, speaker, bridge_loop):
    started = api_client.post("/api/v1/calibration/metronome", json={"device_id": "player-1", "action": "start"})
    stopped = api_client.post("/api/v1/calibration/metronome", json={"device_id": "player-1", "action": "stop"})
    assert started.json() == {"active": True}
    assert stopped.json() == {"active": False}
    assert speaker.actions == ["start", "stop"]


def test_a_session_estimates_the_relative_delay(api_client):
    created = api_client.post("/api/v1/calibration/sessions")
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    reference = [0.0] * 400
    target = [0.0] * 400
    reference[100:105] = [0.2, 0.7, 1.0, 0.7, 0.2]
    target[137:142] = [0.2, 0.7, 1.0, 0.7, 0.2]

    waiting = api_client.post(
        f"/api/v1/calibration/sessions/{session_id}/audio",
        json={"role": "reference", "sample_rate": 8000, "samples": reference},
    )
    complete = api_client.post(
        f"/api/v1/calibration/sessions/{session_id}/audio",
        json={"role": "target", "sample_rate": 8000, "samples": target},
    )

    assert waiting.json()["status"] == "waiting_for_other_recording"
    assert complete.json()["estimate"]["delay_ms"] == pytest.approx(4.625)


def test_a_silent_recording_is_explained_and_logged(api_client, caplog):
    session_id = api_client.post("/api/v1/calibration/sessions").json()["session_id"]

    with caplog.at_level(logging.INFO):
        for role in ("reference", "target"):
            complete = api_client.post(
                f"/api/v1/calibration/sessions/{session_id}/audio",
                json={"role": role, "sample_rate": 8000, "samples": [0.0] * 400},
            )

    payload = complete.json()
    assert payload["valid"] is False
    assert payload["error"] == "No calibration sound was detected; check microphone permission and move closer"
    assert "reason=silence" in caplog.text


def test_an_unknown_session_is_404(api_client):
    resp = api_client.post(
        "/api/v1/calibration/sessions/nope/audio", json={"role": "target", "sample_rate": 8000, "samples": [0.1] * 10}
    )
    assert resp.status_code == 404


def test_a_deleted_session_is_gone(api_client):
    session_id = api_client.post("/api/v1/calibration/sessions").json()["session_id"]
    assert api_client.delete(f"/api/v1/calibration/sessions/{session_id}").status_code == 204
    resp = api_client.post(
        f"/api/v1/calibration/sessions/{session_id}/audio",
        json={"role": "target", "sample_rate": 8000, "samples": [0.1] * 10},
    )
    assert resp.status_code == 404
