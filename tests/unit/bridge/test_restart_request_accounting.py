"""A start that queues behind a restart is served by that restart's spawn.

`start_sendspin` counts requests so a start arriving while another is in
flight is not lost: the holder re-runs the spawn if the count moved.  A
restart holds the same lock, so a start arriving during one returns early —
and its request has in fact been served, by the daemon the restart spawns.
The counter has to say so, or the bookkeeping drifts from what happened.
"""

from __future__ import annotations

import asyncio

import pytest

from sendspin_bridge.bridge.client import SendspinClient


def _client() -> SendspinClient:
    client = SendspinClient("Test Player", "localhost", 9000)
    client.running = True
    client._start_sendspin_lock = asyncio.Lock()
    client._start_sendspin_requests = 0
    client._start_sendspin_processed = 0
    return client


@pytest.mark.asyncio
async def test_a_start_arriving_during_a_restart_is_accounted_for(monkeypatch):
    client = _client()
    spawns: list[str] = []

    async def _spawn():
        spawns.append("spawn")

    async def _stop():
        # A start arrives while the restart is still stopping the old daemon.
        await client.start_sendspin()

    monkeypatch.setattr(client, "_start_sendspin_inner", _spawn)
    monkeypatch.setattr(client, "stop_sendspin", _stop)

    await client.warm_restart({"static_delay_ms": 100})

    assert spawns == ["spawn"], "the queued start spawned a second daemon"
    assert client._start_sendspin_processed == client._start_sendspin_requests, (
        "the restart served the queued start but the counter still calls it pending"
    )


@pytest.mark.asyncio
async def test_a_restart_that_spawns_nothing_leaves_the_request_pending(monkeypatch):
    """A stopped client cannot serve a start; the next one still must."""
    client = _client()
    client.running = False
    monkeypatch.setattr(client, "stop_sendspin", lambda: asyncio.sleep(0))
    monkeypatch.setattr(client, "_start_sendspin_inner", lambda: asyncio.sleep(0))

    client._start_sendspin_requests = 1

    await client.warm_restart({"static_delay_ms": 100})

    assert client._start_sendspin_processed == 0
