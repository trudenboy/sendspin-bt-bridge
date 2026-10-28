#!/usr/bin/env python3
"""CPU cost of the air sensor's parent side on a monitor capture.

Runs the capture's records through the live path — ``A2dpAirParser(check_crc="first")`` and one
``AirLinkTracker`` per link, batches taken every 250 ms of capture time — ``--repeat`` times and
prints the cost per monitor record and the projected fraction of one core per streaming speaker
at 150 records/s (the budget is 0.015 per speaker). ``--crc all`` measures full CRC checking.

    uv run python scripts/air_sensor_bench.py [capture.btsnoop.gz] [--repeat 50] [--crc first|all]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sendspin_bridge.services.bluetooth.a2dp_air import A2dpAirParser, read_btsnoop  # noqa: E402
from sendspin_bridge.services.bluetooth.a2dp_air_envelope import AirLinkTracker  # noqa: E402

if TYPE_CHECKING:
    from sendspin_bridge.services.bluetooth.a2dp_air import MediaPacket, MonitorRecord

DEFAULT_CAPTURE = ROOT / "tests" / "fixtures" / "a2dp_air" / "eneby_clicks.btsnoop.gz"
RECORDS_PER_S = 150
BUDGET_PER_SPEAKER = 0.015
TAKE_EVERY_S = 0.25


def load(path: str | Path) -> list[MonitorRecord]:
    return list(read_btsnoop(path))


def run_once(
    records: list[MonitorRecord], *, check_crc: Literal["all", "first"] = "first"
) -> dict[tuple[int, int], int]:
    """One pass; returns the frames batched per link."""
    parser = A2dpAirParser(check_crc=check_crc)
    trackers: dict[tuple[int, int], AirLinkTracker] = {}
    frames: dict[tuple[int, int], int] = {}
    next_take: float | None = None

    def deliver(packets: list[MediaPacket]) -> None:
        for p in packets:
            key = (p.index, p.handle)
            tr = trackers.get(key)
            if tr is None:
                tr = trackers[key] = AirLinkTracker()
            tr.on_packet(p, timestamp="kernel", in_step=p.t_complete is not None)

    def take(now_us: float, *, force: bool) -> None:
        for key, tr in trackers.items():
            for b in tr.take_batches(now_us, force=force):
                frames[key] = frames.get(key, 0) + len(b.e)

    for rec in records:
        deliver(parser.feed(rec.t, rec.index, rec.opcode, rec.payload, rec.drops))
        if next_take is None:
            next_take = rec.t + TAKE_EVERY_S
        elif rec.t >= next_take:
            take(rec.t * 1e6, force=False)
            next_take += TAKE_EVERY_S
    deliver(parser.flush())
    take(float("inf"), force=True)
    return frames


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture", nargs="?", default=str(DEFAULT_CAPTURE))
    ap.add_argument("--repeat", type=int, default=50)
    ap.add_argument("--crc", choices=("first", "all"), default="first")
    args = ap.parse_args(argv)
    records = load(args.capture)
    if not records:
        print("no records", file=sys.stderr)
        return 1
    span_s = records[-1].t - records[0].t
    frames: dict[tuple[int, int], int] = {}
    wall0, cpu0 = time.perf_counter(), time.process_time()
    for _ in range(args.repeat):
        frames = run_once(records, check_crc=args.crc)
    wall, cpu = time.perf_counter() - wall0, time.process_time() - cpu0
    n = len(records) * args.repeat
    us_wall, us_cpu = wall / n * 1e6, cpu / n * 1e6
    capture_rate = len(records) / span_s if span_s > 0 else 0.0
    print(f"capture: {len(records)} records over {span_s:.2f} s ({capture_rate:.0f} records/s); crc={args.crc}")
    print(f"frames batched per link: {dict(sorted(frames.items()))}")
    print(f"{args.repeat} passes: {us_wall:.2f} us/record wall, {us_cpu:.2f} us/record cpu")
    fraction = us_cpu * RECORDS_PER_S / 1e6
    print(
        f"core fraction at {RECORDS_PER_S} records/s per speaker: {fraction:.5f} "
        f"(budget {BUDGET_PER_SPEAKER}); at the capture's own rate: {us_cpu * capture_rate / 1e6:.5f}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
