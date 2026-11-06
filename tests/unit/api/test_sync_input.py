"""``GET /sync/listening-devices`` and ``POST /sync/sessions/{id}/capture/input`` over HTTP.

A bridge input records the sequence itself; the shell-out is faked with a small PCM payload and
the measurement runs through the real session/estimator path, exactly like a phone upload.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from sendspin_bridge.application import sync_tuning as uc
from sendspin_bridge.sync_tuning import audio_decode, input_recorder
from sendspin_sync import estimator
from sendspin_sync import sequence as seq
from tests.support.sync_sequence import sequence_recording
from tests.unit.api.test_sync_api import _ready, sync_env  # noqa: F401 (fixture)

LOCAL = {
    "id": "local:alsa_input.usb-mic",
    "kind": "local",
    "name": "USB microphone",
    "available": True,
    "node": "alsa_input.usb-mic",
    "mac": None,
}
PHONE = {
    "id": "bluetooth:AA:BB:CC:DD:EE:FF",
    "kind": "bluetooth",
    "name": "Phone",
    "available": True,
    "connected": True,
    "node": None,
    "mac": "AA:BB:CC:DD:EE:FF",
}
HEADSET = {
    "id": "headset-mic:11:22:33:44:55:66",
    "kind": "headset_microphone",
    "name": "Headset",
    "available": True,
    "node": None,
    "mac": "11:22:33:44:55:66",
}
CANDIDATE = {
    "id": "local:alsa_input.builtin-mic",
    "kind": "local",
    "name": "Built-in microphone",
    "available": True,
    "node": "alsa_input.builtin-mic",
    "mac": None,
}


@pytest.fixture(autouse=True)
def _fake_session_store(sync_env):  # noqa: F811
    """Every test here talks to the in-memory group control and session store."""
    return sync_env


@pytest.fixture(autouse=True)
def _clean_source_state():
    from sendspin_bridge.services.source import manager, state

    state.reset()
    manager.reset()
    yield
    state.reset()
    manager.reset()


@pytest.fixture
def source_view(monkeypatch):
    """What ``application.source.inputs()`` answers: the Sources UI's list, not the hardware."""
    from sendspin_bridge.application import source as source_uc

    view = {
        "active_input": None,
        "inputs": [dict(LOCAL), dict(PHONE), dict(HEADSET)],
        "local_candidates": [dict(CANDIDATE)],
        "capture_available": True,
        "capture_kind": "pipewire",
    }

    async def fake_inputs(*, include_monitors: bool = False):
        return view

    monkeypatch.setattr(source_uc, "inputs", fake_inputs)
    return view


@pytest.fixture
def clocked(monkeypatch, sync_env):  # noqa: F811
    """The sequence runs on the test's clock, without waiting."""
    _ctl, _store, _events, clock = sync_env
    monkeypatch.setattr(uc, "_sequence_sleep", lambda dt: setattr(clock, "now", clock.now + dt))
    return clock


class FakeRecorder:
    """The shell-out: remembers its argv and when, and returns a prepared PCM payload."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], float]] = []
        self.payload = b""
        self.failure: Exception | None = None
        self.loop: object = "not called"
        self.thread: threading.Thread | None = None

    def __call__(self, argv: list[str], seconds: float) -> bytes:
        try:
            self.loop = asyncio.get_running_loop()
        except RuntimeError:
            self.loop = None
        self.thread = threading.current_thread()
        self.calls.append((list(argv), seconds))
        if self.failure is not None:
            raise self.failure
        return self.payload

    @property
    def argv(self) -> list[str]:
        return self.calls[0][0]

    @property
    def seconds(self) -> float:
        return self.calls[0][1]


@pytest.fixture
def recorder(monkeypatch):
    fake = FakeRecorder()
    monkeypatch.setattr(input_recorder, "run_capture", fake)
    return fake


def _play_sequence(client, sid, members=("a", "b")):
    resp = client.post(f"/api/v1/sync/sessions/{sid}/sequence", json={"members": list(members)})
    assert resp.status_code == 202, resp.text
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        sequence = client.get(f"/api/v1/sync/sessions/{sid}").json()["sequence"]
        if sequence and sequence["phase"] in ("done", "failed"):
            return sequence
        time.sleep(0.01)
    raise AssertionError("the sequence did not finish")


def _capture(client, sid, input_id, seconds=None):
    body: dict[str, object] = {"input_id": input_id}
    if seconds is not None:
        body["seconds"] = seconds
    return client.post(f"/api/v1/sync/sessions/{sid}/capture/input", json=body)


# -- the device list -------------------------------------------------------------------------


def test_the_device_list_is_the_source_input_list(api_client, source_view):
    resp = api_client.get("/api/v1/sync/listening-devices")
    assert resp.status_code == 200, resp.text
    rows = resp.json()["devices"]
    assert {tuple(sorted(row)) for row in rows} == {("available", "id", "kind", "name")}
    assert [(row["id"], row["kind"], row["available"]) for row in rows] == [
        (LOCAL["id"], "local", True),
        (PHONE["id"], "bluetooth", True),
        (HEADSET["id"], "headset_microphone", True),
        (CANDIDATE["id"], "local", True),
    ]


# -- recording from an input -----------------------------------------------------------------


def test_a_recording_from_a_local_node_measures_every_member(api_client, source_view, clocked, recorder):
    sid = _ready(api_client)
    _play_sequence(api_client, sid)
    recorder.payload, _slots = sequence_recording({"a": 0.0, "b": 0.030})
    resp = _capture(api_client, sid, LOCAL["id"])
    assert resp.status_code == 200, resp.text
    assert {r["member"]: r["status"] for r in resp.json()["results"]} == {"a": "ok", "b": "ok"}
    assert recorder.argv == [
        "pw-record",
        "--target",
        "alsa_input.usb-mic",
        "--rate",
        str(audio_decode.DECODE_RATE),
        "--channels",
        "1",
        "--format",
        "s16",
        "-",
    ]
    session = api_client.get(f"/api/v1/sync/sessions/{sid}").json()
    assert {k: v["status"] for k, v in session["measurements"].items()} == {"a": "ok", "b": "ok"}
    rows = {r["member"]: r["after_ms"] for r in api_client.post(f"/api/v1/sync/sessions/{sid}/proposal").json()["rows"]}
    assert rows["b"] - rows["a"] == pytest.approx(30, abs=2)


def test_a_paired_phone_is_recorded_from_its_capture_node(api_client, source_view, clocked, recorder, monkeypatch):
    monkeypatch.setattr(
        uc, "_audio_source_nodes", lambda: ("bluez_source.AA_BB_CC_DD_EE_FF", "alsa_input.usb-mic"), raising=False
    )
    sid = _ready(api_client)
    _play_sequence(api_client, sid)
    recorder.payload, _slots = sequence_recording({"a": 0.0, "b": 0.030})
    resp = _capture(api_client, sid, PHONE["id"])
    assert resp.status_code == 200, resp.text
    assert recorder.argv[1:3] == ["--target", "bluez_source.AA_BB_CC_DD_EE_FF"]


def test_the_recorder_never_runs_on_the_bridge_loop(api_client, source_view, clocked, recorder):
    sid = _ready(api_client)
    _play_sequence(api_client, sid)
    recorder.payload, _slots = sequence_recording({"a": 0.0, "b": 0.030})
    assert _capture(api_client, sid, LOCAL["id"]).status_code == 200
    assert recorder.loop is None, "the recorder ran on the event loop"
    assert recorder.thread is not None and recorder.thread is not threading.main_thread()


def test_the_recording_length_is_the_sequences_and_is_clamped(api_client, source_view, clocked, recorder):
    sid = _ready(api_client)
    _play_sequence(api_client, sid)
    recorder.payload, _slots = sequence_recording({"a": 0.0, "b": 0.030})
    assert _capture(api_client, sid, LOCAL["id"]).status_code == 200
    # two members play 22 s: the whole sequence fits, the estimator's 20 s window is not the ceiling
    assert recorder.seconds == pytest.approx(seq.length_s(2, "device"))
    assert recorder.seconds >= seq.length_s(2, "device")
    assert _capture(api_client, sid, LOCAL["id"], seconds=1.0).status_code == 200
    assert recorder.calls[1][1] == pytest.approx(estimator.MIN_DURATION_S)
    assert _capture(api_client, sid, LOCAL["id"], seconds=1000.0).status_code == 200
    assert recorder.calls[2][1] == pytest.approx(seq.max_duration_s(2, "device"))


# -- borrow, never select --------------------------------------------------------------------


def test_an_input_the_source_client_is_streaming_from_is_refused_as_busy(api_client, source_view, recorder):
    from sendspin_bridge.services.source import state

    source_view["active_input"] = LOCAL["id"]
    state.publish(enabled=True, active_input=LOCAL["id"], streaming=True, state="streaming")
    sid = _ready(api_client)
    resp = _capture(api_client, sid, LOCAL["id"])
    assert resp.status_code == 409 and resp.json()["code"] == "input_busy"
    assert recorder.calls == []


def test_a_selected_but_idle_source_input_can_be_borrowed(api_client, source_view, clocked, recorder):
    """Enabled and chosen is not busy: nothing reads the node until a stream opens."""
    from sendspin_bridge.services.source import state

    source_view["active_input"] = LOCAL["id"]
    recorder.payload, _slots = sequence_recording({"a": 0.0, "b": 0.030})
    sid = _ready(api_client)
    _play_sequence(api_client, sid)
    state.publish(enabled=True, active_input=LOCAL["id"], streaming=False, state="waiting")
    resp = _capture(api_client, sid, LOCAL["id"])
    assert resp.status_code == 200, resp.text
    assert recorder.calls, "the idle input was not recorded"


def test_the_active_source_input_is_unchanged_by_the_recording(api_client, source_view, clocked, recorder, tmp_config):
    from sendspin_bridge.services.source import state

    saved = json.loads(tmp_config.read_text())
    saved["SENDSPIN_SOURCE_ACTIVE"] = CANDIDATE["id"]
    tmp_config.write_text(json.dumps(saved))
    source_view["active_input"] = CANDIDATE["id"]
    sid = _ready(api_client)
    _play_sequence(api_client, sid)
    recorder.payload, _slots = sequence_recording({"a": 0.0, "b": 0.030})
    assert _capture(api_client, sid, LOCAL["id"]).status_code == 200
    assert json.loads(tmp_config.read_text())["SENDSPIN_SOURCE_ACTIVE"] == CANDIDATE["id"]
    assert state.snapshot().active_input is None and state.snapshot().enabled is False


# -- the stable problems ---------------------------------------------------------------------


def test_an_unknown_input_is_unknown_input(api_client, source_view, recorder):
    sid = _ready(api_client)
    resp = _capture(api_client, sid, "local:not-an-input")
    assert resp.status_code == 404 and resp.json()["code"] == "unknown_input"
    assert recorder.calls == []


def test_a_node_that_is_not_present_is_input_unavailable(api_client, source_view, recorder):
    source_view["inputs"][0]["available"] = False
    sid = _ready(api_client)
    resp = _capture(api_client, sid, LOCAL["id"])
    assert resp.status_code == 409 and resp.json()["code"] == "input_unavailable"
    assert recorder.calls == []


def test_a_phone_with_no_capture_node_is_input_unavailable(api_client, source_view, recorder, monkeypatch):
    monkeypatch.setattr(uc, "_audio_source_nodes", lambda: ("alsa_input.usb-mic",), raising=False)
    sid = _ready(api_client)
    resp = _capture(api_client, sid, PHONE["id"])
    assert resp.status_code == 409 and resp.json()["code"] == "input_unavailable"
    assert recorder.calls == []


def test_a_measurement_with_no_sequence_is_not_recorded(api_client, source_view, recorder):
    sid = _ready(api_client)
    resp = _capture(api_client, sid, LOCAL["id"])
    assert resp.status_code == 409 and resp.json()["code"] == "session_state"
    assert recorder.calls == []


def test_a_recorder_that_refuses_the_node_is_input_unavailable(api_client, source_view, clocked, recorder):
    sid = _ready(api_client)
    _play_sequence(api_client, sid)
    recorder.failure = input_recorder.RecorderUnavailable("parecord: no such device")
    resp = _capture(api_client, sid, LOCAL["id"])
    assert resp.status_code == 409 and resp.json()["code"] == "input_unavailable"
    assert "no such device" in resp.json()["detail"]


def test_one_recording_at_a_time_per_session_is_capture_running(api_client, source_view, clocked, recorder):
    sid = _ready(api_client)
    _play_sequence(api_client, sid)
    lock = threading.Lock()
    lock.acquire()
    with uc._upload_locks_lock:
        uc._upload_locks[sid] = lock
    try:
        resp = _capture(api_client, sid, LOCAL["id"])
        assert resp.status_code == 409 and resp.json()["code"] == "capture_running"
        assert recorder.calls == []
    finally:
        lock.release()
        with uc._upload_locks_lock:
            uc._upload_locks.pop(sid, None)


def test_a_session_that_is_not_measuring_is_refused(api_client, source_view, recorder):
    sid = _ready(api_client)
    assert api_client.post(f"/api/v1/sync/sessions/{sid}/finish").json()["state"] == "restoring"
    resp = _capture(api_client, sid, LOCAL["id"])
    assert resp.status_code == 409 and resp.json()["code"] == "session_state"
    assert recorder.calls == []


def test_the_input_routes_need_credentials_when_auth_is_on(tmp_config):
    from tests.support.api_client import make_client

    client = make_client({"AUTH_ENABLED": True})
    assert client.get("/api/v1/sync/listening-devices").status_code == 401
    assert client.post("/api/v1/sync/sessions/x/capture/input", json={"input_id": LOCAL["id"]}).status_code == 401
