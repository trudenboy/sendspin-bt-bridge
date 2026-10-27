#!/usr/bin/env python3
"""Click-track phase of an A2DP stream at the air interface, from a ``btmon -w`` capture.

Reads a monitor btsnoop, rebuilds the A2DP media packets of the busiest ACL TX
link (or the one given by ``--index``/``--handle``, or every media link with
``--all``), and folds an SBC energy proxy over the sync click-track cycle to
find where the clicks left the host:

* each SBC frame's sub-band scale factors in the chirp band (2–6 kHz) give its
  energy, at the frame's place in the RTP media clock;
* media time is mapped to wall time with the codec's nominal sample rate and the
  median offset between each packet's wall time and its RTP timestamp (its first
  sample) — once with the send time of the packet's last ACL fragment ("send"),
  once with the controller's completion time for it ("complete"). A packet's
  first sample therefore sits at the packet's time and later frames follow at
  the media rate;
* the folded profile is matched against the click pattern (click k at
  ``k·PERIOD_S``, its partner ``gap_ms(k)`` later).

The phase uses the convention of ``sendspin_sync.estimator``: click 0 at
``LEAD_IN_S``, ``phase = (t − epoch − LEAD_IN_S − k·PERIOD_S) mod CYCLE_S`` in ms,
with ``t`` in unix seconds (``--epoch-s`` defaults to 0).

    uv run python scripts/a2dp_air_phase.py capture.btsnoop [--index 1] [--handle 70] [--all]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sendspin_bridge.services.bluetooth.a2dp_air import (  # noqa: E402
    A2dpAirParser,
    MediaPacket,
    ParseStats,
    SbcFrame,
    parse_records,
    read_btsnoop,
)
from sendspin_bridge.services.bluetooth.hci_socket import MONITOR_ACL_TX_PKT  # noqa: E402
from sendspin_sync import click_track as ct  # noqa: E402

if TYPE_CHECKING:
    from collections.abc import Sequence


@dataclass(frozen=True)
class LinkReport:
    index: int
    handle: int
    packets: int
    completed: int
    frames: int
    codec: str
    sbc: str  # human summary of the SBC parameters, "" when not SBC
    pacing_hz: float | None  # RTP ticks per wall second (least squares over send times)
    nominal_hz: int | None
    latency_ms: float | None  # median send → completion of the last fragment
    phase_send_ms: float | None
    phase_complete_ms: float | None


def busiest_tx_link(records, index: int | None = None) -> tuple[int, int] | None:
    """The ``(index, handle)`` with the most ACL TX fragments (optionally on one controller index)."""
    counts: Counter[tuple[int, int]] = Counter()
    for rec in records:
        if rec.opcode == MONITOR_ACL_TX_PKT and len(rec.payload) >= 2 and (index is None or rec.index == index):
            counts[(rec.index, int.from_bytes(rec.payload[:2], "little") & 0x0FFF)] += 1
    return counts.most_common(1)[0][0] if counts else None


def chirp_subbands(frame: SbcFrame) -> range:
    """Sub-bands overlapping the chirp band, without the lowest (bass) sub-band."""
    width = frame.sampling_rate / 2 / frame.subbands
    lo = max(1, int(ct.CHIRP_F0 // width))
    hi = min(frame.subbands - 1, int(ct.CHIRP_F1 // width))
    return range(lo, hi + 1)


def frame_energy(frame: SbcFrame) -> float:
    """Energy proxy: sum of 2**scale_factor over channels in the chirp sub-bands."""
    return float(sum(2 ** row[sb] for row in frame.scale_factors for sb in chirp_subbands(frame)))


def unwrap_rtp(timestamps: Sequence[int]) -> list[int]:
    out: list[int] = []
    base = 0
    for i, ts in enumerate(timestamps):
        if i:
            step = (ts - timestamps[i - 1]) & 0xFFFFFFFF
            base += step - 0x100000000 if step >= 0x80000000 else step
        else:
            base = ts
        out.append(base)
    return out


def pacing_hz(packets: Sequence[MediaPacket]) -> float | None:
    """RTP ticks per wall second: least-squares slope of the RTP timestamp over the send time."""
    if len(packets) < 2:
        return None
    xs = [p.t_send - packets[0].t_send for p in packets]
    ys = unwrap_rtp([p.rtp_ts for p in packets])
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys, strict=True)) / sxx


def air_profile(packets: Sequence[MediaPacket], use: str) -> list[tuple[float, float]]:
    """``[(wall time of the frame's first sample, energy)]`` for every SBC frame.

    ``use`` is ``"send"`` or ``"complete"``: which packet time anchors media time to wall time.
    """
    sbc = [p for p in packets if p.frames]
    if not sbc:
        return []
    rate = sbc[0].frames[0].sampling_rate
    rtp = dict(zip((id(p) for p in sbc), unwrap_rtp([p.rtp_ts for p in sbc]), strict=True))
    anchors = [(p.t_send if use == "send" else p.t_complete) for p in sbc]
    offsets = [t - rtp[id(p)] / rate for p, t in zip(sbc, anchors, strict=True) if t is not None]
    if not offsets:
        return []
    a = statistics.median(offsets)
    rows = []
    for p in sbc:
        pos = rtp[id(p)]
        for f in p.frames:
            rows.append((a + pos / rate, frame_energy(f)))
            pos += f.samples
    return rows


def click_phase_ms(rows: Sequence[tuple[float, float]], frame_s: float, epoch_s: float = 0.0) -> float | None:
    """Fold ``rows`` over the click cycle and return the click-0 phase in ms, ``None`` without clicks.

    The fold runs on a 1 ms grid: each frame spreads its energy above the median floor over the
    milliseconds it covers, so the click pattern's offsets (whole ms) need no rounding. The
    pattern score is maximal over the frames that hold the clicks; the phase is the centre of
    the score's half-maximum peak around its maximum.
    """
    if not rows or frame_s <= 0:
        return None
    bins = round(ct.CYCLE_S * 1000)
    frame_ms = frame_s * 1000.0
    acc, cnt = [0.0] * bins, [0.0] * bins
    floor = statistics.median(e for _, e in rows)
    for w, e in rows:
        lo = ((w - epoch_s) % ct.CYCLE_S) * 1000.0
        hi = lo + frame_ms
        b = int(lo)
        while b < hi:
            overlap = min(hi, b + 1) - max(lo, b)
            acc[b % bins] += max(0.0, e - floor) * overlap
            cnt[b % bins] += overlap
            b += 1
    prof = [acc[i] / cnt[i] if cnt[i] else 0.0 for i in range(bins)]
    if not any(prof):
        return None
    period_ms = round(ct.PERIOD_S * 1000)
    offs = [k * period_ms + g for k in range(ct.CYCLE_CLICKS) for g in (0, ct.gap_ms(k))]
    score = [sum(prof[(p + o) % bins] for o in offs) for p in range(bins)]
    p0 = max(range(bins), key=score.__getitem__)
    half = 0.5 * (score[p0] + statistics.median(score))
    lo_i = p0
    while score[(lo_i - 1) % bins] >= half and p0 - lo_i < bins // 2:
        lo_i -= 1
    hi_i = p0
    while score[(hi_i + 1) % bins] >= half and hi_i - p0 < bins // 2:
        hi_i += 1
    weights = [(i, score[i % bins] - half) for i in range(lo_i, hi_i + 1)]
    total = sum(wt for _, wt in weights)
    centre = sum(i * wt for i, wt in weights) / total if total > 0 else float(p0)
    return (centre - ct.LEAD_IN_S * 1000.0) % (ct.CYCLE_S * 1000.0)


def report(packets: Sequence[MediaPacket], index: int, handle: int, epoch_s: float = 0.0) -> LinkReport:
    pk = [p for p in packets if p.index == index and p.handle == handle]
    frames = [f for p in pk for f in p.frames]
    sbc_desc, nominal, phase_send, phase_complete = "", None, None, None
    if frames:
        f0 = frames[0]
        per_packet = Counter(len(p.frames) for p in pk if p.frames).most_common(1)[0][0]
        bitpools = sorted({f.bitpool for f in frames})
        sbc_desc = (
            f"SBC {f0.sampling_rate} Hz {f0.channel_mode.name.lower().replace('_', ' ')}, {f0.subbands} sub-bands, "
            f"{f0.blocks} blocks, bitpool {'/'.join(map(str, bitpools))}, {per_packet} frames/packet"
        )
        nominal = f0.sampling_rate
        frame_s = f0.samples / f0.sampling_rate
        phase_send = click_phase_ms(air_profile(pk, "send"), frame_s, epoch_s)
        phase_complete = click_phase_ms(air_profile(pk, "complete"), frame_s, epoch_s)
    lat = [p.t_complete - p.t_send for p in pk if p.t_complete is not None]
    codecs = Counter(p.codec for p in pk)
    return LinkReport(
        index=index,
        handle=handle,
        packets=len(pk),
        completed=len(lat),
        frames=len(frames),
        codec=codecs.most_common(1)[0][0] if codecs else "none",
        sbc=sbc_desc,
        pacing_hz=pacing_hz(pk),
        nominal_hz=nominal,
        latency_ms=statistics.median(lat) * 1000.0 if lat else None,
        phase_send_ms=phase_send,
        phase_complete_ms=phase_complete,
    )


def analyze(
    path: str | Path,
    index: int | None = None,
    handle: int | None = None,
    *,
    all_links: bool = False,
    epoch_s: float = 0.0,
) -> tuple[list[LinkReport], ParseStats]:
    stats = ParseStats()
    records = list(read_btsnoop(path, stats=stats))
    packets = parse_records(records, A2dpAirParser(stats=stats))
    if all_links:
        links = sorted({(p.index, p.handle) for p in packets if index is None or p.index == index})
    elif handle is not None:
        idxs = sorted({p.index for p in packets if p.handle == handle and (index is None or p.index == index)})
        links = [(i, handle) for i in idxs]
    else:
        busiest = busiest_tx_link(records, index)
        links = [busiest] if busiest else []
    return [report(packets, i, h, epoch_s) for i, h in links], stats


def _ms(v: float | None) -> str:
    return "n/a" if v is None else f"{v:.1f} ms"


def format_report(r: LinkReport) -> str:
    lines = [
        f"hci{r.index} handle {r.handle} (0x{r.handle:04x}): {r.packets} media packets "
        f"({r.completed} with completion), {r.frames} frames, codec {r.codec}"
    ]
    if r.sbc:
        lines.append(f"  {r.sbc}")
    if r.pacing_hz is not None:
        ppm = f" ({(r.pacing_hz / r.nominal_hz - 1) * 1e6:+.0f} ppm vs {r.nominal_hz})" if r.nominal_hz else ""
        lines.append(f"  pacing {r.pacing_hz:.2f} RTP ticks per wall second{ppm}")
    lines.append(f"  send -> complete (median) {_ms(r.latency_ms)}")
    lines.append(f"  click phase at the air: send {_ms(r.phase_send_ms)}, complete {_ms(r.phase_complete_ms)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture", type=Path, help="btmon -w capture (btsnoop, monitor datalink; .gz ok)")
    ap.add_argument("--index", type=int, help="controller index (hciN)")
    ap.add_argument("--handle", type=lambda s: int(s, 0), help="ACL connection handle (decimal or 0x..)")
    ap.add_argument("--all", action="store_true", help="report every link that carries media")
    ap.add_argument("--epoch-s", type=float, default=0.0, help="phase epoch in unix seconds (default 0)")
    args = ap.parse_args(argv)
    try:
        reports, stats = analyze(args.capture, args.index, args.handle, all_links=args.all, epoch_s=args.epoch_s)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not reports:
        print("no matching ACL TX traffic", file=sys.stderr)
        return 1
    print("\n".join(format_report(r) for r in reports))
    skipped = {k: v for k, v in vars(stats).items() if v}
    if skipped:
        print(f"parser counters: {skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
