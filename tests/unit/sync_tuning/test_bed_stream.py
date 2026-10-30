"""The keep-awake noise stream of a tuning session (Lenco LS-500-class amplifiers)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sendspin_bridge.sync_tuning.bed_stream import BedStreams, _pcm

if TYPE_CHECKING:
    import subprocess


class _FakeProcess:
    def __init__(self) -> None:
        self.written = bytearray()
        self.closed = False
        self.waited = False
        self.terminated = False

        class _Stdin:
            def __init__(self, owner: _FakeProcess) -> None:
                self._owner = owner

            def write(self, data: bytes) -> int:
                if self._owner.closed:
                    raise BrokenPipeError
                self._owner.written.extend(data)
                return len(data)

            def flush(self) -> None:
                pass

            def close(self) -> None:
                self._owner.closed = True

        self.stdin = _Stdin(self)

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return 0

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:  # pragma: no cover — only a stubborn process reaches this
        self.terminated = True


def _manager():
    spawned: dict[str, _FakeProcess] = {}

    def spawn(sink: str) -> _FakeProcess:
        process = _FakeProcess()
        spawned[sink] = process
        return process

    return BedStreams(spawn=spawn), spawned


def test_it_starts_one_stream_per_sink_and_stops_the_rest():
    beds, spawned = _manager()

    beds.sync(["bluez_output.A.1", "bluez_output.B.1"])
    assert beds.active() == ["bluez_output.A.1", "bluez_output.B.1"]

    beds.sync(["bluez_output.B.1"])
    assert beds.active() == ["bluez_output.B.1"]
    assert spawned["bluez_output.A.1"].closed and spawned["bluez_output.A.1"].waited

    beds.stop_all()
    assert beds.active() == []
    assert spawned["bluez_output.B.1"].closed


def test_the_stream_carries_audio_not_silence():
    import time

    beds, spawned = _manager()

    beds.sync(["bluez_output.A.1"])
    deadline = time.monotonic() + 2.0
    while not spawned["bluez_output.A.1"].written and time.monotonic() < deadline:
        time.sleep(0.02)
    beds.stop_all()

    written = spawned["bluez_output.A.1"].written
    assert written
    assert max(abs(value) for value in written[::500]) > 0


def test_a_sink_that_cannot_be_opened_is_skipped_without_losing_the_others():
    def spawn(sink: str) -> subprocess.Popen[bytes]:
        if sink == "broken":
            raise OSError("no such sink")
        return _FakeProcess()  # type: ignore[return-value]

    beds = BedStreams(spawn=spawn)
    beds.sync(["broken", "good"])
    assert beds.active() == ["good"]
    beds.stop_all()


def test_the_bed_is_quiet_and_bounded():
    data = _pcm(0.1)
    values = [int.from_bytes(data[i : i + 2], "little", signed=True) for i in range(0, len(data), 2)]
    assert values
    assert max(abs(v) for v in values) < 2000  # well under the calibration clicks


def test_stopping_twice_is_harmless():
    beds, _spawned = _manager()
    beds.sync(["a"])
    beds.stop_all()
    beds.stop_all()
    assert beds.active() == []
