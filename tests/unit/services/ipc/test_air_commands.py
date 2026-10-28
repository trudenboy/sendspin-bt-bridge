"""Air IPC commands (parent → daemon): encode, decode, validation and daemon intake."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from unittest.mock import MagicMock

import pytest

from sendspin_bridge.services.bluetooth.a2dp_air_envelope import AirBatch
from sendspin_bridge.services.ipc import daemon_process
from sendspin_bridge.services.ipc.bridge_daemon import BridgeDaemon, DaemonArgs
from sendspin_bridge.services.ipc.commands import (
    AIR_ENVELOPE_VERSION,
    AirEnvelope,
    AirFold,
    AirSource,
    InvalidCommand,
    SetAirCalibration,
    air_envelope_from_batch,
    decode_command,
    encode_command,
)

NOW = 5_000_000_000.0
GOOD = {
    "cmd": "air_envelope",
    "v": 1,
    "epoch": 3,
    "seq": 128,
    "u0_us": 371_519.3,
    "t0_us": NOW - 900_000.0,
    "frame_us": 2902.494,
    "timestamp": "kernel",
    "rate": 44100,
    "subbands": 8,
    "blocks": 16,
    "bands": [1, 2],
    "complete_lag_us": None,
    "anchor_reset": False,
    "e": [10, 20, 255],
}
SOURCE = {
    "cmd": "air_source",
    "v": 1,
    "state": "unsupported_codec",
    "reason": "codec:aac",
    "codec": "aac",
    "adapter": "C0:FB:F9:62:D7:D6",
    "index": 1,
    "handle": 70,
    "in_step": False,
    "monitor_drops": None,
    "ipc_dropped": 0,
}


def dec(msg):
    return decode_command(msg, now_raw_us=lambda: NOW)


# ── The brief's tests ────────────────────────────────────────────────────────


def test_envelope_round_trips_with_the_protocol_version():
    cmd = dec(GOOD)
    assert isinstance(cmd, AirEnvelope) and cmd.e == (10, 20, 255) and cmd.bands == (1, 2)
    wire = json.loads(json.dumps(encode_command(cmd)))
    assert wire["protocol_version"] == 2 and wire["v"] == 1
    assert dec(wire) == cmd


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("v", 2),
        ("epoch", -1),
        ("seq", "x"),
        ("t0_us", float("nan")),
        ("t0_us", NOW + 11e6),
        ("frame_us", 100.0),
        ("timestamp", "wall"),
        ("rate", 22050),
        ("subbands", 6),
        ("blocks", 5),
        ("bands", [8]),
        ("complete_lag_us", 500_000.0),
        ("e", []),
        ("e", [256]),
        ("e", [1] * 257),
        ("e", [True]),
        ("u0_us", -1.0),
        ("u0_us", float("inf")),
        ("u0_us", 128 * 2902.494 - 10.0),
        ("anchor_reset", "yes"),
    ],
)
def test_envelope_rejects_out_of_range(key, value):
    with pytest.raises(InvalidCommand):
        dec({**GOOD, key: value})


def test_source_and_calibration_and_fold():
    s = dec(SOURCE)
    assert isinstance(s, AirSource) and s.reason == "codec:aac"
    assert dec({"cmd": "set_air_calibration", "v": 1, "latency_ms": 9999}) == SetAirCalibration(latency_ms=2000.0)
    assert encode_command(SetAirCalibration(latency_ms=203.0)) == {
        "cmd": "set_air_calibration",
        "v": 1,
        "latency_ms": 203.0,
        "protocol_version": 2,
    }
    assert dec({"cmd": "set_air_calibration", "v": 1, "latency_ms": None}) == SetAirCalibration(latency_ms=None)
    with pytest.raises(InvalidCommand):
        dec({"cmd": "air_fold", "v": 1, "id": "a", "from_us": 0.0, "to_us": 31e6})  # > 30 s span
    assert isinstance(dec({"cmd": "air_fold", "v": 1, "id": "a" * 8, "from_us": 0.0, "to_us": 10e6}), AirFold)


def test_source_reason_is_bounded():
    with pytest.raises(InvalidCommand):
        dec({"cmd": "air_source", "v": 1, "state": "streaming", "reason": "x" * 81})


# ── Full envelopes on the wire ───────────────────────────────────────────────


def test_envelope_encodes_the_full_wire_message():
    cmd = dec({**GOOD, "complete_lag_us": 8123.0, "anchor_reset": True})
    assert encode_command(cmd) == {
        "cmd": "air_envelope",
        "protocol_version": 2,
        "v": 1,
        "epoch": 3,
        "seq": 128,
        "u0_us": 371_519.3,
        "t0_us": NOW - 900_000.0,
        "frame_us": 2902.494,
        "timestamp": "kernel",
        "rate": 44100,
        "subbands": 8,
        "blocks": 16,
        "bands": [1, 2],
        "complete_lag_us": 8123.0,
        "anchor_reset": True,
        "e": [10, 20, 255],
    }


def test_source_encodes_the_full_wire_message_and_round_trips():
    cmd = dec(SOURCE)
    wire = encode_command(cmd)
    assert wire == {**SOURCE, "protocol_version": 2}
    assert dec(json.loads(json.dumps(wire))) == cmd


def test_fold_encodes_the_full_wire_message_and_round_trips():
    cmd = AirFold(id="c1f3a9", from_us=1_000.0, to_us=12_001_000.0)
    wire = encode_command(cmd)
    assert wire == {
        "cmd": "air_fold",
        "v": 1,
        "id": "c1f3a9",
        "from_us": 1_000.0,
        "to_us": 12_001_000.0,
        "protocol_version": 2,
    }
    assert dec(wire) == cmd


def test_null_calibration_encodes_null():
    assert encode_command(SetAirCalibration(latency_ms=None)) == {
        "cmd": "set_air_calibration",
        "v": 1,
        "latency_ms": None,
        "protocol_version": 2,
    }


def test_envelope_version_constant_is_one():
    assert AIR_ENVELOPE_VERSION == 1


# ── More validation ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("v", True),  # bools are not ints
        ("v", "1"),
        ("epoch", True),
        ("epoch", 1.0),
        ("epoch", 2**40),
        ("seq", -1),
        ("seq", 2**40),
        ("t0_us", True),
        ("t0_us", "5"),
        ("t0_us", NOW - 11e6),
        ("frame_us", 20_001.0),
        ("frame_us", float("nan")),
        ("timestamp", None),
        ("rate", 44100.0),
        ("subbands", True),
        ("blocks", 0),
        ("bands", []),
        ("bands", [1, 1]),
        ("bands", [-1]),
        ("bands", "12"),
        ("bands", [1.0]),
        ("bands", [True]),
        ("complete_lag_us", -1.0),
        ("complete_lag_us", float("nan")),
        ("complete_lag_us", False),
        ("anchor_reset", 0),
        ("anchor_reset", None),
        ("e", "abc"),
        ("e", [1.0]),
        ("e", [-1]),
        ("e", None),
        ("e", {"0": 1}),
        ("u0_us", 1e14),
    ],
)
def test_envelope_rejects_more_bad_fields(key, value):
    with pytest.raises(InvalidCommand):
        dec({**GOOD, key: value})


@pytest.mark.parametrize("key", [k for k in GOOD if k != "cmd"])
def test_envelope_requires_every_field(key):
    msg = {k: v for k, v in GOOD.items() if k != key}
    with pytest.raises(InvalidCommand):
        dec(msg)


def test_envelope_accepts_the_bounds():
    # u0 one microsecond under seq·frame_us; 256 cells; t0 at the edge of the window.
    cmd = dec({**GOOD, "u0_us": 128 * 2902.494 - 1.0, "e": [0] * 256, "t0_us": NOW + 10e6, "complete_lag_us": 0})
    assert isinstance(cmd, AirEnvelope) and len(cmd.e) == 256 and cmd.complete_lag_us == 0.0


def test_envelope_integers_on_the_wire_become_floats():
    cmd = dec({**GOOD, "u0_us": 400_000, "frame_us": 2902, "seq": 0})
    assert isinstance(cmd, AirEnvelope)
    assert type(cmd.u0_us) is float and type(cmd.frame_us) is float


def test_envelope_messages_do_not_echo_the_bad_value():
    with pytest.raises(InvalidCommand) as info:
        dec({**GOOD, "timestamp": "\x1b[31mEVIL"})
    assert "EVIL" not in str(info.value) and "timestamp" in str(info.value)


def test_envelope_window_uses_the_raw_clock_by_default():
    now = time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW) / 1000
    assert isinstance(decode_command({**GOOD, "t0_us": now}), AirEnvelope)
    with pytest.raises(InvalidCommand):
        decode_command({**GOOD, "t0_us": now + 60e6})


def test_other_commands_ignore_the_clock():
    def _boom():
        raise AssertionError("clock read for a non-envelope command")

    assert decode_command(
        {"cmd": "set_air_calibration", "v": 1, "latency_ms": 1}, now_raw_us=_boom
    ) == SetAirCalibration(1.0)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("v", 2),
        ("state", "dancing"),
        ("state", None),
        ("reason", 5),
        ("reason", "bad\nline"),
        ("codec", "x" * 33),
        ("codec", 3),
        ("adapter", "not-a-mac"),
        ("adapter", "C0:FB:F9:62:D7:D6\n"),
        ("index", -1),
        ("index", True),
        ("index", 70_000),
        ("handle", 0x1000),
        ("handle", "70"),
        ("in_step", "no"),
        ("monitor_drops", -1),
        ("monitor_drops", 1.5),
        ("ipc_dropped", None),
        ("ipc_dropped", -3),
    ],
)
def test_source_rejects_bad_fields(key, value):
    with pytest.raises(InvalidCommand):
        dec({**SOURCE, key: value})


@pytest.mark.parametrize("key", [k for k in SOURCE if k != "cmd"])
def test_source_requires_every_field(key):
    with pytest.raises(InvalidCommand):
        dec({k: v for k, v in SOURCE.items() if k != key})


def test_source_accepts_nulls_where_allowed():
    cmd = dec(
        {
            **SOURCE,
            "state": "unavailable",
            "reason": None,
            "codec": None,
            "adapter": None,
            "index": None,
            "handle": None,
        }
    )
    assert isinstance(cmd, AirSource) and cmd.state == "unavailable" and cmd.adapter is None


@pytest.mark.parametrize(
    "msg",
    [
        {"cmd": "set_air_calibration", "v": 1},
        {"cmd": "set_air_calibration", "v": 1, "latency_ms": "200"},
        {"cmd": "set_air_calibration", "v": 1, "latency_ms": True},
        {"cmd": "set_air_calibration", "v": 1, "latency_ms": float("nan")},
        {"cmd": "set_air_calibration", "v": 1, "latency_ms": float("inf")},
    ],
)
def test_calibration_rejects_bad_values(msg):
    with pytest.raises(InvalidCommand):
        dec(msg)


def test_calibration_clamps_negative_to_zero():
    assert dec({"cmd": "set_air_calibration", "v": 1, "latency_ms": -5}) == SetAirCalibration(latency_ms=0.0)


@pytest.mark.parametrize(
    "msg",
    [
        {"cmd": "air_fold", "v": 1, "id": "", "from_us": 0.0, "to_us": 1e6},
        {"cmd": "air_fold", "v": 1, "id": "a" * 65, "from_us": 0.0, "to_us": 1e6},
        {"cmd": "air_fold", "v": 1, "id": "a b", "from_us": 0.0, "to_us": 1e6},
        {"cmd": "air_fold", "v": 1, "id": 7, "from_us": 0.0, "to_us": 1e6},
        {"cmd": "air_fold", "v": 1, "id": "a", "from_us": 2e6, "to_us": 1e6},
        {"cmd": "air_fold", "v": 1, "id": "a", "from_us": float("nan"), "to_us": 1e6},
        {"cmd": "air_fold", "v": 1, "id": "a", "from_us": True, "to_us": 1e6},
        {"cmd": "air_fold", "v": 1, "id": "a", "from_us": -1.0, "to_us": 1e6},
        {"cmd": "air_fold", "v": 1, "id": "a", "to_us": 1e6},
    ],
)
def test_fold_rejects_bad_values(msg):
    with pytest.raises(InvalidCommand):
        dec(msg)


# ── From the tracker's batch ─────────────────────────────────────────────────


def test_envelope_from_batch_carries_every_field():
    batch = AirBatch(
        epoch=2,
        seq=256,
        u0_us=743_100.0,
        t0_us=NOW,
        frame_us=2902.494,
        rate=44100,
        subbands=8,
        blocks=16,
        bands=(1, 2),
        complete_lag_us=None,
        timestamp="recv",
        anchor_reset=True,
        e=(1, 255),
    )
    cmd = air_envelope_from_batch(batch)
    assert cmd == AirEnvelope(
        epoch=2,
        seq=256,
        u0_us=743_100.0,
        t0_us=NOW,
        frame_us=2902.494,
        timestamp="recv",
        rate=44100,
        subbands=8,
        blocks=16,
        bands=(1, 2),
        complete_lag_us=None,
        anchor_reset=True,
        e=(1, 255),
    )
    assert dec(json.loads(json.dumps(encode_command(cmd)))) == cmd


# ── Daemon intake ────────────────────────────────────────────────────────────


class _Harness:
    def __init__(self, daemon, monkeypatch):
        self.daemon = daemon
        self.errors: list[tuple[str, str]] = []
        self._monkeypatch = monkeypatch
        monkeypatch.setattr(daemon_process, "_emit_error", lambda code, msg, **_k: self.errors.append((code, msg)))

    async def send(self, *messages):
        stop_event = asyncio.Event()
        data = "".join(json.dumps(m) + "\n" for m in messages).encode()

        async def _fake_connect_read_pipe(protocol_factory, pipe):
            protocol = protocol_factory()
            protocol.connection_made(MagicMock())
            protocol.data_received(data)
            protocol.eof_received()

        loop = asyncio.get_running_loop()
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(loop, "connect_read_pipe", _fake_connect_read_pipe)
            await asyncio.wait_for(daemon_process._read_commands([self.daemon], stop_event), timeout=2.0)


class _RecordingSink:
    def __init__(self, *, fail: bool = False):
        self.received: list = []
        self.fail = fail

    def submit(self, command) -> None:
        if self.fail:
            raise RuntimeError("sink broke")
        self.received.append(command)


@pytest.fixture
def daemon_stdin_harness(tmp_path, monkeypatch):
    async def _make(*, air_sensor: bool, sink: _RecordingSink | None = None):
        args = DaemonArgs(client_id="air-id", client_name="Speaker", identity_path=str(tmp_path / "identity.key"))
        daemon = BridgeDaemon(args, {}, None)
        if air_sensor:
            daemon.attach_air_sink(sink or _RecordingSink())
        return _Harness(daemon, monkeypatch)

    return _make


def _envelope_now():
    return {**GOOD, "t0_us": time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW) / 1000}


async def test_daemon_without_air_sensor_ignores_envelopes_quietly(daemon_stdin_harness, caplog):
    h = await daemon_stdin_harness(air_sensor=False)
    for _ in range(50):
        await h.send(GOOD)
    assert h.errors == [] and h.daemon.air_counters["ignored"] == 50
    assert sum("air" in r.message for r in caplog.records if r.levelname == "WARNING") <= 1


async def test_daemon_hands_valid_commands_to_the_sink(daemon_stdin_harness):
    sink = _RecordingSink()
    h = await daemon_stdin_harness(air_sensor=True, sink=sink)
    await h.send(
        _envelope_now(),
        SOURCE,
        {"cmd": "set_air_calibration", "v": 1, "latency_ms": 203.4},
        {"cmd": "air_fold", "v": 1, "id": "c1f3", "from_us": 0.0, "to_us": 12e6},
    )
    assert [type(c) for c in sink.received] == [AirEnvelope, AirSource, SetAirCalibration, AirFold]
    assert h.daemon.air_counters["accepted"] == 4 and h.daemon.air_counters["ignored"] == 0
    assert h.errors == []


async def test_daemon_drops_and_counts_invalid_air_commands_without_errors(daemon_stdin_harness, caplog):
    sink = _RecordingSink()
    h = await daemon_stdin_harness(air_sensor=True, sink=sink)
    caplog.set_level(logging.DEBUG)
    bad = [{**GOOD, "e": [999]}, {**GOOD, "timestamp": "SECRET-RAW"}, {"cmd": "air_fold", "v": 1, "id": "x"}] * 10
    await h.send(*bad)
    assert sink.received == [] and h.errors == []
    assert h.daemon.air_counters["invalid"] == 30
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) <= 1
    assert all("SECRET-RAW" not in r.getMessage() and "999" not in r.getMessage() for r in caplog.records)


async def test_a_broken_sink_never_reaches_the_command_loop(daemon_stdin_harness):
    h = await daemon_stdin_harness(air_sensor=True, sink=_RecordingSink(fail=True))
    h.daemon._bridge_status = {"volume": 50}
    h.daemon._notify = MagicMock()
    h.daemon.apply_bridge_volume = MagicMock()
    await h.send(_envelope_now(), _envelope_now(), {"cmd": "set_volume", "value": 30, "protocol_version": 2})
    assert h.daemon.air_counters["errors"] == 2
    assert h.daemon._bridge_status["volume"] == 30  # the loop went on
    assert h.errors == []


async def test_invalid_non_air_commands_keep_reporting_errors(daemon_stdin_harness):
    h = await daemon_stdin_harness(air_sensor=False)
    await h.send({"cmd": "set_log_level", "level": "LOUD"})
    assert [code for code, _ in h.errors] == ["invalid_command"]
    assert h.daemon.air_counters["invalid"] == 0


async def test_invalid_air_commands_before_the_daemon_exists_are_dropped(monkeypatch):
    errors: list = []
    monkeypatch.setattr(daemon_process, "_emit_error", lambda code, msg, **_k: errors.append(code))
    data = (json.dumps({**GOOD, "e": []}) + "\n" + json.dumps(GOOD) + "\n").encode()

    async def _fake_connect_read_pipe(protocol_factory, pipe):
        protocol = protocol_factory()
        protocol.connection_made(MagicMock())
        protocol.data_received(data)
        protocol.eof_received()

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "connect_read_pipe", _fake_connect_read_pipe)
    await asyncio.wait_for(daemon_process._read_commands([], asyncio.Event()), timeout=2.0)
    assert errors == []


def test_accepts_is_advertised_only_with_a_sink(tmp_path):
    args = DaemonArgs(client_id="air-id", client_name="Speaker", identity_path=str(tmp_path / "identity.key"))
    daemon = BridgeDaemon(args, {}, None)
    assert daemon.air_accepts() == []
    daemon.attach_air_sink(_RecordingSink())
    assert daemon.air_accepts() == [AIR_ENVELOPE_VERSION]
    daemon.detach_air_sink()
    assert daemon.air_accepts() == []


def test_a_daemon_without_a_sink_publishes_no_air_status(tmp_path):
    args = DaemonArgs(client_id="air-id", client_name="Speaker", identity_path=str(tmp_path / "identity.key"))
    status: dict = {}
    daemon = BridgeDaemon(args, status, None)
    daemon.air_intake(AirFold(id="a", from_us=0.0, to_us=1.0))
    assert "air_sensor" not in status


async def test_air_commands_with_a_foreign_protocol_version_log_nothing_raw(daemon_stdin_harness, caplog):
    h = await daemon_stdin_harness(air_sensor=False)
    for _ in range(20):
        await h.send({**GOOD, "protocol_version": "RAW-PV"})
    assert h.daemon.air_counters["ignored"] == 20
    assert all("RAW-PV" not in r.getMessage() for r in caplog.records)


# ── Fix round 1: uniform v, overflow, stdin hardening ────────────────────────


@pytest.mark.parametrize(
    "msg",
    [
        {"cmd": "set_air_calibration", "latency_ms": 200.0},  # v missing
        {"cmd": "set_air_calibration", "v": 2, "latency_ms": 200.0},
        {"cmd": "set_air_calibration", "v": True, "latency_ms": 200.0},
        {"cmd": "air_fold", "id": "a", "from_us": 0.0, "to_us": 1e6},  # v missing
        {"cmd": "air_fold", "v": 2, "id": "a", "from_us": 0.0, "to_us": 1e6},
    ],
)
def test_calibration_and_fold_check_the_payload_version(msg):
    with pytest.raises(InvalidCommand):
        dec(msg)


HUGE = 10**400


@pytest.mark.parametrize(
    "msg",
    [
        {"cmd": "set_air_calibration", "v": 1, "latency_ms": HUGE},
        {"cmd": "air_fold", "v": 1, "id": "a", "from_us": 0.0, "to_us": HUGE},
        {"cmd": "air_fold", "v": 1, "id": "a", "from_us": HUGE, "to_us": 1e6},
        {**GOOD, "t0_us": HUGE},
        {**GOOD, "u0_us": HUGE},
        {**GOOD, "frame_us": HUGE},
        {**GOOD, "complete_lag_us": HUGE},
        {**GOOD, "complete_lag_us": -HUGE},
    ],
)
def test_huge_integers_are_invalid_not_overflow(msg):
    with pytest.raises(InvalidCommand):
        dec(msg)


def test_huge_integers_from_the_wire_are_invalid():
    # json.loads turns a 400-digit literal into an int; it must not leak OverflowError.
    wire = json.dumps(GOOD).replace('"u0_us": 371519.3', '"u0_us": 1' + "0" * 400)
    with pytest.raises(InvalidCommand):
        dec(json.loads(wire))


async def _feed_raw(data: bytes, daemon_ref: list) -> None:
    async def _fake_connect_read_pipe(protocol_factory, pipe):
        protocol = protocol_factory()
        protocol.connection_made(MagicMock())
        protocol.data_received(data)
        protocol.eof_received()

    loop = asyncio.get_running_loop()
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(loop, "connect_read_pipe", _fake_connect_read_pipe)
        await asyncio.wait_for(daemon_process._read_commands(daemon_ref, asyncio.Event()), timeout=5.0)


def _volume_daemon(tmp_path):
    args = DaemonArgs(client_id="air-id", client_name="Speaker", identity_path=str(tmp_path / "identity.key"))
    daemon = BridgeDaemon(args, {"volume": 50}, None)
    daemon._notify = MagicMock()
    daemon.apply_bridge_volume = MagicMock()
    return daemon


SET_VOLUME = (json.dumps({"cmd": "set_volume", "value": 30, "protocol_version": 2}) + "\n").encode()


async def test_an_overlong_line_is_dropped_and_the_reader_keeps_going(tmp_path, caplog):
    daemon = _volume_daemon(tmp_path)
    before = daemon_process.stdin_drop_counters["overlong"]
    overlong = (json.dumps({**GOOD, "pad": "P" * 70_000}) + "\n").encode()
    await _feed_raw(overlong + SET_VOLUME, [daemon])
    assert daemon._bridge_status["volume"] == 30
    assert daemon_process.stdin_drop_counters["overlong"] == before + 1
    assert all("PPPP" not in r.getMessage() for r in caplog.records)


async def test_deeply_nested_json_is_dropped_and_the_reader_keeps_going(tmp_path):
    daemon = _volume_daemon(tmp_path)
    nested = ("[" * 30_000 + "]" * 30_000 + "\n").encode()
    await _feed_raw(nested + SET_VOLUME, [daemon])
    assert daemon._bridge_status["volume"] == 30


async def test_a_recursion_error_while_parsing_is_contained(tmp_path, monkeypatch):
    # Interpreters differ in where json gives up on nesting; force the error so this runs everywhere.
    daemon = _volume_daemon(tmp_path)
    real_loads = json.loads

    def _loads(text, *a, **k):
        if text.startswith("[["):
            raise RecursionError("maximum recursion depth exceeded")
        return real_loads(text, *a, **k)

    monkeypatch.setattr(daemon_process.json, "loads", _loads)
    before = daemon_process.stdin_drop_counters["too_deep"]
    await _feed_raw(b"[[1]]\n" + SET_VOLUME, [daemon])
    assert daemon._bridge_status["volume"] == 30
    assert daemon_process.stdin_drop_counters["too_deep"] == before + 1


async def test_air_commands_before_the_daemon_exists_are_counted():
    before = daemon_process.stdin_drop_counters["air_no_daemon"]
    await _feed_raw((json.dumps(GOOD) + "\n").encode() * 3, [])
    assert daemon_process.stdin_drop_counters["air_no_daemon"] == before + 3
