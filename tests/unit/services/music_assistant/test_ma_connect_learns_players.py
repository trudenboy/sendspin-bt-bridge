"""The player map must exist before the first command, not a minute later.

Which Music Assistant player fronts each of our speakers is learned from a
`players/all` answer, and that answer was only asked for on the sixty-second
refresh timer. For up to a minute after every connect — which includes every
bridge start and every reconnect — the map was empty, so a queue command for
an ungrouped speaker fell through to the derived ids that no current server
knows. Reproduced on the live bridge right after a restart:

    MA queue cmd rejected: shuffle value=False → up8e34f1108d9b…
    MA queue cmd rejected: shuffle value=False → 8e34f110-8d9b…

The same answer is also what lets the first queue poll recognise a solo
speaker's queue, so it belongs before that poll, not after it.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.services.music_assistant.ma_monitor import MaMonitor


@pytest.mark.asyncio
async def test_a_connect_learns_the_players_before_polling_queues(monkeypatch):
    monitor = MaMonitor.__new__(MaMonitor)
    order: list[str] = []

    async def _groups(_ws):
        order.append("groups")

    async def _poll(_ws):
        order.append("poll")

    async def _stale(_ws):
        order.append("stale")

    monitor._refresh_groups_via_ws = _groups
    monitor._poll_queues = _poll
    monitor._refresh_stale_player_metadata = _stale

    await MaMonitor._prime_session(monitor, object())

    assert order == ["stale", "groups", "poll"], order


@pytest.mark.asyncio
async def test_a_failing_refresh_does_not_stop_the_session(monkeypatch):
    """A server that will not answer players/all must not cost us the poll."""
    monitor = MaMonitor.__new__(MaMonitor)
    polled: list[str] = []

    async def _groups(_ws):
        raise TimeoutError

    async def _poll(_ws):
        polled.append("poll")

    async def _stale(_ws):
        return None

    monitor._refresh_groups_via_ws = _groups
    monitor._poll_queues = _poll
    monitor._refresh_stale_player_metadata = _stale

    await MaMonitor._prime_session(monitor, object())

    assert polled == ["poll"]


@pytest.mark.asyncio
async def test_sync_groups_are_moved_to_the_speakers_new_player_once(monkeypatch):
    """After the 2.76 identity change the group still lists the old player;
    the monitor rewrites it once, not on every refresh."""
    from types import SimpleNamespace

    import sendspin_bridge.services.music_assistant.ma_monitor as ma_monitor

    legacy = "fcc3c5f3-15b2-5ddb-99d2-f64b915d8c25"
    players = [
        {
            "player_id": "up184e8a15",
            "display_name": "ENEBY20 @ bridge",
            "available": True,
            "output_protocols": [{"output_protocol_id": "RND_peer"}],
        },
        {
            "player_id": "syncgroup_a",
            "type": "group",
            "provider": "sync_group",
            "display_name": "Living room",
            "group_members": [],
            "static_group_members": ["up" + legacy.replace("-", ""), "upother"],
        },
    ]
    sent: list[tuple[str, dict]] = []

    async def _request(_ws, command, args, **_kw):
        sent.append((command, args))
        return {"result": players} if command == "players/all" else {"result": None}

    monitor = ma_monitor.MaMonitor("http://ma:8095", "token")
    monkeypatch.setattr(monitor, "_request_command", _request)
    client = SimpleNamespace(player_id=legacy, player_name="ENEBY20 @ bridge", sendspin_client_id="RND_peer")
    monkeypatch.setattr(ma_monitor, "_active_bridge_clients", lambda: [client])

    await monitor._refresh_groups_via_ws(object())
    await monitor._refresh_groups_via_ws(object())

    saves = [args for command, args in sent if command == "config/players/save"]
    assert saves == [{"player_id": "syncgroup_a", "values": {"group_members": ["up184e8a15", "upother"]}}]
