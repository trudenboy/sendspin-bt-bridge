"""The live air path (parser → tracker → batches) produces exactly the batches it produced before
the R-19 CPU work.

The digests below were recorded from the pre-optimisation code (commit 4e6c4469) on the fixtures,
untouched and with deterministic corruption (flipped bytes inside SBC payloads, a monitor drop),
so any change in an energy cell, a time, a flag or a batch boundary changes them.
"""

from __future__ import annotations

import hashlib
import random
import struct
from dataclasses import replace
from pathlib import Path

import pytest

from sendspin_bridge.services.bluetooth.a2dp_air import A2dpAirParser, MonitorRecord, read_btsnoop
from sendspin_bridge.services.bluetooth.a2dp_air_envelope import AirLinkTracker
from sendspin_bridge.services.bluetooth.hci_socket import MONITOR_ACL_RX_PKT, MONITOR_ACL_TX_PKT, MONITOR_EVENT_PKT

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "a2dp_air"
TAKE_EVERY_S = 0.25


def _corrupt(records: list[MonitorRecord], every: int, step: int) -> list[MonitorRecord]:
    """Flip one byte of every *every*-th ACL TX payload past the ACL header, at a position that walks
    by *step* (so headers, scale factors and audio bits of the SBC frames all get hit)."""
    out: list[MonitorRecord] = []
    n = 0
    for rec in records:
        if rec.opcode == MONITOR_ACL_TX_PKT:
            n += 1
            if n % every == 0 and len(rec.payload) > 4:
                p = bytearray(rec.payload)
                p[4 + (n * step) % (len(p) - 4)] ^= 0x5A
                rec = replace(rec, payload=bytes(p))
        out.append(rec)
    return out


def _drop_at(records: list[MonitorRecord], k: int) -> list[MonitorRecord]:
    """A monitor drop counter that grows at record *k*."""
    return [replace(r, drops=1 if i >= k else 0) for i, r in enumerate(records)]


def _noise(records: list[MonitorRecord]) -> list[MonitorRecord]:
    """Interleave traffic the air path does not use — ACL RX, LE advertising reports, Command
    Complete, SCO — between the records (never first): the batches must not change."""
    noise = [
        (MONITOR_ACL_RX_PKT, struct.pack("<HH", 0x0046, 8) + b"\x04\x00\x41\x00abcd"),
        (MONITOR_EVENT_PKT, bytes([0x3E, 12, 0x02, 0x01]) + bytes(10)),
        (MONITOR_EVENT_PKT, bytes([0x0E, 4, 0x01, 0x03, 0x0C, 0x00])),
        (6, b"\x01\x00\x03abc"),  # SCO TX
    ]
    out: list[MonitorRecord] = []
    for i, rec in enumerate(records):
        out.append(rec)
        if i + 1 < len(records):
            opcode, payload = noise[i % len(noise)]
            # at the same time as the record before it: the harness's take cadence stays the same
            out.append(MonitorRecord(rec.t, rec.index, opcode, payload, rec.drops))
    return out


def _drop_on_skipped(records: list[MonitorRecord]) -> list[MonitorRecord]:
    """After the capture's last record (its link drained, completions held): an LE advertising
    report carrying a monitor drop 0.1 s later, then nothing the air path reads until a stray NOCP
    2 s later — past the 1 s hold. The drop must still take the link out of step before the held
    completions are released (a feeder that skips the advert must still pass its drop on)."""
    last = records[-1]
    return [
        *records,
        MonitorRecord(last.t + 0.1, last.index, MONITOR_EVENT_PKT, bytes([0x3E, 12, 0x02, 0x01]) + bytes(10), 1),
        MonitorRecord(last.t + 2.0, last.index, MONITOR_EVENT_PKT, bytes([0x13, 5, 1, 0xFF, 0x0F, 1, 0]), 1),
    ]


def _undeclared(records: list[MonitorRecord]) -> list[MonitorRecord]:
    """Between the records (never first), one random record of each monitor opcode and each HCI
    event code the air service does not declare (the hub skips them for it), at the same time and
    drop count as the record before: the batches must not change."""
    rng = random.Random(1904)
    kinds = [(op, None) for op in range(5, 18)] + [
        (MONITOR_EVENT_PKT, code) for code in range(256) if code not in (0x03, 0x05, 0x10, 0x13)
    ]
    out: list[MonitorRecord] = []
    for i, rec in enumerate(records):
        out.append(rec)
        if i + 1 < len(records):
            opcode, code = kinds[i % len(kinds)]
            body = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 24)))
            payload = bytes([code, len(body)]) + body if code is not None else body
            out.append(MonitorRecord(rec.t, rec.index, opcode, payload, rec.drops))
    return out


def _variant(name: str) -> list[MonitorRecord]:
    fixture, _, kind = name.partition(":")
    records = list(read_btsnoop(FIX / f"{fixture}.btsnoop.gz"))
    if kind == "corrupt_a":
        return _corrupt(records, 3, 37)
    if kind == "corrupt_b":
        return _corrupt(records, 5, 11)
    if kind == "undeclared":
        return _undeclared(records)
    if kind == "drop_on_skipped":
        return _drop_on_skipped(records)
    if kind == "noise":
        return _noise(records)
    if kind == "drop":
        return _drop_at(records, len(records) // 2)
    return records


def live_batches(records: list[MonitorRecord], parser: A2dpAirParser) -> tuple[list[str], str]:
    trackers: dict[tuple[int, int], AirLinkTracker] = {}
    out: list[str] = []
    next_take: float | None = None

    def deliver(packets) -> None:
        for p in packets:
            key = (p.index, p.handle)
            tr = trackers.get(key)
            if tr is None:
                tr = trackers[key] = AirLinkTracker()
            tr.on_packet(p, timestamp="kernel", in_step=p.t_complete is not None, clock_epoch=0)

    def take(now_us: float, *, force: bool) -> None:
        for key, tr in trackers.items():
            out.extend(f"{key} {b!r}" for b in tr.take_batches(now_us, force=force))

    for rec in records:
        deliver(parser.feed(rec.t, rec.index, rec.opcode, rec.payload, rec.drops))
        if next_take is None:
            next_take = rec.t + TAKE_EVERY_S
        elif rec.t >= next_take:
            take(rec.t * 1e6, force=False)
            next_take += TAKE_EVERY_S
    deliver(parser.flush())
    take(float("inf"), force=True)
    return out, repr(parser.stats)


GOLDEN: dict[str, tuple[int, str]] = {
    "eneby_clicks": (19, "5c4a8e5668ba7e679a32c78e09762793cb3b42e59e68b62a4a17eb96556464d2"),
    "eneby_clicks:corrupt_a": (19, "eaf9532e5c6c1cae779239cd0bf8b676ae2e338a695e9518c7b0f57dd6225872"),
    "eneby_clicks:corrupt_b": (17, "ef4e1daa68208752f865de6d3be4ecc1c091ce3126f3d1b6594e6dcae4d1c40c"),
    "eneby_clicks:noise": (
        19,
        "5c4a8e5668ba7e679a32c78e09762793cb3b42e59e68b62a4a17eb96556464d2",
    ),  # = clean: the air path ignores the noise
    "eneby_clicks:drop": (19, "2666afb3749f5fbb527679041bf237fabeced90391f5e0644f563894243df408"),
    "eneby_clicks:undeclared": (19, "5c4a8e5668ba7e679a32c78e09762793cb3b42e59e68b62a4a17eb96556464d2"),  # = clean
    "eneby_clicks:drop_on_skipped": (19, "2666afb3749f5fbb527679041bf237fabeced90391f5e0644f563894243df408"),
    "air_music:undeclared": (58, "4dbff0ab79fab326c4c7d07bad47c777cce3cf67a7f976616f437c47dca51279"),  # = clean
    "air_music:drop_on_skipped": (58, "482dd473449dbe2917a2c4d842fcd07cbe594147210cf82ded29308071a70827"),
    "air_music": (58, "4dbff0ab79fab326c4c7d07bad47c777cce3cf67a7f976616f437c47dca51279"),
    "air_music:corrupt_a": (37, "5afa4c0e6bcb844a3b1a55bb38adff2e6cfb9b4903113a6fba0aa051c9cd3247"),
    "air_music:corrupt_b": (32, "b1ee57b36f6b82d4622807868379e3d6b28d28c5526bbb203caeb4eab04dca6b"),
    "air_music:noise": (58, "4dbff0ab79fab326c4c7d07bad47c777cce3cf67a7f976616f437c47dca51279"),  # = clean
    "air_music:drop": (58, "2e67216840de27b0ac4c6dbf79c7e0bb5e8c70f35366e9f3c547bc8ea074da8d"),
}


PARSERS = {
    "decoded": lambda: A2dpAirParser(check_crc="first"),
    "undecoded": lambda: A2dpAirParser(check_crc="first", decode_frames=False),  # the live path (R-19)
}


@pytest.mark.parametrize("parser", sorted(PARSERS))
@pytest.mark.parametrize("variant", sorted(GOLDEN))
def test_live_path_batches_match_the_recorded_output(variant, parser):
    batches, stats = live_batches(_variant(variant), PARSERS[parser]())
    digest = hashlib.sha256("\n".join([*batches, stats]).encode()).hexdigest()
    assert (len(batches), digest) == GOLDEN[variant]
