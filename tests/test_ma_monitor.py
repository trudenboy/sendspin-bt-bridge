"""Tests for Music Assistant now-playing metadata helpers."""

from services.ma_monitor import _build_now_playing


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

    assert result["image_url"] == "/api/ma/artwork?url=%2Fapi%2Fimage%2F123"


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

    assert result["image_url"] == "/api/ma/artwork?url=http%3A%2F%2Fma%3A8095%2Fapi%2Fimage%2Fabc"
