"""Monitor primitives for the HCI monitor hub: kernel stamps, drop counters, REALTIME -> RAW.

Pure helpers; nothing calls them yet (the hub is a later task), so the AVRCP monitor path is
unaffected.
"""

from __future__ import annotations

import socket
import struct

import pytest

from sendspin_bridge.services.bluetooth import hci_socket as hs
from sendspin_bridge.services.bluetooth.hci_clock import RawRealtimeMap


def ts_cmsg(ns):
    return (socket.SOL_SOCKET, hs.SCM_TIMESTAMPNS, struct.pack("qq", ns // 10**9, ns % 10**9))


def ovfl_cmsg(n):
    return (socket.SOL_SOCKET, hs.SO_RXQ_OVFL, struct.pack("I", n))


def test_constants_match_kernel_values():
    assert (hs.SO_TIMESTAMPNS, hs.SCM_TIMESTAMPNS, hs.SO_RXQ_OVFL) == (35, 35, 40)
    assert (hs.SO_MEMINFO, hs.SK_MEMINFO_DROPS, hs.SK_MEMINFO_VARS) == (55, 8, 9)
    assert hs.SO_RCVBUFFORCE == 33


def test_cmsgs_parse_timestamp_and_drops():
    assert hs.parse_monitor_cmsgs([ts_cmsg(1_700_000_000_123_456_789), ovfl_cmsg(7)]) == (1_700_000_000_123_456_789, 7)
    assert hs.parse_monitor_cmsgs([ts_cmsg(5)]) == (5, None)  # the expected case on the monitor channel
    assert hs.parse_monitor_cmsgs([]) == (None, None)
    assert hs.parse_monitor_cmsgs([(socket.SOL_SOCKET, hs.SO_RXQ_OVFL, b"\x01")]) == (None, None)


def test_cmsgs_accept_a_32_bit_timespec_and_reject_malformed_ones():
    # armv7 userspace: the legacy SO_TIMESTAMPNS timespec is two 32-bit longs.
    assert hs.parse_monitor_cmsgs([(socket.SOL_SOCKET, hs.SCM_TIMESTAMPNS, struct.pack("ii", 3, 4))]) == (
        3_000_000_004,
        None,
    )
    bad_nsec = (socket.SOL_SOCKET, hs.SCM_TIMESTAMPNS, struct.pack("qq", 1, 10**9))
    short = (socket.SOL_SOCKET, hs.SCM_TIMESTAMPNS, b"\x00" * 5)
    other_level = (socket.SOL_SOCKET + 1, hs.SCM_TIMESTAMPNS, struct.pack("qq", 1, 2))
    assert hs.parse_monitor_cmsgs([bad_nsec, short, other_level]) == (None, None)


class FakeSock:
    def __init__(self, meminfo):
        self.meminfo = meminfo

    def getsockopt(self, level, opt, buflen):
        assert (level, opt) == (socket.SOL_SOCKET, hs.SO_MEMINFO)
        if self.meminfo is None:
            raise OSError(92, "Protocol not available")
        return struct.pack(f"{len(self.meminfo)}I", *self.meminfo)


def test_meminfo_drops_are_read_from_index_8():
    assert hs.read_meminfo_drops(FakeSock([0, 0, 0, 0, 0, 0, 0, 0, 12])) == 12
    assert hs.read_meminfo_drops(FakeSock(None)) is None


def test_meminfo_too_short_for_the_drops_slot_is_unsupported():
    assert hs.read_meminfo_drops(FakeSock([0] * 8)) is None


def test_drop_counter_uses_meminfo_without_cmsg():
    vals = iter([0, 0, 3]).__next__
    dc = hs.DropCounter(poll=vals)
    dc.on_cmsg(None)
    dc.tick()
    dc.tick()
    dc.tick()
    assert (dc.drops, dc.source) == (3, "meminfo")


def test_drop_counter_keeps_the_last_meminfo_value_when_a_poll_fails():
    vals = iter([5, None]).__next__
    dc = hs.DropCounter(poll=vals)
    dc.tick()
    dc.tick()
    assert (dc.drops, dc.source) == (5, "meminfo")


def test_drop_counter_prefers_cmsg_once_seen():
    dc = hs.DropCounter(poll=lambda: 99)
    dc.on_cmsg(4)
    dc.tick()
    assert (dc.drops, dc.source) == (4, "cmsg")


def test_drop_counter_switches_to_cmsg_after_polling_and_stops_polling():
    polls = []

    def poll():
        polls.append(1)
        return 2

    dc = hs.DropCounter(poll=poll)
    dc.tick()
    dc.on_cmsg(6)
    dc.tick()
    dc.on_cmsg(None)  # a record without the cmsg keeps the last count
    assert (dc.drops, dc.source, len(polls)) == (6, "cmsg", 1)


def test_drop_counter_none_when_nothing_works():
    dc = hs.DropCounter(poll=lambda: None)
    dc.tick()
    assert (dc.drops, dc.source) == (None, "none")


def test_realtime_to_raw_interpolates_between_pairs():
    raw = iter([0, 10, 1_000_000_000, 1_000_000_010]).__next__
    real = iter([5_000_000_000_005, 5_001_000_000_505]).__next__  # 500 ns slew over 1 s
    m = RawRealtimeMap(raw_ns=raw, real_ns=real)
    assert m.sample() and m.sample()
    assert m.to_raw_us(5_000_500_000_255) == pytest.approx(500_000.005, abs=0.01)


def test_to_raw_is_none_until_two_pairs_bracket_the_stamp():
    raw = iter([0, 10, 1_000_000_000, 1_000_000_010]).__next__
    real = iter([5_000_000_000_005, 5_001_000_000_505]).__next__
    m = RawRealtimeMap(raw_ns=raw, real_ns=real)
    assert m.to_raw_us(5_000_000_000_005) is None
    m.sample()
    assert m.to_raw_us(5_000_000_000_005) is None
    m.sample()
    assert m.to_raw_us(5_000_000_000_005) == pytest.approx(0.005, abs=1e-6)  # bracket edges are inside
    assert m.to_raw_us(5_000_000_000_004) is None  # before the first pair
    assert m.to_raw_us(5_001_000_000_506) is None  # after the last pair: wait for the next one


def test_a_wide_bracket_is_retried_then_refused():
    raw = iter([0, 200_000] * 3).__next__  # 200 µs brackets
    m = RawRealtimeMap(raw_ns=raw, real_ns=lambda: 1)
    assert m.sample() is False


def test_a_wide_bracket_is_retried_and_a_narrow_retry_is_accepted():
    raw = iter([0, 200_000, 300_000, 300_020]).__next__
    m = RawRealtimeMap(raw_ns=raw, real_ns=lambda: 1_000_000)
    assert m.sample() is True


def test_clock_step_starts_a_new_epoch():
    raw = iter([0, 10, 1_000_000_000, 1_000_000_010]).__next__
    real = iter([5_000_000_000_005, 5_001_020_000_005]).__next__  # +20 ms step
    m = RawRealtimeMap(raw_ns=raw, real_ns=real)
    m.sample()
    m.sample()
    assert m.epoch == 1


def test_no_interpolation_across_a_clock_step():
    raw = iter([0, 10, 1_000_000_000, 1_000_000_010, 2_000_000_000, 2_000_000_010]).__next__
    real = iter([5_000_000_000_005, 5_001_020_000_005, 5_002_020_000_005]).__next__
    m = RawRealtimeMap(raw_ns=raw, real_ns=real)
    m.sample()
    m.sample()
    assert m.to_raw_us(5_000_500_000_000) is None  # spans the step
    m.sample()
    assert m.epoch == 1
    # Offset after the step is 5_000_020_000_000 ns.
    assert m.to_raw_us(5_001_520_000_005) == pytest.approx(1_500_000.005, abs=0.01)


def test_sockopts_report_what_succeeded():
    class S:
        def __init__(self):
            self.calls = []

        def setsockopt(self, level, opt, val):
            self.calls.append(opt)
            if opt == 33:
                raise PermissionError(1, "no CAP_NET_ADMIN")  # SO_RCVBUFFORCE

    got = hs.enable_monitor_sockopts(S())
    assert got["timestamps"] and got["rxq_ovfl"] and got["rcvbuf"] and not got["rcvbuf_force"]


def test_sockopts_use_rcvbufforce_when_allowed_and_survive_unsupported_options():
    class S:
        def __init__(self):
            self.calls = []

        def setsockopt(self, level, opt, val):
            assert level == socket.SOL_SOCKET
            self.calls.append((opt, val))
            if opt == hs.SO_RXQ_OVFL:
                raise OSError(92, "Protocol not available")

    s = S()
    got = hs.enable_monitor_sockopts(s, rcvbuf=1 << 20)
    assert got == {"timestamps": True, "rxq_ovfl": False, "rcvbuf_force": True, "rcvbuf": True}
    assert (hs.SO_RCVBUFFORCE, 1 << 20) in s.calls
    assert socket.SO_RCVBUF not in [opt for opt, _ in s.calls]
