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
import os
import socket
import struct
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.avrcp_source_tracker import AvrcpSourceTracker

logger = logging.getLogger(__name__)

_AF_BLUETOOTH: int = 31  # PF_BLUETOOTH — hardcoded so imports work on macOS
_BTPROTO_HCI: int = 1
_HCI_DEV_NONE: int = 0xFFFF
_HCI_CHANNEL_MONITOR: int = 2  # read-only passthrough of all HCI traffic
_OPCODE_EVENT_PKT: int = 0x0003
_OPCODE_ACL_RX_PKT: int = 0x0005
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
    if len(data) < 6:
        return
    opcode, _adapter_idx, payload_len = struct.unpack_from("<HHH", data, 0)
    payload = data[6 : 6 + payload_len]

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


# ctypes.Structure mirrors btsocket.btmgmt_socket.SocketAddr exactly — same C
# layout, validated working. We use HCI_CHANNEL_MONITOR (2) instead of channel 3.
class _SocketAddr(ctypes.Structure):
    _fields_ = [
        ("hci_family", ctypes.c_ushort),
        ("hci_dev", ctypes.c_ushort),
        ("hci_channel", ctypes.c_ushort),
    ]


def _open_hci_monitor_socket() -> socket.socket:
    """Open BTPROTO_HCI socket on HCI_CHANNEL_MONITOR. Raises OSError if unavailable."""
    if sys.platform != "linux":
        raise OSError("HCI monitor requires Linux")
    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.socket.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int)
    libc.socket.restype = ctypes.c_int
    libc.bind.argtypes = (ctypes.c_int, ctypes.POINTER(_SocketAddr), ctypes.c_int)
    libc.bind.restype = ctypes.c_int
    fd = libc.socket(_AF_BLUETOOTH, socket.SOCK_RAW, _BTPROTO_HCI)
    if fd < 0:
        raise OSError("Unable to open PF_BLUETOOTH socket")
    try:
        addr = _SocketAddr(
            hci_family=_AF_BLUETOOTH,
            hci_dev=_HCI_DEV_NONE,
            hci_channel=_HCI_CHANNEL_MONITOR,
        )
        r = libc.bind(fd, ctypes.pointer(addr), ctypes.sizeof(addr))
        if r < 0:
            raise OSError(f"Unable to bind HCI_CHANNEL_MONITOR: errno={ctypes.get_errno()}")
        return socket.socket(_AF_BLUETOOTH, socket.SOCK_RAW, _BTPROTO_HCI, fileno=fd)
    except BaseException:
        # Close the raw fd on any failure between socket() and the
        # successful socket.socket() handoff — otherwise the backoff
        # retry loop in _monitor_loop leaks one fd per failed attempt.
        os.close(fd)
        raise


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
        from services.avrcp_source_tracker import get_tracker

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
                while True:
                    data = await asyncio.to_thread(sock.recv, 4096)
                    _process_packet(data, handle_to_mac, tracker)
            except asyncio.CancelledError:
                sock.close()
                return
            except OSError as exc:
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
