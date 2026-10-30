"""A quiet noise stream into one speaker's sink while group tuning runs.

Some amplifiers un-mute too slowly for the calibration clicks: the Lenco LS-500 plays a continuous
tone but never makes an 8 ms chirp after silence audible, at any level (measured through its own
output). Such a speaker is marked "keep awake during tuning", and for the duration of a tuning
session the bridge plays a soft continuous noise into *its* sink only, so its amplifier is already
open when the group's click track reaches it. The other speakers keep the clean click track.

The stream is one ``pacat`` process per speaker fed from a feeder thread; the noise is
deterministic (a hash of the sample index, smoothed), so nothing is stored and the level is stable.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

logger = logging.getLogger(__name__)

#: -30 dBFS: clearly audible as a soft hiss on a quiet speaker, ~24 dB under the calibration clicks.
BED_PEAK = 0.0316
_RATE = 48000
_CHANNELS = 2
_CHUNK_S = 0.01
_STOP_TIMEOUT_S = 2.0


def _noise(i: int) -> float:
    """Deterministic white noise in [-1, 1) from the sample index alone."""
    x = (i * 1103515245 + 12345) & 0x7FFFFFFF
    x ^= x >> 13
    x = (x * 2654435761) & 0x7FFFFFFF
    return (x & 0xFFFF) / 32768.0 - 1.0


def _pcm(seconds: float = 1.0) -> bytes:
    """One second of the bed: a 4-tap smoothed noise, 16-bit stereo, both channels equal."""
    import struct

    total = round(seconds * _RATE)
    frames = bytearray(total * 4)
    low = 0.0
    for i in range(total):
        low = 0.996 * low + 0.004 * _noise(i)
        value = round(low * 6.0 * BED_PEAK * 32767)
        value = max(-32767, min(32767, value))
        struct.pack_into("<hh", frames, i * 4, value, value)
    return bytes(frames)


def _spawn_pacat(sink: str) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            "pacat",
            f"--device={sink}",
            f"--rate={_RATE}",
            f"--channels={_CHANNELS}",
            "--format=s16le",
            "--latency-msec=100",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


class _Bed:
    """One feeder thread writing the bed into *sink* until ``stop``."""

    def __init__(self, sink: str, spawn: Callable[[str], subprocess.Popen[bytes]]) -> None:
        self._sink = sink
        self._stop = threading.Event()
        self._process = spawn(sink)
        self._thread = threading.Thread(target=self._feed, name=f"tuning-bed:{sink}", daemon=True)
        self._thread.start()

    def _feed(self) -> None:
        data = _pcm()
        stdin = self._process.stdin
        if stdin is None:  # pragma: no cover — the spawner always pipes stdin
            return
        while not self._stop.is_set():
            try:
                stdin.write(data)
                stdin.flush()
            except (BrokenPipeError, OSError, ValueError):
                return  # the sink went away (a reconnect, a stop): the manager restarts it if asked
            self._stop.wait(_CHUNK_S)

    def stop(self) -> None:
        self._stop.set()
        stdin = self._process.stdin
        if stdin is not None:
            try:
                stdin.close()
            except OSError:  # pragma: no cover — defensive
                pass
        try:
            self._process.wait(timeout=_STOP_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            self._process.terminate()
            try:
                self._process.wait(timeout=_STOP_TIMEOUT_S)
            except subprocess.TimeoutExpired:  # pragma: no cover — stubborn process
                self._process.kill()
        if self._thread.is_alive():
            self._thread.join(timeout=_STOP_TIMEOUT_S)


class BedStreams:
    """The set of speakers that should keep their amplifier awake right now."""

    def __init__(self, spawn: Callable[[str], subprocess.Popen[bytes]] = _spawn_pacat) -> None:
        self._spawn = spawn
        self._beds: dict[str, _Bed] = {}
        self._lock = threading.Lock()

    def active(self) -> list[str]:
        with self._lock:
            return sorted(self._beds)

    def sync(self, sinks: Iterable[str]) -> None:
        """Keep a bed on exactly *sinks*; start the missing ones and stop the rest."""
        wanted = {sink for sink in sinks if sink}
        with self._lock:
            for sink in list(self._beds):
                if sink not in wanted:
                    self._beds.pop(sink).stop()
        for sink in sorted(wanted):
            with self._lock:
                if sink in self._beds:
                    continue
            try:
                bed = _Bed(sink, self._spawn)
            except (OSError, ValueError) as exc:
                logger.warning("Could not start the tuning keep-awake stream on %s: %s", sink, exc)
                continue
            with self._lock:
                self._beds[sink] = bed
            logger.info("Tuning keep-awake stream started on %s", sink)

    def stop_all(self) -> None:
        with self._lock:
            beds = list(self._beds.values())
            self._beds.clear()
        for bed in beds:
            bed.stop()
        if beds:
            logger.info("Tuning keep-awake streams stopped (%d)", len(beds))
