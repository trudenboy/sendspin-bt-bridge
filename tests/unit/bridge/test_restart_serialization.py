"""A restart must not race the spawn path into a second daemon.

Found on hardware: two restarts arrived while a device was reconnecting, and
the bridge logged two spawns 1 ms apart with different PIDs.  The second
overwrote the tracked handle, so the first daemon kept the Bluetooth sink and
the listen port with nothing watching it — no exit was ever logged for it, and
it had to be killed by hand.

`start_sendspin` coalesces concurrent starts behind a lock.  `warm_restart`
stopped and spawned outside that lock, so it could not see a start in flight
and a start could not see it.
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


class _SpawnRecorder:
    """Counts spawns and reports whether two ever overlapped."""

    def __init__(self):
        self.spawns = 0
        self.in_flight = 0
        self.overlapped = False

    async def spawn(self) -> None:
        self.spawns += 1
        self.in_flight += 1
        self.overlapped = self.overlapped or self.in_flight > 1
        await asyncio.sleep(0.02)  # the real spawn awaits the subprocess
        self.in_flight -= 1

    async def stop(self) -> None:
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_a_warm_restart_and_a_start_do_not_spawn_two_daemons(monkeypatch):
    client = _client()
    recorder = _SpawnRecorder()

    monkeypatch.setattr(client, "_start_sendspin_inner", recorder.spawn)
    monkeypatch.setattr(client, "stop_sendspin", recorder.stop)

    await asyncio.gather(
        client.warm_restart({"static_delay_ms": 100}),
        client.start_sendspin(),
    )

    assert not recorder.overlapped, "a restart and a start spawned daemons at the same time"


@pytest.mark.asyncio
async def test_two_warm_restarts_are_serialised(monkeypatch):
    client = _client()
    recorder = _SpawnRecorder()

    monkeypatch.setattr(client, "_start_sendspin_inner", recorder.spawn)
    monkeypatch.setattr(client, "stop_sendspin", recorder.stop)

    await asyncio.gather(
        client.warm_restart({"static_delay_ms": 100}),
        client.warm_restart({"static_delay_ms": 200}),
    )

    assert not recorder.overlapped, "two restarts spawned daemons at the same time"


@pytest.mark.asyncio
async def test_a_restart_still_respawns(monkeypatch):
    """Serialising must not turn a restart into a no-op."""
    client = _client()
    recorder = _SpawnRecorder()

    monkeypatch.setattr(client, "_start_sendspin_inner", recorder.spawn)
    monkeypatch.setattr(client, "stop_sendspin", recorder.stop)

    await client.warm_restart({"static_delay_ms": 100})

    assert recorder.spawns == 1


@pytest.mark.asyncio
async def test_a_restart_of_a_stopped_client_does_not_spawn(monkeypatch):
    client = _client()
    client.running = False
    recorder = _SpawnRecorder()

    monkeypatch.setattr(client, "_start_sendspin_inner", recorder.spawn)
    monkeypatch.setattr(client, "stop_sendspin", recorder.stop)

    await client.warm_restart({"static_delay_ms": 100})

    assert recorder.spawns == 0


@pytest.mark.asyncio
async def test_a_zombie_restart_does_not_race_a_warm_restart(monkeypatch):
    """The watchdog restart took the same route around the lock."""
    client = _client()
    recorder = _SpawnRecorder()

    monkeypatch.setattr(client, "_start_sendspin_inner", recorder.spawn)
    monkeypatch.setattr(client, "stop_sendspin", recorder.stop)
    # The watchdog settles for a second before respawning; the point here is
    # the ordering, not the wait.
    original_restart = client._restart_daemon
    monkeypatch.setattr(client, "_restart_daemon", lambda **kw: original_restart(settle_s=0.0))

    await asyncio.gather(
        client._zombie_restart(),
        client.warm_restart({"static_delay_ms": 100}),
    )

    assert not recorder.overlapped, "the watchdog restart raced the warm restart"
    assert recorder.spawns == 2
