"""Tests for Music Assistant now-playing metadata helpers."""

from urllib.parse import parse_qs, urlparse

import pytest

from services.ma_monitor import _build_now_playing, _hydrate_missing_queue_neighbors


def test_build_now_playing_wraps_relative_artwork_in_proxy_url():
    queue = {
        "queue_id": "syncgroup_1",
        "current_item": {
            "media_item": {
                "name": "Track",
                "metadata": {"images": [{"path": "/api/image/123"}]},
            }
        },
    }

    result = _build_now_playing(queue)

    parsed = urlparse(result["image_url"])
    query = parse_qs(parsed.query)

    assert parsed.path == "/api/ma/artwork"
    assert query["url"] == ["/api/image/123"]
    assert len(query["sig"][0]) == 64


def test_build_now_playing_wraps_absolute_artwork_in_proxy_url():
    queue = {
        "queue_id": "syncgroup_1",
        "current_item": {
            "media_item": {
                "name": "Track",
                "provider_mappings": [{"thumbnail_url": "http://ma:8095/api/image/abc"}],
            }
        },
    }

    result = _build_now_playing(queue)

    parsed = urlparse(result["image_url"])
    query = parse_qs(parsed.query)

    assert parsed.path == "/api/ma/artwork"
    assert query["url"] == ["http://ma:8095/api/image/abc"]
    assert len(query["sig"][0]) == 64


def test_build_now_playing_extracts_neighbor_tracks_from_queue_items():
    queue = {
        "queue_id": "syncgroup_1",
        "current_index": 1,
        "items": [
            {
                "media_item": {
                    "name": "Previous Song",
                    "artists": [{"name": "Prev Artist"}],
                }
            },
            {
                "media_item": {
                    "name": "Current Song",
                    "artists": [{"name": "Current Artist"}],
                }
            },
            {
                "media_item": {
                    "name": "Next Song",
                    "artists": [{"name": "Next Artist"}],
                }
            },
        ],
        "current_item": {
            "media_item": {
                "name": "Current Song",
                "artists": [{"name": "Current Artist"}],
            }
        },
    }

    result = _build_now_playing(queue)

    assert result["queue_total"] == 3
    assert result["prev_track"] == "Previous Song"
    assert result["prev_artist"] == "Prev Artist"
    assert result["next_track"] == "Next Song"
    assert result["next_artist"] == "Next Artist"


@pytest.mark.asyncio
async def test_hydrate_missing_queue_neighbors_fetches_previous_item_from_queue_api():
    queue = {
        "queue_id": "syncgroup_1",
        "current_index": 119,
    }
    result = {
        "queue_total": 124,
        "track": "Current Song",
        "next_track": "Next Song",
        "next_artist": "Next Artist",
        "next_album": "Next Album",
    }
    calls = []

    async def fetch_items(queue_id: str, limit: int, offset: int) -> list[dict]:
        calls.append((queue_id, limit, offset))
        if offset == 118:
            return [
                {
                    "media_item": {
                        "name": "Previous Song",
                        "artists": [{"name": "Prev Artist"}],
                        "album": {"name": "Prev Album"},
                    }
                }
            ]
        return []

    await _hydrate_missing_queue_neighbors(fetch_items, queue, result)

    assert calls == [("syncgroup_1", 1, 118)]
    assert result["prev_track"] == "Previous Song"
    assert result["prev_artist"] == "Prev Artist"
    assert result["prev_album"] == "Prev Album"
