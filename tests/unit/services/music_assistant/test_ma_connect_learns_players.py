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
