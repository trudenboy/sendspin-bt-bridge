"""Rebuild ``eneby_clicks.btsnoop.gz`` from the full stand capture.

The full capture (``hci_dcold2.btsnoop``, 66 s, ``btmon -w`` on a bridge
streaming the sync click track to an ENEBY20 on hci1, handle 70; SBC 44.1 kHz
joint stereo, bitpool 53, 7 frames per RTP packet) is too big for the repo.
This keeps the records of one controller index inside a time window — by
default the 6 s from 50 s into the capture, which contain three clicks — and
writes them as a gzip btsnoop (fixed gzip mtime, so the output is
byte-for-byte reproducible). The window starts mid-stream on purpose: its
first completion events belong to packets sent before the window.

    uv run python tests/fixtures/a2dp_air/build_fixture.py /path/to/hci_dcold2.btsnoop

On the full capture ``scripts/a2dp_air_phase.py`` reports the click phase
recorded as ``FULL_CAPTURE_PHASE_SEND_MS`` in the fixture test.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import struct
from pathlib import Path

from sendspin_bridge.services.bluetooth.a2dp_air import (
    BTSNOOP_DATALINK_MONITOR,
    BTSNOOP_EPOCH_DELTA_US,
    BTSNOOP_MAGIC,
    read_btsnoop,
)

HERE = Path(__file__).resolve().parent
SOURCE_SHA256 = "42b47e3f5cb463133584878e833add25c26fce81e60af80ff2a21b3c2ade9568"  # hci_dcold2.btsnoop


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", type=Path)
    ap.add_argument("--index", type=int, default=1)
    ap.add_argument("--start", type=float, default=50.0, help="seconds after the first record")
    ap.add_argument("--duration", type=float, default=6.0)
    ap.add_argument("--out", type=Path, default=HERE / "eneby_clicks.btsnoop.gz")
    args = ap.parse_args(argv)
    digest = hashlib.sha256(args.source.read_bytes()).hexdigest()
    if digest != SOURCE_SHA256:
        print(f"warning: {args.source} sha256 {digest} is not the recorded stand capture {SOURCE_SHA256}")

    out = bytearray(BTSNOOP_MAGIC + struct.pack(">II", 1, BTSNOOP_DATALINK_MONITOR))
    t0 = None
    kept = 0
    for rec in read_btsnoop(args.source):
        t0 = rec.t if t0 is None else t0
        if rec.index != args.index or not args.start <= rec.t - t0 < args.start + args.duration:
            continue
        ts_us = round(rec.t * 1e6) + BTSNOOP_EPOCH_DELTA_US
        flags = (rec.index << 16) | rec.opcode
        out += struct.pack(">IIIIq", len(rec.payload), len(rec.payload), flags, 0, ts_us) + rec.payload
        kept += 1
    with open(args.out, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz:
        gz.write(out)
    print(f"{args.out}: {kept} records, {len(out)} bytes raw, {args.out.stat().st_size} bytes gzip")


if __name__ == "__main__":
    main()
