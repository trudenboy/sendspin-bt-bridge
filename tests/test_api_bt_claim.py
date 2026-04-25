"""Tests for ``POST /api/bt/claim/<mac>`` — Claim Audio endpoint.

UX: when a multipoint speaker is currently playing audio sourced from
another paired host (a phone, a laptop), the operator can press "Claim
audio" in the bridge UI to take the speaker over.  We do that by pushing
PlaybackStatus = "Playing" through the speaker's MprisPlayer so BlueZ
re-asserts the bridge as the active MPRIS source on the AVRCP session.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from flask import Flask

from routes.api_bt import bt_bp
from services.mpris_player import MprisPlayer, get_registry


@pytest.fixture
def flask_client():
    app = Flask(__name__)
    app.register_blueprint(bt_bp)
    return app.test_client()


@pytest.fixture(autouse=True)
def _clean_registry():
    """Each test starts with a clean MprisRegistry."""
    reg = get_registry()
    reg._by_mac.clear()
    yield
    reg._by_mac.clear()


def _register_player(mac: str = "AA:BB:CC:DD:EE:FF"):
    player = MprisPlayer(
        mac=mac,
        player_id="player-id-test",
        transport_callback=AsyncMock(return_value=True),
        volume_callback=AsyncMock(return_value=True),
    )
    get_registry().register(mac, player)
    return player


def test_claim_audio_pushes_playing_status_to_mpris_player(flask_client):
    """A successful claim must end with the MprisPlayer reporting
    PlaybackStatus = 'Playing' so BlueZ propagates it to the speaker."""
    player = _register_player()

    response = flask_client.post("/api/bt/claim/AA:BB:CC:DD:EE:FF")

    assert response.status_code == 200, response.data
    assert response.get_json().get("success") is True
    assert player._state.status == "Playing"


def test_claim_audio_returns_404_when_no_mpris_player_for_mac(flask_client):
    """If the MAC is not currently connected (no MprisPlayer in registry),
    return 404 — the UI can disable the button when this happens."""
    response = flask_client.post("/api/bt/claim/AA:BB:CC:DD:EE:FF")

    assert response.status_code == 404
    body = response.get_json() or {}
    assert body.get("success") is False


def test_claim_audio_validates_mac_format(flask_client):
    """Malformed MAC → 400, no registry mutation."""
    response = flask_client.post("/api/bt/claim/not-a-mac")

    assert response.status_code == 400
    body = response.get_json() or {}
    assert body.get("success") is False


def test_claim_audio_accepts_lowercase_mac(flask_client):
    """The endpoint is MAC-case-insensitive (matching the registry's
    normalisation) so links from the UI work regardless of casing."""
    _register_player("AA:BB:CC:DD:EE:FF")

    response = flask_client.post("/api/bt/claim/aa:bb:cc:dd:ee:ff")

    assert response.status_code == 200
