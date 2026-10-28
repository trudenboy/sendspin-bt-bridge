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
``d_enc = 4.5·M/rate``, ``d_comb = ((comb lag + LOOKBACK_FRAMES) / 2)/rate`` (the comb cells
span the cell and the ``LOOKBACK_FRAMES`` before it, as the daemon's tap does; 0 for ``legacy``
rows and the band-pass reference). ``src_mid`` is the source time of the matched cell.

Making a capture (the morning one is copyrighted music and stays private): on the speaker
host run ``btmon -w capture.btsnoop`` (needs root), play a known local WAV through the bridge
to one SBC speaker for a minute or two, stop btmon, then::

    uv run python scripts/air_envelope_corr.py capture.btsnoop source.wav [--index N] [--handle H]
        [--definition legacy|impl|both] [--window-frames 1034] [--hop-frames 344] [--search 20]
        [--envelope comb|bandpass] [--offline-h] [--csv out.csv]

The WAV must be 16- or 24-bit PCM at the capture's SBC sampling rate and start where the
playback started (a constant offset is found by a coarse search, ``--offset-frames`` overrides
it). ``--selftest`` runs both definitions on a synthetic pair. Pure Python: allow a few
minutes for a two-minute track.
"""

from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
import struct
import sys
import wave
from collections import Counter
from dataclasses import dataclass
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
    to_s16,
)
from sendspin_bridge.services.bluetooth import a2dp_air  # noqa: E402
from sendspin_bridge.services.bluetooth.a2dp_air_envelope import envelope_subbands, frame_envelope  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from sendspin_bridge.services.bluetooth.a2dp_air import MediaPacket, SbcFrame

CELL = 128  # samples per envelope cell (one SBC frame of 8 sub-bands x 16 blocks)
LEGACY_GATE = 1.15  # the morning script's "ambiguous" threshold
SEND_GAP_S = 1.0
ANCHOR_SPAN_S = 1.0
BANDPASS_LEAD = 2  # cells of filter lead-in before each band-pass cell
RTP_MOD = 1 << 32


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


def impl_source_cells(data: Sequence[int], ch: int, rate: int, width: int, envelope: str) -> list[float]:
    """The daemon's written cells: comb envelope over each 128 input frames and the LOOKBACK_FRAMES
    before them (or the reference band-pass) per 128 input frames."""
    s16 = to_s16(b"".join(v.to_bytes(width, "little", signed=True) for v in data), width)
    frames = len(s16) // ch
    out = []
    if envelope == "comb":
        lag = comb_lag(rate)
        zeros = (0,) * (lag * ch)
        for start in range(0, frames - CELL + 1, CELL):
            lo = max(0, start - LOOKBACK_FRAMES)  # the span an SBC frame's scale factors see
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
    d_comb = ((comb_lag(rate) + LOOKBACK_FRAMES) / 2.0) / rate if comb and definition == "impl" else 0.0
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
    ap.add_argument("--offline-h", action="store_true", help="add the local-anchor offline H per window")
    ap.add_argument("--csv", type=Path, default=None)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)
    if args.selftest:
        return run_selftest()
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
                else impl_source_cells(data, ch, fs, width, args.envelope)
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
        rows_by_def[d] = rows
        for r in rows:
            line = (
                f"{d:6s} win {r['index']:4d} k {r['k']:6d} {r['status']:12s} rho {_fmt(r['rho'], '.2f')} "
                f"psr {_fmt(r['psr'], '.2f')} lag {_fmt(r['lag'], '+.2f')} frames | t-src {_fmt(r['t_minus_src_s'], '.4f')} s"
            )
            if args.offline_h:
                line += f" | offline_h {_fmt(r['offline_h_s'] and r['offline_h_s'] * 1000, '.2f')} ms"
            print(line)
    for d in definitions:
        print("\n".join(summarise(d, rows_by_def[d])))

    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(
                ["definition", "index", "k", "status", "rho", "psr", "lag_frames", "t_minus_src_s", "offline_h_s"]
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
                        ]
                    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
