"""The bridge records the whole sequence itself: recorder first, then the sequence, stopped after it.

Started after the sequence and stopped after its nominal length, the recording lost whatever Music
Assistant's mute round trips added; the last member's solo could fall off the end and read as
silent. With ``members`` in the request the bridge opens the recorder, plays the sequence, waits
for it to finish and stops the recorder, and knows where in the file the sequence began.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.application import sync_tuning as uc
from sendspin_bridge.sync_tuning import input_recorder
from tests.support.sync_sequence import sequence_recording
from tests.unit.api.test_sync_api import _ready, sync_env  # noqa: F401 (fixture)
from tests.unit.api.test_sync_input import (  # noqa: F401 (fixtures)
    LOCAL,
    _clean_source_state,
    _fake_session_store,
    clocked,
    source_view,
)

pytestmark = pytest.mark.usefixtures("source_view", "clocked")


class FakeCapture:
    """A recorder the bridge opened: hands back a prepared recording when stopped.

    ``warmup_s`` moves the session clock between opening the recorder and the sequence's start, as
    the real recorder's start-up and the first mutes would.
    """

    def __init__(self, store, clock, payload: bytes, warmup_s: float) -> None:
        self.store, self.clock, self.payload, self.warmup_s = store, clock, payload, warmup_s
        self.sequence_at_open: object = "not opened"
        self.phase_at_stop: object = "not stopped"
        self.argv: list[str] = []
        self.sid = ""

    def open(self, argv: list[str]) -> FakeCapture:
        self.argv = list(argv)
        self.sequence_at_open = self.store.get(self.sid).sequence
        self.clock.now += self.warmup_s
        return self

    def wait_for_audio(self, timeout: float) -> bool:
        return True

    def stop(self) -> bytes:
        sequence = self.store.get(self.sid).sequence
        self.phase_at_stop = sequence["phase"] if sequence else None
        return self.payload


@pytest.fixture
def capture(monkeypatch, sync_env):  # noqa: F811
    _ctl, store, _events, clock = sync_env
    fake = FakeCapture(store, clock, b"", warmup_s=1.7)
    monkeypatch.setattr(input_recorder, "open_capture", fake.open)
    return fake


def _record(client, sid, members=("a", "b")):
    return client.post(
        f"/api/v1/sync/sessions/{sid}/capture/input", json={"input_id": LOCAL["id"], "members": list(members)}
    )


def test_the_bridge_records_before_the_sequence_starts_and_stops_after_it_ends(api_client, capture):
    sid = _ready(api_client)
    capture.sid = sid
    capture.payload, _slots = sequence_recording({"a": 0.0, "b": 0.030})

    resp = _record(api_client, sid)

    assert resp.status_code == 200, resp.text
    assert capture.sequence_at_open is None, "the sequence started before the recorder"
    assert capture.phase_at_stop == "done", "the recorder stopped before the sequence finished"
    assert {r["member"]: r["status"] for r in resp.json()["results"]} == {"a": "ok", "b": "ok"}
    assert capture.argv[:3] == ["pw-record", "--target", "alsa_input.usb-mic"]


def test_where_the_recorder_started_decides_a_silent_last_member(api_client, capture):
    """Long silence either side and a silent last member: the clicks alone fit two offsets a member
    apart. The bridge knows when it opened the recorder, so a is a and b is silent."""
    sid = _ready(api_client)
    capture.sid = sid
    capture.warmup_s = 14.0
    capture.payload, _slots = sequence_recording(
        {"a": 0.0, "b": 0.030}, silent=frozenset({"b"}), started_at_s=14.0, tail_s=14.0
    )

    resp = _record(api_client, sid)

    assert resp.status_code == 200, resp.text
    assert {r["member"]: r["status"] for r in resp.json()["results"]} == {"a": "ok", "b": "not_heard"}


def test_a_recorder_that_never_delivers_audio_is_input_unavailable_and_plays_nothing(
    api_client,
    capture,
    sync_env,  # noqa: F811
):
    ctl = sync_env[0]
    sid = _ready(api_client)
    capture.sid = sid
    capture.wait_for_audio = lambda timeout: False
    calls_before = list(ctl.calls)

    resp = _record(api_client, sid)

    assert resp.status_code == 409 and resp.json()["code"] == "input_unavailable"
    assert api_client.get(f"/api/v1/sync/sessions/{sid}").json()["sequence"] is None
    assert [c for c in ctl.calls[len(calls_before) :] if c[0] == "mute"] == []


def test_a_sequence_the_session_refuses_stops_the_recorder(api_client, capture):
    sid = _ready(api_client)
    capture.sid = sid
    stopped: list[bool] = []
    capture.stop = lambda: stopped.append(True) or b""

    resp = _record(api_client, sid, members=("cast",))  # not adjustable

    assert resp.status_code == 409 and resp.json()["code"] == "not_adjustable"
    assert stopped == [True]
    assert uc is not None
