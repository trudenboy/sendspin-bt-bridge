"""No restart may spawn a daemon while the Source feed owns the link.

``start_sendspin`` refuses during a call, but ``warm_restart`` and the
playback watchdog went straight to the spawn path.  A warm restart mid-call
therefore spawned a daemon against a ``PULSE_SINK`` the microphone mode had
removed.  The Source feed resumes the Player itself after a verified A2DP
restore, so a blocked restart must spawn nothing and stay blocked only while
the ownership lasts.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from sendspin_bridge.bridge.client import SendspinClient
from sendspin_bridge.services.bluetooth import headset

MAC = "AA:BB:CC:DD:EE:FF"


def _client(*, lock: bool) -> SendspinClient:
    client = SendspinClient("Test Player", "localhost", 9000)
    client.running = True
    client.bt_manager = SimpleNamespace(mac_address=MAC)
    if lock:
        client._start_sendspin_lock = asyncio.Lock()
        client._start_sendspin_requests = 0
        client._start_sendspin_processed = 0
    return client


class _Recorder:
    """Counts the real spawn/stop entry points instead of running them."""

    def __init__(self) -> None:
        self.spawns = 0
        self.stops = 0

    async def spawn(self) -> None:
        self.spawns += 1

    async def stop(self) -> None:
        self.stops += 1


@pytest.fixture
def owned(monkeypatch):
    service = headset.HeadsetProfileService()
    service._publish(MAC, "microphone")
    monkeypatch.setattr(headset, "_service", service)
    return service


def _recorder(client: SendspinClient, monkeypatch) -> _Recorder:
    recorder = _Recorder()
    monkeypatch.setattr(client, "_start_sendspin_inner", recorder.spawn)
    monkeypatch.setattr(client, "stop_sendspin", recorder.stop)
    return recorder


@pytest.mark.parametrize("lock", [True, False], ids=["locked", "unlocked"])
async def test_a_warm_restart_does_not_spawn_while_the_microphone_owns_the_link(monkeypatch, owned, lock):
    client = _client(lock=lock)
    recorder = _recorder(client, monkeypatch)

    await client.warm_restart({})

    assert recorder.spawns == 0
    # A restart still stops the daemon: it must not keep playing into a sink
    # the microphone mode removed.
    assert recorder.stops == 1


@pytest.mark.parametrize("lock", [True, False], ids=["locked", "unlocked"])
async def test_a_zombie_restart_does_not_spawn_while_the_microphone_owns_the_link(monkeypatch, owned, lock):
    client = _client(lock=lock)
    recorder = _recorder(client, monkeypatch)

    await client._zombie_restart()

    assert recorder.spawns == 0


async def test_a_restart_spawns_again_once_the_microphone_releases_the_link(monkeypatch, owned):
    client = _client(lock=True)
    recorder = _recorder(client, monkeypatch)

    await client.warm_restart({})
    assert recorder.spawns == 0

    owned._publish(MAC, None)
    await client.warm_restart({})

    assert recorder.spawns == 1
