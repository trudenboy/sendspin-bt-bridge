"""WrittenEnvelope: the air sensor's tap on the input PCM the playout engine writes.

``PlayoutPlayer._push`` hands every block it pushed to ``add_block`` on the feeder thread.
The block is in the **input** format (before ``audioconvert``/``audioresample``): S16LE,
S24LE packed in 3 bytes or S32LE. Supported are those at 44.1 and 48 kHz; anything else
records nothing and reports ``unsupported`` (``input:<rate>/<bits>``).

Each sample is reduced to its top 16 bits at C speed, the comb high-pass
``y[n] = x[n] − x[n−d]`` (``d = comb_lag(rate)``, 3 at 44.1/48 kHz) runs on each channel,
and every cell of ``CELL_FRAMES`` input frames gets ``w = log2(1 + max|y|)`` over both
channels and over the cell plus the ``9·M`` input frames before it (the span an SBC frame's
scale factors see; ``M`` from :meth:`WrittenEnvelope.set_sbc_format`, 8 until told), the scheduled port time ``s`` of its first frame (``ScheduleMap.at``), ``T = s + D_out``
with the output delay at write time and the map's kind (``program``/``silence``). Silence cells
have ``w = 0``. Whether a cell is muted is decided when it is read, from a RAW-timed mute
timeline at the cell's port time ``s`` (a mute applied after the push still covers audio that
reaches the port after it; R-8); ``w`` itself never depends on mute.

Threading: ``add_block`` and ``mute_transition`` run on the feeder; ``slice``, ``cell_near``
and the properties may run anywhere. The lock is held for appending one block's cells or
copying a slice, never for the envelope arithmetic.
"""

from __future__ import annotations

import math
import sys
import threading
from array import array
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from operator import sub
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sendspin_bridge.services.audio.playout.scheduler import Block, InputFormat

SUPPORTED_RATES = (44100, 48000)
SUPPORTED_SAMPLE_BYTES = (2, 3, 4)
CELL_FRAMES = 128
# An SBC frame's scale factors see its own B·M samples and the 9·M before them (its 10·M-tap
# analysis window; libsbc 1.3.1: an impulse shows up in frame k for input samples 128k - 72 …
# 128k + 126). A written cell takes its peak over the same span for M = 8, so the two envelopes
# rise and fall on the same samples whatever the attack and decay of the sound.
LOOKBACK_FRAMES = 72  # 9·M for M = 8, the default until the air side says otherwise
SBC_LOOKBACK_BLOCKS = 9  # (10·M-tap window - M) / M


def lookback_frames(subbands: int, air_rate: int, input_rate: int) -> int:
    """``9·M`` air samples in input frames (scaled when PulseAudio resamples)."""
    return round(SBC_LOOKBACK_BLOCKS * subbands * input_rate / air_rate)


MUTE_KEEP_US = 30_000_000.0  # mute transitions older than this are forgotten (the state before them stays)
INTERNAL_ERROR = "internal_error"

_Ring = TypeVar("_Ring", "array[float]", bytearray)

_BIG_ENDIAN = sys.byteorder == "big"
_NAN = float("nan")


def comb_lag(rate: int) -> int:
    """Comb lag in samples for an input rate: the response peaks near 7.35 kHz (3 at 44.1/48 kHz)."""
    return max(1, round(rate / 14700))


def to_s16(pcm: bytes, sample_bytes: int) -> memoryview:
    """The top 16 bits of each little-endian sample, as a ``"h"`` memoryview (C-speed slicing)."""
    if sample_bytes == 2:
        data: bytes | bytearray = pcm[: len(pcm) & ~1]
    elif sample_bytes in (3, 4):
        n = len(pcm) // sample_bytes
        src = pcm[: n * sample_bytes]
        out = bytearray(2 * n)
        out[0::2] = src[sample_bytes - 2 :: sample_bytes]
        out[1::2] = src[sample_bytes - 1 :: sample_bytes]
        data = out
    else:
        raise ValueError(f"unsupported sample width: {sample_bytes} bytes")
    if _BIG_ENDIAN:
        swapped = array("h")
        swapped.frombytes(data)
        swapped.byteswap()
        return memoryview(swapped)
    return memoryview(data).cast("h")


def _comb_abs(samples: memoryview, offset: int) -> list[int]:
    """``|x[k] − x[k − offset]|`` for every k ≥ offset of an interleaved ``"h"`` view."""
    return list(map(abs, map(sub, samples[offset:], samples[:-offset])))


def comb_peak_log2(s16: memoryview, channels: int, start: int, stop: int, prev: tuple[int, ...], lag: int) -> float:
    """``log2(1 + max|x[n] − x[n−lag]|)`` over frames ``start:stop`` and all channels.

    ``prev`` holds the ``lag`` interleaved frames before ``start`` (the comb's history).
    """
    seg = array("h", prev)
    seg.frombytes(s16[start * channels : stop * channels].tobytes())
    peaks = _comb_abs(memoryview(seg), lag * channels)
    return math.log2(1 + max(peaks, default=0))


@dataclass(frozen=True, slots=True)
class WrittenSlice:
    i0: int  # global index of the first cell since the output opened
    cell_us: float  # 128 / input rate
    w: array  # 'f', 0 for silence cells (muted cells keep their value)
    s: array  # 'd' RAW µs (NaN when unknown)
    t: array  # 'd' T = s + D_out at write time
    program: bytes  # 1 = ScheduleMap kind "program", 0 = "silence"
    muted: bytes  # 1 = the mute timeline is muted at the cell's port time s_i (resolved at slice time)
    gen: int = 0  # the ring generation the cells belong to (see WrittenEnvelope.gen)
    x: array | None = None  # 'd' written stream time (ns) of each cell's first frame


def _muted_at(times: Sequence[float], states: Sequence[bool], s_us: float) -> bool:
    if math.isnan(s_us):
        return False
    idx = bisect_right(times, s_us) - 1
    return idx >= 0 and states[idx]


class WrittenEnvelope:
    """The written-side envelope ring (see the module docstring)."""

    def __init__(self, *, seconds: float = 20.0) -> None:
        self._seconds = seconds
        self._lock = threading.Lock()
        # Ring (guarded by _lock).
        self._fmt: InputFormat | None = None
        self._sbc: tuple[int, int] | None = None  # (sub-bands, air rate) of the speaker's SBC stream
        self._unsupported: str | None = None
        self._error: str | None = None
        self._cell_us: float | None = None
        self._cap = 0
        self._n = 0  # cells written since the output opened (global index of the next cell)
        self._w = array("f")
        self._s = array("d")
        self._t = array("d")
        self._x = array("d")
        self._program = bytearray()
        self._gen = 0  # bumped by every reset/format change; a block computed across one is dropped
        # Mute timeline (guarded by _lock): RAW µs of each transition and the state it set.
        self._mute_t: list[float] = []
        self._mute_v: list[bool] = []
        # Feeder-only carry: the comb history and the partial cell at the end of the last block.
        self._carry_gen = -1
        self._hist = b""
        self._fp: list[int] = []  # per-frame comb peaks: the look-back, then the partial cell
        self._fp_back = 0  # look-back frames at the start of _fp (at most the look-back)
        self._carry_x_ns = 0  # x of the partial cell's first frame

    # -- feeder side ------------------------------------------------------------------------

    def add_block(
        self,
        block: Block,
        fmt: InputFormat,
        schedule_at: Callable[[int], float | None],
        kind_at: Callable[[int], str | None],
        d_out_us: float,
    ) -> None:
        """Record the cells a pushed block completes (feeder thread)."""
        rate, channels, width = fmt.sample_rate, fmt.channels, fmt.sample_bytes
        supported = rate in SUPPORTED_RATES and width in SUPPORTED_SAMPLE_BYTES and channels >= 1
        with self._lock:
            if self._error is not None:
                return
            if fmt != self._fmt:
                self._configure_locked(fmt, supported)
            if not supported:
                return
            gen = self._gen
            back = self._lookback_locked(rate)
        lag = comb_lag(rate)
        hist_len = lag * channels
        if gen != self._carry_gen:
            self._carry_gen = gen
            self._hist = bytes(2 * hist_len)
            self._fp = []
            self._fp_back = 0
            self._carry_x_ns = 0

        samples = to_s16(block.pcm, width)
        frames = len(samples) // channels
        if frames <= 0:
            return
        buf = bytearray(self._hist)
        buf += samples[: frames * channels]
        view = memoryview(buf).cast("h")
        peaks = _comb_abs(view, hist_len)
        self._hist = view[-hist_len:].tobytes()
        frame_peaks = peaks if channels == 1 else list(map(max, *(peaks[c::channels] for c in range(channels))))

        # A cell's peak spans its CELL_FRAMES frames and the `back` (9·M) before them.
        held = len(self._fp)  # frames carried from earlier blocks (look-back and partial cell)
        fp = self._fp + frame_peaks
        cells: list[tuple[int, int]] = []  # (x of the first frame, comb peak)
        x0 = block.x0_ns
        pos = self._fp_back  # index in fp of the next cell's first frame
        while len(fp) - pos >= CELL_FRAMES:
            peak = max(fp[max(0, pos - back) : pos + CELL_FRAMES])
            x = self._carry_x_ns if pos < held else x0 + round((pos - held) * 1e9 / rate)
            cells.append((x, peak))
            pos += CELL_FRAMES
        if pos < len(fp) and pos >= held:  # a partial cell starts in this block
            self._carry_x_ns = x0 + round((pos - held) * 1e9 / rate)
        keep = max(0, pos - back)
        self._fp = fp[keep:]
        self._fp_back = pos - keep
        if not cells:
            return

        rows = []
        for x, peak in cells:
            s = schedule_at(x)
            s_us = _NAN if s is None else float(s)
            program = kind_at(x) == "program"
            rows.append((math.log2(1 + peak) if program else 0.0, s_us, s_us + d_out_us, program, float(x)))

        with self._lock:
            if gen != self._gen:
                return
            cap = self._cap
            for w, s_us, t_us, program, x_ns in rows:
                k = self._n % cap
                self._w[k] = w
                self._s[k] = s_us
                self._t[k] = t_us
                self._x[k] = x_ns
                self._program[k] = 1 if program else 0
                self._n += 1

    def mute_transition(self, t_raw_us: float, muted: bool) -> None:
        """The player applied a change of the effective mute (user mute, volume 0, tail mute) at RAW ``t``."""
        with self._lock:
            times, states = self._mute_t, self._mute_v
            if states and states[-1] == muted:
                return
            if times and t_raw_us < times[-1]:
                t_raw_us = times[-1]  # RAW never goes back; keep the list sorted regardless
            times.append(t_raw_us)
            states.append(muted)
            # Keep the newest transition before the cutoff: it is the state the window starts in.
            keep_from = bisect_left(times, t_raw_us - MUTE_KEEP_US) - 1
            if keep_from > 0:
                del times[:keep_from]
                del states[:keep_from]

    def fail(self) -> None:
        """Stop recording until the next reset (the player's tap call raised): ``error = internal_error``."""
        with self._lock:
            self._error = INTERNAL_ERROR

    def set_sbc_format(self, subbands: int, air_rate: int) -> None:
        """The speaker's SBC sub-bands and rate (from the air side): cells written from now on look
        back ``9·M`` air samples. Cells already in the ring keep the span they were cut with."""
        with self._lock:
            self._sbc = (subbands, air_rate)

    def reset(self) -> None:
        """The output was reopened: forget every cell (``x`` restarts at 0). The mute timeline stays."""
        with self._lock:
            self._fmt = None
            self._unsupported = None
            self._error = None
            self._cell_us = None
            self._n = 0
            self._gen += 1

    # -- reader side ------------------------------------------------------------------------

    @property
    def seconds(self) -> float:
        """How much the ring holds (s)."""
        return self._seconds

    @property
    def unsupported(self) -> str | None:
        with self._lock:
            return self._unsupported

    @property
    def error(self) -> str | None:
        with self._lock:
            return self._error

    @property
    def gen(self) -> int:
        """Bumped by every reset and format change: cell indexes of another generation are unrelated."""
        with self._lock:
            return self._gen

    @property
    def lookback_frames(self) -> int:
        """Input frames a cell currently looks back (``LOOKBACK_FRAMES`` until the format is known)."""
        with self._lock:
            return self._lookback_locked(self._fmt.sample_rate if self._fmt is not None else None)

    def _lookback_locked(self, input_rate: int | None) -> int:
        if self._sbc is None or input_rate is None:
            return LOOKBACK_FRAMES if self._sbc is None else self._sbc[0] * SBC_LOOKBACK_BLOCKS
        return lookback_frames(self._sbc[0], self._sbc[1], input_rate)

    @property
    def cell_us(self) -> float | None:
        with self._lock:
            return self._cell_us

    def newest_cell(self) -> int | None:
        with self._lock:
            return self._n - 1 if self._n else None

    def held(self) -> tuple[int, int] | None:
        """``(i0, i1)`` of the cells the ring holds now (all written since the last reset), None if none."""
        with self._lock:
            if self._cell_us is None or not self._n:
                return None
            return max(0, self._n - self._cap), self._n

    def mute_timeline(self) -> list[tuple[float, bool]]:
        """The retained mute transitions ``(t_raw_us, muted)``, oldest first."""
        with self._lock:
            return list(zip(self._mute_t, self._mute_v, strict=True))

    def slice(self, i0: int, i1: int) -> WrittenSlice | None:
        """Copy of cells ``i0`` (inclusive) to ``i1`` (exclusive); None if any is not in the ring."""
        with self._lock:
            cell_us = self._cell_us
            if cell_us is None or i0 > i1 or i0 < max(0, self._n - self._cap) or i1 > self._n:
                return None
            w = self._take(self._w, i0, i1)
            s = self._take(self._s, i0, i1)
            t = self._take(self._t, i0, i1)
            x = self._take(self._x, i0, i1)
            program = bytes(self._take(self._program, i0, i1))
            gen = self._gen
            times = list(self._mute_t)
            states = list(self._mute_v)
        muted = bytes(_muted_at(times, states, v) for v in s) if states else bytes(len(s))
        return WrittenSlice(i0=i0, cell_us=cell_us, w=w, s=s, t=t, program=program, muted=muted, gen=gen, x=x)

    def cell_near(self, s_us: float, around: int, span_s: float = 2.0) -> int | None:
        """Newest cell within ±``span_s`` of cell ``around`` whose ``s`` is within half a cell of ``s_us``.

        ``s`` is not monotonic (placements and inserts step it back), so this scans instead of bisecting.
        """
        with self._lock:
            cell_us = self._cell_us
            if cell_us is None or not self._n:
                return None
            span = math.ceil(span_s * 1e6 / cell_us)
            lo = max(around - span, self._n - self._cap, 0)
            hi = min(around + span + 1, self._n)
            if lo >= hi:
                return None
            s = self._take(self._s, lo, hi)
        half = cell_us / 2.0
        for k in range(len(s) - 1, -1, -1):
            if abs(s[k] - s_us) <= half:
                return lo + k
        return None

    # -- helpers ----------------------------------------------------------------------------

    def _configure_locked(self, fmt: InputFormat, supported: bool) -> None:
        """A new input format: an empty ring sized for it (call with the lock)."""
        self._fmt = fmt
        self._n = 0
        self._gen += 1
        if not supported:
            self._unsupported = f"input:{fmt.sample_rate}/{fmt.sample_bytes * 8}"
            self._cell_us = None
            self._cap = 0
            return
        self._unsupported = None
        self._cell_us = CELL_FRAMES / fmt.sample_rate * 1e6
        cap = max(1, math.ceil(self._seconds * fmt.sample_rate / CELL_FRAMES))
        if cap != self._cap:
            self._cap = cap
            self._w = array("f", [0.0]) * cap
            self._s = array("d", [_NAN]) * cap
            self._t = array("d", [_NAN]) * cap
            self._x = array("d", [_NAN]) * cap
            self._program = bytearray(cap)

    def _take(self, ring: _Ring, i0: int, i1: int) -> _Ring:
        """Cells ``i0:i1`` of one ring array, unwrapped (call with the lock)."""
        cap = self._cap
        a = i0 % cap if cap else 0
        n = i1 - i0
        if a + n <= cap:
            return ring[a : a + n]
        return ring[a:] + ring[: a + n - cap]
