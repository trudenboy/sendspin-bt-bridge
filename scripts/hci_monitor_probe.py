#!/usr/bin/env python3
"""Passive HCI monitor probe: which timestamp and drop paths does this kernel deliver?

Read-only. Opens ``HCI_CHANNEL_MONITOR`` (needs ``CAP_NET_RAW``), enables ``SO_TIMESTAMPNS``,
``SO_RXQ_OVFL`` and a larger receive buffer, reads for a few seconds and prints counts only (never
record contents): how many records carried an ``SCM_TIMESTAMPNS`` cmsg, how many an
``SO_RXQ_OVFL`` cmsg, the ``SO_MEMINFO`` drop counter, and the REALTIME - RAW bracket statistics.
Sends nothing to any controller; traffic only shows up if something is already happening (any
HCI activity works, audio is not needed).

Stdlib only and self-contained so it can be copied as one file, e.g. into the add-on container:

    docker exec -i addon_85b1ecde_sendspin_bt_bridge python3 - --seconds 5 < scripts/hci_monitor_probe.py

The constants mirror ``sendspin_bridge.services.bluetooth.hci_socket`` (a unit test keeps them in
step).
"""

from __future__ import annotations

import argparse
import ctypes
import itertools
import os
import socket
import statistics
import struct
import sys
import time

AF_BLUETOOTH = 31
BTPROTO_HCI = 1
HCI_CHANNEL_MONITOR = 2
HCI_DEV_NONE = 0xFFFF
SO_RCVBUFFORCE = 33
SO_TIMESTAMPNS = 35
SCM_TIMESTAMPNS = SO_TIMESTAMPNS
SO_RXQ_OVFL = 40
SO_MEMINFO = 55
SK_MEMINFO_DROPS = 8
SK_MEMINFO_VARS = 9
RAW_CLOCK = getattr(time, "CLOCK_MONOTONIC_RAW", time.CLOCK_MONOTONIC)


class _SockaddrHci(ctypes.Structure):
    _fields_ = [("hci_family", ctypes.c_ushort), ("hci_dev", ctypes.c_ushort), ("hci_channel", ctypes.c_ushort)]


def open_monitor() -> socket.socket:
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    fd = libc.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
    if fd < 0:
        raise OSError(ctypes.get_errno(), "socket(AF_BLUETOOTH) failed")
    addr = _SockaddrHci(AF_BLUETOOTH, HCI_DEV_NONE, HCI_CHANNEL_MONITOR)
    if libc.bind(fd, ctypes.byref(addr), ctypes.sizeof(addr)) < 0:
        err = ctypes.get_errno()
        os.close(fd)
        raise OSError(err, "bind(HCI_CHANNEL_MONITOR) failed (needs CAP_NET_RAW)")
    return socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI, fileno=fd)


def set_opt(sock: socket.socket, opt: int, value: int) -> str:
    try:
        sock.setsockopt(socket.SOL_SOCKET, opt, value)
    except OSError as exc:
        return f"no ({exc.strerror or exc})"
    return "yes"


def meminfo_drops(sock: socket.socket) -> int | None:
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, SO_MEMINFO, 4 * SK_MEMINFO_VARS)
    except OSError:
        return None
    if len(raw) < 4 * (SK_MEMINFO_DROPS + 1):
        return None
    return struct.unpack_from("I", raw, 4 * SK_MEMINFO_DROPS)[0]


def bracket() -> tuple[int, int]:
    """(width_ns, real - raw_mid ns) of one (raw, real, raw) read."""
    raw0 = time.clock_gettime_ns(RAW_CLOCK)
    real = time.time_ns()
    raw1 = time.clock_gettime_ns(RAW_CLOCK)
    return raw1 - raw0, real - (raw0 + raw1) // 2


def probe(sock: socket.socket, seconds: float) -> dict[str, object]:
    opts = {
        "SO_TIMESTAMPNS": set_opt(sock, SO_TIMESTAMPNS, 1),
        "SO_RXQ_OVFL": set_opt(sock, SO_RXQ_OVFL, 1),
        "SO_RCVBUFFORCE": set_opt(sock, SO_RCVBUFFORCE, 4 << 20),
    }
    if opts["SO_RCVBUFFORCE"] != "yes":
        opts["SO_RCVBUF"] = set_opt(sock, socket.SO_RCVBUF, 4 << 20)
    sock.settimeout(0.25)
    drops_start = meminfo_drops(sock)
    records = ts_cmsgs = ovfl_cmsgs = 0
    other_cmsgs: set[tuple[int, int, int]] = set()
    stamp_lag_us: list[float] = []
    ovfl_last: int | None = None
    widths: list[int] = []
    offsets: list[int] = []
    deadline = time.monotonic() + seconds
    next_pair = 0.0
    while (now := time.monotonic()) < deadline:
        if now >= next_pair:  # one bracketed pair every 250 ms, like the hub
            width, offset = bracket()
            widths.append(width)
            offsets.append(offset)
            next_pair = now + 0.25
        try:
            _data, ancdata, _flags, _addr = sock.recvmsg(4096, 256)
        except TimeoutError:
            continue
        recv_real = time.time_ns()
        records += 1
        for level, kind, cdata in ancdata:
            if level == socket.SOL_SOCKET and kind == SCM_TIMESTAMPNS and len(cdata) in (8, 16):
                sec, nsec = struct.unpack("qq" if len(cdata) == 16 else "ii", cdata)
                ts_cmsgs += 1
                stamp_lag_us.append((recv_real - (sec * 1_000_000_000 + nsec)) / 1000.0)
            elif level == socket.SOL_SOCKET and kind == SO_RXQ_OVFL and len(cdata) == 4:
                ovfl_cmsgs += 1
                ovfl_last = struct.unpack("I", cdata)[0]
            else:
                other_cmsgs.add((level, kind, len(cdata)))
    drops_end = meminfo_drops(sock)
    steps = [abs(b - a) for a, b in itertools.pairwise(offsets)]
    return {
        "sockopts": opts,
        "records": records,
        "scm_timestampns_cmsgs": ts_cmsgs,
        "so_rxq_ovfl_cmsgs": ovfl_cmsgs,
        "so_rxq_ovfl_last": ovfl_last,
        "other_cmsgs": sorted(other_cmsgs),
        "meminfo_drops_start": drops_start,
        "meminfo_drops_end": drops_end,
        "drop_path": "cmsg" if ovfl_cmsgs else ("meminfo" if drops_end is not None else "none"),
        "stamp_to_recv_lag_us_median": round(statistics.median(stamp_lag_us), 1) if stamp_lag_us else None,
        "stamp_to_recv_lag_us_max": round(max(stamp_lag_us), 1) if stamp_lag_us else None,
        "bracket_pairs": len(widths),
        "bracket_width_ns_median": int(statistics.median(widths)) if widths else None,
        "bracket_width_ns_max": max(widths) if widths else None,
        "brackets_over_50us": sum(1 for w in widths if w > 50_000),
        "realtime_minus_raw_ns_first": offsets[0] if offsets else None,
        "realtime_minus_raw_step_ns_max": max(steps) if steps else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args(argv)
    print(f"kernel: {os.uname().release}  python: {sys.version.split()[0]}")
    try:
        sock = open_monitor()
    except OSError as exc:
        print(f"cannot open the HCI monitor socket: {exc}")
        return 2
    try:
        result = probe(sock, args.seconds)
    finally:
        sock.close()
    for key, value in result.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
