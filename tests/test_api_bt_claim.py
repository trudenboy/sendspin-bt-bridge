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

from sendspin_bridge.services.audio.mpris_player import MprisPlayer, get_registry
from sendspin_bridge.web.routes.api_bt import bt_bp


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


@pytest.mark.parametrize(
    "mac_in_url",
    [
        "aa-bb-cc-dd-ee-ff",  # dash separators (Windows MAC style)
        "AA-BB-CC-DD-EE-FF",
        "aabbccddeeff",  # no separators (compact form)
        "AABBCCDDEEFF",
    ],
)
def test_claim_audio_accepts_alternate_mac_separators(flask_client, mac_in_url):
    """Regression test for Copilot review on PR #195: the endpoint and the
    MprisRegistry are inconsistent if the endpoint validator only accepts
    colon-separated MACs while the registry tolerates dashes / no
    separators.  Operators (and any UI that derives the MAC from a
    different source) must be able to claim regardless of the
    representation; canonicalise on the server before validating."""
    _register_player("AA:BB:CC:DD:EE:FF")

    response = flask_client.post(f"/api/bt/claim/{mac_in_url}")

    assert response.status_code == 200, response.data


def test_claim_audio_rejects_too_short_mac_after_normalisation(flask_client):
    """Once we canonicalise dashes / no-sep forms, validation must still
    reject anything that isn't 12 hex digits.  Otherwise we'd silently
    accept ``DEADBEEF`` and surface a confusing 404 instead of 400."""
    response = flask_client.post("/api/bt/claim/DEADBEEF")
    assert response.status_code == 400
