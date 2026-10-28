"""Calibrating a speaker's air-to-sound latency inside the group sync tuning wizard (A16c).

While the phone sequence plays, a watcher stamps each solo in RAW time, arms an air click fold for
every bridge member the air sensor is effective for, and asks its daemon for the written fold. After
the upload, ``L_as = A − H_cal`` for every bridge member (``A`` = acoustic lag against the session's
reference, ``H_cal`` = unwrap(φ_air − φ_T)), with the first failing reason otherwise.

The env drives the real use cases, router, session, store, config persistence and reconfiguration
with a fake air sensor service (the parent's air fold) and a fake daemon whose stdin answers
``air_fold`` through ``air_sensor.fold`` (the written fold). The estimator's results are stubbed.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import uuid
from types import SimpleNamespace

import pytest

from sendspin_bridge.application import sync_tuning as uc
from sendspin_bridge.application.sync_tuning import air_span
from sendspin_bridge.services.bluetooth.air_sensor_service import AirClickFold, AirFoldResult
from sendspin_bridge.sync_tuning import capture_tokens, session_store
from sendspin_bridge.sync_tuning.session_store import SessionStore
from sendspin_sync import sequence as seq
from sendspin_sync.models import Measurement, Member, Recording
from tests.support.sync_fakes import FakeControl, FakeHost

MAC = "AA:BB:CC:DD:EE:07"
ADAPTER = "C0:FB:F9:62:D7:D6"
OCTET = {"content-type": "application/octet-stream"}


def test_air_span_is_offset_by_settle_and_host_latency():
    assert air_span((100.0e6, 107.0e6), 800.0) == pytest.approx((100.0e6 + 0.8e6 + 0.82e6, 107.0e6 - 0.3e6 + 0.82e6))
    assert air_span((100.0e6, 107.0e6), None) == pytest.approx((100.0e6 + 0.8e6 + 0.02e6, 107.0e6 - 0.3e6 + 0.02e6))


def test_an_air_fold_finished_with_a_lower_bound_folds_only_frames_inside_it():
    fold = AirClickFold(from_us=0.0)
    for i in range(200):
        fold.add_energy(i * 10_000.0, 1.0, 0, rate=44100, subbands=8, samples=128)
    assert fold.result(1_990_000.0).frames == 200
    assert fold.result(1_990_000.0, from_us=1_000_000.0).frames == 100


# -- the env ------------------------------------------------------------------------------------


class _Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeAirService:
    """The parent's air sensor service: arm_fold / finish_fold with results from the env's table."""

    def __init__(self, loop, results) -> None:
        self.loop = loop
        self._results = results
        self.armed: dict[str, tuple[str, float]] = {}
        self.arms: list[tuple[str, float]] = []
        self.finished: list[tuple[str, float | None, float]] = []

    def subscribe(self, mac):
        return None

    def unsubscribe(self, mac):
        return None

    def arm_fold(self, mac, from_us, *, max_s=30.0):
        fold_id = uuid.uuid4().hex
        self.armed[fold_id] = (mac.upper(), from_us)
        self.arms.append((mac.upper(), from_us))
        return fold_id

    def finish_fold(self, fold_id, to_us, *, from_us=None):
        mac, _ = self.armed.pop(fold_id)
        self.finished.append((mac, from_us, to_us))
        return self._results(mac)


class _DaemonStdin:
    """The daemon's stdin: records every line and answers ``air_fold`` like the daemon's written fold."""

    class _Transport:
        def get_write_buffer_size(self):
            return 0

        def is_closing(self):
            return False

    def __init__(self, sink, answer) -> None:
        self.transport = self._Transport()
        self._sink, self._answer = sink, answer

    def write(self, data):
        self._sink.append(data)
        msg = json.loads(data)
        if msg.get("cmd") == "air_fold":
            self._answer(msg)

    async def drain(self):
        return None


@pytest.fixture
def sync_air_env(monkeypatch, tmp_config, tmp_path, api_client):
    from sendspin_bridge.application import devices as devices_uc
    from sendspin_bridge.bridge.client import SendspinClient
    from sendspin_bridge.config.players import player_id_for_mac
    from sendspin_bridge.services.bluetooth import air_sensor_service as air_mod
    from sendspin_bridge.services.bluetooth import device_registry
    from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot
    from sendspin_bridge.services.lifecycle import bridge_runtime_state

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    built: list[SimpleNamespace] = []

    def build(
        *,
        reference: str | None = "kiosk",
        acoustic: dict | None = None,
        air_phase: dict | None = None,
        sched_phase: dict | None = None,
        air_state: str = "locked",
        effective: bool = True,
        switch: bool = True,
        air_epochs: int = 1,
        codec: str = "sbc",
        host_latency_ms: float | None = 800.0,
        delay_report_ms: float | None = 150.0,
    ) -> SimpleNamespace:
        acoustic = {"kiosk": 1200.0, "eneby": 1415.0} if acoustic is None else acoustic
        air_phase = {"eneby": 2012.0} if air_phase is None else air_phase
        sched_phase = {"eneby": 2000.0} if sched_phase is None else sched_phase

        # Music Assistant: a Sendspin web player (the reference) and one bridge speaker.
        ctl = FakeControl()
        ctl.member_list = [
            Member("kiosk", "Kiosk (headset)", True, "sendspin", delay_ms=0),
            Member("eneby", "ENEBY", True, "bridge", delay_ms=0),
        ]
        ctl.delays_now = {"kiosk": 0, "eneby": 0}
        ctl.volumes = {"kiosk": 40, "eneby": 40}
        ctl.muted_now = {"kiosk": False, "eneby": False}
        clock = _Clock(1000.0)
        events: list[dict] = []
        store = SessionStore(
            control_factory=lambda: ctl,
            host=FakeHost(),
            publish=events.append,
            snapshot_dir=lambda: tmp_path,
            clock=clock,
        )
        monkeypatch.setattr(session_store, "_store", store)
        monkeypatch.setattr(uc, "_control", lambda: ctl)
        group = {"id": "g", "name": "Downstairs", "members": [{"id": "kiosk"}, {"id": "eneby"}]}
        monkeypatch.setattr(uc, "get_ma_groups", lambda: [group])
        monkeypatch.setattr(capture_tokens, "tokens", capture_tokens.CaptureTokens(clock=clock))
        monkeypatch.setattr(uc, "_capture_base_url", lambda: "http://192.168.1.5:8080")
        monkeypatch.setattr(uc, "capture_https_base", lambda: None)

        # The bridge: one speaker with a fake daemon process.
        pid = player_id_for_mac(MAC)
        tmp_config.write_text(
            json.dumps(
                {
                    "CONFIG_SCHEMA_VERSION": 6,
                    "EXPERIMENTAL_AIR_SENSOR": switch,
                    "PLAYOUT_SYNC": "measure" if effective else "off",
                    "players": [{"id": pid, "name": "ENEBY", "backend": {"type": "bluetooth_a2dp", "mac": MAC}}],
                    "PLAYER_STATE": {},
                }
            )
        )
        writes: list[bytes] = []
        bt = SimpleNamespace(
            mac_address=MAC, connected=False, check_bluetooth_available=lambda: True, effective_adapter_mac=ADAPTER
        )
        client = SendspinClient("ENEBY", "localhost", 9000, bt_manager=bt)
        client.config_id = pid
        client.air_to_sound_latency = []
        snapshot = DeviceRegistrySnapshot(active_clients=[client])
        monkeypatch.setattr(device_registry, "get_device_registry_snapshot", lambda: snapshot)
        monkeypatch.setattr(uc, "get_ma_player_ids", lambda: {client.player_id: "eneby"})
        monkeypatch.setattr(bridge_runtime_state, "get_main_loop", lambda: loop)
        monkeypatch.setattr(devices_uc, "_sync_ha_options_later", lambda: None)

        def answer(msg):
            phase = sched_phase.get("eneby")
            fold = {
                "id": msg["id"],
                "phase_ms": phase,
                "ok": phase is not None,
                "reason": None if phase is not None else "no_written_clicks",
            }
            client._update_status({"air_sensor": {**client.status.get("air_sensor"), "fold": fold}})

        def air_result(mac):
            if codec != "sbc":
                return AirFoldResult(None, False, 0, None, None, None, reason="not_sbc")
            if air_epochs > 1:
                return AirFoldResult(None, False, air_epochs, "sbc", 44100, ADAPTER, reason="air_epoch_changed")
            phase = air_phase.get("eneby")
            if phase is None:
                return AirFoldResult(None, False, 1, "sbc", 44100, ADAPTER, reason="no_air_clicks", frames=900)
            return AirFoldResult(phase, True, 1, "sbc", 44100, ADAPTER, frames=900, clicks=4, quality=5.0)

        service = FakeAirService(loop, air_result) if switch else None
        air_mod.set_air_sensor_service(service)

        async def _start():
            client._daemon_proc = SimpleNamespace(returncode=None, pid=4242, stdin=_DaemonStdin(writes, answer))
            status: dict = {"bt_reported_delay_ms": delay_report_ms, "playout": {"host_latency_ms": host_latency_ms}}
            if effective and switch:
                status["air_sensor"] = {
                    "state": air_state,
                    "accepts": [1],
                    "codec": codec,
                    "rate": 44100,
                    "adapter": ADAPTER,
                }
            elif switch:
                status["air_sensor"] = {"state": "off", "reason": "needs_measure"}
            client._update_status(status)
            await asyncio.sleep(0)

        asyncio.run_coroutine_threadsafe(_start(), loop).result(timeout=5)

        # RAW time and the sequence's waits: the sequence moves on only after the watcher saw a phase.
        raw = _Clock(5_000_000_000.0)
        monkeypatch.setattr(uc, "_raw_now_us", raw)
        monkeypatch.setattr(uc, "_AIR_POLL_S", 0.001)
        monkeypatch.setattr(uc, "_air_wait_until", lambda _us: None)
        env = SimpleNamespace()

        def sleep(dt):
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                watch = uc._air_watches.get(env.sid)
                current = store.get(env.sid).sequence
                if watch is None or current is None or watch.seen == (current["phase"], current["index"]):
                    break
                time.sleep(0.001)
            clock.now += dt
            raw.now += dt * 1e6

        monkeypatch.setattr(uc, "_sequence_sleep", sleep)

        # The estimator: each member's acoustic phase on the recording's clock.
        def windows(_pcm, sample_rate, slots, _offset):
            return [Recording(member=s.member, start_time_s=0.0, sample_rate=sample_rate, pcm=b"") for s in slots]

        def estimate(rec):
            phase = acoustic.get(rec.member)
            if phase is None:
                return Measurement(member=rec.member, status="not_heard", reason="no_clicks")
            return Measurement(member=rec.member, status="ok", phase_ms=phase, confidence=1.0, clicks=5, spread_ms=2.5)

        monkeypatch.setattr(seq, "click_times", lambda _pcm, _rate: [])
        monkeypatch.setattr(seq, "align", lambda _events, _slots, _duration: 0.0)
        monkeypatch.setattr(seq, "windows", windows)
        monkeypatch.setattr("sendspin_sync.session.estimator.estimate", estimate)
        from sendspin_bridge.sync_tuning import audio_decode

        monkeypatch.setattr(audio_decode, "decode_to_mono", lambda _body, max_seconds: (b"\0\0" * 16000, 16000))

        def ready() -> str:
            sid = api_client.post("/api/v1/sync/sessions", json={"group_id": "g", "mode": "semi"}).json()["id"]
            env.sid = sid
            assert api_client.post(f"/api/v1/sync/sessions/{sid}/signal", json={"action": "stop"}).status_code == 200
            assert api_client.post(f"/api/v1/sync/sessions/{sid}/signal", json={"action": "start"}).status_code == 200
            if reference is not None:
                r = api_client.post(f"/api/v1/sync/sessions/{sid}/reference", json={"member": reference})
                assert r.status_code == 200, r.text
            return sid

        def play_sequence() -> None:
            r = api_client.post(f"/api/v1/sync/sessions/{env.sid}/sequence", json={"members": ["kiosk", "eneby"]})
            assert r.status_code == 202, r.text
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                current = store.get(env.sid).sequence
                if current and current["phase"] in ("done", "failed"):
                    break
                time.sleep(0.005)
            for t in list(session_store._background):  # the sequence, its watcher and the fold workers
                t.join(timeout=5)

        def record_and_get_session() -> dict:
            sid = ready()
            play_sequence()
            link = api_client.post(f"/api/v1/sync/sessions/{sid}/capture-link", json={"lang": "en"}).json()
            up = api_client.post(f"/api/v1/sync/capture/{link['token']}/recording", content=b"x" * 100, headers=OCTET)
            assert up.status_code == 200, up.text
            return api_client.get(f"/api/v1/sync/sessions/{sid}").json()

        def sent(_member: str) -> list[dict]:
            return [json.loads(line) for line in writes]

        def player_config(_member: str) -> dict:
            return json.loads(tmp_config.read_text())["players"][0]

        env.__dict__.update(
            client=api_client,
            store=store,
            events=events,
            service=service,
            bridge=client,
            raw=raw,
            ready=ready,
            play_sequence=play_sequence,
            record_and_get_session=record_and_get_session,
            sent=sent,
            player_config=player_config,
        )
        built.append(env)
        return env

    yield build
    air_mod.set_air_sensor_service(None)
    uc._air_watches.clear()
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=5)
    loop.close()


def _eneby(session: dict) -> dict:
    return next(c for c in session["air_calibrations"] if c["member"] == "eneby")


BASE = {
    "reference": "kiosk",
    "acoustic": {"kiosk": 1200.0, "eneby": 1415.0},
    "air_phase": {"eneby": 2012.0},
    "sched_phase": {"eneby": 2000.0},
}


# -- candidates ---------------------------------------------------------------------------------


def test_latency_is_acoustic_lag_minus_host_placement(sync_air_env):
    env = sync_air_env(**BASE)
    c = _eneby(env.record_and_get_session())
    assert c["eligible"] and c["reason"] is None and c["acoustic_lag_ms"] == pytest.approx(215.0)
    assert c["host_placement_ms"] == pytest.approx(12.0) and c["latency_ms"] == pytest.approx(203.0)
    assert (c["codec"], c["rate"], c["adapter"], c["delay_report_ms"]) == ("sbc", 44100, ADAPTER, 150.0)


def test_only_bridge_members_get_a_candidate(sync_air_env):
    session = sync_air_env(**BASE).record_and_get_session()
    assert [c["member"] for c in session["air_calibrations"]] == ["eneby"]


def test_live_lock_is_not_required(sync_air_env):
    env = sync_air_env(**BASE, air_state="acquiring")
    assert _eneby(env.record_and_get_session())["eligible"]


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        ({"effective": False}, "not_effective"),
        ({"switch": False}, "not_effective"),
        ({"reference": None}, "no_reference"),
        ({"acoustic": {"kiosk": 1200.0, "eneby": None}}, "not_measured"),
        ({"acoustic": {"kiosk": None, "eneby": 1415.0}}, "not_measured"),
        ({"air_phase": {"eneby": None}}, "no_air_clicks"),
        ({"sched_phase": {"eneby": None}}, "no_written_clicks"),
        ({"air_epochs": 2}, "air_epoch_changed"),
        ({"codec": "aac"}, "not_sbc"),
    ],
)
def test_ineligible_reasons(sync_air_env, kw, reason):
    c = _eneby(sync_air_env(**{**BASE, **kw}).record_and_get_session())
    assert not c["eligible"] and c["reason"] == reason and c["latency_ms"] is None


@pytest.mark.parametrize(
    ("kw", "reason"),
    [
        # Not effective wins over every later reason; no reference wins over a missing measurement.
        ({"effective": False, "reference": None, "air_phase": {"eneby": None}}, "not_effective"),
        ({"reference": None, "acoustic": {"kiosk": 1.0, "eneby": None}}, "no_reference"),
        ({"acoustic": {"kiosk": 1.0, "eneby": None}, "codec": "aac"}, "not_measured"),
        ({"codec": "aac", "sched_phase": {"eneby": None}}, "not_sbc"),
        ({"air_phase": {"eneby": None}, "sched_phase": {"eneby": None}}, "no_air_clicks"),
    ],
)
def test_reasons_come_in_the_spec_order(sync_air_env, kw, reason):
    assert _eneby(sync_air_env(**{**BASE, **kw}).record_and_get_session())["reason"] == reason


def test_phase_wrap_is_unwrapped(sync_air_env):
    env = sync_air_env(
        reference="kiosk",
        acoustic={"kiosk": 9_950.0, "eneby": 165.0},
        air_phase={"eneby": 9_995.0},
        sched_phase={"eneby": 9_990.0},
    )
    c = _eneby(env.record_and_get_session())
    assert c["acoustic_lag_ms"] == pytest.approx(215.0) and c["latency_ms"] == pytest.approx(210.0)


def test_a_reference_picked_after_the_upload_makes_the_candidate_eligible(sync_air_env):
    env = sync_air_env(**{**BASE, "reference": None})
    session = env.record_and_get_session()
    assert _eneby(session)["reason"] == "no_reference"
    r = env.client.post(f"/api/v1/sync/sessions/{env.sid}/reference", json={"member": "kiosk"})
    assert r.status_code == 200
    assert _eneby(r.json())["latency_ms"] == pytest.approx(203.0)


def test_no_candidates_before_the_upload(sync_air_env):
    env = sync_air_env(**BASE)
    env.ready()
    env.play_sequence()
    assert env.client.get(f"/api/v1/sync/sessions/{env.sid}").json()["air_calibrations"] == []


def test_sync_events_carry_the_candidates(sync_air_env):
    env = sync_air_env(**BASE)
    env.record_and_get_session()
    uploaded = [e for e in env.events if (e.get("sequence") or {}).get("uploaded")]
    assert uploaded and _eneby(uploaded[-1])["latency_ms"] == pytest.approx(203.0)


# -- the folds ----------------------------------------------------------------------------------


def test_the_air_fold_covers_the_solo_offset_by_settle_and_host_latency(sync_air_env):
    env = sync_air_env(**BASE, host_latency_ms=800.0)
    env.record_and_get_session()
    [(mac, armed_from)] = env.service.arms
    [(fmac, lo, hi)] = env.service.finished
    assert mac == fmac == MAC and not env.service.armed
    # Armed at the solo's start + SETTLE_S; the span starts h = 800 + 20 ms later and lasts the solo
    # minus SETTLE_S and TAIL_S (the stamps are exact on the env's clock).
    assert lo - armed_from == pytest.approx(0.82e6)
    assert hi - lo == pytest.approx((seq.solo_seconds("device") - seq.SETTLE_S - seq.TAIL_S) * 1e6)


def test_the_written_fold_asks_the_daemon_for_12_s_ending_at_the_air_span_end(sync_air_env):
    env = sync_air_env(**BASE)
    env.record_and_get_session()
    [(_mac, _lo, hi)] = env.service.finished
    [fold] = [m for m in env.sent("eneby") if m["cmd"] == "air_fold"]
    assert fold["to_us"] == pytest.approx(hi) and fold["to_us"] - fold["from_us"] == pytest.approx(12e6)
    assert fold["v"] == 1


def test_not_effective_arms_nothing_and_asks_the_daemon_nothing(sync_air_env):
    env = sync_air_env(**{**BASE, "effective": False})
    env.record_and_get_session()
    assert env.service.arms == [] and [m for m in env.sent("eneby") if m["cmd"] == "air_fold"] == []


def test_switch_off_starts_no_watcher(sync_air_env, monkeypatch):
    started: list = []
    monkeypatch.setattr(uc, "_AirWatch", lambda *a, **kw: started.append(a))
    env = sync_air_env(**{**BASE, "switch": False})
    env.ready()
    env.play_sequence()
    assert started == []


def test_a_failure_before_the_finish_still_detaches_the_armed_fold(sync_air_env, monkeypatch):
    def boom(_us):
        raise RuntimeError("wait failed")

    env = sync_air_env(**BASE)
    monkeypatch.setattr(uc, "_air_wait_until", boom)  # replaces the env's no-op
    session = env.record_and_get_session()
    assert env.service.armed == {} and len(env.service.finished) == 1
    assert not _eneby(session)["eligible"]


# -- save ---------------------------------------------------------------------------------------


def test_save_stores_entry_and_applies_it(sync_air_env):
    env = sync_air_env(**BASE)
    env.record_and_get_session()
    r = env.client.post(f"/api/v1/sync/sessions/{env.sid}/air-calibrations", json={"members": ["eneby"]})
    assert r.status_code == 200, r.text
    assert [c["member"] for c in r.json()] == ["eneby"]
    entry = env.player_config("eneby")["air_to_sound_latency"][0]
    assert (entry["codec"], entry["rate"], entry["adapter"]) == ("sbc", 44100, ADAPTER)
    assert (entry["reference"], entry["source"], entry["spread_ms"]) == ("Kiosk (headset)", "sync_wizard", 2.5)
    assert entry["latency_ms"] == pytest.approx(203.0) and entry["measured_at"]
    assert env.sent("eneby")[-1] == {"cmd": "set_air_calibration", "v": 1, "latency_ms": 203.0, "protocol_version": 2}


def test_saving_an_ineligible_member_is_refused_and_stores_nothing(sync_air_env):
    env = sync_air_env(**{**BASE, "air_phase": {"eneby": None}})
    env.record_and_get_session()
    r = env.client.post(f"/api/v1/sync/sessions/{env.sid}/air-calibrations", json={"members": ["eneby"]})
    assert r.status_code == 409 and r.json()["code"] == "not_eligible"
    assert "air_to_sound_latency" not in env.player_config("eneby")


def test_saving_an_unknown_member_is_refused(sync_air_env):
    env = sync_air_env(**BASE)
    env.record_and_get_session()
    r = env.client.post(f"/api/v1/sync/sessions/{env.sid}/air-calibrations", json={"members": ["kiosk"]})
    assert r.status_code == 409 and r.json()["code"] == "not_eligible"


def test_a_latency_outside_the_stored_range_is_refused(sync_air_env):
    env = sync_air_env(**{**BASE, "air_phase": {"eneby": 2300.0}})  # L = 215 - 300 < 0
    c = _eneby(env.record_and_get_session())
    assert c["latency_ms"] == pytest.approx(-85.0)
    r = env.client.post(f"/api/v1/sync/sessions/{env.sid}/air-calibrations", json={"members": ["eneby"]})
    assert r.status_code == 409 and r.json()["code"] == "not_eligible"
