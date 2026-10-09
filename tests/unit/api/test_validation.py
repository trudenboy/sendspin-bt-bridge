"""Malformed requests are refused by the contract before any use case runs."""

from __future__ import annotations

import pytest


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/v1/playback", {"action": "invalid"}),
        ("/api/v1/devices/kitchen/playback", {"action": "rewind"}),
        ("/api/v1/groups/g1/playback", {"action": "stop"}),
        ("/api/v1/devices/kitchen/transport", {"command": "self-destruct"}),
        ("/api/v1/music-assistant/queue-commands", {"action": "shuffle-everything"}),
    ],
)
def test_unknown_actions_are_422(api_client, path, body):
    resp = api_client.post(path, json=body)
    assert resp.status_code == 422
    problem = resp.json()
    assert problem["code"] == "invalid_request"
    assert problem["errors"], "a validation problem names the fields at fault"


@pytest.mark.parametrize("level", [-1, 101, "loud"])
def test_volume_is_bounded(api_client, level):
    assert api_client.put("/api/v1/devices/kitchen/volume", json={"level": level}).status_code == 422


def test_a_body_that_is_not_json_is_refused(api_client):
    resp = api_client.put(
        "/api/v1/devices/kitchen/volume",
        content=b"level=5",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    assert resp.status_code == 422
