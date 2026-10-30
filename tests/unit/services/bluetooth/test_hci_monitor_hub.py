"""HciMonitorHub: the reader thread that owns the HCI monitor socket when the air sensor is on.

No real socket is opened: ``fake_hub_socket`` scripts ``recvmsg`` results, then answers
``EAGAIN`` (the hub's socket is non-blocking) until closed, and records the socket options the hub
sets. Its ``fileno()`` is a pipe that is readable exactly while a read would return something, so
the hub's readability wait (``poll``) behaves as on the real monitor socket.
"""

from __future__ import annotations

import errno
import logging
import os
import socket
import struct
import threading
import time

import pytest

from sendspin_bridge.services.bluetooth import hci_socket as hs
from sendspin_bridge.services.bluetooth.hci_monitor_hub import HciMonitorHub


class FakeMonitorSocket:
    def __init__(self, records, *, meminfo_drops=None, fail_first_read=False):
        self._records = list(records)
        self._meminfo_drops = meminfo_drops
        self._fail_first_read = fail_first_read
        self.closed = False
        self.setsockopt_calls: list[tuple[int, int, int]] = []
        self.getsockopt_calls: list[tuple[int, int, int]] = []
        self.timeouts: list[float | None] = []
        self._ready_r, self._ready_w = os.pipe()
        self._ready = False
        self._sync_ready()

    def _sync_ready(self):
        want = bool(self._records) or self._fail_first_read
        if want and not self._ready:
            os.write(self._ready_w, b"r")
        elif not want and self._ready:
            os.read(self._ready_r, 1)
        self._ready = want

    def fileno(self):
        return self._ready_r

    def setsockopt(self, level, opt, value):
        self.setsockopt_calls.append((level, opt, value))

    def getsockopt(self, level, opt, buflen=0):
        self.getsockopt_calls.append((level, opt, buflen))
        if opt == hs.SO_MEMINFO and self._meminfo_drops is not None:
            values = [0] * hs.SK_MEMINFO_VARS
            values[hs.SK_MEMINFO_DROPS] = self._meminfo_drops
            return struct.pack(f"{hs.SK_MEMINFO_VARS}I", *values)
        raise OSError(errno.ENOPROTOOPT, "not supported")

    def settimeout(self, value):
        self.timeouts.append(value)

    def recvmsg(self, bufsize, ancbufsize=0):
        if self.closed:
            raise OSError(errno.EBADF, "closed")
        if self._fail_first_read:
            self._fail_first_read = False
            self._sync_ready()
            raise OSError(errno.ENETDOWN, "monitor went away")
        if self._records:
            item = self._records.pop(0)
            self._sync_ready()
            if len(item) == 2:
                data, anc = item
                return data, anc, 0, None
            return item
        if self.timeouts and self.timeouts[-1] == 0.0:
            raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
        raise TimeoutError("timed out")

    def close(self):
        if not self.closed:
            os.close(self._ready_r)
            os.close(self._ready_w)
        self.closed = True


@pytest.fixture
def fake_hub_socket():
    made: list[FakeMonitorSocket] = []

    def build(records, *, meminfo_drops=None):
        def open_socket():
            sock = FakeMonitorSocket(records, meminfo_drops=meminfo_drops)
            made.append(sock)
            return sock

        open_socket.made = made  # type: ignore[attr-defined]
        return open_socket

    return build


@pytest.fixture
def fake_hub_socket_failing_once():
    calls = {"n": 0}

    def open_socket():
        calls["n"] += 1
        return FakeMonitorSocket([], fail_first_read=calls["n"] == 1)

    return open_socket


def frame(opcode, index, payload):
    return struct.pack("<HHH", opcode, index, len(payload)) + payload


def consumer(wants=False):
    class C:
        wants_acl_tx = wants

        def __init__(self):
            self.seen, self.states = [], []

        def on_record(self, rec):
            self.seen.append(rec)

        def on_hub_state(self, state, reason):
            self.states.append((state, reason))

    return C()


def timestamp_cmsg(real_ns):
    sec, nsec = divmod(real_ns, 1_000_000_000)
    return (socket.SOL_SOCKET, hs.SCM_TIMESTAMPNS, struct.pack("qq", sec, nsec))


def test_records_reach_consumers_with_a_raw_time(fake_hub_socket):
    c = consumer()
    hub = HciMonitorHub(
        open_socket=fake_hub_socket([(frame(hs.MONITOR_ACL_RX_PKT, 1, b"\x46\x00\x04\x00abcd"), [])]),
        seed=lambda m: None,
    )
    hub.add_consumer(c)
    hub.start()
    hub.stop()
    assert [(r.opcode, r.index, r.timestamp) for r in c.seen] == [(hs.MONITOR_ACL_RX_PKT, 1, "recv")]
    assert c.seen[0].payload == b"\x46\x00\x04\x00abcd"
    assert c.seen[0].t_raw_us > 0


def test_acl_tx_only_to_consumers_that_want_it(fake_hub_socket):
    a, b = consumer(False), consumer(True)
    hub = HciMonitorHub(
        open_socket=fake_hub_socket([(frame(hs.MONITOR_ACL_TX_PKT, 0, b"\x46\x20\x00\x00"), [])]),
        seed=lambda m: None,
    )
    hub.add_consumer(a)
    hub.add_consumer(b)
    hub.start()
    hub.stop()
    assert (len(a.seen), len(b.seen)) == (0, 1)


def test_handles_are_per_controller(fake_hub_socket):
    def cc(handle, mac):
        params = bytes([0]) + struct.pack("<H", handle) + mac + bytes([1, 0])
        return bytes([0x03, len(params)]) + params

    recs = [
        (frame(hs.MONITOR_EVENT_PKT, 0, cc(70, bytes([1, 2, 3, 4, 5, 6]))), []),
        (frame(hs.MONITOR_EVENT_PKT, 1, cc(70, bytes([6, 5, 4, 3, 2, 1]))), []),
    ]
    hub = HciMonitorHub(open_socket=fake_hub_socket(recs), seed=lambda m: None)
    hub.start()
    hub.stop()
    assert hub.peer(0, 70) == "06:05:04:03:02:01" and hub.peer(1, 70) == "01:02:03:04:05:06"


def test_disconnection_complete_forgets_only_that_controllers_handle(fake_hub_socket):
    def dc(handle):
        params = bytes([0]) + struct.pack("<H", handle) + bytes([0x13])
        return bytes([0x05, len(params)]) + params

    def seed(peers):
        peers[(0, 70)] = "AA:AA:AA:AA:AA:AA"
        peers[(1, 70)] = "BB:BB:BB:BB:BB:BB"

    hub = HciMonitorHub(open_socket=fake_hub_socket([(frame(hs.MONITOR_EVENT_PKT, 1, dc(70)), [])]), seed=seed)
    hub.start()
    hub.stop()
    assert hub.peer(0, 70) == "AA:AA:AA:AA:AA:AA" and hub.peer(1, 70) is None


def test_new_index_gives_the_adapter_address(fake_hub_socket):
    payload = bytes([0, 1]) + bytes([0xD6, 0xD7, 0x62, 0xF9, 0xFB, 0xC0]) + b"hci1\0\0\0\0"
    hub = HciMonitorHub(
        open_socket=fake_hub_socket([(frame(hs.MONITOR_NEW_INDEX, 1, payload), [])]), seed=lambda m: None
    )
    hub.start()
    hub.stop()
    assert hub.adapters() == {1: "C0:FB:F9:62:D7:D6"}


def test_del_index_forgets_the_adapter_and_its_links(fake_hub_socket):
    payload = bytes([0, 1]) + bytes([0xD6, 0xD7, 0x62, 0xF9, 0xFB, 0xC0]) + b"hci1\0\0\0\0"

    def seed(peers):
        peers[(1, 70)] = "BB:BB:BB:BB:BB:BB"

    recs = [(frame(hs.MONITOR_NEW_INDEX, 1, payload), []), (frame(hs.MONITOR_DEL_INDEX, 1, b""), [])]
    hub = HciMonitorHub(open_socket=fake_hub_socket(recs), seed=seed)
    hub.start()
    hub.stop()
    assert hub.adapters() == {} and hub.peer(1, 70) is None


def test_seed_runs_on_open_and_keys_by_controller(fake_hub_socket):
    def seed(peers):
        peers[(1, 12)] = "11:22:33:44:55:66"

    hub = HciMonitorHub(open_socket=fake_hub_socket([]), seed=seed)
    hub.start()
    hub.stop()
    assert hub.peer(1, 12) == "11:22:33:44:55:66" and hub.peer(0, 12) is None
    assert hub.peers() == {(1, 12): "11:22:33:44:55:66"}


def test_permission_error_is_unavailable_with_reason():
    def deny():
        raise PermissionError(1, "Operation not permitted")

    c = consumer()
    hub = HciMonitorHub(open_socket=deny, seed=lambda m: None)
    hub.add_consumer(c)
    hub.start()
    hub.stop()
    assert not hub.available and hub.unavailable_reason == "no_cap_net_raw"
    assert ("error", "no_cap_net_raw") in c.states


def test_eacces_without_permission_error_type_is_no_cap_net_raw():
    def deny():
        raise OSError(errno.EACCES, "denied")

    hub = HciMonitorHub(open_socket=deny, seed=lambda m: None)
    hub.start()
    hub.stop()
    assert hub.unavailable_reason == "no_cap_net_raw"


def test_non_linux_is_unsupported_platform(monkeypatch):
    from sendspin_bridge.services.bluetooth import hci_monitor_hub as mod

    monkeypatch.setattr(mod.sys, "platform", "darwin")

    def no_linux():
        raise OSError("HCI raw sockets require Linux")

    hub = HciMonitorHub(open_socket=no_linux, seed=lambda m: None)
    hub.start()
    hub.stop()
    assert hub.unavailable_reason == "unsupported_platform"


def test_other_open_errors_retry_with_backoff():
    calls = {"n": 0}
    opened = threading.Event()

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError(errno.ENODEV, "no such device")
        opened.set()
        return FakeMonitorSocket([])

    c = consumer()
    hub = HciMonitorHub(open_socket=flaky, seed=lambda m: None, backoff_s=0.0)
    hub.add_consumer(c)
    hub.start()
    assert opened.wait(2)
    assert hub.wait_open(2)
    hub.stop()
    assert c.states[0] == ("error", "monitor_error")
    assert ("open", None) in c.states and hub.generation == 1


def test_socket_error_reopens_and_bumps_the_generation(fake_hub_socket_failing_once):
    c = consumer()
    hub = HciMonitorHub(open_socket=fake_hub_socket_failing_once, seed=lambda m: None, backoff_s=0.0)
    hub.add_consumer(c)
    hub.start()
    assert hub.wait_open(2)
    hub.stop()
    assert hub.generation == 2 and [s for s, _ in c.states].count("open") == 2
    assert ("error", "monitor_error") in c.states and c.states[-1] == ("closed", None)


def test_open_sets_timestamps_and_a_large_receive_buffer(fake_hub_socket):
    opener = fake_hub_socket([])
    hub = HciMonitorHub(open_socket=opener, seed=lambda m: None)
    hub.start()
    hub.stop()
    sock = opener.made[0]
    opts = {opt for _level, opt, _value in sock.setsockopt_calls}
    assert hs.SO_TIMESTAMPNS in opts and hs.SO_RXQ_OVFL in opts
    assert sock.timeouts == [0.0] and sock.closed  # non-blocking: reads drain until EAGAIN (R-20)


def test_meminfo_drops_are_attached_when_no_cmsg(fake_hub_socket):
    c = consumer()
    sock = fake_hub_socket([(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [])], meminfo_drops=5)
    hub = HciMonitorHub(open_socket=sock, seed=lambda m: None)
    hub.add_consumer(c)
    hub.start()
    hub.stop()
    assert c.seen[-1].drops == 5 and hub.counters()["drop_source"] == "meminfo"
    assert hub.counters()["drops"] == 5


def test_no_drop_source_means_unknown_not_zero(fake_hub_socket):
    c = consumer()
    hub = HciMonitorHub(
        open_socket=fake_hub_socket([(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [])]), seed=lambda m: None
    )
    hub.add_consumer(c)
    hub.start()
    hub.stop()
    assert c.seen[-1].drops is None and hub.counters()["drop_source"] == "none"


def test_a_failing_consumer_does_not_stop_the_others(fake_hub_socket):
    class Bad:
        wants_acl_tx = False

        def on_record(self, rec):
            raise RuntimeError("boom")

        def on_hub_state(self, s, r):
            pass

    good = consumer()
    hub = HciMonitorHub(
        open_socket=fake_hub_socket([(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [])] * 3), seed=lambda m: None
    )
    hub.add_consumer(Bad())
    hub.add_consumer(good)
    hub.start()
    hub.stop()
    assert len(good.seen) == 3


def test_removed_consumer_gets_nothing(fake_hub_socket):
    a, b = consumer(), consumer()
    hub = HciMonitorHub(
        open_socket=fake_hub_socket([(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [])]), seed=lambda m: None
    )
    hub.add_consumer(a)
    hub.add_consumer(b)
    hub.remove_consumer(a)
    hub.start()
    hub.stop()
    assert (len(a.seen), len(b.seen)) == (0, 1)


def test_a_consumer_added_while_open_is_told_on_the_hub_thread(fake_hub_socket):
    hub = HciMonitorHub(open_socket=fake_hub_socket([]), seed=lambda m: None)
    hub.start()
    assert hub.wait_open(2)
    late = consumer()
    seen_on: list[str] = []
    original = late.on_hub_state

    def on_state(state, reason):
        seen_on.append(threading.current_thread().name)
        original(state, reason)

    late.on_hub_state = on_state
    hub.add_consumer(late)
    hub.stop()
    assert late.states[0] == ("open", None) and seen_on[0] == "hci-monitor"


def test_start_is_idempotent_and_names_the_thread(fake_hub_socket):
    started: list[str] = []

    def thread_factory(*, target, name, daemon):
        started.append(name)
        return threading.Thread(target=target, name=name, daemon=daemon)

    hub = HciMonitorHub(open_socket=fake_hub_socket([]), seed=lambda m: None, thread_factory=thread_factory)
    hub.start()
    hub.start()
    hub.stop()
    hub.stop()
    assert started == ["hci-monitor"]


class _ScriptedClockMap:
    """Resolves a REALTIME stamp only after the hub has taken two samples (one after the record)."""

    def __init__(self, offset_us):
        self.samples = 0
        self.offset_us = offset_us
        self.epoch = 0

    def sample(self):
        self.samples += 1
        return True

    def to_raw_us(self, real_ns):
        if self.samples < 2:
            return None
        return real_ns / 1000.0 - self.offset_us


def test_kernel_stamps_wait_for_a_later_pair_and_keep_order(fake_hub_socket):
    ticks = iter(range(0, 10_000))

    def monotonic():
        # 0.3 s per call: every loop pass is a 250 ms tick.
        return next(ticks) * 0.3

    clock_map = _ScriptedClockMap(offset_us=1_000.0)
    stamp = 1_700_000_000_123_456_789
    recs = [
        (frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [timestamp_cmsg(stamp)]),
        (frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x01"), []),
    ]
    c = kernel_consumer()
    hub = HciMonitorHub(
        open_socket=fake_hub_socket(recs), seed=lambda m: None, clock_map=clock_map, monotonic=monotonic
    )
    hub.add_consumer(c)
    hub.start()
    hub.stop()
    assert [r.payload for r in c.seen] == [b"\x13\x00", b"\x13\x01"]
    assert c.seen[0].timestamp == "kernel"
    assert c.seen[0].t_raw_us == pytest.approx(stamp / 1000.0 - 1_000.0)
    assert c.seen[1].timestamp == "recv"


def test_an_unresolvable_kernel_stamp_falls_back_to_receive_time(fake_hub_socket):
    class NeverMap:
        epoch = 0

        def sample(self):
            return True

        def to_raw_us(self, real_ns):
            return None

    c = kernel_consumer()
    hub = HciMonitorHub(
        open_socket=fake_hub_socket([(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [timestamp_cmsg(10**18)])]),
        seed=lambda m: None,
        clock_map=NeverMap(),
    )
    hub.add_consumer(c)
    hub.start()
    hub.stop()
    assert [r.timestamp for r in c.seen] == ["recv"]
    assert hub.counters()["recv_stamped"] == 1


def test_the_clock_is_sampled_at_every_open(fake_hub_socket_failing_once):
    clock_map = _ScriptedClockMap(offset_us=0.0)
    hub = HciMonitorHub(
        open_socket=fake_hub_socket_failing_once, seed=lambda m: None, backoff_s=0.0, clock_map=clock_map
    )
    hub.start()
    assert hub.wait_open(2)
    hub.stop()
    assert clock_map.samples >= 2


def test_counters_report_records_and_streaming_links(fake_hub_socket):
    recs = [
        (frame(hs.MONITOR_ACL_TX_PKT, 0, struct.pack("<HH", 0x2046, 0)), []),
        (frame(hs.MONITOR_ACL_TX_PKT, 1, struct.pack("<HH", 0x2046, 0)), []),
        (frame(hs.MONITOR_ACL_TX_PKT, 1, struct.pack("<HH", 0x2046, 0)), []),
        (frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), []),
    ]
    hub = HciMonitorHub(open_socket=fake_hub_socket(recs), seed=lambda m: None)
    hub.start()
    hub.stop()
    counters = hub.counters()
    assert counters["records"] == 4 and counters["streaming_links"] == 2
    assert counters["cpu_fraction"] is None  # no 30 s window completed yet


def test_cpu_fraction_is_thread_time_over_wall_time_per_window(fake_hub_socket):
    wall = iter(range(0, 10_000))
    cpu = iter(range(0, 10_000))
    hub = HciMonitorHub(
        open_socket=fake_hub_socket([]),
        seed=lambda m: None,
        monotonic=lambda: next(wall) * 10.0,
        thread_time=lambda: next(cpu) * 0.1,
    )
    hub.start()
    hub.stop()
    fraction = hub.counters()["cpu_fraction"]
    assert isinstance(fraction, float) and 0.0 < fraction < 0.05


def test_cpu_windows_counts_completed_windows(fake_hub_socket):
    wall = iter(range(0, 10_000))
    cpu = iter(range(0, 10_000))
    hub = HciMonitorHub(
        open_socket=fake_hub_socket([]),
        seed=lambda m: None,
        monotonic=lambda: next(wall) * 10.0,
        thread_time=lambda: next(cpu) * 0.1,
    )
    assert hub.counters()["cpu_windows"] == 0
    hub.start()
    deadline = time.monotonic() + 5.0
    while hub.counters()["cpu_windows"] < 1 and time.monotonic() < deadline:
        time.sleep(0.01)
    hub.stop()
    assert hub.counters()["cpu_windows"] >= 1


def test_cpu_windows_is_zero_within_the_first_window(fake_hub_socket):
    wall = iter(range(0, 100_000))
    cpu = iter(range(0, 100_000))
    hub = HciMonitorHub(
        open_socket=fake_hub_socket([]),
        seed=lambda m: None,
        monotonic=lambda: min(next(wall), 20) * 0.3,
        thread_time=lambda: next(cpu) * 0.003,
    )
    hub.start()
    assert hub.wait_open(2)
    hub.stop()
    assert hub.counters()["cpu_windows"] == 0


def test_avrcp_consumer_routes_by_index_and_handle(fake_hub_socket, monkeypatch):
    from sendspin_bridge.services.bluetooth import hci_avrcp_monitor as m

    noted = []
    tracker = type("T", (), {"note_activity": lambda self, mac: noted.append(mac)})()
    c = m.AvrcpConsumer(tracker=tracker)
    c._conn[(1, 70)] = "AA:AA:AA:AA:AA:AA"
    c._conn[(0, 70)] = "BB:BB:BB:BB:BB:BB"
    avc = bytes([0x00, 0x11, 0x0E, 0x00, 0x48, 0x7C, 0x44, 0x00])  # play pressed
    acl = struct.pack("<HH", 70, 4 + len(avc)) + struct.pack("<HH", len(avc), 0x0041) + avc
    c.on_record(type("R", (), {"opcode": hs.MONITOR_ACL_RX_PKT, "index": 1, "payload": acl})())
    assert noted == ["AA:AA:AA:AA:AA:AA"]


def _cc_event(handle, bdaddr_le):
    params = bytes([0]) + struct.pack("<H", handle) + bdaddr_le + bytes([1, 0])
    return bytes([0x03, len(params)]) + params


def _play_press_acl(handle):
    avc = bytes([0x00, 0x11, 0x0E, 0x00, 0x48, 0x7C, 0x44, 0x00])
    return struct.pack("<HH", handle, 4 + len(avc)) + struct.pack("<HH", len(avc), 0x0041) + avc


def test_avrcp_consumer_on_the_hub_keeps_two_adapters_apart(fake_hub_socket):
    from sendspin_bridge.services.bluetooth import hci_avrcp_monitor as m

    noted: list[str] = []
    tracker = type("T", (), {"note_activity": lambda self, mac: noted.append(mac)})()
    recs = [
        (frame(hs.MONITOR_EVENT_PKT, 0, _cc_event(70, bytes([1, 2, 3, 4, 5, 6]))), []),
        (frame(hs.MONITOR_EVENT_PKT, 1, _cc_event(70, bytes([6, 5, 4, 3, 2, 1]))), []),
        (frame(hs.MONITOR_ACL_RX_PKT, 1, _play_press_acl(70)), []),
        (frame(hs.MONITOR_ACL_RX_PKT, 0, _play_press_acl(70)), []),
    ]
    hub = HciMonitorHub(open_socket=fake_hub_socket(recs), seed=lambda m: None)
    hub.add_consumer(m.AvrcpConsumer(tracker=tracker, peers=hub.peers))
    hub.start()
    hub.stop()
    assert noted == ["01:02:03:04:05:06", "06:05:04:03:02:01"]


def test_avrcp_consumer_takes_the_seeded_links_on_open(fake_hub_socket):
    from sendspin_bridge.services.bluetooth import hci_avrcp_monitor as m

    noted: list[str] = []
    tracker = type("T", (), {"note_activity": lambda self, mac: noted.append(mac)})()

    def seed(peers):
        peers[(1, 70)] = "CC:CC:CC:CC:CC:CC"

    hub = HciMonitorHub(
        open_socket=fake_hub_socket([(frame(hs.MONITOR_ACL_RX_PKT, 1, _play_press_acl(70)), [])]), seed=seed
    )
    hub.add_consumer(m.AvrcpConsumer(tracker=tracker, peers=hub.peers))
    hub.start()
    hub.stop()
    assert noted == ["CC:CC:CC:CC:CC:CC"]


def test_avrcp_consumer_does_not_want_acl_tx():
    from sendspin_bridge.services.bluetooth import hci_avrcp_monitor as m

    assert m.AvrcpConsumer(tracker=object()).wants_acl_tx is False


def test_get_hub_is_a_process_wide_singleton():
    from sendspin_bridge.services.bluetooth.hci_monitor_hub import get_hub

    assert get_hub() is get_hub()


# ---------------------------------------------------------------- fix round 1


def kernel_consumer():
    c = consumer(True)
    c.wants_kernel_time = True
    return c


class _FrozenClock:
    """Monotonic time that never advances: after the open tick no 250 ms tick ever comes."""

    def __call__(self):
        return 100.0


def test_a_quiet_bus_press_reaches_the_avrcp_consumer_without_waiting_for_a_tick(fake_hub_socket):
    from sendspin_bridge.services.bluetooth import hci_avrcp_monitor as m

    noted = threading.Event()
    tracker = type("T", (), {"note_activity": lambda self, mac: noted.set()})()

    def seed(peers):
        peers[(1, 70)] = "CC:CC:CC:CC:CC:CC"

    class NeverMap:
        epoch = 0

        def sample(self):
            return True

        def to_raw_us(self, real_ns):
            return None

    recs = [(frame(hs.MONITOR_ACL_RX_PKT, 1, _play_press_acl(70)), [timestamp_cmsg(10**18)])]
    hub = HciMonitorHub(open_socket=fake_hub_socket(recs), seed=seed, clock_map=NeverMap(), monotonic=_FrozenClock())
    hub.add_consumer(m.AvrcpConsumer(tracker=tracker, peers=hub.peers))
    hub.add_consumer(kernel_consumer())  # a kernel-time consumer keeps the record pending
    hub.start()
    try:
        assert noted.wait(1.0)
    finally:
        hub.stop()


def test_order_is_kept_per_consumer_on_both_paths(fake_hub_socket):
    ticks = iter(range(0, 10_000))
    clock_map = _ScriptedClockMap(offset_us=0.0)
    recs = [
        (frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [timestamp_cmsg(1_700_000_000_000_000_000)]),
        (frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x01"), []),
        (frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x02"), [timestamp_cmsg(1_700_000_000_000_100_000)]),
    ]
    plain, timed = consumer(), kernel_consumer()
    hub = HciMonitorHub(
        open_socket=fake_hub_socket(recs),
        seed=lambda m: None,
        clock_map=clock_map,
        monotonic=lambda: next(ticks) * 0.3,
    )
    hub.add_consumer(plain)
    hub.add_consumer(timed)
    hub.start()
    hub.stop()
    expected = [b"\x13\x00", b"\x13\x01", b"\x13\x02"]
    assert [r.payload for r in plain.seen] == expected
    assert [r.payload for r in timed.seen] == expected
    assert [r.timestamp for r in timed.seen] == ["kernel", "recv", "kernel"]
    assert {r.timestamp for r in plain.seen} == {"recv"}


def test_without_kernel_time_consumers_nothing_waits(fake_hub_socket):
    got = threading.Event()
    plain = consumer()
    plain.on_record = lambda rec: got.set()
    recs = [(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [timestamp_cmsg(10**18)])]
    opener = fake_hub_socket(recs)
    hub = HciMonitorHub(open_socket=opener, seed=lambda m: None, monotonic=_FrozenClock())
    hub.add_consumer(plain)
    hub.start()
    try:
        assert got.wait(1.0)
    finally:
        hub.stop()
    assert opener.made[0].timeouts == [0.0] and hub.counters()["recv_stamped"] == 0


def recording_waiter(waits):
    """The hub's default readability wait, recording each timeout and its result."""
    from sendspin_bridge.services.bluetooth.hci_monitor_hub import _poll_waiter

    def factory(sock, wake_fd):
        wait = _poll_waiter(sock, wake_fd)

        def recorded(timeout_s):
            ready = wait(timeout_s)
            waits.append((timeout_s, ready))
            return ready

        return recorded

    return factory


def test_the_readability_wait_is_bounded_by_the_next_tick(fake_hub_socket):
    class NeverMap:
        epoch = 0

        def sample(self):
            return True

        def to_raw_us(self, real_ns):
            return None

    waits: list[tuple[float, bool]] = []
    recs = [(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [timestamp_cmsg(10**18)])]
    hub = HciMonitorHub(
        open_socket=fake_hub_socket(recs),
        seed=lambda m: None,
        clock_map=NeverMap(),
        readable_waiter=recording_waiter(waits),
    )
    hub.add_consumer(kernel_consumer())
    hub.start()
    assert hub.wait_open(2)
    time.sleep(0.6)  # at least two idle waits
    hub.stop()
    idle = [t for t, ready in waits if not ready and t > 0]
    assert len(idle) >= 2 and all(t <= 0.25 + 1e-6 for t in idle)


def test_records_carry_the_clock_epoch(fake_hub_socket):
    ticks = iter(range(0, 10_000))
    clock_map = _ScriptedClockMap(offset_us=0.0)
    clock_map.epoch = 3
    recs = [
        (frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [timestamp_cmsg(1_700_000_000_000_000_000)]),
        (frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x01"), []),
    ]
    plain, timed = consumer(), kernel_consumer()
    hub = HciMonitorHub(
        open_socket=fake_hub_socket(recs),
        seed=lambda m: None,
        clock_map=clock_map,
        monotonic=lambda: next(ticks) * 0.3,
    )
    hub.add_consumer(plain)
    hub.add_consumer(timed)
    hub.start()
    hub.stop()
    assert [r.clock_epoch for r in timed.seen] == [3, 3]
    assert [r.clock_epoch for r in plain.seen] == [3, 3]


def test_cpu_fraction_reports_within_the_first_window(fake_hub_socket):
    wall = iter(range(0, 100_000))
    cpu = iter(range(0, 100_000))
    hub = HciMonitorHub(
        open_socket=fake_hub_socket([]),
        seed=lambda m: None,
        monotonic=lambda: min(next(wall), 20) * 0.3,  # never reaches the end of a 30 s window
        thread_time=lambda: next(cpu) * 0.003,
    )
    hub.start()
    assert hub.wait_open(2)
    deadline = time.monotonic() + 5.0
    while hub.counters()["cpu_fraction"] is None and time.monotonic() < deadline:
        time.sleep(0.01)
    hub.stop()
    fraction = hub.counters()["cpu_fraction"]
    assert isinstance(fraction, float) and 0.0 < fraction < 0.05


def test_an_unexpected_error_while_serving_reopens_with_monitor_error(fake_hub_socket):
    class ExplodingOnce:
        epoch = 0

        def __init__(self):
            self.calls = 0

        def sample(self):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("unexpected")
            return True

        def to_raw_us(self, real_ns):
            return None

    c = consumer()
    hub = HciMonitorHub(open_socket=fake_hub_socket([]), seed=lambda m: None, clock_map=ExplodingOnce(), backoff_s=0.0)
    hub.add_consumer(c)
    hub.start()
    assert hub.wait_open(2)
    hub.stop()
    assert ("error", "monitor_error") in c.states and hub.generation == 2


def test_avrcp_consumer_forgets_links_of_a_removed_controller():
    from sendspin_bridge.services.bluetooth import hci_avrcp_monitor as m

    noted: list[str] = []
    tracker = type("T", (), {"note_activity": lambda self, mac: noted.append(mac)})()
    c = m.AvrcpConsumer(tracker=tracker)
    c._conn[(1, 70)] = "AA:AA:AA:AA:AA:AA"
    c._conn[(0, 70)] = "BB:BB:BB:BB:BB:BB"
    rec = type("R", (), {"opcode": hs.MONITOR_DEL_INDEX, "index": 1, "payload": b""})()
    c.on_record(rec)
    assert c._conn == {(0, 70): "BB:BB:BB:BB:BB:BB"}


# ---------------------------------------------------------------- R-19: routing by what consumers use


def _mixed_records():
    return [
        (frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x05\x01\x46\x00\x01\x00"), []),  # NOCP
        (frame(hs.MONITOR_ACL_RX_PKT, 0, b"\x46\x00\x04\x00abcd"), []),
        (frame(hs.MONITOR_EVENT_PKT, 0, b"\x3e\x02\x02\x00"), []),  # LE meta
        (frame(hs.MONITOR_ACL_TX_PKT, 0, b"\x46\x20\x00\x00"), []),
        (frame(hs.MONITOR_EVENT_PKT, 0, b"\x05\x04\x00\x46\x00\x13"), []),  # Disconnection Complete
    ]


@pytest.mark.parametrize("timed", [False, True])
def test_a_consumer_declaring_opcodes_and_event_codes_gets_only_those(fake_hub_socket, timed):
    picky = kernel_consumer() if timed else consumer(True)
    picky.opcodes = frozenset({hs.MONITOR_EVENT_PKT, hs.MONITOR_ACL_TX_PKT})
    picky.event_codes = frozenset({0x13, 0x05})
    everything = kernel_consumer() if timed else consumer(True)
    hub = HciMonitorHub(open_socket=fake_hub_socket(_mixed_records()), seed=lambda m: None)
    hub.add_consumer(picky)
    hub.add_consumer(everything)
    hub.start()
    hub.stop()
    assert [(r.opcode, r.payload[0]) for r in picky.seen] == [
        (hs.MONITOR_EVENT_PKT, 0x13),
        (hs.MONITOR_ACL_TX_PKT, 0x46),
        (hs.MONITOR_EVENT_PKT, 0x05),
    ]
    assert len(everything.seen) == 5


def test_opcodes_do_not_override_wants_acl_tx(fake_hub_socket):
    c = consumer(False)
    c.opcodes = frozenset({hs.MONITOR_ACL_TX_PKT, hs.MONITOR_ACL_RX_PKT})
    hub = HciMonitorHub(open_socket=fake_hub_socket(_mixed_records()), seed=lambda m: None)
    hub.add_consumer(c)
    hub.start()
    hub.stop()
    assert [r.opcode for r in c.seen] == [hs.MONITOR_ACL_RX_PKT]


def test_records_nobody_uses_still_update_the_link_maps(fake_hub_socket):
    c = consumer(True)
    c.opcodes = frozenset({hs.MONITOR_ACL_RX_PKT})
    hub = HciMonitorHub(open_socket=fake_hub_socket(_mixed_records()), seed=lambda m: m.update({(0, 0x46): "AA"}))
    hub.add_consumer(c)
    hub.start()
    hub.stop()
    assert hub.peers() == {}  # the Disconnection Complete nobody consumes still removed the link
    assert hub.counters()["records"] == 5
    assert [r.opcode for r in c.seen] == [hs.MONITOR_ACL_RX_PKT]


def test_avrcp_consumer_declares_what_it_reads():
    from sendspin_bridge.services.bluetooth import hci_avrcp_monitor as m

    c = m.AvrcpConsumer(tracker=object())
    assert c.opcodes == frozenset({hs.MONITOR_EVENT_PKT, hs.MONITOR_ACL_RX_PKT, hs.MONITOR_DEL_INDEX})
    assert c.event_codes == frozenset({0x03, 0x05})  # Connection / Disconnection Complete


def _rxq(drops):
    return (socket.SOL_SOCKET, hs.SO_RXQ_OVFL, struct.pack("I", drops))


@pytest.mark.parametrize("timed", [False, True])
def test_a_drop_change_reaches_a_consumer_that_skips_the_record_carrying_it(fake_hub_socket, timed):
    picky = kernel_consumer() if timed else consumer(True)
    picky.opcodes = frozenset({hs.MONITOR_ACL_TX_PKT})
    advert = frame(hs.MONITOR_EVENT_PKT, 0, b"\x3e\x02\x02\x00")
    recs = [
        (frame(hs.MONITOR_ACL_TX_PKT, 0, b"\x46\x20\x00\x00"), [_rxq(0)]),
        (advert, [_rxq(0)]),  # skipped
        (advert, [_rxq(1)]),  # carries the drop: delivered
        (advert, [_rxq(1)]),  # skipped again
        (frame(hs.MONITOR_ACL_RX_PKT, 0, b"\x46\x00\x04\x00abcd"), [_rxq(2)]),  # delivered
    ]
    hub = HciMonitorHub(open_socket=fake_hub_socket(recs), seed=lambda m: None)
    hub.add_consumer(picky)
    hub.start()
    hub.stop()
    assert [(r.opcode, r.drops) for r in picky.seen] == [
        (hs.MONITOR_ACL_TX_PKT, 0),
        (hs.MONITOR_EVENT_PKT, 1),
        (hs.MONITOR_ACL_RX_PKT, 2),
    ]


def test_repeated_monitor_errors_warn_once_per_ten_minutes(caplog):
    calls = {"n": 0}
    opened = threading.Event()

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 4:
            raise OSError(errno.ENODEV, "no such device")
        opened.set()
        return FakeMonitorSocket([])

    hub = HciMonitorHub(open_socket=flaky, seed=lambda m: None, backoff_s=0.0)
    with caplog.at_level("DEBUG", logger="sendspin_bridge.services.bluetooth.hci_monitor_hub"):
        hub.start()
        assert opened.wait(2)
        hub.stop()
    retry = [r for r in caplog.records if "retrying" in r.getMessage()]
    assert len(retry) == 4
    assert [r.levelname for r in retry] == ["WARNING", "DEBUG", "DEBUG", "DEBUG"]


# ---------------------------------------------------------------- R-20: batched reads


class EndlessMonitorSocket(FakeMonitorSocket):
    """Always readable; every read returns the next numbered record (continuous traffic)."""

    def __init__(self):
        super().__init__([])
        os.write(self._ready_w, b"r")
        self.reads = 0

    def _sync_ready(self):
        pass

    def recvmsg(self, bufsize, ancbufsize=0):
        if self.closed:
            raise OSError(errno.EBADF, "closed")
        self.reads += 1
        return frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00" + struct.pack("<I", self.reads)), [], 0, None


def test_one_wakeup_drains_every_queued_datagram_in_order(fake_hub_socket):
    waits: list[tuple[float, bool]] = []
    seen_at: list[tuple[bytes, int]] = []
    c = consumer()
    c.on_record = lambda rec: seen_at.append((rec.payload, sum(1 for _t, ready in waits if ready)))
    recs = [(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13" + bytes([i])), []) for i in range(10)]
    hub = HciMonitorHub(open_socket=fake_hub_socket(recs), seed=lambda m: None, readable_waiter=recording_waiter(waits))
    hub.add_consumer(c)
    hub.start()
    hub.stop()
    assert [p for p, _ in seen_at] == [b"\x13" + bytes([i]) for i in range(10)]
    assert {n for _, n in seen_at} == {1}  # all ten read after the first readable wakeup


def test_a_drain_stops_at_its_bound_and_the_rest_follows_in_order(fake_hub_socket):
    passes = {"n": 0}

    def monotonic():  # the reader calls it once per loop pass (event records never ask for the time)
        if threading.current_thread().name == "hci-monitor":
            passes["n"] += 1
        return time.monotonic()

    seen: list[tuple[bytes, int]] = []
    c = consumer()
    c.on_record = lambda rec: seen.append((rec.payload, passes["n"]))
    recs = [(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00" + struct.pack("<H", i)), []) for i in range(25)]
    hub = HciMonitorHub(open_socket=fake_hub_socket(recs), seed=lambda m: None, monotonic=monotonic, drain_max=10)
    hub.add_consumer(c)
    hub.start()
    assert hub.wait_open(2)
    hub.stop()
    assert [p for p, _ in seen] == [b"\x13\x00" + struct.pack("<H", i) for i in range(25)]
    per_pass = [n for _, n in seen]
    sizes = [per_pass.count(n) for n in sorted(set(per_pass))]
    assert sizes == [10, 10, 5]


def test_ticks_keep_their_schedule_under_continuous_traffic():
    class CountingMap:
        epoch = 0

        def __init__(self):
            self.samples = 0

        def sample(self):
            self.samples += 1
            return True

        def to_raw_us(self, real_ns):
            return None

    sock = EndlessMonitorSocket()
    clock_map = CountingMap()
    hub = HciMonitorHub(open_socket=lambda: sock, seed=lambda m: None, clock_map=clock_map, drain_max=64)
    hub.start()
    time.sleep(0.9)
    samples = clock_map.samples
    hub.stop(timeout=0.2)
    assert samples >= 3  # the open sample and at least two 250 ms ticks
    assert sock.reads > 64


def test_stop_cuts_the_batching_sleep_short_and_still_delivers_what_was_queued(fake_hub_socket):
    c = consumer()
    recs = [(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13" + bytes([i])), []) for i in range(3)]
    hub = HciMonitorHub(open_socket=fake_hub_socket(recs), seed=lambda m: None, batch_s=5.0)
    hub.add_consumer(c)
    hub.start()
    time.sleep(0.1)  # the reader is now in its 5 s batching sleep
    t0 = time.monotonic()
    hub.stop()
    assert time.monotonic() - t0 < 1.0
    assert [r.payload for r in c.seen] == [b"\x13" + bytes([i]) for i in range(3)]


def test_stop_on_a_quiet_bus_is_prompt(fake_hub_socket):
    from sendspin_bridge.services.bluetooth.hci_monitor_hub import _poll_waiter

    def stretched(sock, wake_fd):
        wait = _poll_waiter(sock, wake_fd)
        return lambda timeout_s: wait(timeout_s * 40)  # every readability wait now lasts up to 10 s

    hub = HciMonitorHub(open_socket=fake_hub_socket([]), seed=lambda m: None, readable_waiter=stretched)
    hub.start()
    assert hub.wait_open(2)
    time.sleep(0.05)  # the reader is now in a long readability wait
    t0 = time.monotonic()
    hub.stop()
    assert time.monotonic() - t0 < 1.0  # stop() wakes the wait instead of letting it run out


def _recording_pipe(monkeypatch):
    from sendspin_bridge.services.bluetooth import hci_monitor_hub as mod

    made: list[tuple[int, int]] = []
    real_pipe = os.pipe

    def pipe():
        fds = real_pipe()
        made.append(fds)
        return fds

    monkeypatch.setattr(mod.os, "pipe", pipe)
    return mod, made


def _is_closed(fd):
    try:
        os.fstat(fd)
    except OSError as exc:
        return exc.errno == errno.EBADF
    return False


def test_the_wake_pipe_is_closed_when_making_it_non_blocking_fails(monkeypatch):
    mod, made = _recording_pipe(monkeypatch)

    def refuse(fd, blocking):
        raise OSError(errno.EINVAL, "no")

    monkeypatch.setattr(mod.os, "set_blocking", refuse)
    hub = HciMonitorHub(open_socket=lambda: None, seed=lambda m: None)
    with pytest.raises(OSError):
        hub._serve(object())
    assert made and all(_is_closed(fd) for fd in made[0])


def test_the_wake_pipe_read_end_is_closed_even_if_closing_the_write_end_fails(monkeypatch):
    mod, made = _recording_pipe(monkeypatch)
    real_close = os.close

    def close(fd):
        real_close(fd)
        if made and fd == made[0][1]:
            raise OSError(errno.EIO, "close failed")

    monkeypatch.setattr(mod.os, "close", close)
    hub = HciMonitorHub(open_socket=lambda: None, seed=lambda m: None)
    monkeypatch.setattr(hub, "_serve_open", lambda sock, wake_fd: None)
    with pytest.raises(OSError):
        hub._serve(object())
    assert made and _is_closed(made[0][0])


def test_records_arriving_together_are_read_after_the_batching_interval(fake_hub_socket):
    got = threading.Event()
    c = consumer()
    c.on_record = lambda rec: got.set()
    recs = [(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00"), [])]
    hub = HciMonitorHub(open_socket=fake_hub_socket(recs), seed=lambda m: None, batch_s=0.3)
    hub.add_consumer(c)
    t0 = time.monotonic()
    hub.start()
    try:
        assert got.wait(2)
        assert time.monotonic() - t0 >= 0.25
    finally:
        hub.stop()


def test_the_default_batching_interval_keeps_avrcp_latency_small():
    from sendspin_bridge.services.bluetooth import hci_monitor_hub as mod

    assert 0.0 < mod._BATCH_S <= 0.05  # AVRCP correlation tolerates <= 50 ms of extra latency


# ---------------------------------------------------------------- Python 3.14


class NoRecvmsgMonitorSocket(FakeMonitorSocket):
    """Python 3.14: recvmsg answers "getsockaddrlen: bad family"; plain recv works."""

    def recvmsg(self, bufsize, ancbufsize=0):
        raise OSError(errno.EAFNOSUPPORT, "getsockaddrlen: bad family")

    def recv(self, bufsize):
        if self.closed:
            raise OSError(errno.EBADF, "closed")
        if self._records:
            item = self._records.pop(0)
            self._sync_ready()
            return item[0] if isinstance(item, tuple) else item
        if self.timeouts and self.timeouts[-1] == 0.0:
            raise BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable")
        raise TimeoutError("timed out")


def test_a_python_without_recvmsg_on_bluetooth_falls_back_to_recv(caplog):
    """Python 3.14 broke recvmsg for AF_BLUETOOTH; the hub must keep working.

    Without recvmsg there is no SCM_TIMESTAMPNS, so the records are stamped with
    the receive time (design R-3) instead of the kernel's microsecond time.
    """
    recs = [(frame(hs.MONITOR_EVENT_PKT, 0, b"\x13\x00" + struct.pack("<H", i)), []) for i in range(3)]
    caplog.set_level(logging.WARNING, logger="sendspin_bridge.services.bluetooth.hci_monitor_hub")
    c = consumer()
    hub = HciMonitorHub(open_socket=lambda: NoRecvmsgMonitorSocket(recs), seed=lambda m: None)
    hub.add_consumer(c)
    hub.start()
    assert hub.wait_open(2)
    hub.stop()

    assert [rec.payload for rec in c.seen] == [b"\x13\x00" + struct.pack("<H", i) for i in range(3)]
    assert all(rec.timestamp == "recv" for rec in c.seen)
    assert any("receive time" in record.getMessage() for record in caplog.records)
