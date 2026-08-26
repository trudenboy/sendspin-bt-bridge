"""A queue command's answer must not be lost to a busy server.

`_request_command` read up to ten messages looking for its own reply. Music
Assistant pushes `player_updated` and `player_queue_updated` on its own, and a
household with several speakers can easily put more than ten of those on the
wire between a command and its acknowledgement. Past the tenth, the method
gave up and returned an empty payload — the bridge then reported the command
as failed, while the server had carried it out.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from sendspin_bridge.services.music_assistant.ma_monitor import MaMonitor


class _BusySocket:
    """Answers after *noise* unrelated events have gone past."""

    def __init__(self, noise: int) -> None:
        self._pending: list[dict] = [{"event": "player_updated"} for _ in range(noise)]
        self.sent: list[dict] = []

    async def send(self, raw: str) -> None:
        message = json.loads(raw)
        self.sent.append(message)
        self._pending.append({"message_id": message["message_id"], "result": {"ok": True}})

    async def recv(self) -> str:
        if not self._pending:
            await asyncio.sleep(3600)
        return json.dumps(self._pending.pop(0))


def _monitor() -> MaMonitor:
    import itertools

    monitor = MaMonitor.__new__(MaMonitor)
    monitor._msg_id = itertools.count(1)
    monitor._pending_queue_refresh = False
    monitor._pending_groups_refresh = False
    return monitor


@pytest.mark.asyncio
async def test_an_answer_behind_fifty_events_still_arrives():
    monitor = _monitor()
    socket = _BusySocket(noise=50)

    response = await monitor._request_command(socket, "player_queues/shuffle", {"shuffle_enabled": False})

    assert response.get("result") == {"ok": True}


@pytest.mark.asyncio
async def test_the_events_that_went_past_are_not_thrown_away():
    """They drive the now-playing refresh; losing them stalls the UI."""
    monitor = _monitor()
    socket = _BusySocket(noise=3)

    flushed: list[str] = []
    monitor._pending_groups_refresh = False
    monitor._pending_queue_refresh = False

    async def _flush(_ws):
        if monitor._pending_groups_refresh:
            flushed.append("player_updated")
            monitor._pending_groups_refresh = False
        if monitor._pending_queue_refresh:
            flushed.append("player_queue_updated")
            monitor._pending_queue_refresh = False

    monitor._flush_deferred_updates = _flush

    await monitor._request_command(socket, "player_queues/shuffle", {})

    assert flushed == ["player_updated"], "the events that went past were dropped"


@pytest.mark.asyncio
async def test_a_queued_command_also_survives_a_busy_server():
    """The UI's queue buttons go through the command queue, not the direct path."""
    monitor = _monitor()
    monitor._cmd_queue = asyncio.Queue()
    monitor._flush_deferred_updates = _noop_flush
    socket = _BusySocket(noise=40)
    answer: asyncio.Future = asyncio.get_running_loop().create_future()
    await monitor._cmd_queue.put(("player_queues/next", {}, answer))

    await monitor._drain_cmd_queue(socket)

    assert (await answer)["result"] == {"ok": True}


async def _noop_flush(_ws):
    return None
