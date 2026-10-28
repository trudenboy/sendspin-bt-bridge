#!/usr/bin/env python3
"""CPU cost of the air sensor's parent side on a monitor capture.

Runs the capture's records through the live path — ``A2dpAirParser(check_crc="first",
decode_frames=False)`` and one
``AirLinkTracker`` per link, batches taken every 250 ms of capture time — ``--repeat`` times and
prints the cost per monitor record and the projected fraction of one core per streaming speaker
at 150 records/s (the budget is 0.04 per speaker). ``--crc all`` measures full CRC checking.

``--hub`` also replays the capture through the monitor hub's reader (``HciMonitorHub`` serving a
fake socket that returns the records as monitor datagrams with kernel timestamps, on the hub's own
thread, with the AVRCP consumer and an ``AirSensorService`` subscribed to every ACL link of the
capture) and prints the hub thread's CPU per record — what the live CPU guard measures, minus the
non-A2DP traffic a capture filtered to ACL TX + events does not carry — and the hub's own share
(hub path minus parser + tracker).

    uv run python scripts/air_sensor_bench.py [capture.btsnoop.gz] [--repeat 50] [--crc first|all] [--hub]
"""

from __future__ import annotations

import argparse
import asyncio
import socket
import struct
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from sendspin_bridge.services.bluetooth.a2dp_air import A2dpAirParser, read_btsnoop  # noqa: E402
from sendspin_bridge.services.bluetooth.a2dp_air_envelope import AirLinkTracker  # noqa: E402
from sendspin_bridge.services.bluetooth.air_sensor_service import AirSensorService  # noqa: E402
from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import (  # noqa: E402
    AvrcpConsumer,
    _parse_connection_complete,
)
from sendspin_bridge.services.bluetooth.hci_clock import RawRealtimeMap  # noqa: E402
from sendspin_bridge.services.bluetooth.hci_monitor_hub import HciMonitorHub  # noqa: E402
from sendspin_bridge.services.bluetooth.hci_socket import (  # noqa: E402
    MONITOR_ACL_TX_PKT,
    MONITOR_EVENT_PKT,
    SCM_TIMESTAMPNS,
    DropCounter,
)

if TYPE_CHECKING:
    from sendspin_bridge.services.bluetooth.a2dp_air import MediaPacket, MonitorRecord

DEFAULT_CAPTURE = ROOT / "tests" / "fixtures" / "a2dp_air" / "eneby_clicks.btsnoop.gz"
RECORDS_PER_S = 150
BUDGET_PER_SPEAKER = 0.04
TAKE_EVERY_S = 0.25


def load(path: str | Path) -> list[MonitorRecord]:
    return list(read_btsnoop(path))


def run_once(
    records: list[MonitorRecord], *, check_crc: Literal["all", "first"] = "first"
) -> dict[tuple[int, int], int]:
    """One pass; returns the frames batched per link."""
    parser = A2dpAirParser(check_crc=check_crc, decode_frames=False)
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


_REALTIME_OFFSET_NS = 1_000_000_000_000  # REALTIME = RAW + this in the replay (any constant works)


class _ReplayClock:
    """The replay's RAW clock: the capture time of the record the socket returned last."""

    def __init__(self, t0: float) -> None:
        self.t = t0

    def monotonic(self) -> float:
        return self.t

    def raw_ns(self) -> int:
        return int(self.t * 1e9)

    def real_ns(self) -> int:
        return self.raw_ns() + _REALTIME_OFFSET_NS


class _ReplaySocket:
    """A monitor socket returning the capture's records as datagrams with SCM_TIMESTAMPNS cmsgs.

    Measures the reading thread's CPU from its first ``recvmsg`` to ``close`` (after the hub has
    delivered what was still waiting for a clock pair).
    """

    def __init__(self, records: list[MonitorRecord], clock: _ReplayClock) -> None:
        self._items = [
            (
                r.t,
                struct.pack("<HHH", r.opcode, r.index, len(r.payload)) + r.payload,
                [
                    (
                        socket.SOL_SOCKET,
                        SCM_TIMESTAMPNS,
                        struct.pack("qq", *divmod(int(r.t * 1e9) + _REALTIME_OFFSET_NS, 1_000_000_000)),
                    )
                ],
            )
            for r in records
        ]
        self._clock = clock
        self._i = 0
        self.cpu_start: float | None = None
        self.cpu_s = 0.0
        self.exhausted = threading.Event()

    def setsockopt(self, *_args: object) -> None:
        pass

    def getsockopt(self, *_args: object) -> bytes:
        raise OSError("not a socket")

    def settimeout(self, _timeout: float) -> None:
        pass

    def recvmsg(self, _bufsize: int, _ancbufsize: int) -> tuple[bytes, list[tuple[int, int, bytes]], int, None]:
        if self.cpu_start is None:
            self.cpu_start = time.thread_time()
        if self._i >= len(self._items):
            self.exhausted.set()
            time.sleep(0.005)  # idle, as a real read timeout would be (no CPU)
            raise TimeoutError
        t, data, anc = self._items[self._i]
        self._i += 1
        self._clock.t = t
        return data, anc, 0, None

    def close(self) -> None:
        if self.cpu_start is not None:
            self.cpu_s = time.thread_time() - self.cpu_start


class _NullAvrcpTracker:
    def note_activity(self, mac: str, *, now: float | None = None) -> None:
        pass


def run_hub_once(records: list[MonitorRecord]) -> tuple[float, dict[tuple[int, int], int]]:
    """One replay through the hub; returns the hub thread's CPU seconds and the frames per link
    that reached the air service's trackers."""
    links = sorted(
        {(r.index, int.from_bytes(r.payload[:2], "little") & 0x0FFF) for r in records if r.opcode == MONITOR_ACL_TX_PKT}
    )
    peers = {key: f"00:00:00:00:{key[0]:02X}:{key[1] & 0xFF:02X}" for key in links}
    for r in records:  # a Connection Complete in the capture names the link's peer (the hub maps it)
        if r.opcode == MONITOR_EVENT_PKT and len(r.payload) >= 2 and r.payload[0] == 0x03:
            conn = _parse_connection_complete(r.payload[2:])
            if conn is not None:
                peers[(r.index, conn[0])] = conn[1]
    clock = _ReplayClock(records[0].t - 0.001)
    sock = _ReplaySocket(records, clock)
    hub = HciMonitorHub(
        open_socket=lambda: sock,
        seed=lambda m: m.update(peers),
        clock_map=RawRealtimeMap(raw_ns=clock.raw_ns, real_ns=clock.real_ns),
        drop_counter_factory=lambda _s: DropCounter(poll=lambda: 0),
        raw_ns=clock.raw_ns,
        monotonic=clock.monotonic,
    )
    loop = asyncio.new_event_loop()
    try:
        hub.add_consumer(AvrcpConsumer(tracker=_NullAvrcpTracker(), peers=hub.peers))
        service = AirSensorService(hub=hub, loop=loop, resolve_client=lambda _mac: None)
        for mac in peers.values():
            service.subscribe(mac)
        hub.start()
        sock.exhausted.wait(600)
        hub.stop(timeout=5.0)
        frames = {}
        for key, mac in peers.items():
            state = service.tracker_state(mac)
            frames[key] = state.frames if state is not None else 0
    finally:
        loop.close()
    return sock.cpu_s, frames


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture", nargs="?", default=str(DEFAULT_CAPTURE))
    ap.add_argument("--repeat", type=int, default=50)
    ap.add_argument("--crc", choices=("first", "all"), default="first")
    ap.add_argument("--hub", action="store_true", help="also replay through the monitor hub (hub thread CPU)")
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
    if args.hub:
        hub_cpu = 0.0
        hub_frames: dict[tuple[int, int], int] = {}
        for _ in range(args.repeat):
            cpu_s, hub_frames = run_hub_once(records)
            hub_cpu += cpu_s
        us_hub = hub_cpu / n * 1e6
        print(f"hub replay: frames reaching the air trackers per link: {dict(sorted(hub_frames.items()))}")
        print(
            f"hub path: {us_hub:.2f} us/record hub-thread cpu (hub's own share {us_hub - us_cpu:.2f}); "
            f"core fraction at {RECORDS_PER_S} records/s: {us_hub * RECORDS_PER_S / 1e6:.5f}, "
            f"at the capture's own rate: {us_hub * capture_rate / 1e6:.5f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
