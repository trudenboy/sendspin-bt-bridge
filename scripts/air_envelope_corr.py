#!/usr/bin/env python3
"""Offline envelope correlation of a ``btmon -w`` capture against its source WAV.

Matches the SBC scale-factor envelope that left over the air to the envelope of the PCM the
bridge was given, window by window, and prints per window the correlation ``rho``, the
peak-to-side-lobe ratio (PSR), the lag (frames, fractional), a status, and the air time of the
window middle minus the source time. Two PSR definitions are computed side by side so Task A17
can decide on real music:

``legacy``
    The morning prototype's definition, kept verbatim: air = ``ln(Σ 2^(2·sf) + 1)`` over
    sub-bands 1–3; source = mono through a 5 kHz Q 0.8 biquad, ``ln(peak²·1e8 + 1)`` per 128
    samples; un-normalised covariance with the means removed once per window; side lobe = the
    best correlation at least 3 cells from the peak (``|L − L*| > 2``) inside ±``--search``;
    parabolic fraction. A window is ambiguous below PSR 1.15.
``impl``
    The daemon's definition: air side ``frame_envelope`` (``a2dp_air_envelope``), written side
    the comb envelope (``air_tap.comb_peak_log2``, or ``--envelope bandpass`` for the reference
    band-pass), and ``air_correlator.correlate_window`` itself (main-lobe-capped side lobe over
    a fixed ±32 scan, gate PSR ≥ 1.17 and rho ≥ 0.25). There is no copy of that math here: the
    PSR printed is the PSR the gate runs on.

``--offline-h`` adds the A17 offline truth: ``offline_h_s = α_local + u_mid − src_mid − (d_enc − d_comb)``
with ``α_local`` the median of ``t_send − u`` over the packets within ±1 s of the window, taken
inside the window's anchor segment only (segments end at an RTP skip, sequence gap, SSRC change
or a send gap over 1 s) and independent of the live sensor's running median;
``d_enc = 4.5·M/rate``, ``d_comb = ((comb lag + 9·M) / 2)/rate`` (the comb cells
span the cell and the 9·M frames before it, as the daemon's tap does; 0 for ``legacy``
rows and the band-pass reference). ``src_mid`` is the source time of the matched cell.

``--anchor media|count`` adds the live sensor's ``H`` per window (``anchor_h_s``): the packets
go through the real ``AirLinkTracker`` with its batches taken every 250 ms, ``count`` with its
frame-count anchor (R-18), ``media`` with the pre-R-18 anchor (median ``t_send − u`` reseeded by
``−Δu`` at RTP steps) swapped in; the window middle's air time is placed like the daemon places it
(proportionally across a skipped span). A summary gives the median, p10–p90 and the largest step
between consecutive confirmed windows.

``--offline-h WINDOWS`` (no WAV) is A17 Step 3, live vs offline ``H`` on the same capture: WINDOWS
is the daemon's ``Air window:`` log lines (``--player`` keeps one speaker's) or a CSV with
``t_mid_us,s_mid_us,h_ms[,status]``; only confirmed windows count. The live ``t_mid`` is RAW µs,
the capture's clock is the kernel's REALTIME stamp, so the offset between them is fitted: the
capture runs through the real ``AirLinkTracker`` (as ``--anchor count``), and the offset that puts
every live ``t_mid`` on its air-time lattice (to µs) is taken, the one nearest the coarse offset from
the log lines' wall times (``--log-tz``) or ``--raw-offset``. Offsets whole cells apart fit as well;
a whole-cell shift moves the matched cell and its offline air time together, and the summary
prints the worst p95 over them. Per window ``H_offline = t_air − s_mid − (d_enc − d_comb)``, with
``t_air`` the local-anchor air time of the matched cell (as above, independent of the live running
median) and the daemon's own ``s_mid`` and ``d_enc − d_comb`` (the offline side cannot recompute
``s``); pass: p95 ``|H_live − H_offline|`` ≤ 2 ms. The match identifies the cell by time, not by
content: it checks the live air times against the local anchor, not which PCM a window matched.

Making a capture (the morning one is copyrighted music and stays private): on the speaker
host run ``btmon -w capture.btsnoop`` (needs root), play a known local WAV through the bridge
to one SBC speaker for a minute or two, stop btmon, then::

    uv run python scripts/air_envelope_corr.py capture.btsnoop source.wav [--index N] [--handle H]
        [--definition legacy|impl|both] [--window-frames 1034] [--hop-frames 344] [--search 20]
        [--envelope comb|bandpass] [--offline-h] [--anchor media|count] [--csv out.csv]
    uv run python scripts/air_envelope_corr.py capture.btsnoop --offline-h bridge.log --player NAME [--log-tz 3]

The WAV must be 16- or 24-bit PCM at the capture's SBC sampling rate and start where the
playback started (a constant offset is found by a coarse search, ``--offset-frames`` overrides
it). ``--selftest`` runs both definitions on a synthetic pair. Pure Python: allow a few
minutes for a two-minute track.
"""

from __future__ import annotations

import argparse
import bisect
import csv
import itertools
import math
import random
import re
import statistics
import struct
import sys
import wave
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from operator import mul
from pathlib import Path
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sendspin_bridge.services.audio.playout import air_correlator  # noqa: E402
from sendspin_bridge.services.audio.playout.air_tap import (  # noqa: E402
    LOOKBACK_FRAMES,
    comb_lag,
    comb_peak_log2,
    lookback_frames,
    to_s16,
)
from sendspin_bridge.services.bluetooth import a2dp_air  # noqa: E402
from sendspin_bridge.services.bluetooth.a2dp_air_envelope import (  # noqa: E402
    ANCHOR_PACKETS,
    AirLinkTracker,
    envelope_subbands,
    frame_envelope,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sendspin_bridge.services.bluetooth.a2dp_air import MediaPacket, SbcFrame

CELL = 128  # samples per envelope cell (one SBC frame of 8 sub-bands x 16 blocks)
LEGACY_GATE = 1.15  # the morning script's "ambiguous" threshold
SEND_GAP_S = 1.0
ANCHOR_SPAN_S = 1.0
BATCH_S = 0.25  # the parent takes the tracker's batches this often
BANDPASS_LEAD = 2  # cells of filter lead-in before each band-pass cell
RTP_MOD = 1 << 32
LOG_LATENCY_S = 2.0  # a window is logged ≈ half a window (1.5 s) + half a hop after its middle
COARSE_SPAN_S = 1.5  # the clock offset is searched this far either side of the coarse one
TIE_S = 20e-6  # offsets fitting within this of the best are equally good (whole cells apart)
DH_PASS_MS = 2.0  # A17 Step 3: p95 |H_live - H_offline| ≤ 2 ms


# -- air side ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PacketTime:
    seg: int  # anchor segment (ends at a skip, gap, SSRC change, long send gap)
    u_s: float  # media time of the packet's first sample (unwrapped RTP / rate)
    t_send: float


@dataclass(frozen=True, slots=True)
class CellMeta:
    rate: int
    subbands: int
    cell0_ticks: int  # unwrapped RTP ticks of cell 0
    packet_of: tuple[int, ...]  # packet index of each cell (-1 for a skipped cell)
    packets: tuple[PacketTime, ...]


def legacy_air_value(frame: SbcFrame) -> float:
    """Morning definition: ``ln(Σ_ch Σ_{b=1..3} 2^(2·sf) + 1)``."""
    e = sum(2.0 ** (2 * frame.scale_factors[c][s]) for c in range(frame.channels) for s in (1, 2, 3))
    return math.log(e + 1.0)


def impl_air_value(frame: SbcFrame) -> float:
    """The daemon's air cell: ``frame_envelope`` over the band for the frame's rate and sub-bands."""
    return float(frame_envelope(frame, envelope_subbands(frame.sampling_rate, frame.subbands)))


def air_cells(
    packets: Sequence[MediaPacket], value: Callable[[SbcFrame], float]
) -> tuple[list[float | None], CellMeta]:
    """One cell per SBC frame on the RTP clock (a skipped stretch stays ``None``)."""
    if not packets or not packets[0].frames:
        raise ValueError("no SBC frames in the capture")
    f0 = packets[0].frames[0]
    rate, spf = f0.sampling_rate, f0.blocks * f0.subbands
    if spf != CELL:
        raise ValueError(f"SBC frames of {spf} samples are not supported (expected {CELL})")
    times: list[PacketTime] = []
    ticks: list[int] = []
    seg = 0
    prev: MediaPacket | None = None
    unwrapped = packets[0].rtp_ts
    for p in packets:
        if prev is not None:
            step = (p.rtp_ts - prev.rtp_ts) % RTP_MOD
            if (
                p.ssrc != prev.ssrc
                or p.seq != (prev.seq + 1) & 0xFFFF
                or step != len(prev.frames) * spf
                or p.t_send - prev.t_send > SEND_GAP_S
                or p.t_send < prev.t_send
            ):
                seg += 1
            unwrapped += step if step < RTP_MOD // 2 else step - RTP_MOD
        ticks.append(unwrapped)
        times.append(PacketTime(seg, unwrapped / rate, p.t_send))
        prev = p
    base = ticks[0]
    total = (ticks[-1] - base) // spf + len(packets[-1].frames)
    cells: list[float | None] = [None] * total
    owner = [-1] * total
    for i, p in enumerate(packets):
        first = (ticks[i] - base) // spf
        for n, frame in enumerate(p.frames):
            if 0 <= first + n < total:
                cells[first + n] = value(frame)
                owner[first + n] = i
    return cells, CellMeta(rate, f0.subbands, base, tuple(owner), tuple(times))


def select_packets(path: str | Path, index: int, handle: int | None) -> list[MediaPacket]:
    pk = [p for p in a2dp_air.parse_records(a2dp_air.read_btsnoop(path)) if p.index == index and p.codec == "sbc"]
    if not pk:
        raise ValueError(f"no SBC media packets for controller index {index}")
    chosen = handle if handle is not None else Counter(p.handle for p in pk).most_common(1)[0][0]
    return [p for p in pk if p.handle == chosen and p.frames]


def local_anchor(packets: Sequence[PacketTime], at: int) -> float | None:
    """Median ``t_send − u`` of the packets within ±1 s of packet ``at``, inside its anchor segment."""
    centre = packets[at]
    vals = [q.t_send - q.u_s for q in packets if q.seg == centre.seg and abs(q.t_send - centre.t_send) <= ANCHOR_SPAN_S]
    return statistics.median(vals) if vals else None


# -- live anchors (--anchor) ----------------------------------------------------------------------


class _MediaAnchor:
    """The pre-R-18 send anchor, for comparison: median of the last ANCHOR_PACKETS ``t_send − u``,
    shifted by ``−Δu`` at every forward RTP step. Plugged into the real tracker in place of its
    count-axis anchor; ``value()`` answers in the tracker's frame (``t0 = seq·frame_us + value``),
    i.e. ``α + (u − count·frame_us)`` of the newest packet."""

    def __init__(self) -> None:
        self._window: list[float] = []
        self._u_us: float | None = None  # media time of the packet about to be added (set by the caller)
        self._skew: float | None = None  # u - count*frame_us of the newest packet added

    def set_media(self, u_us: float) -> None:
        self._u_us = u_us

    def add(self, t_send_us: float, x_us: float) -> None:
        assert self._u_us is not None
        skew = self._u_us - x_us
        if self._skew is not None and skew > self._skew:
            step = skew - self._skew
            self._window = [v - step for v in self._window]  # the reseed: alpha_old - du
        self._skew = skew
        self._window.append(t_send_us - self._u_us)
        del self._window[:-ANCHOR_PACKETS]

    def value(self) -> float | None:
        if not self._window or self._skew is None:
            return None
        return statistics.median(self._window) + self._skew

    def reset(self) -> None:
        self._window, self._skew = [], None


def live_air_times(packets: Sequence[MediaPacket], anchor: str) -> dict[int, float]:
    """Air time (RAW s) the live parent gives each air cell, keyed by :func:`air_cells`' index.

    The packets go through the real :class:`AirLinkTracker`, its batches taken every
    :data:`BATCH_S` of send time as the hub's sender does. ``count`` is the tracker as it is (R-18:
    frame-count anchor); ``media`` swaps in the pre-R-18 media-time anchor (:class:`_MediaAnchor`).
    """
    if anchor not in ("count", "media"):
        raise ValueError(f"unknown anchor {anchor!r}")
    tr = AirLinkTracker()
    media = _MediaAnchor() if anchor == "media" else None
    if media is not None:
        tr._anchor = media  # type: ignore[assignment]  # analysis only: the tracker's one anchor seam
    base: int | None = None
    ticks = packets[0].rtp_ts if packets else 0
    epoch_base: dict[int, int] = {}
    batches: list = []
    prev: MediaPacket | None = None
    next_take = packets[0].t_send + BATCH_S if packets else 0.0
    rate = packets[0].frames[0].sampling_rate if packets and packets[0].frames else 44100
    for p in packets:
        if prev is not None:
            step = (p.rtp_ts - prev.rtp_ts) % RTP_MOD
            ticks += step if step < RTP_MOD // 2 else step - RTP_MOD
        if base is None:
            base = ticks
        if media is not None:
            media.set_media((ticks - base) * 1e6 / rate)  # only u's steps matter to the anchor
        tr.on_packet(p, timestamp="kernel", in_step=False)
        epoch_base.setdefault(tr.state(p.t_send * 1e6).epoch, ticks)  # the packet an epoch starts at
        prev = p
        if p.t_send >= next_take:
            batches += tr.take_batches(p.t_send * 1e6)
            next_take += BATCH_S
    batches += tr.take_batches(1e18, force=True)
    out: dict[int, float] = {}
    if base is None:
        return out
    for b in batches:
        first = epoch_base[b.epoch] - base + round(b.u0_us * b.rate / 1e6)
        spf = b.blocks * b.subbands
        for k in range(len(b.e)):
            out[(first + k * spf) // spf] = (b.t0_us + k * b.frame_us) / 1e6
    return out


def air_time_at(times: dict[int, float], keys: Sequence[int], cell: int) -> float | None:
    """Air time of air cell ``cell``; inside a skipped span (no cell held) it is placed
    proportionally between the held cells either side, as the daemon places a window middle."""
    if cell in times:
        return times[cell]
    j = bisect.bisect_right(keys, cell)
    if j == 0 or j == len(keys):
        return None
    lo, hi = keys[j - 1], keys[j]
    return times[lo] + (cell - lo) / (hi - lo) * (times[hi] - times[lo])


# -- live windows vs offline (--offline-h WINDOWS) ------------------------------------------------


@dataclass(frozen=True, slots=True)
class LiveWindow:
    """One confirmed window of the daemon: ``t_mid`` (RAW µs), ``s_mid`` (µs), ``H`` (ms)."""

    t_mid_us: float
    s_mid_us: float
    h_ms: float
    wall_s: float | None = None  # wall time of the log line (seconds since the epoch), when known


@dataclass(frozen=True, slots=True)
class ClockFit:
    offset_s: float  # capture clock (btsnoop, REALTIME) minus RAW, in seconds
    fit_median_us: float  # median |live t_mid - nearest live-tracker air time| at that offset
    tied: tuple[float, ...]  # every offset in the search span that fits as well (whole cells apart), refined


_WINDOW_RE = re.compile(
    r"^(?P<wall>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3})?.*?(?:\[(?P<player>[^\]@]+?) @[^\]]*\])?\s*Air window: "
    r"mode=\S+ status=(?P<status>\w+) t_mid=(?P<t>[\d.]+) s_mid=(?P<s>[-\d.]+) H=(?P<h>[-\d.]+)(?:ms)?"
)


def parse_live_windows(
    path: str | Path, *, player: str | None = None, tz_hours: float | None = None
) -> list[LiveWindow]:
    """The confirmed live windows from the daemon's ``Air window:`` log lines or a CSV.

    A CSV has the columns ``t_mid_us``, ``s_mid_us``, ``h_ms`` and optionally ``status`` (only
    ``confirmed`` rows are kept). Log lines carry their wall time, read in ``tz_hours`` (default:
    the local time zone of the machine running this script); ``player`` keeps one player's lines.
    """
    text = Path(path).read_text(errors="replace").lstrip()
    out: list[LiveWindow] = []
    if "t_mid_us" in text.split("\n", 1)[0]:
        for row in csv.DictReader(text.splitlines()):
            if (row.get("status") or "confirmed") == "confirmed":
                out.append(LiveWindow(float(row["t_mid_us"]), float(row["s_mid_us"]), float(row["h_ms"])))
        return out
    tz = None if tz_hours is None else timezone(timedelta(hours=tz_hours))
    for line in text.splitlines():
        m = _WINDOW_RE.search(line)
        if m is None or m["status"] != "confirmed" or m["s"] == "-" or m["h"] == "-":
            continue
        if player is not None and (m["player"] or "").strip() != player:
            continue
        wall = None
        if m["wall"]:
            dt = datetime.strptime(m["wall"], "%Y-%m-%d %H:%M:%S,%f")
            wall = (dt.replace(tzinfo=tz) if tz is not None else dt).timestamp()
        out.append(LiveWindow(float(m["t"]), float(m["s"]), float(m["h"]), wall))
    return out


def coarse_raw_offset(windows: Sequence[LiveWindow]) -> float | None:
    """Capture clock minus RAW from the log lines' wall times: a window is logged about half a window
    plus half a hop after its middle went out (:data:`LOG_LATENCY_S`); good to a second or so."""
    vals = [w.wall_s - w.t_mid_us / 1e6 for w in windows if w.wall_s is not None]
    return statistics.median(vals) - LOG_LATENCY_S if vals else None


def _nearest(tarr: Sequence[float], x: float) -> int:
    j = bisect.bisect_left(tarr, x)
    return min((i for i in (j - 1, j) if 0 <= i < len(tarr)), key=lambda i: abs(tarr[i] - x))


def fit_clock_offset(
    windows: Sequence[LiveWindow], times: dict[int, float], coarse_s: float, span_s: float = COARSE_SPAN_S
) -> ClockFit:
    """The capture-clock offset that puts every live ``t_mid`` on the live tracker's air times.

    The tracker run offline on the capture reproduces the live air-time lattice (same packets, same
    kernel stamps, same anchor), so at the right offset the live ``t_mid`` land on it to µs. The
    lattice search starts from one pivot window; each candidate is then refined on the median over
    all windows (:func:`_refine`). Offsets whole cells apart fit about equally well: the one nearest
    ``coarse_s`` is taken (a whole-cell shift moves the matched cell and its offline air time
    together, so ΔH hardly depends on it). A constant error on every window is absorbed into the
    offset and is not visible here.
    """
    keys = sorted(times)
    tarr = [times[k] for k in keys]
    lo, hi = tarr[0], tarr[-1]
    inside = [w for w in windows if lo + span_s <= w.t_mid_us / 1e6 + coarse_s <= hi - span_s]
    if not inside:
        raise ValueError("no live window falls inside the capture at the coarse clock offset")
    pivot = inside[len(inside) // 2].t_mid_us / 1e6
    first = bisect.bisect_left(tarr, pivot + coarse_s - span_s)
    last = bisect.bisect_right(tarr, pivot + coarse_s + span_s)
    scored: list[tuple[float, float]] = []
    for t in tarr[first:last]:
        offset = t - pivot
        res = [abs(tarr[_nearest(tarr, x)] - x) for w in windows if lo <= (x := w.t_mid_us / 1e6 + offset) <= hi]
        if len(res) >= max(1, len(inside) // 2):
            scored.append((statistics.median(res), offset))
    if not scored:
        raise ValueError("no clock offset puts the live windows inside the capture")
    best = min(s for s, _ in scored)
    tied = tuple(_refine(windows, tarr, o) for s, o in scored if s <= best + TIE_S)
    offset = min(tied, key=lambda o: abs(o - coarse_s))
    res = [abs(tarr[_nearest(tarr, x)] - x) for w in windows if lo <= (x := w.t_mid_us / 1e6 + offset) <= hi]
    return ClockFit(offset, statistics.median(res) * 1e6, tied)


def _refine(windows: Sequence[LiveWindow], tarr: Sequence[float], offset: float) -> float:
    """Shift a lattice offset by the median signed residual over **every** window inside the capture,
    so one wrong window (the search's pivot included) cannot carry the offset with it."""
    res = [x - tarr[_nearest(tarr, x)] for w in windows if tarr[0] <= (x := w.t_mid_us / 1e6 + offset) <= tarr[-1]]
    return offset - statistics.median(res) if res else offset


def offline_air_time(meta: CellMeta, cell: int, cache: dict[int, float | None] | None = None) -> float | None:
    """Air time of air cell ``cell`` from the **local** anchor (``α_local + u``, :func:`local_anchor`);
    a skipped cell is placed proportionally between the held cells either side."""
    memo: dict[int, float | None] = {} if cache is None else cache

    def held(c: int) -> float | None:
        p = meta.packet_of[c]
        if p not in memo:
            memo[p] = local_anchor(meta.packets, p)
        alpha = memo[p]
        return None if alpha is None else alpha + (meta.cell0_ticks + c * CELL) / meta.rate

    if not 0 <= cell < len(meta.packet_of):
        return None
    if meta.packet_of[cell] >= 0:
        return held(cell)
    left = next((c for c in range(cell - 1, -1, -1) if meta.packet_of[c] >= 0), None)
    right = next((c for c in range(cell + 1, len(meta.packet_of)) if meta.packet_of[c] >= 0), None)
    if left is None or right is None:
        return None
    t_lo, t_hi = held(left), held(right)
    if t_lo is None or t_hi is None:
        return None
    return t_lo + (cell - left) / (right - left) * (t_hi - t_lo)


def compare_at_offset(
    windows: Sequence[LiveWindow], times: dict[int, float], meta: CellMeta, offset_s: float
) -> list[dict]:
    """Per live window: the matched air cell, ``H_offline = t_air − s_mid − (d_enc − d_comb)`` with
    ``t_air`` the local-anchor air time of that cell (the daemon's own ``d_enc − d_comb``, read off
    its ``H − (t_mid − s_mid)``), and ``ΔH = H_live − H_offline``."""
    keys = sorted(times)
    tarr = [times[k] for k in keys]
    cache: dict[int, float | None] = {}
    rows: list[dict] = []
    for w in windows:
        x = w.t_mid_us / 1e6 + offset_s
        if not tarr[0] <= x <= tarr[-1]:
            continue
        i = _nearest(tarr, x)
        t_air = offline_air_time(meta, keys[i], cache)
        if t_air is None:
            continue
        credit_ms = w.h_ms - (w.t_mid_us - w.s_mid_us) / 1000.0
        h_off = ((t_air - offset_s) * 1e6 - w.s_mid_us) / 1000.0 + credit_ms
        rows.append(
            {
                "t_mid_us": w.t_mid_us,
                "s_mid_us": w.s_mid_us,
                "h_live_ms": w.h_ms,
                "cell": keys[i],
                "fit_us": (x - tarr[i]) * 1e6,
                "h_offline_ms": h_off,
                "dh_ms": w.h_ms - h_off,
            }
        )
    return rows


def live_vs_offline(
    windows: Sequence[LiveWindow], packets: Sequence[MediaPacket], *, coarse_offset_s: float
) -> tuple[list[dict], ClockFit]:
    """A17 Step 3: the live windows' ``H`` against the offline local-anchor ``H`` of the same air cells."""
    times = live_air_times(packets, "count")
    _, meta = air_cells(packets, lambda _f: 0.0)
    fit = fit_clock_offset(windows, times, coarse_offset_s)
    return compare_at_offset(windows, times, meta, fit.offset_s), fit


def dh_summary(rows: Sequence[dict]) -> dict[str, float]:
    """``n``, median ΔH, p95 and max of ``|ΔH|`` (ms), and the median ``|fit residual|`` (µs)."""
    dh = [r["dh_ms"] for r in rows]
    absdh = [abs(v) for v in dh]
    return {
        "n": len(dh),
        "median_ms": percentile(dh, 50),
        "p95_abs_ms": percentile(absdh, 95),
        "max_abs_ms": max(absdh) if absdh else float("nan"),
        "fit_median_us": percentile([abs(r["fit_us"]) for r in rows], 50),
    }


def run_offline_h_windows(args: argparse.Namespace) -> int:
    try:
        windows = parse_live_windows(args.offline_h, player=args.player, tz_hours=args.log_tz)
        if not windows:
            print(f"error: no confirmed live windows in {args.offline_h}", file=sys.stderr)
            return 2
        coarse = args.raw_offset if args.raw_offset is not None else coarse_raw_offset(windows)
        if coarse is None:
            print("error: the windows carry no wall time: pass --raw-offset", file=sys.stderr)
            return 2
        packets = select_packets(args.capture, args.index, args.handle)
        times = live_air_times(packets, "count")
        _, meta = air_cells(packets, lambda _f: 0.0)
        fit = fit_clock_offset(windows, times, coarse)
    except (ValueError, OSError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    rows = compare_at_offset(windows, times, meta, fit.offset_s)
    for r in rows:
        print(
            f"window t_mid {r['t_mid_us']:.1f} cell {r['cell']:6d} fit {r['fit_us']:+8.1f} us | "
            f"H live {r['h_live_ms']:8.3f} offline {r['h_offline_ms']:8.3f} dH {r['dh_ms']:+.3f} ms"
        )
    st = dh_summary(rows)
    step = max(1, len(fit.tied) // 50)
    worst = max(dh_summary(compare_at_offset(windows, times, meta, o))["p95_abs_ms"] for o in fit.tied[::step])
    fit_ok = fit.fit_median_us <= TIE_S * 1e6
    verdict = "PASS" if st["p95_abs_ms"] <= DH_PASS_MS and fit_ok else "FAIL"
    if not fit_ok:
        verdict += f" (fit: median residual {fit.fit_median_us:.1f} us > {TIE_S * 1e6:.0f} us, no consistent offset)"
    print(
        f"clock offset {fit.offset_s:.6f} s (coarse {coarse:.3f}; {len(fit.tied)} offsets whole cells apart fit "
        f"as well, worst p95 over them {worst:.3f} ms); fit residual median {fit.fit_median_us:.1f} us"
    )
    print(
        f"live vs offline H over {st['n']} of {len(windows)} windows: median dH {st['median_ms']:+.3f} ms, "
        f"p95 |dH| {st['p95_abs_ms']:.3f} ms, max {st['max_abs_ms']:.3f} ms -> {verdict} (<= {DH_PASS_MS} ms)"
    )
    if args.csv:
        cols = ["t_mid_us", "s_mid_us", "h_live_ms", "cell", "fit_us", "h_offline_ms", "dh_ms"]
        with open(args.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(cols)
            for r in rows:
                w.writerow([r[c] for c in cols])
    return 0


# -- source side ------------------------------------------------------------------------------


def read_wav(path: str | Path) -> tuple[list[int], int, int, int]:
    """Interleaved samples as signed ints at the file's own width, plus channels, rate, bytes per sample."""
    with wave.open(str(path), "rb") as w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        data = list(struct.unpack(f"<{n * ch}h", raw))
    elif width == 3:
        data = [int.from_bytes(raw[i : i + 3], "little", signed=True) for i in range(0, n * ch * 3, 3)]
    else:
        raise ValueError(f"only 16- and 24-bit PCM WAV files are supported, got {8 * width}-bit")
    return data, ch, rate, width


def _mono(data: Sequence[int], ch: int, width: int) -> list[float]:
    full = float(1 << (8 * width - 1))
    if ch == 1:
        return [v / full for v in data]
    return [sum(data[i : i + ch]) / (ch * full) for i in range(0, len(data) - ch + 1, ch)]


def biquad_bp(x: Sequence[float], fs: float, f0: float, q: float) -> list[float]:
    w = 2 * math.pi * f0 / fs
    al = math.sin(w) / (2 * q)
    a0 = 1 + al
    b0, b2, a1, a2 = al / a0, -al / a0, -2 * math.cos(w) / a0, (1 - al) / a0
    y = []
    x1 = x2 = y1 = y2 = 0.0
    for v in x:
        o = b0 * v + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1, y2, y1 = x1, v, y1, o
        y.append(o)
    return y


def legacy_source_cells(data: Sequence[int], ch: int, rate: int, width: int) -> list[float]:
    """Morning definition: mono through a 5 kHz Q 0.8 biquad, ``ln(peak²·1e8 + 1)`` per 128 samples."""
    y = biquad_bp(_mono(data, ch, width), rate, 5000.0, 0.8)
    out = []
    for i in range(0, len(y) - CELL, CELL):
        pk = max(abs(v) for v in y[i : i + CELL])
        out.append(math.log(pk * pk * 1e8 + 1.0))
    return out


def impl_source_cells(
    data: Sequence[int], ch: int, rate: int, width: int, envelope: str, *, lookback: int = LOOKBACK_FRAMES
) -> list[float]:
    """The daemon's written cells: comb envelope over each 128 input frames and the ``lookback``
    (9·M) before them, or the reference band-pass, per 128 input frames."""
    s16 = to_s16(b"".join(v.to_bytes(width, "little", signed=True) for v in data), width)
    frames = len(s16) // ch
    out = []
    if envelope == "comb":
        lag = comb_lag(rate)
        zeros = (0,) * (lag * ch)
        for start in range(0, frames - CELL + 1, CELL):
            lo = max(0, start - lookback)  # the span an SBC frame's scale factors see
            prev = tuple(s16[(lo - lag) * ch : lo * ch]) if lo >= lag else zeros
            out.append(comb_peak_log2(s16, ch, lo, start + CELL, prev, lag))
    else:
        lead = BANDPASS_LEAD * CELL
        for start in range(0, frames - CELL + 1, CELL):
            lo = max(0, start - lead)
            seg = s16[lo * ch : (start + CELL) * ch].tolist()
            out.append(air_correlator.bandpass_peak_log2(seg, ch, rate, start - lo, start + CELL - lo))
    return out


# -- correlation ------------------------------------------------------------------------------


def legacy_windows(
    air: Sequence[float],
    src: Sequence[float],
    *,
    window: int = 2000,
    hop: int = 1000,
    search: int = 20,
    offset: int | None = None,
) -> list[dict]:
    """The morning definition per window; ``src[offset + k + L]`` is matched to ``air[k]`` (offset defaults to ``search + SIDE_SCAN``)."""
    off = search + air_correlator.SIDE_SCAN if offset is None else offset
    out: list[dict] = []
    for index, k in enumerate(range(0, len(air) - window + 1, hop)):
        if off + k - search < 0 or off + k + window + search > len(src):
            continue
        a = list(air[k : k + window])
        b = list(src[off + k - search : off + k + window + search])
        ma, mb = statistics.fmean(a), statistics.fmean(b)
        a = [v - ma for v in a]
        b = [v - mb for v in b]
        sc = [sum(map(mul, a, b[i : i + window])) / window for i in range(2 * search + 1)]
        i_best = max(range(len(sc)), key=sc.__getitem__)
        norm = (sum(v * v for v in a) / window * sum(v * v for v in b) / len(b)) ** 0.5 or 1.0
        others = [v for i, v in enumerate(sc) if abs(i - i_best) > 2]
        psr = sc[i_best] / max(others) if others and max(others) > 0 else float("inf")
        frac = 0.0
        if 0 < i_best < len(sc) - 1:
            ym, y0, yp = sc[i_best - 1], sc[i_best], sc[i_best + 1]
            den = ym - 2 * y0 + yp
            frac = 0.5 * (ym - yp) / den if den else 0.0
        out.append(
            {
                "index": index,
                "k": k,
                "status": "confirmed" if psr >= LEGACY_GATE else "ambiguous",
                "rho": sc[i_best] / norm,
                "psr": psr,
                "lag": i_best - search + frac,
            }
        )
    return out


def impl_windows(
    air_cells_: Sequence[float | None],
    written_cells: Sequence[float],
    program: bytes,
    *,
    window: int,
    hop: int,
    search: int,
    offset: int | None = None,
) -> list[dict]:
    """The daemon's definition per window, through ``air_correlator.correlate_window``.

    ``written_cells[offset + k + L]`` is matched to ``air_cells_[k]``; ``offset`` defaults to
    ``search + SIDE_SCAN`` (the written side padded by the search range plus the side-lobe margin),
    the same default as :func:`legacy_windows`.
    """
    margin = air_correlator.SIDE_SCAN
    off = search + margin if offset is None else offset
    out: list[dict] = []
    for index, k in enumerate(range(0, len(air_cells_) - window + 1, hop)):
        start = off + k - search - margin
        stop = start + window + 2 * (search + margin)
        if start < 0 or stop > len(written_cells):
            continue
        res = air_correlator.correlate_window(
            air_cells_[k : k + window], written_cells[start:stop], program[start:stop], -search, search, margin=margin
        )
        out.append({"index": index, "k": k, "status": res.status, "rho": res.rho, "psr": res.psr, "lag": res.lag})
    return out


def coarse_offset(air: Sequence[float], src: Sequence[float], width: int = 2000, stride: int = 4) -> int:
    """Source cell of air cell 0 by a coarse dot-product search around the last quarter of the air."""
    w = min(width, len(air))
    first = len(air) * 3 // 4 - w // 2
    first = max(0, min(first, len(air) - w))
    seg = list(air[first : first + w])
    m = statistics.fmean(seg)
    seg = [v - m for v in seg]
    best: tuple[float, int] | None = None
    for off in range(0, len(src) - w + 1, stride):
        s = sum(map(mul, seg, src[off : off + w]))  # seg is zero-mean: the source mean drops out
        if best is None or s > best[0]:
            best = (s, off)
    if best is None:
        raise ValueError("source shorter than the air window")
    return best[1] - first


# -- synthetic data ---------------------------------------------------------------------------


def synthetic_pair(
    *, lag_frames: int = 7, seed: int = 3, n: int = 2400, search: int = 20, echo_frames: int = 0, echo_gain: float = 0.0
) -> tuple[list[float], list[float]]:
    """``(air, src)`` with ``air[i] = src[pad + i + lag_frames]`` plus noise (and an optional echo).

    ``src`` is padded by ``search + SIDE_SCAN`` cells on each side, the layout both window
    functions default to.
    """
    rng = random.Random(seed)
    pad = search + air_correlator.SIDE_SCAN
    spare = abs(lag_frames) + echo_frames
    src: list[float] = []
    level = 3.0
    for _ in range(n + 2 * pad + spare):
        level = 0.85 * level + rng.gauss(0.0, 1.0)  # a bursty, music-like envelope
        src.append(8.0 * level)  # about the 1/8-bit scale of a real air envelope (the flatness floors apply)
    air = []
    for i in range(n):
        v = src[pad + i + lag_frames]
        if echo_frames:
            v += echo_gain * src[pad + i + lag_frames + echo_frames]
        air.append(v + rng.gauss(0.0, 1.6))
    return air, src


# -- reporting --------------------------------------------------------------------------------


def percentile(values: Sequence[float], pct: float) -> float:
    if not values:
        return float("nan")
    xs = sorted(values)
    pos = (len(xs) - 1) * pct / 100.0
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (pos - lo)


def summarise(name: str, rows: Sequence[dict]) -> list[str]:
    lines = [
        f"summary {name}: {len(rows)} windows, "
        + ", ".join(f"{s}={c}" for s, c in sorted(Counter(r["status"] for r in rows).items()))
    ]
    for status in ("confirmed", "rejected", "ambiguous"):
        psr = [r["psr"] for r in rows if r["status"] == status and r["psr"] is not None and math.isfinite(r["psr"])]
        if psr:
            lines.append(
                f"  psr {status:9s} n={len(psr):4d} median {percentile(psr, 50):.3f} "
                f"p10 {percentile(psr, 10):.3f} p90 {percentile(psr, 90):.3f}"
            )
    return lines


def _fmt(v: float | None, spec: str) -> str:
    return "-" if v is None else format(v, spec)


def annotate(
    rows: list[dict], *, definition: str, offset: int, meta: CellMeta, window: int, offline_h: bool, comb: bool
) -> None:
    """Add ``t_minus_src_s`` (global median anchor, the morning column) and ``offline_h_s`` to each row."""
    rate = meta.rate
    alpha_global = statistics.median(q.t_send - q.u_s for q in meta.packets)
    d_enc = 4.5 * meta.subbands / rate
    back = lookback_frames(meta.subbands, rate, rate)
    d_comb = ((comb_lag(rate) + back) / 2.0) / rate if comb and definition == "impl" else 0.0
    for r in rows:
        r["t_minus_src_s"] = r["offline_h_s"] = None
        if r["lag"] is None:
            continue
        mid = r["k"] + window // 2
        src_t = (offset + mid + r["lag"]) * CELL / rate
        u_mid = (meta.cell0_ticks + mid * CELL) / rate
        r["t_minus_src_s"] = alpha_global + u_mid - src_t
        if offline_h:
            near = min(
                (i for i in range(len(meta.packet_of)) if meta.packet_of[i] >= 0),
                key=lambda i: abs(i - mid),
                default=None,
            )
            alpha = local_anchor(meta.packets, meta.packet_of[near]) if near is not None else None
            if alpha is not None:
                r["offline_h_s"] = alpha + u_mid - src_t - (d_enc - d_comb)


def annotate_anchor_h(
    rows: list[dict],
    times: dict[int, float],
    *,
    offset: int,
    rate: int,
    subbands: int,
    window: int,
    comb: bool,
) -> None:
    """Add ``anchor_h_s``: the live sensor's ``H = t_mid − src_mid − (d_enc − d_comb)`` per window,
    ``t_mid`` the air time :func:`live_air_times` gives the window's middle cell."""
    keys = sorted(times)
    d_enc = 4.5 * subbands / rate
    d_comb = ((comb_lag(rate) + lookback_frames(subbands, rate, rate)) / 2.0) / rate if comb else 0.0
    for r in rows:
        r["anchor_h_s"] = None
        if r["lag"] is None:
            continue
        mid = r["k"] + window // 2
        t_mid = air_time_at(times, keys, mid)
        if t_mid is not None:
            r["anchor_h_s"] = t_mid - (offset + mid + r["lag"]) * CELL / rate - (d_enc - d_comb)


def h_stability(rows: Sequence[dict]) -> dict[str, float]:
    """Median, p10, p90 of ``anchor_h_s`` over the confirmed windows and the largest step between
    consecutive confirmed windows, in ms."""
    hs = [r["anchor_h_s"] * 1000.0 for r in rows if r["status"] == "confirmed" and r.get("anchor_h_s") is not None]
    jumps = [abs(b - a) for a, b in itertools.pairwise(hs)]
    return {
        "n": len(hs),
        "median_ms": percentile(hs, 50),
        "p10_ms": percentile(hs, 10),
        "p90_ms": percentile(hs, 90),
        "max_jump_ms": max(jumps) if jumps else 0.0,
    }


def run_selftest() -> int:
    air, src = synthetic_pair(lag_frames=7, seed=3)
    legacy = [x for x in legacy_windows(air, src, window=600, hop=300, search=20) if x["psr"] >= LEGACY_GATE]
    impl = [
        x
        for x in impl_windows(air, src, bytes([1]) * len(src), window=600, hop=300, search=20)
        if x["status"] == "confirmed"
    ]
    ok = (
        bool(legacy)
        and bool(impl)
        and all(abs(x["lag"] - 7) < 0.3 for x in legacy)
        and all(abs(x["lag"] - 7) < 0.6 for x in impl)
    )
    print(f"selftest legacy {len(legacy)} impl {len(impl)} windows confirmed at lag 7: {'ok' if ok else 'FAILED'}")
    return 0 if ok else 1


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture", nargs="?", type=Path)
    ap.add_argument("wav", nargs="?", type=Path)
    ap.add_argument("--index", type=int, default=1, help="controller index in the capture")
    ap.add_argument("--handle", type=int, default=None, help="ACL handle (default: the busiest SBC link)")
    ap.add_argument("--definition", choices=("legacy", "impl", "both"), default="both")
    ap.add_argument("--window-frames", type=int, default=1034)
    ap.add_argument("--hop-frames", type=int, default=344)
    ap.add_argument("--search", type=int, default=20)
    ap.add_argument("--envelope", choices=("comb", "bandpass"), default="comb", help="written-side envelope of impl")
    ap.add_argument(
        "--offset-frames", type=int, default=None, help="source cell of air cell 0 (default: coarse search)"
    )
    ap.add_argument(
        "--offline-h",
        nargs="?",
        const=True,
        default=None,
        metavar="WINDOWS",
        help="without a value: add the local-anchor offline H per window; with the daemon's "
        "'Air window:' log lines or a CSV (t_mid_us,s_mid_us,h_ms[,status]): compare the live H of every "
        "confirmed window with the offline H of the same air cell (no WAV needed)",
    )
    ap.add_argument("--player", default=None, help="--offline-h WINDOWS: keep this player's log lines")
    ap.add_argument(
        "--raw-offset",
        type=float,
        default=None,
        help="--offline-h WINDOWS: approximate capture clock minus RAW, seconds (default: from the log wall times)",
    )
    ap.add_argument(
        "--log-tz", type=float, default=None, help="--offline-h WINDOWS: UTC offset of the log's wall times, hours"
    )
    ap.add_argument(
        "--anchor",
        choices=("media", "count"),
        default=None,
        help="add the live sensor's per-window H with the frame-count (R-18) or the pre-R-18 media-time anchor",
    )
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()
    if isinstance(args.offline_h, str):
        if Path(args.offline_h).suffix.lower() == ".wav" or (
            args.wav is not None and args.wav.suffix.lower() == ".wav"
        ):
            ap.error(
                "--offline-h took a value next to a WAV: with a WAV use a bare --offline-h after it "
                "(capture source.wav --offline-h); --offline-h WINDOWS takes the daemon log or a CSV and no WAV"
            )
        if args.capture is None:
            ap.error("--offline-h WINDOWS needs the capture")
        return run_offline_h_windows(args)
    args.offline_h = bool(args.offline_h)
    if args.capture is None or args.wav is None:
        ap.error("capture and wav are required (or --selftest)")

    try:
        packets = select_packets(args.capture, args.index, args.handle)
        data, ch, fs, width = read_wav(args.wav)
        definitions = ["legacy", "impl"] if args.definition == "both" else [args.definition]
        window, hop, search = args.window_frames, args.hop_frames, args.search
        series: dict[str, tuple[list[float | None], CellMeta, list[float]]] = {}
        for d in definitions:
            cells, meta = air_cells(packets, legacy_air_value if d == "legacy" else impl_air_value)
            if meta.rate != fs:
                print(
                    f"error: source sampling rate {fs} Hz differs from the capture's SBC rate {meta.rate} Hz",
                    file=sys.stderr,
                )
                return 2
            written = (
                legacy_source_cells(data, ch, fs, width)
                if d == "legacy"
                else impl_source_cells(
                    data, ch, fs, width, args.envelope, lookback=lookback_frames(meta.subbands, fs, fs)
                )
            )
            series[d] = (cells, meta, written)
    except (ValueError, OSError, wave.Error) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    first = series[definitions[0]]
    fill = statistics.fmean(c for c in first[0] if c is not None)
    offset = args.offset_frames
    if offset is None:
        offset = coarse_offset([fill if c is None else c for c in first[0]], first[2])
    print(
        f"{len(packets)} packets, {len(first[0])} air cells, {len(first[2])} source cells; air cell 0 = source cell {offset}"
    )

    rows_by_def: dict[str, list[dict]] = {}
    times = live_air_times(packets, args.anchor) if args.anchor else None
    for d in definitions:
        cells, meta, written = series[d]
        if d == "legacy":
            mean = statistics.fmean(c for c in cells if c is not None)
            rows = legacy_windows(
                [mean if c is None else c for c in cells], written, window=window, hop=hop, search=search, offset=offset
            )
        else:
            pad = [0.0] * (search + air_correlator.SIDE_SCAN)
            rows = impl_windows(
                cells,
                pad + written + pad,
                bytes([1]) * (len(written) + 2 * len(pad)),
                window=window,
                hop=hop,
                search=search,
                offset=offset + len(pad),
            )
        annotate(
            rows,
            definition=d,
            offset=offset,
            meta=meta,
            window=window,
            offline_h=args.offline_h,
            comb=args.envelope == "comb",
        )
        if times is not None:
            annotate_anchor_h(
                rows,
                times,
                offset=offset,
                rate=meta.rate,
                subbands=meta.subbands,
                window=window,
                comb=args.envelope == "comb" and d == "impl",
            )
        rows_by_def[d] = rows
        for r in rows:
            line = (
                f"{d:6s} win {r['index']:4d} k {r['k']:6d} {r['status']:12s} rho {_fmt(r['rho'], '.2f')} "
                f"psr {_fmt(r['psr'], '.2f')} lag {_fmt(r['lag'], '+.2f')} frames | t-src {_fmt(r['t_minus_src_s'], '.4f')} s"
            )
            if args.offline_h:
                line += f" | offline_h {_fmt(r['offline_h_s'] and r['offline_h_s'] * 1000, '.2f')} ms"
            if times is not None:
                line += f" | H({args.anchor}) {_fmt(r['anchor_h_s'] and r['anchor_h_s'] * 1000, '.2f')} ms"
            print(line)
    for d in definitions:
        print("\n".join(summarise(d, rows_by_def[d])))
        if times is not None:
            st = h_stability(rows_by_def[d])
            print(
                f"  anchor {args.anchor} H over {st['n']} confirmed windows: median {st['median_ms']:.2f} ms "
                f"p10 {st['p10_ms']:.2f} p90 {st['p90_ms']:.2f} max jump {st['max_jump_ms']:.2f} ms"
            )

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(
                [
                    "definition",
                    "index",
                    "k",
                    "status",
                    "rho",
                    "psr",
                    "lag_frames",
                    "t_minus_src_s",
                    "offline_h_s",
                    "anchor_h_s",
                ]
            )
            for d in definitions:
                for r in rows_by_def[d]:
                    w.writerow(
                        [
                            d,
                            r["index"],
                            r["k"],
                            r["status"],
                            r["rho"],
                            r["psr"],
                            r["lag"],
                            r["t_minus_src_s"],
                            r["offline_h_s"],
                            r.get("anchor_h_s"),
                        ]
                    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
