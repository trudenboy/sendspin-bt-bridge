"""A session's keep-awake streams live and die with that session.

The streams keep sleepy amplifiers awake between clicks. They were one global set, stopped only by
an explicit DELETE: finishing a session, its idle timeout or a failure left ``pacat`` playing into the
speaker, and starting a session for another group stopped the first group's streams.
"""

from __future__ import annotations

import time

import pytest

from sendspin_bridge.application import sync_tuning as uc
from sendspin_bridge.sync_tuning.bed_stream import BedStreams
from tests.unit.api.test_sync_api import sync_env  # noqa: F401 (fixture)


class FakeProcess:
    def __init__(self, sink: str, started: list[str], stopped: list[str]) -> None:
        self.sink, self.stopped = sink, stopped
        self.stdin = self
        started.append(sink)

    def write(self, _data: bytes) -> None:
        pass

    def flush(self) -> None:
        pass

    def close(self) -> None:
        pass

    def wait(self, timeout: float | None = None) -> int:
        if self.sink not in self.stopped:
            self.stopped.append(self.sink)
        return 0

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


@pytest.fixture
def beds(monkeypatch, sync_env):  # noqa: F811
    started: list[str] = []
    stopped: list[str] = []
    streams = BedStreams(spawn=lambda sink: FakeProcess(sink, started, stopped))  # type: ignore[arg-type,return-value]
    monkeypatch.setattr(uc, "_BEDS", streams)
    monkeypatch.setattr(uc, "_keep_awake_sinks", lambda group_id: [f"sink-{group_id}"])
    yield streams, started, stopped
    streams.stop_all()


def _settled(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _create(client, group="g") -> str:
    resp = client.post("/api/v1/sync/sessions", json={"group_id": group, "mode": "semi"})
    assert resp.status_code in (200, 201), resp.text
    return resp.json()["id"]


def test_finishing_a_session_stops_its_keep_awake_stream(api_client, beds):
    streams, _started, _stopped = beds
    sid = _create(api_client)
    assert streams.active() == ["sink-g"]

    assert api_client.post(f"/api/v1/sync/sessions/{sid}/finish").status_code == 200

    assert _settled(lambda: streams.active() == []), streams.active()


def test_an_expired_session_stops_its_keep_awake_stream(api_client, beds, sync_env):  # noqa: F811
    streams, _started, _stopped = beds
    _ctl, store, _events, clock = sync_env
    _create(api_client)
    assert streams.active() == ["sink-g"]

    clock.now += 10_000
    store.tick()

    assert _settled(lambda: streams.active() == []), streams.active()
