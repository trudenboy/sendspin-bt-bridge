"""Air envelope of one A2DP link: SBC energy cells with send-anchored RAW times.

:class:`AirLinkTracker` consumes the :class:`~.a2dp_air.MediaPacket` records of one
``(index, handle)`` in send order and cuts them into :class:`AirBatch` es for the speaker's daemon
(design "AirLinkTracker"). Times are ``CLOCK_MONOTONIC_RAW``: the hub feeds the parser RAW seconds,
so ``t_send``/``t_complete`` are RAW seconds and the tracker does no clock conversion.

* **Energy.** Per SBC frame ``e = round(8·log2(1 + Σ_ch Σ_b∈B 2^sf))`` clipped to 0…254 over the
  sub-bands ``B`` whose centre lies in 2.5–8.5 kHz (``{1}`` when none does); :data:`MISSING` (255)
  marks a frame whose payload did not parse inside a contiguous packet run.
* **Media time.** ``u`` is the unwrapped RTP timestamp since the epoch start over the SBC rate.
* **Send anchor (R-5, canonical).** ``α`` = running median of the last :data:`ANCHOR_PACKETS`
  ``t_send − u`` of the anchor segment; a frame's cell time is ``t = u + α`` (``d_enc`` is applied
  by the daemon). The completion lag is telemetry only.
* **Anchor segment.** Contiguous RTP sequence with a forward timestamp step of more than one frame
  is PulseAudio's bluez5 "Skipping": the same epoch continues, the frame counter ``seq`` continues,
  the media time ``u0_us`` includes the skipped samples, and the median is reseeded with
  ``α_old − Δu_step`` (sends continued while media time jumped). The first batch after it carries
  ``anchor_reset``. A forward step of at most one frame only carries the anchor the same way.
  Frames the SBC media header counts but that did not parse are :data:`MISSING`, not a skip.
* **Epoch.** A sequence gap, more than :data:`SEND_GAP_S` between sends, an SSRC change, a backward
  (or implausibly large) timestamp step, a change of SBC parameters or of the timestamp source
  (``kernel``/``recv``) or of the hub's clock epoch, a non-SBC or fragmented packet,
  :meth:`AirLinkTracker.on_link_reset` and :meth:`AirLinkTracker.on_discontinuity` start a new
  epoch: ``seq`` and ``u0_us`` restart at 0, the anchor empties, and nothing of the epoch is sent
  before it has :data:`MIN_EPOCH_PACKETS` packets.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_bridge.services.bluetooth.a2dp_air import ChannelMode, MediaPacket, SbcFrame

__all__ = [
    "ANCHOR_PACKETS",
    "MAX_BATCH_FRAMES",
    "MIN_EPOCH_PACKETS",
    "MISSING",
    "SEND_GAP_S",
    "AirBatch",
    "AirLinkTracker",
    "SendAnchor",
    "TrackerState",
    "envelope_subbands",
    "frame_envelope",
]

MISSING = 255
MAX_BATCH_FRAMES = 128
ANCHOR_PACKETS = 64
SEND_GAP_S = 1.0
MIN_EPOCH_PACKETS = 32
BAND_LO_HZ = 2500.0
BAND_HI_HZ = 8500.0
MAX_ENERGY = 254
BATCH_AGE_US = 250_000.0  # a partial batch goes out once its first cell is this old
COMPLETE_LAG_WINDOW_S = 2.0
NON_SBC_S = 2.0  # non-SBC media for this long = unsupported codec
NO_TRAFFIC_S = 2.0
MAX_PENDING_FRAMES = 4096  # cells kept while nobody takes batches (oldest dropped in batch-sized chunks)
MAX_LAGS = 1024
_RTP_MOD = 1 << 32


def envelope_subbands(rate: int, subbands: int) -> tuple[int, ...]:
    """Sub-bands ``b ≥ 1`` whose centre ``(b + ½)·rate/(2M)`` lies in 2.5–8.5 kHz; ``(1,)`` if none."""
    width = rate / (2 * subbands)
    bands = tuple(b for b in range(1, subbands) if BAND_LO_HZ <= (b + 0.5) * width <= BAND_HI_HZ)
    return bands or (1,)


def frame_envelope(frame: SbcFrame, bands: tuple[int, ...]) -> int:
    """Log peak amplitude of *bands* in 1/8-bit steps: ``round(8·log2(1 + Σ 2^sf))``, 0…254."""
    total = 0
    for row in frame.scale_factors:
        for b in bands:
            if b < len(row):
                total += 1 << row[b]
    return min(MAX_ENERGY, round(8.0 * math.log2(1 + total)))


class SendAnchor:
    """Running median of ``t_send − u`` over the last ANCHOR_PACKETS packets of an anchor segment (µs)."""

    __slots__ = ("_sorted", "_window")

    def __init__(self) -> None:
        self._window: deque[float] = deque()
        self._sorted: list[float] = []

    def add(self, t_send_us: float, u_us: float) -> None:
        alpha = t_send_us - u_us
        if len(self._window) >= ANCHOR_PACKETS:
            old = self._window.popleft()
            del self._sorted[bisect.bisect_left(self._sorted, old)]
        self._window.append(alpha)
        bisect.insort(self._sorted, alpha)

    def value(self) -> float | None:
        n = len(self._sorted)
        if not n:
            return None
        mid = n // 2
        return self._sorted[mid] if n % 2 else (self._sorted[mid - 1] + self._sorted[mid]) / 2.0

    def reseed(self, alpha_us: float) -> None:
        """Anchor reset: shift the window so its median is ``alpha_us`` (``α_old − Δu_step``)."""
        current = self.value()
        if current is None:
            self._window.append(alpha_us)
            self._sorted.append(alpha_us)
            return
        shift = alpha_us - current
        self._window = deque(v + shift for v in self._window)
        self._sorted = [v + shift for v in self._sorted]

    def reset(self) -> None:
        """New epoch: nothing to carry."""
        self._window.clear()
        self._sorted.clear()


@dataclass(frozen=True, slots=True)
class AirBatch:
    epoch: int
    seq: int  # first frame's index among the frames sent in the epoch (continuity check)
    u0_us: float  # RTP media time of the first frame since the epoch start (skips included)
    t0_us: float  # RAW energy-cell time of the first frame: u + alpha (d_enc not applied)
    frame_us: float
    rate: int
    subbands: int
    blocks: int
    bands: tuple[int, ...]
    complete_lag_us: float | None
    timestamp: str  # kernel | recv
    anchor_reset: bool  # first batch of a new anchor segment inside the epoch
    e: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class TrackerState:
    state: Literal["streaming", "no_traffic", "unsupported_codec"]
    reason: str | None
    codec: str | None
    epoch: int
    anchor_segments: int
    frames: int
    in_step: bool


@dataclass(slots=True)
class _Run:
    """Contiguous cells of one anchor segment, not yet sent."""

    epoch: int
    seq0: int
    u0_us: float
    frame_us: float
    rate: int
    subbands: int
    blocks: int
    bands: tuple[int, ...]
    timestamp: str
    anchor_reset: bool
    e: list[int] = field(default_factory=list)
    alpha: float | None = None  # frozen anchor once closed
    closed: bool = False


@dataclass(slots=True)
class _Prev:
    seq: int
    ssrc: int
    t_send: float
    rtp_ts: int
    u: int  # unwrapped RTP ticks since the epoch start
    frames: int
    timestamp: str
    clock_epoch: int | None


_Params = tuple[int, int, int, "ChannelMode"]


class AirLinkTracker:
    """Per-link air envelope: see the module docstring. Not thread-safe (one hub thread feeds it)."""

    def __init__(self, *, codec_hint: Callable[[], str | None] = lambda: None, max_frames_per_s: int = 400) -> None:
        self._codec_hint = codec_hint
        self._max_frames_per_s = max_frames_per_s
        self._anchor = SendAnchor()
        self._runs: deque[_Run] = deque()
        self._pending = 0
        self._lags: deque[tuple[float, float]] = deque()
        self._epoch = 0
        self._epoch_packets = 0
        self._segments = 1
        self._sent = 0
        self._pending_reset = False
        self._prev: _Prev | None = None
        self._params: _Params | None = None
        self._bands: tuple[int, ...] = (1,)
        self._frames_total = 0
        self._last_t: float | None = None
        self._last_sbc = False
        self._in_step = False
        self._timestamp = "kernel"
        self._non_sbc_since: float | None = None
        self._unsupported: str | None = None

    # -- input ----------------------------------------------------------------

    def on_link_reset(self) -> None:
        self._end_epoch()

    def on_discontinuity(self) -> None:
        self._end_epoch()

    def on_packet(self, p: MediaPacket, *, timestamp: str, in_step: bool, clock_epoch: int | None = None) -> None:
        """One media packet of this link in send order; ``p.t_send``/``p.t_complete`` are RAW seconds."""
        t = p.t_send
        self._last_t = t
        self._in_step = in_step
        self._timestamp = timestamp
        if p.codec != "sbc":
            self._last_sbc = False
            self._end_epoch()
            if self._non_sbc_since is None:
                self._non_sbc_since = t
            if t - self._non_sbc_since >= NON_SBC_S:
                self._unsupported = f"codec:{self._codec_hint() or 'unknown'}"
            return
        self._last_sbc = True
        self._non_sbc_since = None
        if self._unsupported is not None and self._unsupported.startswith("codec:"):
            self._unsupported = None
        if p.sbc_fragmented:
            self._unsupported = "sbc_fragmented"  # explains the silence until frames decode again
            self._end_epoch()  # frames not decoded: their count is unknown
            return
        if self._unsupported == "sbc_fragmented":
            self._unsupported = None
        params = self._params_of(p)
        if params is None:
            self._end_epoch()  # nothing parsed and no parameters to count by
            return
        rate, blocks, subbands, _ = params
        if rate / (blocks * subbands) > self._max_frames_per_s:
            self._unsupported = "sbc_params"
            self._end_epoch()
            return
        if self._unsupported == "sbc_params":
            self._unsupported = None
        spf = blocks * subbands
        prev = self._prev
        u = 0
        if prev is not None:
            step = (p.rtp_ts - prev.rtp_ts) % _RTP_MOD
            if step >= _RTP_MOD // 2:
                step -= _RTP_MOD
            u = prev.u + step
            diff = u - (prev.u + prev.frames * spf)
            if (
                timestamp != prev.timestamp
                or clock_epoch != prev.clock_epoch
                or p.ssrc != prev.ssrc
                or p.seq != (prev.seq + 1) & 0xFFFF
                or t - prev.t_send > SEND_GAP_S
                or params != self._params
                or diff < 0
                or diff > rate * SEND_GAP_S
            ):
                self._end_epoch()
                prev = None
                u = 0
            elif diff > 0:
                self._step(diff, spf, rate)
        if prev is None:
            self._params = params
            self._bands = envelope_subbands(rate, subbands)
        frame_us = spf * 1e6 / rate
        u_us = u * 1e6 / rate
        self._anchor.add(t * 1e6, u_us)
        cells = [frame_envelope(f, self._bands) for f in p.frames]
        # frames the media header counts but that did not parse (they follow the parsed prefix)
        unparsed = (p.sbc_frame_count or 0) - len(cells)
        if unparsed > 0:
            cells += [MISSING] * unparsed
        if cells:
            self._append(u_us, cells, frame_us)
        self._epoch_packets += 1
        self._prev = _Prev(p.seq, p.ssrc, t, p.rtp_ts, u, len(cells), timestamp, clock_epoch)
        if p.t_complete is not None:
            self._lags.append((t, (p.t_complete - t) * 1e6))
            if len(self._lags) > MAX_LAGS:
                self._lags.popleft()
        while self._lags and t - self._lags[0][0] > COMPLETE_LAG_WINDOW_S:
            self._lags.popleft()

    def _params_of(self, p: MediaPacket) -> _Params | None:
        if not p.frames:
            return self._params if self._prev is not None else None
        f = p.frames[0]
        return (f.sampling_rate, f.blocks, f.subbands, f.channel_mode)

    def _step(self, diff: int, spf: int, rate: int) -> None:
        """Timestamp ahead of the frames before it on a contiguous sequence: samples never sent.

        Unparsed frames were already filled from the media header count, so any positive step is a
        skip. The anchor is always carried forward (sends continued while media time jumped); only a
        step of more than one frame is a new anchor segment the daemon is told about.
        """
        self._close_run()
        alpha = self._anchor.value()
        if alpha is not None:
            self._anchor.reseed(alpha - diff * 1e6 / rate)
        if diff > spf:  # PA "Skipping": anchor reset in the same epoch
            self._segments += 1
            self._pending_reset = True

    def _append(self, u_us: float, values: list[int], frame_us: float) -> None:
        run = self._runs[-1] if self._runs and not self._runs[-1].closed else None
        if run is None:
            assert self._params is not None
            rate, blocks, subbands, _ = self._params
            run = _Run(
                epoch=self._epoch,
                seq0=self._sent,
                u0_us=u_us,
                frame_us=frame_us,
                rate=rate,
                subbands=subbands,
                blocks=blocks,
                bands=self._bands,
                timestamp=self._timestamp,
                anchor_reset=self._pending_reset,
            )
            self._pending_reset = False
            self._runs.append(run)
        run.e.extend(values)
        self._sent += len(values)
        self._frames_total += len(values)
        self._pending += len(values)
        while self._pending > MAX_PENDING_FRAMES:
            self._drop_oldest()

    def _drop_oldest(self) -> None:
        run = self._runs[0]
        n = min(MAX_BATCH_FRAMES, len(run.e))
        self._advance(run, n)
        if not run.e and (run.closed or len(self._runs) > 1):
            self._runs.popleft()

    def _advance(self, run: _Run, n: int) -> None:
        # Clearing anchor_reset is fine also when _drop_oldest drops a reset batch: the daemon then
        # sees a forward seq jump and re-acquires, which covers the anchor reset.
        del run.e[:n]
        run.seq0 += n
        run.u0_us += n * run.frame_us
        run.anchor_reset = False
        self._pending -= n

    def _close_run(self) -> None:
        if self._runs and not self._runs[-1].closed:
            run = self._runs[-1]
            run.closed = True
            run.alpha = self._anchor.value()

    def _end_epoch(self) -> None:
        if self._prev is None:
            return
        self._close_run()
        if self._epoch_packets < MIN_EPOCH_PACKETS:
            while self._runs and self._runs[-1].epoch == self._epoch:
                self._pending -= len(self._runs.pop().e)
        self._epoch += 1
        self._epoch_packets = 0
        self._segments = 1
        self._sent = 0
        self._pending_reset = False
        self._prev = None
        self._params = None
        self._anchor.reset()

    # -- output ---------------------------------------------------------------

    def _complete_lag_us(self) -> float | None:
        return statistics.median(lag for _, lag in self._lags) if self._lags else None

    def take_batches(self, now_us: float, *, force: bool = False) -> list[AirBatch]:
        """Cut pending cells into batches of ≤ MAX_BATCH_FRAMES; a partial batch of the open segment
        waits until its first cell is BATCH_AGE_US old unless *force*. ``now_us`` is RAW µs."""
        out: list[AirBatch] = []
        lag = self._complete_lag_us()
        while self._runs:
            run = self._runs[0]
            if run.epoch == self._epoch and self._epoch_packets < MIN_EPOCH_PACKETS:
                break
            alpha = run.alpha if run.closed else self._anchor.value()
            if alpha is None:
                break
            while run.e:
                n = min(MAX_BATCH_FRAMES, len(run.e))
                if n < MAX_BATCH_FRAMES and not (run.closed or force or now_us - (run.u0_us + alpha) >= BATCH_AGE_US):
                    break
                out.append(
                    AirBatch(
                        epoch=run.epoch,
                        seq=run.seq0,
                        u0_us=run.u0_us,
                        t0_us=run.u0_us + alpha,
                        frame_us=run.frame_us,
                        rate=run.rate,
                        subbands=run.subbands,
                        blocks=run.blocks,
                        bands=run.bands,
                        complete_lag_us=lag,
                        timestamp=run.timestamp,
                        anchor_reset=run.anchor_reset,
                        e=tuple(run.e[:n]),
                    )
                )
                self._advance(run, n)
            if run.e or not run.closed:
                break
            self._runs.popleft()
        return out

    def state(self, now_us: float) -> TrackerState:
        if self._last_t is None or now_us - self._last_t * 1e6 > NO_TRAFFIC_S * 1e6:
            state: Literal["streaming", "no_traffic", "unsupported_codec"] = "no_traffic"
            reason = None
        elif self._unsupported is not None:
            state, reason = "unsupported_codec", self._unsupported
        else:
            state, reason = "streaming", None
        if self._last_t is None:
            codec = None
        else:
            codec = "sbc" if self._last_sbc else (self._codec_hint() or "unknown")
        return TrackerState(
            state=state,
            reason=reason,
            codec=codec,
            epoch=self._epoch,
            anchor_segments=self._segments,
            frames=self._frames_total,
            in_step=self._in_step,
        )
