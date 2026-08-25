"""A queue that disappears must stop being reported.

The poll replaced the now-playing cache only when it had something to put
there.  A successful poll that found no queues left the previous snapshot in
place, so a speaker whose queue was removed went on reporting the queue it
used to have — and the device card went on offering queue controls for it.
"""

from __future__ import annotations

import asyncio

from sendspin_bridge.services.music_assistant import ma_runtime_state


def _monitor():
    from sendspin_bridge.services.music_assistant.ma_monitor import MaMonitor

    monitor = MaMonitor.__new__(MaMonitor)
    monitor._next_id = lambda: 1
    monitor._defer_incoming_event = lambda _evt: None
    return monitor


def _poll_answering(monkeypatch, queues: list[dict]) -> None:
    async def _recv(*_a, **_kw):
        return {"message_id": 1, "result": queues}

    monkeypatch.setattr("sendspin_bridge.services.music_assistant.ma_monitor._send", lambda *_a, **_kw: asyncio.sleep(0))
    monkeypatch.setattr("sendspin_bridge.services.music_assistant.ma_monitor._recv", _recv)
    async def _no_syncgroup_queues(_q):
        return []

    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_monitor._find_syncgroup_queues",
        _no_syncgroup_queues,
    )
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_monitor._find_solo_player_queues",
        lambda _q: [],
    )
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_monitor._active_bridge_clients",
        lambda: [],
    )


def test_a_poll_that_finds_nothing_clears_what_it_replaced(monkeypatch):
    ma_runtime_state.replace_ma_now_playing({"up4098e820": {"connected": True, "state": "playing"}})
    assert ma_runtime_state.get_ma_now_playing_for_group("up4098e820")

    _poll_answering(monkeypatch, [])
    try:
        asyncio.run(_monitor()._poll_queues(object()))

        assert ma_runtime_state.get_ma_now_playing_for_group("up4098e820") == {}
    finally:
        ma_runtime_state.replace_ma_now_playing({})


def test_the_device_card_stops_offering_a_queue_that_is_gone(monkeypatch):
    """The capability reads that cache; a stale entry keeps it available."""
    from types import SimpleNamespace

    from sendspin_bridge.services.bluetooth.device_health_state import build_device_capabilities

    ma_runtime_state.replace_ma_now_playing({"up4098e820": {"connected": True}})
    _poll_answering(monkeypatch, [])
    try:
        asyncio.run(_monitor()._poll_queues(object()))
        device = SimpleNamespace(
            player_name="Kitchen",
            server_connected=True,
            bluetooth_connected=True,
            bt_management_enabled=True,
            has_sink=True,
            enabled=True,
            extra={"ma_connected": True},
            ma_now_playing=ma_runtime_state.get_ma_now_playing_for_group("up4098e820"),
        )

        queue = build_device_capabilities(device)["actions"]["queue_control"]

        assert queue["currently_available"] is False
        assert queue["blocked_reason_detail"]["code"] == "ma_queue_unknown"
    finally:
        ma_runtime_state.replace_ma_now_playing({})
