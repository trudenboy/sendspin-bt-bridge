"""Playout scheduler: turns timed Sendspin chunks into a contiguous output stream.

The engine asks for one block at a time at the write head and says when that block's
first frame will leave the port (``audible_us``, RAW µs). The scheduler writes program
audio where a chunk is due and silence (``fill``) elsewhere, and records every run in the
:class:`ScheduleMap` as ``x`` (output stream time, ns) -> ``s`` (scheduled RAW time, µs),
so the engine can compute the playout error ``e = t - s(x_port)``.

Placement aligns the next program frame to the write head exactly: it either inserts
silence before the frame or drops the frames of its head that are already due, never
both. It happens for the first chunk, after a gap of more than 2 ms between chunks,
after the input ran dry, after ``clear`` and ``shift``, and on ``request_placement``.
Smaller gaps are written contiguously and left to the loop (the schedule still records
each chunk's own play time).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_bridge.services.audio.playout.timeline import ScheduleMap, SegmentKind

_NS_PER_S = 1_000_000_000
_US_PER_S = 1_000_000.0
# A contiguous chunk whose play time differs from the running schedule by more than this
# gets its own segment; below it the difference is play-time rounding (µs integers).
_SEGMENT_EPSILON_US = 1.0


@dataclass(frozen=True, slots=True)
class InputFormat:
    sample_rate: int
    channels: int = 2
    sample_bytes: int = 2

    def frame_bytes(self) -> int:
        return self.channels * self.sample_bytes


@dataclass(frozen=True, slots=True)
class Block:
    pcm: bytes
    x0_ns: int
    duration_ns: int
    program: bool  # any program frame in the block
    # Corrections and jumps only; placement silence and late drops are not counted here.
    dropped: int  # frames removed by corrections or a jump
    inserted: int  # frames added by corrections or a jump
    # Audio dropped by a placement because it was already due: the head of the placed
    # chunk, plus any chunk dropped whole because all of it was due.
    late_drop_us: float


class _Chunk:
    __slots__ = ("frames", "mv", "play_us", "pos")

    def __init__(self, play_us: float, mv: memoryview, frames: int) -> None:
        self.play_us = play_us  # RAW time of frame 0 (shifts included)
        self.mv = mv
        self.frames = frames
        self.pos = 0  # next frame to write


def _zeros(fmt: InputFormat, frames: int) -> bytes:
    return bytes(frames * fmt.frame_bytes())


class PlayoutScheduler:
    GAP_PLACEMENT_US = 2_000

    def __init__(
        self,
        *,
        schedule: ScheduleMap,
        block_ms: int = 10,
        fill: Callable[[InputFormat, int], bytes] | None = None,
    ) -> None:
        self._map = schedule
        self._block_ms = block_ms
        self._fill = fill or _zeros
        self._fmt: InputFormat | None = None
        self._queue: deque[_Chunk] = deque()
        # Output time is exact per format epoch: x = epoch_x0 + frames * 1e9 // fs.
        self._epoch_x0_ns = 0
        self._epoch_frames = 0
        # Mirror of the map's last segment, for linear continuation.
        self._seg: tuple[int, float, SegmentKind] | None = None
        self._placement: str | None = "start"  # pending placement reason
        self._pending_silence: int | None = None  # silence frames a decided placement still owes
        self._owed_insert = 0  # silence frames a negative jump still has to write
        self._expect_us: float | None = None  # play time of the frame after the last chunk
        self._decided = False  # the pending placement was already counted
        self._placed: _Chunk | None = None  # chunk aligned by the last placement
        self._last_reason: str | None = None
        self._placements = 0

    # -- inputs -------------------------------------------------------------------------

    def set_format(self, fmt: InputFormat) -> None:
        """Set the input format; a change starts a new epoch and drops queued audio."""
        if fmt == self._fmt:
            return
        if self._fmt is not None:
            self._epoch_x0_ns = self.x_w_ns
            self._epoch_frames = 0
            self._drop_queue("format")  # queued bytes are in the old format; re-place the next chunk
        self._fmt = fmt

    def submit(self, play_time_us: int, pcm: bytes) -> None:
        fmt = self._require_format()
        fb = fmt.frame_bytes()
        frames = len(pcm) // fb
        if frames:
            self._queue.append(_Chunk(float(play_time_us), memoryview(pcm)[: frames * fb], frames))

    def clear(self) -> int:
        """Drop queued program audio; the next chunk is placed. Returns the write head (x_end)."""
        self._drop_queue("clear")
        return self.x_w_ns

    def shift(self, shift_us: int) -> bool:
        """Move every queued chunk by ``shift_us`` and place the next frame exactly.

        Audio whose new time has already passed is dropped at the next render.
        Returns whether anything was queued.
        """
        for chunk in self._queue:
            chunk.play_us += shift_us
        if self._expect_us is not None:
            self._expect_us += shift_us
        self.request_placement("delay")
        return bool(self._queue)

    def request_placement(self, reason: str) -> None:
        """Re-place the next program frame at the write head on the next render."""
        self._placement = reason
        self._pending_silence = None
        self._decided = False

    # -- output -------------------------------------------------------------------------

    def render(
        self,
        *,
        audible_us: float,
        now_us: float,
        rate: float = 0.0,
        jump_us: float | None = None,
    ) -> Block:
        """One block at the write head. ``audible_us`` is the predicted RAW time at which the write
        head will leave the port (engine: t + (x_w − x_port)(1 + ρ̂)). Placement aligns the next
        program frame to it exactly.

        ``jump_us`` (None or 0.0 = no jump) is executed frame-exact at the write head: > 0 drops
        that much audio (owed placement silence first, then queued program), < 0 inserts that
        much ``fill``; the frames are reported in ``Block.dropped``/``inserted``. ``rate``
        corrections arrive in Stage 3."""
        del now_us  # used by the corrections of Stage 3
        if rate != 0.0:
            raise NotImplementedError("rate corrections arrive in Stage 3")
        fmt = self._require_format()
        fs = fmt.sample_rate
        fb = fmt.frame_bytes()
        n_block = fs * self._block_ms // 1000
        x0 = self.x_w_ns
        parts: list[bytes | memoryview] = []
        program = False
        late_drop_frames = 0
        dropped = 0
        inserted = 0
        if jump_us:
            jump = round(jump_us * fs / _US_PER_S)
            if jump > 0:
                dropped = self._jump_drop(jump, fs)
            elif jump < 0:
                self._owed_insert += -jump
        i = 0  # frames written in this block

        while i < n_block:
            room = n_block - i
            if self._owed_insert:
                n = min(room, self._owed_insert)
                self._write_silence(parts, i, n, audible_us)
                i += n
                inserted += n
                self._owed_insert -= n
                if not self._owed_insert:
                    self._resume_program(i)
                continue
            if self._pending_silence is not None:
                n = min(room, self._pending_silence)
                self._write_silence(parts, i, n, audible_us)
                i += n
                self._pending_silence -= n
                if self._pending_silence == 0:
                    self._pending_silence = None
                    if self._queue:
                        self._start_program(i, self._queue[0])
                continue
            if not self._queue:
                if self._placement is None:
                    self._placement = "starved"
                self._write_silence(parts, i, room, audible_us)
                i = n_block
                continue

            chunk = self._queue[0]
            if self._placement is None and chunk.pos == 0 and chunk is not self._placed:
                if self._expect_us is None or abs(chunk.play_us - self._expect_us) > self.GAP_PLACEMENT_US:
                    self._placement = "gap"
                else:
                    self._continue_program(i, chunk)
            if self._placement is not None:
                drop = self._place(chunk, i, audible_us, fs)
                if drop is None:  # all of it already due
                    late_drop_frames += chunk.frames - chunk.pos
                    self._finish(self._queue.popleft(), fs)
                    continue
                late_drop_frames += drop
                if self._pending_silence is not None:
                    continue

            n = min(room, chunk.frames - chunk.pos)
            parts.append(chunk.mv[chunk.pos * fb : (chunk.pos + n) * fb])
            chunk.pos += n
            i += n
            program = True
            if chunk.pos == chunk.frames:
                self._finish(self._queue.popleft(), fs)

        self._epoch_frames += n_block
        return Block(
            pcm=b"".join(parts),
            x0_ns=x0,
            duration_ns=self.x_w_ns - x0,
            program=program,
            dropped=dropped,
            inserted=inserted,
            late_drop_us=late_drop_frames * _US_PER_S / fs,
        )

    # -- state --------------------------------------------------------------------------

    @property
    def x_w_ns(self) -> int:
        return self._x_at(0)

    @property
    def queued(self) -> bool:
        return bool(self._queue)

    @property
    def last_placement_reason(self) -> str | None:
        return self._last_reason

    @property
    def placement_count(self) -> int:
        """Number of placements decided so far (lets the engine see a placement jump)."""
        return self._placements

    # -- internals ----------------------------------------------------------------------

    def _require_format(self) -> InputFormat:
        if self._fmt is None:
            raise RuntimeError("set_format() must be called first")
        return self._fmt

    def _x_at(self, offset_frames: int) -> int:
        fs = self._fmt.sample_rate if self._fmt else 1
        return self._epoch_x0_ns + (self._epoch_frames + offset_frames) * _NS_PER_S // fs

    def _s_linear(self, x_ns: int) -> float | None:
        if self._seg is None:
            return None
        x_s, s_s, _ = self._seg
        return s_s + (x_ns - x_s) / 1000.0

    def _append(self, x_ns: int, s_us: float, kind: SegmentKind) -> None:
        self._map.append(x_ns, s_us, kind)
        self._seg = (x_ns, s_us, kind)

    def _drop_queue(self, reason: str) -> None:
        self._queue.clear()
        self._expect_us = None
        self._placed = None
        self._owed_insert = 0
        self.request_placement(reason)

    def _write_silence(self, parts: list[bytes | memoryview], i: int, n: int, audible_us: float) -> None:
        assert self._fmt is not None
        if self._seg is None or self._seg[2] != "silence":
            x = self._x_at(i)
            s = self._s_linear(x)
            if s is None:  # nothing scheduled yet: silence is due when it leaves the port
                s = audible_us + i * _US_PER_S / self._fmt.sample_rate
            self._append(x, s, "silence")
        parts.append(self._fill(self._fmt, n))

    def _place(self, chunk: _Chunk, i: int, audible_us: float, fs: int) -> int | None:
        """Decide the pending placement for ``chunk`` at block offset ``i``.

        Either owes silence before the chunk (counted down over the next blocks, not
        re-decided) or drops its due head, never both. Returns the dropped head frames,
        or None when the whole remaining chunk is already due (the caller drops it and
        the placement stays pending for the next chunk).
        """
        if not self._decided:
            self._decided = True
            self._last_reason = self._placement
            self._placements += 1
        audible_i = audible_us + i * _US_PER_S / fs
        p_next = chunk.play_us + chunk.pos * _US_PER_S / fs
        n = round((p_next - audible_i) * fs / _US_PER_S)
        drop = max(0, -n)
        if drop >= chunk.frames - chunk.pos:
            return None
        self._placement = None
        self._decided = False
        self._placed = chunk
        if n > 0:
            self._pending_silence = n
            return 0
        chunk.pos += drop
        self._start_program(i, chunk)
        return drop

    def _jump_drop(self, frames: int, fs: int) -> int:
        """Drop ``frames`` at the write head; returns how many were actually dropped."""
        left = frames
        cancel = min(left, self._owed_insert)  # an unfinished insert is undone first
        self._owed_insert -= cancel
        left -= cancel
        if self._pending_silence is not None:
            n = min(left, self._pending_silence)
            self._pending_silence -= n
            left -= n
            if self._pending_silence:
                return frames - left - cancel
            self._pending_silence = None
        while left and self._queue:
            chunk = self._queue[0]
            n = min(left, chunk.frames - chunk.pos)
            chunk.pos += n
            left -= n
            if chunk.pos == chunk.frames:
                self._finish(self._queue.popleft(), fs)
        self._resume_program(0)
        return frames - left - cancel

    def _resume_program(self, i: int) -> None:
        """After a jump, schedule the next program frame at offset ``i`` with its own time."""
        if self._queue and self._placement is None and self._pending_silence is None and not self._owed_insert:
            self._start_program(i, self._queue[0])
            self._placed = self._queue[0]

    def _start_program(self, i: int, chunk: _Chunk) -> None:
        assert self._fmt is not None
        self._append(self._x_at(i), chunk.play_us + chunk.pos * _US_PER_S / self._fmt.sample_rate, "program")

    def _continue_program(self, i: int, chunk: _Chunk) -> None:
        x = self._x_at(i)
        s = self._s_linear(x)
        if s is None or self._seg is None or self._seg[2] != "program" or abs(chunk.play_us - s) > _SEGMENT_EPSILON_US:
            self._append(x, chunk.play_us, "program")

    def _finish(self, chunk: _Chunk, fs: int) -> None:
        self._expect_us = chunk.play_us + chunk.frames * _US_PER_S / fs
