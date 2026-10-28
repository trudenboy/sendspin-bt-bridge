"""HCI-level AVRCP passthrough command monitor.

Watches raw HCI_CHANNEL_MONITOR traffic to identify the source MAC for
inbound AVRCP passthrough commands and writes it into
``AvrcpSourceTracker``.  The inbound MPRIS dispatch then awaits
``tracker.wait_for_next_activity`` to synchronise on the freshest
source signal before resolving — fixes Next/Previous mis-routing and
the timing race on Play/Pause when 2+ speakers share an adapter.

This is the canonical (and currently only) source of activity signals
for the tracker: the legacy MediaPlayer1.PropertiesChanged D-Bus path
was removed once HCI proved sufficient (see commit ``35286638``).

Graceful degradation: if the socket can't be opened (non-Linux,
missing ``CAP_NET_RAW``, no BT adapter), logs at INFO and the task
self-disables.  The resolver then falls back to ``default_client``
unconditionally — single-speaker-per-adapter setups still work
correctly via the fast path; multi-speaker setups lose source
correlation and route every press to BlueZ's chosen ``players[0]``.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import socket
import struct
import sys
from typing import TYPE_CHECKING, Any, Protocol

from sendspin_bridge.services.bluetooth.hci_socket import (
    MONITOR_ACL_RX_PKT,
    MONITOR_DEL_INDEX,
    MONITOR_EVENT_PKT,
    parse_monitor_packet,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from sendspin_bridge.services.bluetooth.avrcp_source_tracker import AvrcpSourceTracker


class _RecordLike(Protocol):
    @property
    def opcode(self) -> int: ...

    @property
    def index(self) -> int: ...

    @property
    def payload(self) -> bytes: ...


logger = logging.getLogger(__name__)

_AF_BLUETOOTH: int = 31  # PF_BLUETOOTH — hardcoded so imports work on macOS
_BTPROTO_HCI: int = 1
_HCI_DEV_NONE: int = 0xFFFF
_HCI_CHANNEL_MONITOR: int = 2  # read-only passthrough of all HCI traffic
_OPCODE_EVENT_PKT: int = MONITOR_EVENT_PKT
_OPCODE_ACL_RX_PKT: int = MONITOR_ACL_RX_PKT
_HCI_EVT_CONNECTION_COMPLETE: int = 0x03
_HCI_EVT_DISCONNECT_COMPLETE: int = 0x05
_AVRCP_PID: int = 0x110E
_AVC_OPCODE_PASSTHROUGH: int = 0x7C
_AVC_SUBUNIT_PANEL_BYTE: int = 0x48  # (0x09 << 3) | 0
_BACKOFF_BASE: float = 5.0
_BACKOFF_MAX: float = 60.0
_BACKOFF_FACTOR: float = 2.0

# HCI ioctl numbers for enumerating existing connections (so the monitor
# can attribute ACL packets from connections that existed BEFORE it was
# bound — the kernel's HCI_CHANNEL_MONITOR replay only re-emits device
# state events, not historical Connection Complete events).
_HCIGETDEVLIST: int = 0x800448D2  # _IOR('H', 210, int)
_HCIGETCONNLIST: int = 0x800448D4  # _IOR('H', 212, int)
_HCI_LINK_TYPE_ACL: int = 1
_MAX_CONNS_PER_DEV: int = 16


class _HciDevReq(ctypes.Structure):
    _fields_ = [("dev_id", ctypes.c_uint16), ("dev_opt", ctypes.c_uint32)]


class _HciDevListReq(ctypes.Structure):
    # Variable-length array — DEV_NUM_MAX is HCI_MAX_DEV (16 in mainline).
    _fields_ = [("dev_num", ctypes.c_uint16), ("dev_req", _HciDevReq * 16)]


class _HciConnInfo(ctypes.Structure):
    _fields_ = [
        ("handle", ctypes.c_uint16),
        ("bdaddr", ctypes.c_uint8 * 6),
        ("type", ctypes.c_uint8),
        ("out", ctypes.c_uint8),
        ("state", ctypes.c_uint16),
        ("link_mode", ctypes.c_uint32),
    ]


class _HciConnListReq(ctypes.Structure):
    _fields_ = [
        ("dev_id", ctypes.c_uint16),
        ("conn_num", ctypes.c_uint16),
        ("conn_info", _HciConnInfo * _MAX_CONNS_PER_DEV),
    ]


def _parse_connection_complete(params: bytes) -> tuple[int, str] | None:
    if len(params) < 11:
        return None
    status = params[0]
    if status != 0:
        return None
    (handle,) = struct.unpack_from("<H", params, 1)
    bdaddr = params[3:9]
    link_type = params[9]
    if link_type != 0x01:  # only ACL
        return None
    mac = ":".join(f"{b:02X}" for b in reversed(bdaddr))
    return handle, mac


def _parse_disconnect_complete(params: bytes) -> int | None:
    if len(params) < 4:
        return None
    if params[0] != 0:
        return None
    (handle,) = struct.unpack_from("<H", params, 1)
    return handle


def _parse_avrcp_passthrough(data: bytes) -> int | None:
    if len(data) < 8:
        return None
    avctp_hdr = data[0]
    if avctp_hdr & 0x01:  # IPID=1 invalid PID
        return None
    if avctp_hdr & 0x02:  # C/R=1 response, not a command from speaker
        return None
    (pid,) = struct.unpack_from(">H", data, 1)
    if pid != _AVRCP_PID:
        return None
    # AVC frame at offset 3: ctype(1) subunit(1) opcode(1) op_byte(1) len(1)
    if data[4] != _AVC_SUBUNIT_PANEL_BYTE:
        return None
    if data[5] != _AVC_OPCODE_PASSTHROUGH:
        return None
    op_byte = data[6]
    if op_byte & 0x80:  # state_flag=1 means released
        return None
    return op_byte & 0x7F


_AVRCP_OP_NAMES = {
    0x44: "play",
    0x46: "pause",
    0x4B: "next",
    0x4C: "previous",
    0x40: "vol_up",
    0x41: "vol_down",
}


def _dispatch_acl_packet(
    payload: bytes,
    handle_to_mac: dict[int, str],
    tracker: AvrcpSourceTracker,
) -> None:
    if len(payload) < 4:
        return
    handle_flags, _ = struct.unpack_from("<HH", payload, 0)
    handle = handle_flags & 0x0FFF
    mac = handle_to_mac.get(handle)
    if mac is None:
        return
    l2cap = payload[4:]
    if len(l2cap) < 4:
        return
    _, cid = struct.unpack_from("<HH", l2cap, 0)
    if cid < 0x0040:
        return
    l2cap_payload = l2cap[4:]
    op_id = _parse_avrcp_passthrough(l2cap_payload)
    if op_id is None:
        return
    logger.debug(
        "HCI AVRCP: %s from %s (op_id=0x%02X)",
        _AVRCP_OP_NAMES.get(op_id, f"0x{op_id:02X}"),
        mac,
        op_id,
    )
    tracker.note_activity(mac)


def _process_packet(
    data: bytes,
    handle_to_mac: dict[int, str],
    tracker: AvrcpSourceTracker,
) -> None:
    frame = parse_monitor_packet(data)
    if frame is None:
        return
    opcode, _adapter_idx, payload = frame

    if opcode == _OPCODE_EVENT_PKT:
        if len(payload) < 2:
            return
        event_code = payload[0]
        params = payload[2:]
        if event_code == _HCI_EVT_CONNECTION_COMPLETE:
            result = _parse_connection_complete(params)
            if result:
                handle, mac = result
                handle_to_mac[handle] = mac
                logger.debug("HCI conn: handle=0x%04X mac=%s", handle, mac)
        elif event_code == _HCI_EVT_DISCONNECT_COMPLETE:
            disc_handle = _parse_disconnect_complete(params)
            if disc_handle is not None:
                removed = handle_to_mac.pop(disc_handle, None)
                if removed:
                    logger.debug("HCI disc: handle=0x%04X mac=%s", disc_handle, removed)

    elif opcode == _OPCODE_ACL_RX_PKT:
        _dispatch_acl_packet(payload, handle_to_mac, tracker)


def _hci_ioctl(fd: int, request: int, req: ctypes.Structure) -> int:
    """Thin libc.ioctl wrapper kept patchable in tests."""
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.ioctl.restype = ctypes.c_int
    return libc.ioctl(fd, request, ctypes.byref(req))


def _seed_handle_map_from_kernel(handle_to_mac: dict[int, str]) -> None:
    """Populate ``handle_to_mac`` from the kernel's existing HCI connections.

    HCI_CHANNEL_MONITOR replays device-state events (REG/OPEN/UP), but not
    past Connection Complete events.  Without this seed, every ACL packet
    from connections that existed when the monitor bound is silently
    dropped because the dispatcher can't resolve handle → MAC, and inbound
    AVRCP commands fall back to default_client (mis-routing).

    Errors are swallowed: the monitor stays usable for new connections via
    Connection Complete event parsing.
    """
    if sys.platform != "linux":
        return
    try:
        sock = socket.socket(_AF_BLUETOOTH, socket.SOCK_RAW, _BTPROTO_HCI)
    except OSError as exc:
        logger.debug("HCI seed: cannot open enumeration socket: %s", exc)
        return
    try:
        dev_list = _HciDevListReq()
        dev_list.dev_num = len(dev_list.dev_req)
        if _hci_ioctl(sock.fileno(), _HCIGETDEVLIST, dev_list) < 0:
            logger.debug("HCI seed: HCIGETDEVLIST failed (errno=%d)", ctypes.get_errno())
            return
        for i in range(dev_list.dev_num):
            dev_id = dev_list.dev_req[i].dev_id
            conn_list = _HciConnListReq()
            conn_list.dev_id = dev_id
            conn_list.conn_num = _MAX_CONNS_PER_DEV
            if _hci_ioctl(sock.fileno(), _HCIGETCONNLIST, conn_list) < 0:
                continue
            for j in range(conn_list.conn_num):
                ci = conn_list.conn_info[j]
                if ci.type != _HCI_LINK_TYPE_ACL:
                    continue
                mac = ":".join(f"{b:02X}" for b in reversed(bytes(ci.bdaddr)))
                handle_to_mac[ci.handle] = mac
                logger.debug("HCI seed: hci%d handle=0x%04X mac=%s", dev_id, ci.handle, mac)
    finally:
        sock.close()


def _seed_peer_map_from_kernel(peers: dict[tuple[int, int], str]) -> None:
    """Populate ``peers`` with ``(dev_id, handle) → MAC`` for the kernel's existing ACL links.

    The index-aware twin of :func:`_seed_handle_map_from_kernel`, used by the monitor hub
    (``EXPERIMENTAL_AIR_SENSOR`` on): two controllers can hand out the same handle, so the key
    keeps the controller index (R-2). Errors are swallowed like the legacy seed's.
    """
    if sys.platform != "linux":
        return
    try:
        sock = socket.socket(_AF_BLUETOOTH, socket.SOCK_RAW, _BTPROTO_HCI)
    except OSError as exc:
        logger.debug("HCI seed: cannot open enumeration socket: %s", exc)
        return
    try:
        dev_list = _HciDevListReq()
        dev_list.dev_num = len(dev_list.dev_req)
        if _hci_ioctl(sock.fileno(), _HCIGETDEVLIST, dev_list) < 0:
            logger.debug("HCI seed: HCIGETDEVLIST failed (errno=%d)", ctypes.get_errno())
            return
        for i in range(dev_list.dev_num):
            dev_id = dev_list.dev_req[i].dev_id
            conn_list = _HciConnListReq()
            conn_list.dev_id = dev_id
            conn_list.conn_num = _MAX_CONNS_PER_DEV
            if _hci_ioctl(sock.fileno(), _HCIGETCONNLIST, conn_list) < 0:
                continue
            for j in range(conn_list.conn_num):
                ci = conn_list.conn_info[j]
                if ci.type != _HCI_LINK_TYPE_ACL:
                    continue
                peers[(dev_id, ci.handle)] = ":".join(f"{b:02X}" for b in reversed(bytes(ci.bdaddr)))
    finally:
        sock.close()


def _process_record(
    rec: _RecordLike,
    conn_map: dict[tuple[int, int], str],
    tracker: AvrcpSourceTracker,
) -> None:
    """Index-aware twin of :func:`_process_packet` for hub records: links are ``(index, handle)``."""
    opcode, index, payload = rec.opcode, rec.index, rec.payload
    if opcode == _OPCODE_EVENT_PKT:
        if len(payload) < 2:
            return
        event_code = payload[0]
        params = payload[2:]
        if event_code == _HCI_EVT_CONNECTION_COMPLETE:
            result = _parse_connection_complete(params)
            if result:
                conn_map[(index, result[0])] = result[1]
        elif event_code == _HCI_EVT_DISCONNECT_COMPLETE:
            disc_handle = _parse_disconnect_complete(params)
            if disc_handle is not None:
                conn_map.pop((index, disc_handle), None)
    elif opcode == MONITOR_DEL_INDEX:
        for key in [k for k in conn_map if k[0] == index]:
            del conn_map[key]
    elif opcode == _OPCODE_ACL_RX_PKT:
        if len(payload) < 4:
            return
        (handle_flags,) = struct.unpack_from("<H", payload, 0)
        handle = handle_flags & 0x0FFF
        mac = conn_map.get((index, handle))
        if mac is None:
            return
        # The legacy dispatcher does the AVRCP parse; it sees just this link.
        _dispatch_acl_packet(payload, {handle: mac}, tracker)


class AvrcpConsumer:
    """AVRCP source correlation as a monitor-hub consumer (``EXPERIMENTAL_AIR_SENSOR`` on).

    Same behaviour as :class:`HciAvrcpMonitor`, but the hub owns the socket and links are keyed by
    ``(index, handle)``. ``peers`` (the hub's ``peers``) supplies the seeded links at every open.
    Runs on the hub thread; ``AvrcpSourceTracker.note_activity`` is thread-safe.
    """

    wants_acl_tx = False
    # What _process_record reads; the hub skips the rest for this consumer.
    opcodes = frozenset({_OPCODE_EVENT_PKT, _OPCODE_ACL_RX_PKT, MONITOR_DEL_INDEX})
    event_codes = frozenset({_HCI_EVT_CONNECTION_COMPLETE, _HCI_EVT_DISCONNECT_COMPLETE})

    def __init__(
        self,
        *,
        tracker: AvrcpSourceTracker | Any | None = None,
        peers: Callable[[], Mapping[tuple[int, int], str]] | None = None,
    ) -> None:
        self._tracker = tracker
        self._peers = peers
        self._conn: dict[tuple[int, int], str] = {}

    def _get_tracker(self) -> AvrcpSourceTracker:
        if self._tracker is None:
            from sendspin_bridge.services.bluetooth.avrcp_source_tracker import get_tracker

            self._tracker = get_tracker()
        return self._tracker

    def on_record(self, rec: _RecordLike) -> None:
        if rec.opcode in (_OPCODE_EVENT_PKT, _OPCODE_ACL_RX_PKT, MONITOR_DEL_INDEX):
            _process_record(rec, self._conn, self._get_tracker())

    def on_hub_state(self, state: str, reason: str | None) -> None:
        self._conn.clear()
        if state == "open" and self._peers is not None:
            self._conn.update(self._peers())
            logger.info("AvrcpConsumer: tracking AVRCP source MACs (seeded %d existing connections)", len(self._conn))


def _open_hci_monitor_socket() -> socket.socket:
    """Open BTPROTO_HCI socket on HCI_CHANNEL_MONITOR. Raises OSError if unavailable."""
    from sendspin_bridge.services.bluetooth.hci_socket import (
        HCI_CHANNEL_MONITOR,
        HCI_DEV_NONE,
        open_hci_socket,
    )

    return open_hci_socket(hci_dev=HCI_DEV_NONE, channel=HCI_CHANNEL_MONITOR)


async def _read_packets(sock: socket.socket, handle_to_mac: dict[int, str], tracker) -> None:
    """Read the monitor socket on the event loop until it fails.

    The monitor channel carries every HCI packet of every controller, so
    this runs hundreds of times a second while speakers stream. Reading each
    packet through ``asyncio.to_thread`` cost an executor round trip apiece;
    instead the loop wakes when the socket is readable and drains everything
    that is ready.
    """
    loop = asyncio.get_running_loop()
    readable = asyncio.Event()
    sock.setblocking(False)
    fd = sock.fileno()
    loop.add_reader(fd, readable.set)
    try:
        while True:
            await readable.wait()
            readable.clear()
            while True:
                try:
                    data = sock.recv(4096)
                except BlockingIOError:
                    break
                _process_packet(data, handle_to_mac, tracker)
    finally:
        loop.remove_reader(fd)


class HciAvrcpMonitor:
    """Background asyncio task that monitors HCI traffic for AVRCP passthrough commands.

    Feeds AvrcpSourceTracker with accurate per-device MAC attribution so
    resolve_avrcp_source_client() routes Next/Previous correctly even when BlueZ
    routes all inbound AVRCP to players[0].

    Follows the SinkMonitor lifecycle pattern: start()/stop() are idempotent;
    OSError on socket open is logged at INFO and the task self-disables.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task[None] | None = None

    @property
    def available(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    async def _monitor_loop(self) -> None:
        from sendspin_bridge.services.bluetooth.avrcp_source_tracker import get_tracker

        tracker = get_tracker()
        handle_to_mac: dict[int, str] = {}
        current_backoff = _BACKOFF_BASE
        while True:
            try:
                sock = _open_hci_monitor_socket()
            except OSError as exc:
                logger.info(
                    "HciAvrcpMonitor: cannot open HCI monitor socket (%s) — "
                    "AVRCP source correlation will rely on D-Bus heuristic only",
                    exc,
                )
                self._task = None
                return
            # Seed handle→MAC from existing connections.  HCI_CHANNEL_MONITOR
            # only emits Connection Complete events for connections that
            # form AFTER bind, so without this the monitor silently drops
            # ACL packets for already-connected speakers.
            _seed_handle_map_from_kernel(handle_to_mac)
            logger.info(
                "HciAvrcpMonitor: HCI_CHANNEL_MONITOR open — tracking AVRCP source MACs (seeded %d existing connections)",
                len(handle_to_mac),
            )
            current_backoff = _BACKOFF_BASE
            try:
                await _read_packets(sock, handle_to_mac, tracker)
            except asyncio.CancelledError:
                sock.close()
                return
            except (OSError, ValueError, TypeError) as exc:
                sock.close()
                handle_to_mac.clear()
                logger.warning(
                    "HciAvrcpMonitor: socket error (%s) — retrying in %.0fs",
                    exc,
                    current_backoff,
                )
                try:
                    await asyncio.sleep(current_backoff)
                except asyncio.CancelledError:
                    return
                current_backoff = min(current_backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)


_MONITOR = HciAvrcpMonitor()


def get_monitor() -> HciAvrcpMonitor:
    """Return the process-wide HciAvrcpMonitor singleton."""
    return _MONITOR
