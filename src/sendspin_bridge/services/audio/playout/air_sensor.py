"""AirSensor: when the audio the daemon wrote left over the air (design "Correlation", "Lock").

The parent ships the speaker's SBC energy cells (:class:`AirEnvelope`, validated by the IPC
layer); the playout player's tap (:class:`WrittenEnvelope`) records the energy of the PCM it
wrote with its schedule ``s`` and heard schedule ``T = s + D_out``. Once per :data:`HOP_S` this
module cuts a :data:`WINDOW_S` window of air cells, copies the written cells that cover the search
range around its prior, runs the correlator **off the event loop** (``run_window``, the default
executor in the daemon) and turns a confirmed lag into the host placement

    H = t_mid − s(i_m) − (d_enc − d_comb)        E = H + L_as − D_out   (with a calibration)

with ``t_mid`` the energy-cell air time of the window's middle air cell, ``i_m`` its written
partner (fractional; ``s`` interpolated inside the schedule segment that holds it, else the
longest one), ``d_enc = 4.5·M/rate`` the SBC analysis delay of the air energy and ``d_comb`` the
comb's half lag on the written side.

**Lag.** ``L`` relates the written cell index (since the output opened) to the air media time:
``i = u / cell_us + L``. PulseAudio consumes ``x`` and the encoder stamps it with RTP media time
at the same rate, so ``L`` is constant until the output reopens, the epoch changes or PulseAudio
plays audio that is not ours (an underrun the port ran through). Engine jumps move ``s``, not
``L``; a PA "Skipping" anchor reset keeps ``L`` (media time includes the skip) and steps ``H`` by
the skipped content (air time runs on the frame count, R-18).

**Search policy (R-15).** Acquisition (no lag, or no lock for :data:`NO_LOCK_S`): the prior puts
the window's middle air cell on the written cell whose ``s`` is nearest ``t_mid − δ₀ − d_enc``,
range ±:data:`ACQUIRE_US`. Tracking: ±:data:`TRACK_CELLS` around the last lag. After an
``underrun``/``pa_step``/``stall`` event ±:data:`WIDE_CELLS` until a window lying wholly after
the event is confirmed (and while a lag candidate is pending, so the candidate can be confirmed or
refuted). After an anchor reset the windows search ±:data:`RESET_CELLS` until one is confirmed.
Skipped and rejected windows never end either. Every search passes the
correlator ``SIDE_SCAN`` extra lags each side (range-independent PSR).

**Continuity.** A confirmed lag more than :data:`JUMP_CONFIRM_CELLS` from the current one (or the
first lag of an epoch, an output or after ``no_lock``) is held as a candidate and accepted only
when the next confirmed window agrees within :data:`AGREE_CELLS`; a lone wrong peak never moves
the lag.

**Lock (R-10).** The age of the lock is the air time since the middle of the newest accepted
window: ``locked`` ≤ :data:`LOCK_HOLD_S`, ``holding`` up to :data:`NO_LOCK_S`, then ``no_lock``
(values null). A new epoch, a reopen or a tap generation change (format change) clears the lag:
``acquiring``. ``fallback`` → ``unavailable``/``playout_fallback`` and ``accepts = []``.

**Windows.** Valid when the correlator's checks pass (≥ 90 % air present, ≥ 80 % written program)
and at most :data:`MAX_MUTED_FRACTION` of the written partner cells are muted; muted, invalid and
flat windows are *skipped* (they never count against the confidence), edge/no-side-lobe/gate
failures are *rejected*. A window is cut only when its newest air cell is at least
:data:`MIN_ADVANCE_S` later in media time than the last window's: air that stopped arriving (a
stall, an AVDTP gap) is never correlated twice, so two copies of one window cannot "agree".

Nothing here raises into the daemon: any exception puts the sensor into
``unavailable``/``internal_error`` until the next reopen.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import statistics
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from sendspin_bridge.services.audio.playout.air_correlator import PSR_GATE, SIDE_SCAN, WindowResult, correlate_window
from sendspin_bridge.services.audio.playout.air_tap import CELL_FRAMES, INTERNAL_ERROR, LOOKBACK_FRAMES, comb_lag
from sendspin_bridge.services.ipc.commands import AirEnvelope, AirFold, AirSource, SetAirCalibration

if TYPE_CHECKING:
    from array import array
    from collections.abc import Awaitable, Callable

    from sendspin_bridge.services.audio.playout.air_tap import WrittenEnvelope
    from sendspin_bridge.services.ipc.commands import AirCommand

logger = logging.getLogger(__name__)

LOCK_HOLD_S = 5.0
NO_LOCK_S = 20.0
WINDOW_S = 3.0
HOP_S = 1.0
TRACK_CELLS = 20
WIDE_CELLS = 86
RESET_CELLS = 5
ACQUIRE_US = 500_000.0
DELTA0_US = 10_000.0
JUMP_CONFIRM_CELLS = 3
MAX_MUTED_FRACTION = 0.2

MIN_ADVANCE_S = HOP_S / 2  # a window's air must end this much later than the last window's
AGREE_CELLS = 1.0  # a candidate lag is confirmed by the next confirmed window within this
MISSING = 255  # air energy of an unparsable frame
AIR_RING_S = 20.0
SBC_ANALYSIS_DELAY_BLOCKS = 4.5  # the SBC encoder's analysis delay in sub-band samples (·M input samples)
CONF_PSR_FULL = 1.40  # R-11: the PSR median at which the confidence's PSR term reaches 1
CONF_WINDOW_S = 10.0
JITTER_WINDOW_S = 30.0
H_MEDIAN_OF = 5
INFO_EVERY_S = 60.0
DEBUG_SUMMARY_S = 10.0
_GRID_TOL_US = 1.0  # a grid point this close to an air frame takes its value as is
_CONTIGUOUS = 1.001  # frames closer than this many frame durations are contiguous (interpolated)
_SOURCE_STATES = {"unavailable": "unavailable", "unsupported_codec": "unsupported_codec", "no_traffic": "no_signal"}

EngineEvent = Literal["underrun", "pa_step", "stall", "reopen", "fallback"]
AddStatus = Literal["ok", "new_epoch", "anchor_reset", "dropped"]


def enc_delay_us(subbands: int, rate: int) -> float:
    """``d_enc``: the SBC scale-factor energy lags the PCM it encodes by ``4.5·M / rate``."""
    return SBC_ANALYSIS_DELAY_BLOCKS * subbands / rate * 1e6


def comb_delay_us(rate: int) -> float:
    """``d_comb``: the written comb ``x[n] − x[n−d]`` is centred ``d/2`` samples back."""
    return comb_lag(rate) / 2.0 / rate * 1e6


def lookback_delay_us(rate: int, frames: int = LOOKBACK_FRAMES) -> float:
    """A written cell's span reaches ``frames`` (9·M) back, so its energy is centred half of that
    earlier than the cell; credited back with ``d_comb`` (at equal rates it cancels ``d_enc``)."""
    return frames / 2.0 / rate * 1e6


class AirStream:
    """Air cells of the current epoch on the media-time axis (20 s ring).

    Each frame keeps its own air time ``t0_us + k·frame_us`` from its batch: batches never span a
    skip, and the parent anchors ``t0_us`` on the frame count (R-18), so air time is linear in the
    frame count while the content position (the correlation grid) follows media time ``u``.

    Not thread-safe: the daemon loop owns it.
    """

    def __init__(self, *, seconds: float = AIR_RING_S) -> None:
        self._seconds = seconds
        self._epoch: int | None = None
        self._u: list[float] = []
        self._t: list[float] = []
        self._e: list[int | None] = []
        self._next_seq: int | None = None
        self._next_u_min = 0.0
        self._frame_us: float | None = None
        self._t_floor = -math.inf
        self.subbands = 8
        self.rate = 44100

    @property
    def epoch(self) -> int | None:
        return self._epoch

    @property
    def frame_us(self) -> float | None:
        return self._frame_us

    def clear(self, *, t_floor: float | None = None) -> None:
        """Forget the frames (the epoch stays); with ``t_floor``, later frames aired before it are ignored."""
        self._u, self._t, self._e = [], [], []
        self._next_seq = None
        if t_floor is not None:
            self._t_floor = t_floor

    def add(self, env: AirEnvelope) -> AddStatus:
        n = len(env.e)
        status: AddStatus
        if env.epoch != self._epoch:
            self._epoch = env.epoch
            self.clear()
            self._t_floor = -math.inf
            status = "new_epoch"
        elif self._next_seq is None:
            status = "anchor_reset" if env.anchor_reset else "ok"
        elif (
            env.seq != self._next_seq
            or env.u0_us < self._next_u_min
            or self._frame_us is None
            or abs(env.frame_us - self._frame_us) > 1e-6 * self._frame_us
        ):
            self.clear()
            return "dropped"
        else:
            status = "anchor_reset" if env.anchor_reset else "ok"
        self._next_seq = env.seq + n
        self._next_u_min = env.u0_us + n * env.frame_us - 1.0
        self._frame_us = env.frame_us
        self.subbands, self.rate = env.subbands, env.rate
        for k, e in enumerate(env.e):
            t = env.t0_us + k * env.frame_us
            if t < self._t_floor:
                continue
            self._u.append(env.u0_us + k * env.frame_us)
            self._t.append(t)
            self._e.append(None if e == MISSING else e)
        self._trim()
        return status

    def newest_u(self) -> float | None:
        return self._u[-1] if self._u else None

    def frame_at_or_before(self, u_us: float) -> float | None:
        j = bisect_right(self._u, u_us + _GRID_TOL_US) - 1
        return self._u[j] if j >= 0 else None

    def window(self, u_end_us: float, n: int, cell_us: float) -> tuple[list[float | None], float, float] | None:
        """``n`` cells ending at media time ``u_end_us`` on a ``cell_us`` grid, the grid's first media
        time and the energy-cell air time of its middle (``n // 2``); None if the span is not held."""
        us = self._u
        if not us or self._frame_us is None or n < 1:
            return None
        u_first = u_end_us - (n - 1) * cell_us
        if u_first < us[0] - _GRID_TOL_US or u_end_us > us[-1] + _GRID_TOL_US:
            return None
        cells = [self._value_at(u_first + k * cell_us) for k in range(n)]
        u_mid = u_first + (n // 2) * cell_us
        return cells, u_first, self._air_time_at(u_mid)

    def _air_time_at(self, u: float) -> float:
        """Air time of media time ``u``. Air time runs on the frame count (R-18): along contiguous
        frames it advances with ``u``; a skipped media span between two held frames airs no time of
        its own, so a ``u`` inside it is placed proportionally between the frames either side."""
        us, ts = self._u, self._t
        j = max(0, bisect_right(us, u + _GRID_TOL_US) - 1)
        if (
            j + 1 < len(us)
            and u > us[j]
            and self._frame_us is not None
            and us[j + 1] - us[j] > _CONTIGUOUS * self._frame_us
        ):
            return ts[j] + (u - us[j]) / (us[j + 1] - us[j]) * (ts[j + 1] - ts[j])
        return ts[j] + (u - us[j])

    def _value_at(self, u: float) -> float | None:
        us, es = self._u, self._e
        j = bisect_right(us, u + _GRID_TOL_US) - 1
        if j < 0:
            return None
        if u - us[j] <= _GRID_TOL_US:
            return es[j]
        if j + 1 >= len(us) or self._frame_us is None or us[j + 1] - us[j] > _CONTIGUOUS * self._frame_us:
            return None
        a, b = es[j], es[j + 1]
        if a is None or b is None:
            return None
        f = (u - us[j]) / (us[j + 1] - us[j])
        return a + (b - a) * f

    def _trim(self) -> None:
        if not self._u or self._frame_us is None:
            return
        cap = math.ceil(self._seconds * 1e6 / self._frame_us)
        if len(self._u) > cap + cap // 4:
            cut = bisect_right(self._u, self._u[-1] - self._seconds * 1e6)
            del self._u[:cut], self._t[:cut], self._e[:cut]


@dataclass(frozen=True, slots=True)
class _Plan:
    """One window's inputs (copies) and what the loop needs to place its result."""

    cells: list[float | None]
    w: list[float]
    program: bytes
    lag_lo: int
    lag_hi: int
    margin: int
    frac: float  # media grid phase: correlator lag = L + frac
    t_mid: float
    s: array
    t: array
    n: int
    cell_us: float
    d_enc: float
    d_comb: float
    mode: str
    t_first: float
    gen: int  # tap generation of the written slice


@dataclass(frozen=True, slots=True)
class AirWindow:
    """An accepted (or candidate) confirmed window."""

    at_us: float  # energy-cell air time of the window's middle
    lag: float  # L (cells)
    h_us: float
    d_out_us: float
    psr: float
    s_mid: float


async def _run_in_executor(fn: Callable[[], WindowResult]) -> WindowResult:
    return await asyncio.get_running_loop().run_in_executor(None, fn)


def _iso_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="seconds")


class AirSensor:
    """The daemon's air sensor (see the module docstring). Runs on the daemon loop."""

    def __init__(
        self,
        *,
        tap: WrittenEnvelope,
        now_us: Callable[[], float],
        output_delay_ms: Callable[[], float],
        run_window: Callable[[Callable[[], WindowResult]], Awaitable[WindowResult]] | None = None,
        wall_iso: Callable[[], str] = _iso_now,
        log: logging.Logger = logger,
    ) -> None:
        self._tap = tap
        self._now = now_us
        self._output_delay_ms = output_delay_ms
        self._run_window = run_window or _run_in_executor
        self._wall_iso = wall_iso
        self._log = log
        self._debug_windows = os.environ.get("SENDSPIN_AIR_DEBUG") == "1"
        self._air = AirStream()
        self._tap_gen = tap.gen
        self._source: AirSource | None = None
        self._latency_ms: float | None = None
        self._fold: dict[str, object] | None = None  # status ``fold``: the answer to the last air_fold
        self._fallback = False
        self._error: str | None = None
        self._busy = False
        self._token = 0  # bumped by every reset; a window computed across one is dropped
        # Counters.
        self._epochs = 0
        self._frames = 0
        self._missing = 0
        self._dropped = 0
        self._confirmed_n = 0
        self._rejected_n = 0
        self._skipped_n = 0
        self._timestamp: str | None = None
        self._complete_lag_us: float | None = None
        self._rate: int | None = None  # SBC sampling rate of the newest air batch
        # Lock state.
        self._lag: float | None = None
        self._candidate: AirWindow | None = None
        self._windows: deque[AirWindow] = deque()
        self._outcomes: deque[tuple[float, bool]] = deque()  # (air time, accepted) of non-skipped windows
        self._last_at: float | None = None  # air time of the newest accepted window's middle
        self._last_psr: float | None = None
        self._last_confirmed_iso: str | None = None
        self._last_window_now: float | None = None
        self._last_u_end: float | None = None  # media time of the last window's newest air cell
        self._wide_after: float | None = None  # wide search until a window starts after this air time
        self._reset_pending = False
        # Logging.
        self._state_logged: str | None = None
        self._info_at: dict[str, float] = {}
        self._summary_at: float | None = None

    # -- inputs (daemon loop) ---------------------------------------------------------------

    def submit(self, command: AirCommand) -> None:
        """:class:`AirSink` entry: one validated air command."""
        if isinstance(command, AirEnvelope):
            self.on_envelope(command)
        elif isinstance(command, AirSource):
            self.on_source(command)
        elif isinstance(command, SetAirCalibration):
            self.set_calibration(command.latency_ms)
        elif isinstance(command, AirFold):
            # The written fold arrives with the calibration task (A16b); until then every fold is
            # answered, so the parent never waits for one.
            self._fold = {"id": command.id, "phase_ms": None, "ok": False, "reason": "not_implemented"}

    def on_envelope(self, env: AirEnvelope) -> None:
        try:
            status = self._air.add(env)
            self._tap.set_sbc_format(env.subbands, env.rate)
            if status == "dropped":
                self._dropped += 1
                self._reset_lock()
                return
            if status == "new_epoch":
                self._epochs += 1
                self._reset_lock()
            elif status == "anchor_reset":
                self._reset_pending = True
            self._frames += len(env.e)
            self._missing += sum(1 for e in env.e if e == MISSING)
            self._timestamp = env.timestamp
            self._complete_lag_us = env.complete_lag_us
            self._rate = env.rate
        except Exception:
            self._fail()

    def on_source(self, src: AirSource) -> None:
        try:
            self._source = src
        except Exception:
            self._fail()

    def set_calibration(self, latency_ms: float | None) -> None:
        try:
            self._latency_ms = None if latency_ms is None else float(latency_ms)
        except Exception:
            self._fail()

    def on_engine_event(self, kind: EngineEvent) -> None:
        try:
            if kind == "fallback":
                self._fallback = True
                self._reset_lock()
            elif kind == "reopen":
                self._reopen()
            elif kind in ("underrun", "pa_step", "stall"):
                h = self._reported_h()
                self._wide_after = self._now() + (h if h is not None and h > 0 else 0.0)
        except Exception:
            self._fail()

    # -- window step ------------------------------------------------------------------------

    async def step(self) -> None:
        """Compute at most one window per :data:`HOP_S`, off the loop via ``run_window``."""
        if self._busy:
            return
        try:
            await self._step()
        except Exception:
            self._fail()
        finally:
            self._busy = False

    async def _step(self) -> None:
        self._check_tap()
        now = self._now()
        state, reason = self._state(now)
        self._note_state(state, reason, now)
        if self._fallback or self._error is not None or state not in ("acquiring", "locked", "holding", "no_lock"):
            return
        if self._last_window_now is not None and now - self._last_window_now < HOP_S * 1e6:
            return
        if self._lag is not None and self._last_at is not None and now - self._last_at > NO_LOCK_S * 1e6:
            # no_lock: search as in acquisition, and the first lag found again needs two windows.
            self._lag = None
            self._candidate = None
        plan = self._plan(now)
        if plan is None:
            return
        self._last_window_now = now
        if isinstance(plan, str):
            self._skipped_n += 1
            return
        token = self._token
        self._busy = True
        result = await self._run_window(lambda: _correlate(plan))
        if token != self._token or self._error is not None:
            return
        self._apply(plan, result)

    def _plan(self, now: float) -> _Plan | Literal["skipped"] | None:
        tap = self._tap
        cell = tap.cell_us
        newest = tap.newest_cell()
        u_new = self._air.newest_u()
        if cell is None or newest is None or u_new is None:
            return None
        n = round(WINDOW_S * 1e6 / cell)
        in_rate = round(CELL_FRAMES / cell * 1e6)
        d_enc = enc_delay_us(self._air.subbands, self._air.rate)
        d_comb = comb_delay_us(in_rate) + lookback_delay_us(in_rate, tap.lookback_frames)  # cells' centre
        mode, half = self._search(now, cell)
        margin = SIDE_SCAN
        u_end: float | None = u_new
        for _ in range(3):
            if u_end is None:
                return None
            win = self._air.window(u_end, n, cell)
            if win is None:
                return None
            cells, u_first, t_mid = win
            base_f = u_first / cell
            base = math.floor(base_f)
            frac = base_f - base
            if mode == "acquire":
                prior = self._prior_lag(t_mid - DELTA0_US - d_enc, u_first + (n // 2) * cell, newest, cell)
                if prior is None:
                    return None
            else:
                assert self._lag is not None
                prior = self._lag
            centre = round(prior + frac)
            i0 = base + centre - half - margin
            i1 = base + centre + half + margin + n
            excess = i1 - (newest + 1)
            if excess <= 0:
                break
            u_end = self._air.frame_at_or_before(u_end - excess * cell)
        else:
            return None
        if self._last_u_end is not None and u_end - self._last_u_end < MIN_ADVANCE_S * 1e6:
            return None  # the air has not moved on (a stall or AVDTP gap): never correlate it twice
        if i0 < 0:
            return None
        sl = tap.slice(i0, i1)
        if sl is None:
            return None
        if sl.gen != self._tap_gen:
            self._check_tap()
            return None
        self._last_u_end = u_end
        span = sl.muted[half + margin : half + margin + n]
        if sum(span) > MAX_MUTED_FRACTION * n:
            return "skipped"
        t_first = t_mid - (n // 2) * cell
        return _Plan(
            cells=cells,
            w=list(sl.w),
            program=sl.program,
            lag_lo=centre - half,
            lag_hi=centre + half,
            margin=margin,
            frac=frac,
            t_mid=t_mid,
            s=sl.s,
            t=sl.t,
            n=n,
            cell_us=cell,
            d_enc=d_enc,
            d_comb=d_comb,
            mode=mode,
            t_first=t_first,
            gen=sl.gen,
        )

    def _search(self, now: float, cell: float) -> tuple[str, int]:
        if self._lag is None or self._last_at is None or now - self._last_at > NO_LOCK_S * 1e6:
            return "acquire", round(ACQUIRE_US / cell)
        if self._wide_after is not None or self._candidate is not None:
            return "wide", WIDE_CELLS
        if self._reset_pending:
            return "reset", RESET_CELLS
        return "track", TRACK_CELLS

    def _prior_lag(self, s_target: float, u_mid: float, newest: int, cell: float) -> float | None:
        """The acquisition prior: the written cell whose ``s`` is nearest ``s_target``, as a lag."""
        last = self._tap.slice(newest, newest + 1)
        if last is None or math.isnan(last.s[0]):
            return None
        around = min(newest, newest - round((last.s[0] - s_target) / cell))
        idx = self._tap.cell_near(s_target, around=max(0, around))
        if idx is None:
            idx = around
        return idx - u_mid / cell

    def _apply(self, plan: _Plan, res: WindowResult) -> None:
        if plan.gen != self._tap.gen:
            self._check_tap()  # the cells were renumbered while the window ran: its lag means nothing
            return
        if res.status == "confirmed" and res.lag is not None:
            # The widened/narrowed search ends only with a confirmed window (wholly after the event).
            if plan.mode == "reset":
                self._reset_pending = False
            if self._wide_after is not None and plan.mode == "wide" and plan.t_first > self._wide_after:
                self._wide_after = None
        if res.status in ("invalid", "flat"):
            self._skipped_n += 1
            return
        if res.status != "confirmed" or res.lag is None or res.psr is None:
            self._reject(plan.t_mid)
            self._debug_window(plan, res, None)
            return
        placed = self._placement(plan, res.lag)
        if placed is None:
            self._skipped_n += 1
            return
        h_us, d_out_us, s_mid = placed
        win = AirWindow(plan.t_mid, res.lag - plan.frac, h_us, d_out_us, res.psr, s_mid)
        self._debug_window(plan, res, win)
        cand = self._candidate
        if self._lag is not None and abs(win.lag - self._lag) <= JUMP_CONFIRM_CELLS:
            if cand is not None:
                self._reject(cand.at_us)
                self._candidate = None
            self._accept(win)
        elif cand is not None and abs(win.lag - cand.lag) <= AGREE_CELLS:
            self._candidate = None
            self._accept(cand)
            self._accept(win)
        else:
            if cand is not None:
                self._reject(cand.at_us)
            self._candidate = win

    def _placement(self, plan: _Plan, lag: float) -> tuple[float, float, float] | None:
        """``(H, D_out, s_mid)`` for a confirmed correlator lag; None without a schedule there."""
        s, t, cell = plan.s, plan.t, plan.cell_us
        off = lag - (plan.lag_lo - plan.margin)  # local index of the written partner of air cell 0
        lo = max(0, math.floor(off))
        hi = min(len(s) - 1, math.ceil(off + plan.n - 1))
        # Segments of the matched span: a step of s by more than half a cell off its cadence ends one.
        segments: list[tuple[int, int]] = []
        start = None
        for k in range(lo, hi + 1):
            ok = not math.isnan(s[k])
            if ok and start is not None and abs(s[k] - s[k - 1] - cell) <= cell / 2:
                continue
            if start is not None:
                segments.append((start, k - 1))
            start = k if ok else None
        if start is not None:
            segments.append((start, hi))
        if not segments:
            return None
        # H describes the window's middle: s from the segment holding it (an engine step inside the
        # window is placed by where the middle is), else from the longest one.
        x_m = off + plan.n // 2
        k_m = math.floor(x_m)
        middle = [(a, b) for a, b in segments if a <= k_m <= b and b > a]
        a, b = middle[0] if middle else max(segments, key=lambda seg: seg[1] - seg[0])
        if b > a:
            k = min(max(math.floor(x_m), a), b - 1)
            slope = s[k + 1] - s[k]
        else:
            k, slope = a, cell
        s_mid = s[k] + (x_m - k) * slope
        d_out = t[k] - s[k]
        if math.isnan(d_out):
            d_out = self._output_delay_ms() * 1000.0
        return plan.t_mid - s_mid - (plan.d_enc - plan.d_comb), d_out, s_mid

    def _accept(self, win: AirWindow) -> None:
        if self._last_at is not None and win.at_us - self._last_at > NO_LOCK_S * 1e6:
            # A lock regained after no_lock: what was measured before it says nothing about now.
            self._windows.clear()
            self._outcomes.clear()
        self._lag = win.lag
        self._confirmed_n += 1
        self._windows.append(win)
        self._outcomes.append((win.at_us, True))
        if self._last_at is None or win.at_us >= self._last_at:
            self._last_at = win.at_us
            self._last_psr = win.psr
        self._last_confirmed_iso = self._wall_iso()
        self._prune(win.at_us)

    def _reject(self, at_us: float) -> None:
        self._rejected_n += 1
        self._outcomes.append((at_us, False))
        self._prune(at_us)

    def _prune(self, at_us: float) -> None:
        while len(self._windows) > H_MEDIAN_OF and at_us - self._windows[0].at_us > JITTER_WINDOW_S * 1e6:
            self._windows.popleft()
        while self._outcomes and at_us - self._outcomes[0][0] > CONF_WINDOW_S * 1e6:
            self._outcomes.popleft()

    # -- resets -----------------------------------------------------------------------------

    def _reset_lock(self) -> None:
        self._token += 1
        self._lag = None
        self._candidate = None
        self._windows.clear()
        self._outcomes.clear()
        self._last_at = None
        self._last_psr = None
        self._last_window_now = None
        self._last_u_end = None
        self._wide_after = None
        self._reset_pending = False

    def _reopen(self) -> None:
        """The output reopened (or the tap's cell indexes restarted): the lag is gone."""
        self._tap_gen = self._tap.gen
        self._error = None
        self._reset_lock()
        self._air.clear(t_floor=self._now())

    def _check_tap(self) -> None:
        if self._tap.gen != self._tap_gen:
            self._reopen()

    def fail(self) -> None:
        """Something around the sensor failed (the daemon's containment): ``unavailable``/``internal_error``."""
        self._fail()

    def _fail(self) -> None:
        if self._error is None:
            self._log.exception("Air sensor failed; disabled until the output reopens")
        self._error = INTERNAL_ERROR
        self._token += 1

    # -- snapshot ---------------------------------------------------------------------------

    def _state(self, now: float) -> tuple[str, str | None]:
        if self._fallback:
            return "unavailable", "playout_fallback"
        if self._error is not None or self._tap.error is not None:
            return "unavailable", INTERNAL_ERROR
        src = self._source
        if src is not None and src.state != "streaming":
            return _SOURCE_STATES.get(src.state, "unavailable"), src.reason
        unsupported = self._tap.unsupported
        if unsupported is not None:
            return "unsupported_input", unsupported
        if src is None and self._frames == 0:
            return "no_signal", None
        if self._last_at is None:
            return "acquiring", None
        age = now - self._last_at
        if age <= LOCK_HOLD_S * 1e6:
            return "locked", None
        if age <= NO_LOCK_S * 1e6:
            return "holding", None
        return "no_lock", None

    def _reported_h(self) -> float | None:
        if not self._windows:
            return None
        return statistics.median(w.h_us for w in list(self._windows)[-H_MEDIAN_OF:])

    def _confidence(self, state: str, now: float) -> float | None:
        if state not in ("locked", "holding") or not self._windows or self._last_at is None:
            return None
        recent = [w.psr for w in self._windows if now - w.at_us <= CONF_WINDOW_S * 1e6]
        psrs = recent or [w.psr for w in list(self._windows)[-H_MEDIAN_OF:]]
        psr_term = min(1.0, max(0.0, (statistics.median(psrs) - PSR_GATE) / (CONF_PSR_FULL - PSR_GATE)))
        outs = [ok for at, ok in self._outcomes if now - at <= CONF_WINDOW_S * 1e6]
        f_ok = sum(outs) / len(outs) if outs else 1.0
        age = now - self._last_at
        decay = (
            1.0 if state == "locked" else max(0.0, 1.0 - (age - LOCK_HOLD_S * 1e6) / ((NO_LOCK_S - LOCK_HOLD_S) * 1e6))
        )
        return psr_term * f_ok * decay

    def _jitter_ms(self, now: float) -> float | None:
        hs = [w.h_us for w in self._windows if now - w.at_us <= JITTER_WINDOW_S * 1e6]
        if not hs:
            return None
        med = statistics.median(hs)
        return 1.4826 * statistics.median(abs(h - med) for h in hs) / 1000.0

    def accepted_windows(self) -> tuple[AirWindow, ...]:
        """The accepted windows behind the reported values (the last 30 s, at least 5), oldest first."""
        return tuple(self._windows)

    def snapshot(self) -> dict[str, object]:
        """The status dict (``DeviceStatus.air_sensor``). Call it on the daemon loop, like every
        other method: the sensor's state is not locked."""
        try:
            return self._snapshot()
        except Exception:
            self._fail()
            return {"state": "unavailable", "reason": INTERNAL_ERROR, "accepts": [] if self._fallback else [1]}

    def _snapshot(self) -> dict[str, object]:
        now = self._now()
        state, reason = self._state(now)
        valued = state in ("locked", "holding")
        h = self._reported_h() if valued else None
        e = None
        if h is not None and self._latency_ms is not None:
            d_out = statistics.median(w.d_out_us for w in list(self._windows)[-H_MEDIAN_OF:])
            e = h + self._latency_ms * 1000.0 - d_out
        src = self._source
        return {
            "state": state,
            "reason": reason,
            "codec": src.codec if src else None,
            "rate": self._rate,
            "adapter": src.adapter if src else None,
            "host_placement_ms": _ms(h),
            "air_error_ms": _ms(e),
            "air_to_sound_ms": self._latency_ms,
            "calibrated": self._latency_ms is not None,
            "confidence": _round(self._confidence(state, now), 3),
            "jitter_ms": _round(self._jitter_ms(now) if valued else None, 3),
            "psr": _round(self._last_psr if valued else None, 3),
            "lag_cells": _round(self._lag, 3),
            "windows_confirmed": self._confirmed_n,
            "windows_rejected": self._rejected_n,
            "windows_skipped": self._skipped_n,
            "last_confirmed_at": self._last_confirmed_iso,
            "epochs": self._epochs,
            "frames": self._frames,
            "missing_frames": self._missing,
            "anchor": "send",
            "complete_lag_ms": _ms(self._complete_lag_us),
            "timestamp": self._timestamp,
            "monitor_drops": src.monitor_drops if src else None,
            "ipc_dropped": src.ipc_dropped if src else 0,
            "dropped_messages": self._dropped,
            "accepts": [] if self._fallback else [1],
            "fold": None if self._fold is None else dict(self._fold),
        }

    # -- logging ----------------------------------------------------------------------------

    def _note_state(self, state: str, reason: str | None, now: float) -> None:
        if state != self._state_logged:
            self._state_logged = state
            last = self._info_at.get(state)
            if last is None or now - last >= INFO_EVERY_S * 1e6:
                self._info_at[state] = now
                h = self._reported_h() if state == "locked" else None
                if h is not None:
                    psr = "-" if self._last_psr is None else f"{self._last_psr:.2f}"
                    self._log.info("Air sensor: locked (H=%.2f ms, PSR=%s)", h / 1000.0, psr)
                elif reason:
                    self._log.info("Air sensor: %s (%s)", state, reason)
                else:
                    self._log.info("Air sensor: %s", state)
        if self._summary_at is None or now - self._summary_at >= DEBUG_SUMMARY_S * 1e6:
            self._summary_at = now
            if self._log.isEnabledFor(logging.DEBUG):
                h = self._reported_h()
                self._log.debug(
                    "Air sensor: state=%s H=%s lag=%s confirmed=%d rejected=%d skipped=%d frames=%d",
                    state,
                    "-" if h is None else f"{h / 1000.0:.2f}ms",
                    "-" if self._lag is None else f"{self._lag:.2f}",
                    self._confirmed_n,
                    self._rejected_n,
                    self._skipped_n,
                    self._frames,
                )

    def _debug_window(self, plan: _Plan, res: WindowResult, win: AirWindow | None) -> None:
        if not self._debug_windows:
            return
        self._log.debug(
            "Air window: mode=%s status=%s t_mid=%.1f s_mid=%s H=%s psr=%s rho=%s lag=%s",
            plan.mode,
            res.status,
            plan.t_mid,
            "-" if win is None else f"{win.s_mid:.1f}",
            "-" if win is None else f"{win.h_us / 1000.0:.3f}ms",
            "-" if res.psr is None else f"{res.psr:.3f}",
            "-" if res.rho is None else f"{res.rho:.3f}",
            "-" if win is None else f"{win.lag:.3f}",
        )


def _correlate(plan: _Plan) -> WindowResult:
    """The window computation (runs in ``run_window``, off the loop)."""
    return correlate_window(plan.cells, plan.w, plan.program, plan.lag_lo, plan.lag_hi, margin=plan.margin)


def _ms(us: float | None) -> float | None:
    return None if us is None else round(us / 1000.0, 3)


def _round(v: float | None, nd: int) -> float | None:
    return None if v is None else round(v, nd)
