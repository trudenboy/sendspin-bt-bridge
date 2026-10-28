"""HciMonitorHub: one reader thread owns the HCI monitor socket (only with ``EXPERIMENTAL_AIR_SENSOR``).

With the switch on, the hub replaces the asyncio ``HciAvrcpMonitor`` as the owner of the single
``HCI_CHANNEL_MONITOR`` socket (spec R-1). Every record is stamped in ``CLOCK_MONOTONIC_RAW`` µs and
handed to the registered consumers on the hub thread; AVRCP source correlation is one consumer
(``AvrcpConsumer``), the air sensor will be another.

Timestamps: the kernel stamps each monitor frame in ``CLOCK_REALTIME`` (``SO_TIMESTAMPNS``). A
stamped record waits (≤ 250 ms, in order) until a later ``RawRealtimeMap`` pair brackets it; a
record without a stamp, or one no pair can bracket, gets the RAW time at ``recvmsg`` return and
``timestamp="recv"`` (R-3). The map is sampled at every (re)open and then every 250 ms.

Drops: the ``SO_RXQ_OVFL`` cmsg when the kernel sends one, else ``SO_MEMINFO`` polled every
250 ms (the only path seen on the monitor channel so far). ``drops=None`` means unknown, never zero.

Handles are kept per controller: ``(index, handle) → peer MAC`` (R-2), seeded from the kernel's
connection list at every open and maintained from Connection/Disconnection Complete events;
``index → adapter MAC`` comes from ``NEW_INDEX``/``DEL_INDEX``. Raw records are never logged.
"""

from __future__ import annotations

import collections
import errno
import logging
import struct
import sys
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol

from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import (
    _HCI_EVT_CONNECTION_COMPLETE,
    _HCI_EVT_DISCONNECT_COMPLETE,
    _parse_connection_complete,
    _parse_disconnect_complete,
    _seed_peer_map_from_kernel,
)
from sendspin_bridge.services.bluetooth.hci_clock import RawRealtimeMap, _raw_ns
from sendspin_bridge.services.bluetooth.hci_socket import (
    HCI_CHANNEL_MONITOR,
    HCI_DEV_NONE,
    MONITOR_ACL_TX_PKT,
    MONITOR_DEL_INDEX,
    MONITOR_EVENT_PKT,
    MONITOR_NEW_INDEX,
    DropCounter,
    enable_monitor_sockopts,
    open_hci_socket,
    parse_monitor_cmsgs,
    parse_monitor_packet,
    read_meminfo_drops,
)

if TYPE_CHECKING:
    import socket
    from collections.abc import Callable

logger = logging.getLogger(__name__)

HubState = Literal["open", "error", "closed"]
UnavailableReason = Literal["no_cap_net_raw", "unsupported_platform", "monitor_error"]

_RECV_BUFSIZE = 4096
_ANC_BUFSIZE = 256
_SOCKET_TIMEOUT_S = 0.5
_TICK_S = 0.25  # clock pair + SO_MEMINFO poll cadence
_PENDING_MAX_AGE_US = 250_000.0  # a kernel-stamped record waits at most this long for a bracketing pair
_PENDING_MAX = 8192  # bound on records waiting for a pair (≈ 10 s of a busy monitor)
_CPU_WINDOW_S = 30.0
_STREAMING_WINDOW_S = 30.0
_CONSUMER_LOG_INTERVAL_S = 600.0
_BACKOFF_BASE_S = 5.0
_BACKOFF_MAX_S = 60.0
_BACKOFF_FACTOR = 2.0


@dataclass(frozen=True, slots=True)
class HubRecord:
    t_raw_us: float
    index: int
    opcode: int
    payload: bytes
    drops: int | None
    timestamp: Literal["kernel", "recv"]


class HubConsumer(Protocol):
    wants_acl_tx: bool

    def on_record(self, rec: HubRecord) -> None: ...  # hub thread; must not block

    def on_hub_state(self, state: HubState, reason: str | None) -> None: ...  # hub thread


def _open_monitor_socket() -> socket.socket:
    """Open the monitor socket; an OSError carries the kernel errno so the hub can classify it."""
    import ctypes

    try:
        return open_hci_socket(hci_dev=HCI_DEV_NONE, channel=HCI_CHANNEL_MONITOR)
    except OSError as exc:
        # open_hci_socket reports libc failures as text only; the errno libc left is still in
        # ctypes' per-thread copy (the cleanup close in its error path does not go through ctypes).
        err = ctypes.get_errno() if exc.errno is None and sys.platform == "linux" else 0
        if err:
            raise OSError(err, str(exc)) from exc
        raise


def _default_drop_counter(sock: socket.socket) -> DropCounter:
    return DropCounter(poll=lambda: read_meminfo_drops(sock))


def _classify_open_error(exc: OSError) -> UnavailableReason:
    if sys.platform != "linux":
        return "unsupported_platform"
    if isinstance(exc, PermissionError) or exc.errno in (errno.EPERM, errno.EACCES):
        return "no_cap_net_raw"
    return "monitor_error"


def _mac(bdaddr_le: bytes) -> str:
    return ":".join(f"{b:02X}" for b in reversed(bdaddr_le))


class _Pending:
    __slots__ = ("drops", "index", "opcode", "payload", "real_ns", "t_recv_us")

    def __init__(
        self, real_ns: int | None, opcode: int, index: int, payload: bytes, drops: int | None, t_recv_us: float
    ) -> None:
        self.real_ns = real_ns
        self.opcode = opcode
        self.index = index
        self.payload = payload
        self.drops = drops
        self.t_recv_us = t_recv_us


class HciMonitorHub:
    """Reader thread ``hci-monitor`` owning the monitor socket; fans records out to consumers."""

    def __init__(
        self,
        *,
        open_socket: Callable[[], Any] = _open_monitor_socket,
        seed: Callable[[dict[tuple[int, int], str]], None] = _seed_peer_map_from_kernel,
        clock_map: RawRealtimeMap | None = None,
        drop_counter_factory: Callable[[Any], DropCounter] = _default_drop_counter,
        thread_factory: Callable[..., threading.Thread] = threading.Thread,
        backoff_s: float = _BACKOFF_BASE_S,
        raw_ns: Callable[[], int] = _raw_ns,
        monotonic: Callable[[], float] = time.monotonic,
        thread_time: Callable[[], float] = time.thread_time,
    ) -> None:
        self._open_socket = open_socket
        self._seed = seed
        self._clock_map = clock_map if clock_map is not None else RawRealtimeMap()
        self._drop_counter_factory = drop_counter_factory
        self._thread_factory = thread_factory
        self._backoff_base = backoff_s
        self._raw_ns = raw_ns
        self._monotonic = monotonic
        self._thread_time = thread_time

        self._lock = threading.Lock()
        self._consumers: tuple[HubConsumer, ...] = ()
        self._greet: list[HubConsumer] = []
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._stop_deadline: float | None = None
        self._open_event = threading.Event()

        self._is_open = False
        self._reason: UnavailableReason | None = None
        self._generation = 0
        self._adapters: dict[int, str] = {}
        self._peers: dict[tuple[int, int], str] = {}

        self._pending: collections.deque[_Pending] = collections.deque()
        self._drop_counter: DropCounter | None = None
        self._drop_source: str = "none"
        self._drops: int | None = None
        self._records = 0
        self._recv_stamped = 0
        self._clock_epoch0 = self._clock_map.epoch
        self._tx_links: dict[tuple[int, int], float] = {}
        self._cpu_fraction: float | None = None
        self._window_start: tuple[float, float] | None = None
        self._next_tick = 0.0
        self._consumer_errors: dict[int, float] = {}

    # ------------------------------------------------------------------ consumers

    def add_consumer(self, c: HubConsumer) -> None:
        with self._lock:
            if c in self._consumers:
                return
            self._consumers = (*self._consumers, c)
            if self._is_open:
                # Told "open" on the hub thread, like every other state callback.
                self._greet.append(c)

    def remove_consumer(self, c: HubConsumer) -> None:
        with self._lock:
            self._consumers = tuple(x for x in self._consumers if x is not c)
            self._greet = [x for x in self._greet if x is not c]

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        """Start the reader thread (idempotent)."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._stop_deadline = None
            self._open_event.clear()
            thread = self._thread_factory(target=self._run, name="hci-monitor", daemon=True)
            self._thread = thread
        thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Stop the reader: it drains what is already queued (≤ *timeout*), closes the socket and exits."""
        with self._lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_deadline = self._monotonic() + timeout
            self._stop.set()
        thread.join(timeout + 0.5)
        if thread.is_alive():
            logger.warning("HciMonitorHub: reader thread did not stop within %.1fs", timeout)
        with self._lock:
            if self._thread is thread:
                self._thread = None

    def wait_open(self, timeout: float) -> bool:
        """Block until the socket is open and its first read returned (a record or the idle timeout)."""
        return self._open_event.wait(timeout)

    # ------------------------------------------------------------------ queries

    @property
    def available(self) -> bool:
        return self._is_open

    @property
    def unavailable_reason(self) -> str | None:
        return self._reason

    @property
    def generation(self) -> int:
        return self._generation

    def adapters(self) -> dict[int, str]:
        with self._lock:
            return dict(self._adapters)

    def peer(self, index: int, handle: int) -> str | None:
        with self._lock:
            return self._peers.get((index, handle))

    def peers(self) -> dict[tuple[int, int], str]:
        with self._lock:
            return dict(self._peers)

    def counters(self) -> dict[str, float | int | str | None]:
        now = self._monotonic()
        with self._lock:
            streaming = sum(1 for t in self._tx_links.values() if now - t <= _STREAMING_WINDOW_S)
            return {
                "records": self._records,
                "drops": self._drops,
                "drop_source": self._drop_source,
                "clock_steps": self._clock_map.epoch - self._clock_epoch0,
                "cpu_fraction": self._cpu_fraction,
                "streaming_links": streaming,
                "recv_stamped": self._recv_stamped,
                "generation": self._generation,
            }

    # ------------------------------------------------------------------ thread

    def _run(self) -> None:
        backoff = self._backoff_base
        first = True
        try:
            while first or not self._stop.is_set():
                first = False
                try:
                    sock = self._open_socket()
                except OSError as exc:
                    reason = _classify_open_error(exc)
                    self._fail(reason, f"cannot open HCI monitor socket ({exc})")
                    if reason != "monitor_error" or self._stop.wait(backoff):
                        return
                    backoff = min(backoff * _BACKOFF_FACTOR, _BACKOFF_MAX_S)
                    continue
                backoff = self._backoff_base
                try:
                    self._serve(sock)
                    return
                except (OSError, ValueError, TypeError) as exc:
                    self._flush_pending(force=True)  # records already read still reach the consumers
                    self._fail("monitor_error", f"socket error ({exc})")
                finally:
                    self._close(sock)
                if self._stop.wait(backoff):
                    return
                backoff = min(backoff * _BACKOFF_FACTOR, _BACKOFF_MAX_S)
        finally:
            with self._lock:
                self._is_open = False
            self._notify_state("closed", None)

    def _close(self, sock: Any) -> None:
        with self._lock:
            self._is_open = False
        self._open_event.clear()
        try:
            sock.close()
        except OSError:
            pass

    def _fail(self, reason: UnavailableReason, detail: str) -> None:
        with self._lock:
            self._is_open = False
            self._reason = reason
        if reason == "monitor_error":
            logger.warning("HciMonitorHub: %s — retrying", detail)
        else:
            logger.info("HciMonitorHub: %s — monitor unavailable (%s)", detail, reason)
        self._notify_state("error", reason)

    def _serve(self, sock: Any) -> None:
        opts = enable_monitor_sockopts(sock)
        sock.settimeout(_SOCKET_TIMEOUT_S)
        drop_counter = self._drop_counter_factory(sock)
        seeded: dict[tuple[int, int], str] = {}
        try:
            self._seed(seeded)
        except Exception:
            logger.debug("HciMonitorHub: kernel connection seed failed", exc_info=True)
        self._pending.clear()
        with self._lock:
            self._drop_counter = drop_counter
            self._adapters.clear()  # the kernel replays NEW_INDEX on bind
            self._peers = seeded
            self._generation += 1
            self._is_open = True
            self._reason = None
            self._greet.clear()
            consumers = self._consumers
        # Resample at once: a long pause (reopen backoff) may have moved the clock offset.
        self._tick(self._monotonic())
        logger.info(
            "HciMonitorHub: HCI_CHANNEL_MONITOR open (generation %d, %d seeded links, timestamps %s)",
            self._generation,
            len(seeded),
            "on" if opts.get("timestamps") else "off",
        )
        for c in consumers:
            self._call_state(c, "open", None)

        while True:
            self._greet_new_consumers()
            now = self._monotonic()
            if now >= self._next_tick:
                self._tick(now)
            if self._stopping_past_deadline(now):
                self._finish_pending()
                return
            try:
                data, ancdata, _flags, _addr = sock.recvmsg(_RECV_BUFSIZE, _ANC_BUFSIZE)
            except TimeoutError:
                self._open_event.set()
                if self._stop.is_set():
                    self._finish_pending()
                    return
                continue
            self._open_event.set()
            self._on_datagram(data, ancdata)

    def _stopping_past_deadline(self, now: float) -> bool:
        deadline = self._stop_deadline
        return self._stop.is_set() and deadline is not None and now > deadline

    def _greet_new_consumers(self) -> None:
        if not self._greet:
            return
        with self._lock:
            greet, self._greet = self._greet, []
        for c in greet:
            self._call_state(c, "open", None)

    def _tick(self, now: float) -> None:
        self._next_tick = now + _TICK_S
        drop_counter = self._drop_counter
        if drop_counter is not None:
            drop_counter.tick()
            self._drops, self._drop_source = drop_counter.drops, drop_counter.source
        self._clock_map.sample()
        self._flush_pending(force=False)
        cpu = self._thread_time()
        if self._window_start is None:
            self._window_start = (now, cpu)
        elif now - self._window_start[0] >= _CPU_WINDOW_S:
            wall0, cpu0 = self._window_start
            self._cpu_fraction = max(0.0, cpu - cpu0) / (now - wall0)
            self._window_start = (now, cpu)

    def _on_datagram(self, data: bytes, ancdata: list[tuple[int, int, bytes]]) -> None:
        t_recv_us = self._raw_ns() / 1000.0
        frame = parse_monitor_packet(data)
        if frame is None:
            return
        opcode, index, payload = frame
        real_ns, rxq_drops = parse_monitor_cmsgs(ancdata)
        drop_counter = self._drop_counter
        if drop_counter is not None and rxq_drops is not None:
            drop_counter.on_cmsg(rxq_drops)
            self._drops, self._drop_source = drop_counter.drops, drop_counter.source
        self._update_maps(opcode, index, payload)
        self._records += 1
        entry = _Pending(real_ns, opcode, index, payload, self._drops, t_recv_us)
        if not self._pending and real_ns is None:
            self._deliver(entry, None)
            return
        self._pending.append(entry)
        if len(self._pending) > _PENDING_MAX:
            self._deliver(self._pending.popleft(), None)

    def _flush_pending(self, *, force: bool) -> None:
        """Deliver waiting records in order: bracketed → kernel time; stale, unstamped or forced → recv."""
        pending = self._pending
        if not pending:
            return
        now_us = self._raw_ns() / 1000.0
        while pending:
            entry = pending[0]
            t_raw: float | None = None
            if entry.real_ns is not None:
                t_raw = self._clock_map.to_raw_us(entry.real_ns)
                if t_raw is None and not force and now_us - entry.t_recv_us < _PENDING_MAX_AGE_US:
                    return
            pending.popleft()
            self._deliver(entry, t_raw)

    def _finish_pending(self) -> None:
        self._greet_new_consumers()  # a consumer added just before stop still sees "open" before "closed"
        if self._pending:
            self._clock_map.sample()
            self._flush_pending(force=True)

    def _update_maps(self, opcode: int, index: int, payload: bytes) -> None:
        if opcode == MONITOR_EVENT_PKT:
            if len(payload) < 2:
                return
            code, params = payload[0], payload[2:]
            if code == _HCI_EVT_CONNECTION_COMPLETE:
                result = _parse_connection_complete(params)
                if result is not None:
                    with self._lock:
                        self._peers[(index, result[0])] = result[1]
            elif code == _HCI_EVT_DISCONNECT_COMPLETE:
                handle = _parse_disconnect_complete(params)
                if handle is not None:
                    with self._lock:
                        self._peers.pop((index, handle), None)
                        self._tx_links.pop((index, handle), None)
        elif opcode == MONITOR_ACL_TX_PKT:
            if len(payload) >= 2:
                (handle_flags,) = struct.unpack_from("<H", payload, 0)
                now = self._monotonic()
                with self._lock:
                    self._tx_links[(index, handle_flags & 0x0FFF)] = now
        elif opcode == MONITOR_NEW_INDEX:
            if len(payload) >= 8:
                with self._lock:
                    self._adapters[index] = _mac(payload[2:8])
        elif opcode == MONITOR_DEL_INDEX:
            with self._lock:
                self._adapters.pop(index, None)
                for key in [k for k in self._peers if k[0] == index]:
                    del self._peers[key]
                for key in [k for k in self._tx_links if k[0] == index]:
                    del self._tx_links[key]

    def _deliver(self, entry: _Pending, t_raw_us: float | None) -> None:
        if t_raw_us is None:
            self._recv_stamped += 1
            rec = HubRecord(entry.t_recv_us, entry.index, entry.opcode, entry.payload, entry.drops, "recv")
        else:
            rec = HubRecord(t_raw_us, entry.index, entry.opcode, entry.payload, entry.drops, "kernel")
        acl_tx = rec.opcode == MONITOR_ACL_TX_PKT
        for c in self._consumers:
            if acl_tx and not c.wants_acl_tx:
                continue
            try:
                c.on_record(rec)
            except Exception:
                self._log_consumer_error(c, "on_record")

    def _notify_state(self, state: HubState, reason: str | None) -> None:
        for c in self._consumers:
            self._call_state(c, state, reason)

    def _call_state(self, c: HubConsumer, state: HubState, reason: str | None) -> None:
        try:
            c.on_hub_state(state, reason)
        except Exception:
            self._log_consumer_error(c, "on_hub_state")

    def _log_consumer_error(self, c: HubConsumer, where: str) -> None:
        now = self._monotonic()
        last = self._consumer_errors.get(id(c))
        if last is not None and now - last < _CONSUMER_LOG_INTERVAL_S:
            return
        self._consumer_errors[id(c)] = now
        logger.warning("HciMonitorHub: consumer %s failed in %s", type(c).__name__, where, exc_info=True)


_HUB: HciMonitorHub | None = None
_HUB_LOCK = threading.Lock()


def get_hub() -> HciMonitorHub:
    """Return the process-wide hub (created on first use; started only with the switch on)."""
    global _HUB
    with _HUB_LOCK:
        if _HUB is None:
            _HUB = HciMonitorHub()
        return _HUB


class AsyncHubLifecycle:
    """The hub behind the async ``start()``/``stop()`` the bridge lifecycle awaits (like ``HciAvrcpMonitor``)."""

    def __init__(self, hub: Any, consumers: list[Any]) -> None:
        self._hub = hub
        self._consumers = consumers

    @property
    def available(self) -> bool:
        return bool(getattr(self._hub, "available", False))

    async def start(self) -> None:
        for c in self._consumers:
            self._hub.add_consumer(c)
        self._hub.start()

    async def stop(self) -> None:
        import asyncio

        await asyncio.to_thread(self._hub.stop)
