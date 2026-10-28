"""The air IPC over a real subprocess pipe.

The parent's command writer (``SendspinClient.send_air_command``) writes to a
real child's stdin; the child (``air_ipc_child.py``) runs the real stdin
reader, decode and a real ``AirSensor``; its status comes back over stdout.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from sendspin_bridge.bridge.client import SendspinClient
from sendspin_bridge.services.ipc.commands import AirEnvelope, AirFold, AirSource, SetAirCalibration, encode_command

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="CLOCK_MONOTONIC_RAW, pipes")

CHILD = Path(__file__).with_name("air_ipc_child.py")
FRAME_US = 2902.494  # SBC 44.1 kHz, 8 sub-bands, 16 blocks
BATCH = 128


def _raw_us() -> float:
    return time.clock_gettime_ns(time.CLOCK_MONOTONIC_RAW) / 1e3


def envelope(*, seq: int = 0, epoch: int = 1, e: list[int] | None = None) -> AirEnvelope:
    cells = e if e is not None else [(seq + k) * 7 % 200 + 20 for k in range(BATCH)]
    return AirEnvelope(
        epoch=epoch,
        seq=seq,
        u0_us=seq * FRAME_US,
        t0_us=_raw_us(),
        frame_us=FRAME_US,
        timestamp="kernel",
        rate=44100,
        subbands=8,
        blocks=16,
        bands=(1, 2),
        complete_lag_us=None,
        anchor_reset=False,
        e=tuple(cells),
    )


def fixture_envelopes(seconds: float) -> list[AirEnvelope]:
    total = int(seconds * 1e6 / FRAME_US)
    return [
        envelope(seq=seq, e=[(seq + k) * 7 % 200 + 20 for k in range(min(BATCH, total - seq))])
        for seq in range(0, total, BATCH)
    ]


class Child:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self.sender = SimpleNamespace(
            player_name="child",
            air_accepts=(1,),
            _daemon_proc=proc,
            AIR_WRITE_BUFFER_LIMIT=SendspinClient.AIR_WRITE_BUFFER_LIMIT,
        )
        self.last: dict = {}
        self._lines: asyncio.Queue[bytes] = asyncio.Queue()
        self._pump = asyncio.ensure_future(self._read())

    async def _read(self) -> None:
        assert self.proc.stdout is not None
        while line := await self.proc.stdout.readline():
            self._lines.put_nowait(line)

    def send(self, command) -> bool:
        """The parent's real, non-blocking air writer."""
        return SendspinClient.send_air_command(self.sender, command)  # type: ignore[arg-type]

    async def send_all(self, commands) -> None:
        for command in commands:
            assert self.send(command), "the parent's writer skipped a command"
            await self.proc.stdin.drain()  # type: ignore[union-attr]

    def write_raw(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)

    async def read_status_until(self, predicate, timeout: float) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AssertionError(f"no matching status in {timeout}s; last: {json.dumps(self.last)[:600]}")
            try:
                line = await asyncio.wait_for(self._lines.get(), remaining)
            except TimeoutError:
                continue
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if message.get("type") != "status":
                continue
            payload = message.get("status", message)
            if not isinstance(payload, dict):
                continue
            self.last = payload
            if predicate(payload):
                return payload


@pytest.fixture
async def air_child():
    started: list[Child] = []

    async def _start() -> Child:
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            str(CHILD),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        child = Child(proc)
        started.append(child)
        await child.read_status_until(lambda s: "air_sensor" in s, timeout=15)
        return child

    yield _start
    for child in started:
        if child.proc.stdin is not None:
            with contextlib.suppress(Exception):
                child.proc.stdin.close()
        if child.proc.returncode is None:
            child.proc.kill()
        with contextlib.suppress(Exception):
            await child.proc.wait()
        child._pump.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await child._pump


def _counters(status: dict) -> dict:
    return status.get("child_counters", {})


async def test_the_daemon_advertises_the_air_payload_version_at_start(air_child):
    child = await air_child()
    assert child.last["air_sensor"]["accepts"] == [1]


async def test_batches_cross_a_real_pipe_and_status_comes_back(air_child):
    child = await air_child()
    await child.send_all(fixture_envelopes(seconds=6))
    status = await child.read_status_until(lambda s: s.get("air_sensor", {}).get("frames", 0) > 1500, timeout=10)
    assert status["air_sensor"]["dropped_messages"] == 0
    assert status["air_sensor"]["accepts"] == [1]
    assert _counters(status)["air_invalid"] == 0


async def test_max_size_batch_fits_the_line_limit(air_child):
    child = await air_child()
    await child.send_all([envelope(e=[254] * 256)])
    status = await child.read_status_until(lambda s: s.get("air_sensor", {}).get("frames", 0) >= 256, timeout=5)
    assert status["air_sensor"]["dropped_messages"] == 0
    assert _counters(status)["air_invalid"] == 0


async def test_every_air_verb_is_accepted_over_the_pipe(air_child):
    child = await air_child()
    commands = [
        AirSource(
            state="unsupported_codec",
            reason="codec:aac",
            codec="aac",
            adapter="C0:FB:F9:62:D7:D6",
            index=1,
            handle=70,
            in_step=False,
            monitor_drops=None,
            ipc_dropped=0,
        ),
        SetAirCalibration(latency_ms=123.0),
        AirFold(id="fold-1", from_us=_raw_us() - 2e6, to_us=_raw_us() - 1e6),
    ]
    await child.send_all(commands)
    status = await child.read_status_until(
        lambda s: s.get("air_sensor", {}).get("air_to_sound_ms") == 123.0 and _counters(s).get("air_accepted") == 3,
        timeout=5,
    )
    assert status["air_sensor"]["codec"] == "aac"
    assert _counters(status)["air_accepted"] == 3
    assert _counters(status)["air_invalid"] == 0


async def test_garbage_lines_are_counted_not_fatal(air_child):
    child = await air_child()
    child.write_raw(b'{"cmd": "air_envelope", "v": 1, "e": "nope"}\n' + b"\xff\xfe\n")
    await child.proc.stdin.drain()
    status = await child.read_status_until(lambda s: _counters(s).get("air_invalid", 0) >= 1, timeout=5)
    assert child.proc.returncode is None
    # The daemon still takes good commands afterwards.
    await child.send_all([envelope(e=[100] * 10)])
    await child.read_status_until(lambda s: s["air_sensor"]["frames"] >= 10, timeout=5)
    assert status["air_sensor"]["accepts"] == [1]


async def test_a_hostile_stream_does_not_stop_the_reader(air_child):
    child = await air_child()
    good = (json.dumps(encode_command(envelope(e=[90] * 20))) + "\n").encode()
    hostile = [
        b"not json at all\n",
        b"[" * 30_000 + b"]" * 30_000 + b"\n",
        b"\x00" * 1000 + b"\n",
        b'{"cmd": "air_fold", "v": 1, "id": "../x", "from_us": 1, "to_us": 2}\n',
        b'{"cmd": "air_envelope", "v": 2}\n',
        b'{"cmd": "air_source", "v": 1, "state": "bogus"}\n',
        b'{"cmd": "no_such_verb"}\n',
        b"\n",
    ]
    child.write_raw(b"".join(hostile) + good)
    await child.proc.stdin.drain()
    await child.read_status_until(
        lambda s: s.get("air_sensor", {}).get("frames", 0) >= 20 and _counters(s).get("air_invalid", 0) >= 3,
        timeout=5,
    )
    assert child.proc.returncode is None


async def test_an_overlong_line_is_dropped_once_and_its_tail_never_runs(air_child):
    child = await air_child()
    # The over-long line arrives across several pipe reads and ends with its own newline; its last
    # piece, written on its own, is a complete valid command. A reader that resumed at a read
    # boundary would run that tail (a second accepted command, 999 ms).
    junk = b'{"pad": "' + b"P" * (300_000 - 9)  # whole 50 kB pieces: the reader holds nothing of it at the end
    for start in range(0, len(junk), 50_000):
        child.write_raw(junk[start : start + 50_000])
        await child.proc.stdin.drain()
        await asyncio.sleep(0.01)
    child.write_raw((json.dumps(encode_command(SetAirCalibration(latency_ms=999.0))) + "\n").encode())
    await child.proc.stdin.drain()
    # The next line is a distinct valid command: it must run.
    await child.send_all([SetAirCalibration(latency_ms=5.0)])
    status = await child.read_status_until(
        lambda s: s.get("air_sensor", {}).get("air_to_sound_ms") == 5.0 and _counters(s).get("stdin_overlong", 0) >= 1,
        timeout=5,
    )
    assert _counters(status)["stdin_overlong"] == 1
    assert _counters(status)["air_accepted"] == 1  # only the 5 ms line; the tail never ran
    assert status["air_sensor"]["air_to_sound_ms"] == 5.0


async def test_the_writer_skips_instead_of_blocking_when_the_child_does_not_read(air_child):
    child = await air_child()
    sender = child.sender
    os_pipe_filled = 0
    # Stop the child from reading: SIGSTOP it, then write until the parent's writer refuses.
    import signal

    child.proc.send_signal(signal.SIGSTOP)
    try:
        t0 = time.monotonic()
        for _ in range(20_000):
            if not SendspinClient.send_air_command(sender, envelope()):  # type: ignore[arg-type]
                break
            os_pipe_filled += 1
        else:
            pytest.fail("the writer never refused against a stopped reader")
        assert time.monotonic() - t0 < 2.0  # never awaited a drain
        buffered = child.proc.stdin.transport.get_write_buffer_size()  # type: ignore[union-attr]
        assert buffered <= SendspinClient.AIR_WRITE_BUFFER_LIMIT + 4096
    finally:
        child.proc.send_signal(signal.SIGCONT)
    # Once the child reads again, the backlog drains and the daemon is healthy.
    await child.proc.stdin.drain()  # type: ignore[union-attr]
    status = await child.read_status_until(lambda s: s.get("air_sensor", {}).get("frames", 0) > 0, timeout=10)
    assert child.proc.returncode is None
    assert status["air_sensor"]["accepts"] == [1]


async def test_stop_ends_the_child_cleanly(air_child):
    child = await air_child()
    child.write_raw(b'{"cmd": "stop", "protocol_version": 2}\n')
    await child.proc.stdin.drain()
    assert await asyncio.wait_for(child.proc.wait(), 10) == 0
