"""Tests for Music Assistant now-playing metadata helpers."""

import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

import services.ma_monitor as ma_monitor
import state
from services.device_registry import DeviceRegistrySnapshot
from services.ma_monitor import (
    MaMonitor,
    _build_now_playing,
    _find_solo_player_queues,
    _find_syncgroup_queues,
    _hydrate_missing_queue_neighbors,
)


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


@pytest.mark.asyncio
async def test_find_syncgroup_queues_uses_registry_snapshot(monkeypatch):
    state.set_ma_groups({}, [{"id": "syncgroup_1", "name": "Kitchen", "members": []}])
    monkeypatch.setattr(
        ma_monitor,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(active_clients=[SimpleNamespace(status={"group_id": "runtime_group_1"})]),
    )
    try:
        result = await _find_syncgroup_queues(
            [
                {"queue_id": "syncgroup_1", "active": False},
                {"queue_id": "runtime_group_1", "active": True},
                {"queue_id": "other", "active": True},
            ]
        )
    finally:
        state.set_ma_groups({}, [])

    assert result == [{"queue_id": "runtime_group_1", "active": True}]


def test_find_solo_player_queues_uses_registry_snapshot(monkeypatch):
    state.set_ma_groups({}, [{"id": "syncgroup_1", "name": "Kitchen", "members": []}])
    monkeypatch.setattr(
        ma_monitor,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(
            active_clients=[
                SimpleNamespace(player_id="sendspin-kitchen"),
                SimpleNamespace(player_id="syncgroup_1"),
            ]
        ),
    )
    try:
        result = _find_solo_player_queues(
            [
                {"queue_id": "sendspin-kitchen", "active": True},
                {"queue_id": "syncgroup_1", "active": True},
            ]
        )
    finally:
        state.set_ma_groups({}, [])

    assert result == [("sendspin-kitchen", {"queue_id": "sendspin-kitchen", "active": True})]


def test_find_solo_player_queues_keeps_legacy_uuid_queue_matching(monkeypatch):
    state.set_ma_groups({}, [])
    monkeypatch.setattr(
        ma_monitor,
        "get_device_registry_snapshot",
        lambda: DeviceRegistrySnapshot(
            active_clients=[SimpleNamespace(player_id="d3002d0d-db47-51e2-b3a2-00f79b7fc683")]
        ),
    )
    try:
        result = _find_solo_player_queues(
            [
                {"queue_id": "upd3002d0ddb4751e2b3a200f79b7fc683", "active": True},
            ]
        )
    finally:
        state.set_ma_groups({}, [])

    assert result == [
        (
            "d3002d0d-db47-51e2-b3a2-00f79b7fc683",
            {"queue_id": "upd3002d0ddb4751e2b3a200f79b7fc683", "active": True},
        )
    ]


@pytest.mark.asyncio
async def test_request_command_flushes_interleaved_queue_event(monkeypatch):
    monitor = MaMonitor("http://ma:8095", "token")
    sent = []
    messages = iter(
        [
            json.dumps({"event": "player_queue_updated"}),
            json.dumps({"message_id": 1, "result": {"ok": True}}),
        ]
    )

    async def _recv():
        return next(messages)

    async def _send(payload: str):
        sent.append(json.loads(payload))

    ws = SimpleNamespace(send=_send, recv=_recv)
    poll_calls = []

    async def _fake_poll(_ws):
        poll_calls.append("poll")

    monkeypatch.setattr(monitor, "_poll_queues", _fake_poll)

    response = await monitor._request_command(ws, "player_queues/next", {"queue_id": "syncgroup_1"})

    assert response["result"]["ok"] is True
    assert sent[0]["command"] == "player_queues/next"
    assert poll_calls == ["poll"]


@pytest.mark.asyncio
async def test_request_queue_refresh_sets_pending_flag_and_wake_event():
    monitor = MaMonitor("http://ma:8095", "token")
    monitor._running = True
    monitor._ws = object()

    ok = await monitor.request_queue_refresh("syncgroup_1")

    assert ok is True
    assert monitor._pending_queue_refresh is True
    assert monitor._wake_event.is_set() is True


@pytest.mark.asyncio
async def test_send_queue_cmd_returns_ack_metadata(monkeypatch):
    monitor = MaMonitor("http://ma:8095", "token")
    monitor._running = True
    monitor._ws = object()

    async def _fake_execute_cmd(command: str, args: dict) -> dict:
        assert command == "player_queues/next"
        assert args == {"queue_id": "syncgroup_1"}
        return {"result": {"ok": True}}

    monkeypatch.setattr(monitor, "execute_cmd", _fake_execute_cmd)
    monkeypatch.setattr(ma_monitor, "_monitor_instance", monitor)
    state.set_ma_groups({}, [{"id": "syncgroup_1", "name": "Kitchen", "members": []}])
    try:
        result = await ma_monitor.send_queue_cmd("next", None, "syncgroup_1")
    finally:
        ma_monitor._monitor_instance = None
        state.set_ma_groups({}, [])

    assert result["accepted"] is True
    assert result["queue_id"] == "syncgroup_1"
    assert isinstance(result["ack_latency_ms"], int)
    assert result["accepted_at"] is not None


@pytest.mark.asyncio
async def test_send_queue_cmd_repeat_does_not_evaluate_seek_payload(monkeypatch):
    monitor = MaMonitor("http://ma:8095", "token")
    monitor._running = True
    monitor._ws = object()

    async def _fake_execute_cmd(command: str, args: dict) -> dict:
        assert command == "player_queues/repeat"
        assert args == {"queue_id": "syncgroup_1", "repeat_mode": "all"}
        return {"result": {"ok": True}}

    monkeypatch.setattr(monitor, "execute_cmd", _fake_execute_cmd)
    monkeypatch.setattr(ma_monitor, "_monitor_instance", monitor)
    state.set_ma_groups({}, [{"id": "syncgroup_1", "name": "Kitchen", "members": []}])
    try:
        result = await ma_monitor.send_queue_cmd("repeat", "all", "syncgroup_1")
    finally:
        ma_monitor._monitor_instance = None
        state.set_ma_groups({}, [])

    assert result["accepted"] is True
    assert result["queue_id"] == "syncgroup_1"
