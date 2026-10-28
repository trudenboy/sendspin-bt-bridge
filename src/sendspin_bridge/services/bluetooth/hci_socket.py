"""Shared helper for opening BTPROTO_HCI raw sockets on a chosen channel.

Python stdlib's ``socket.bind((hci_dev,))`` form for ``AF_BLUETOOTH /
SOCK_RAW / BTPROTO_HCI`` doesn't expose ``hci_channel``, so it can only
target the kernel's default ``HCI_CHANNEL_RAW`` channel. We need:

- ``HCI_CHANNEL_RAW`` (0) for sending HCI commands like
  ``Write_Class_Of_Device`` / ``Read_Class_Of_Device`` to a specific
  controller.
- ``HCI_CHANNEL_MONITOR`` (2) for passive sniffing of all HCI traffic
  across all controllers (used by the AVRCP monitor).

Both paths use the same ``ctypes``-bound ``libc.socket()`` /
``libc.bind()`` pair with the canonical ``sockaddr_hci`` C layout. The
helper here is the one place that wires that up; callers pass in the
channel and ``hci_dev`` they want.

Returns a regular Python ``socket.socket`` adopting the libc fd, so
callers can use ``settimeout`` / ``recv`` / ``send`` / ``close`` like
any other socket.
"""

from __future__ import annotations

import ctypes
import socket
import struct
import sys
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

# AF_BLUETOOTH constants — hardcoded so imports don't fail on macOS dev
# boxes where ``socket.AF_BLUETOOTH`` may be absent. Real binding only
# works on Linux (other platforms raise OSError).
AF_BLUETOOTH: int = 31  # PF_BLUETOOTH
BTPROTO_HCI: int = 1
HCI_CHANNEL_RAW: int = 0  # send HCI commands to a chosen controller
HCI_CHANNEL_USER: int = 1  # exclusive (steals from bluetoothd) — unused here
HCI_CHANNEL_MONITOR: int = 2  # passive read-only passthrough of HCI traffic
HCI_CHANNEL_CONTROL: int = 3  # mgmt API — used by btsocket.btmgmt_socket
HCI_DEV_NONE: int = 0xFFFF

# HCI_CHANNEL_MONITOR packet opcodes (include/net/bluetooth/hci_mon.h). The
# same values are the low 16 bits of a btsnoop record's flags for the
# monitor datalink (2001) that ``btmon -w`` writes.
MONITOR_NEW_INDEX: int = 0
MONITOR_DEL_INDEX: int = 1
MONITOR_COMMAND_PKT: int = 2
MONITOR_EVENT_PKT: int = 3
MONITOR_ACL_TX_PKT: int = 4
MONITOR_ACL_RX_PKT: int = 5
MONITOR_HEADER_LEN: int = 6  # struct hci_mon_hdr { __le16 opcode; __le16 index; __le16 len; }


def parse_monitor_packet(data: bytes) -> tuple[int, int, bytes] | None:
    """Split one HCI_CHANNEL_MONITOR read into ``(opcode, index, payload)``.

    Returns ``None`` when the header is short or the payload is shorter
    than the header's ``len`` field (a truncated read); trailing bytes
    beyond ``len`` are ignored.
    """
    if len(data) < MONITOR_HEADER_LEN:
        return None
    opcode, index, length = struct.unpack_from("<HHH", data, 0)
    end = MONITOR_HEADER_LEN + length
    if len(data) < end:
        return None
    return opcode, index, bytes(data[MONITOR_HEADER_LEN:end])


# Socket options the monitor hub uses (asm-generic/socket.h, the values on every Linux arch the
# bridge ships for). Hardcoded: ``socket`` does not export them.
SO_RCVBUFFORCE: int = 33  # needs CAP_NET_ADMIN; falls back to SO_RCVBUF (capped by rmem_max)
SO_TIMESTAMPNS: int = 35  # SO_TIMESTAMPNS_OLD: a native ``struct timespec`` in CLOCK_REALTIME
SCM_TIMESTAMPNS: int = SO_TIMESTAMPNS
SO_RXQ_OVFL: int = 40  # cumulative u32 drop count as a cmsg (hci_sock_recvmsg likely never sends it)
SO_MEMINFO: int = 55  # u32[SK_MEMINFO_VARS]
SK_MEMINFO_DROPS: int = 8  # index into the u32 array SO_MEMINFO returns
SK_MEMINFO_VARS: int = 9

_NSEC_PER_SEC = 1_000_000_000
# struct timespec: two 64-bit fields on 64-bit userspace, two 32-bit longs on armv7.
_TIMESPEC_FORMATS = {struct.calcsize("qq"): "qq", struct.calcsize("ii"): "ii"}
_U32 = struct.Struct("I")


def enable_monitor_sockopts(sock: socket.socket, *, rcvbuf: int = 4 << 20) -> dict[str, bool]:
    """Set SO_TIMESTAMPNS, SO_RXQ_OVFL and the receive buffer (SO_RCVBUFFORCE, else SO_RCVBUF); returns what succeeded."""

    def _try(opt: int, value: int) -> bool:
        try:
            sock.setsockopt(socket.SOL_SOCKET, opt, value)
        except OSError:
            return False
        return True

    result = {
        "timestamps": _try(SO_TIMESTAMPNS, 1),
        "rxq_ovfl": _try(SO_RXQ_OVFL, 1),
        "rcvbuf_force": _try(SO_RCVBUFFORCE, rcvbuf),
    }
    result["rcvbuf"] = result["rcvbuf_force"] or _try(socket.SO_RCVBUF, rcvbuf)
    return result


def parse_monitor_cmsgs(ancdata: list[tuple[int, int, bytes]]) -> tuple[int | None, int | None]:
    """(realtime_ns, rxq_ovfl_drops) from recvmsg ancillary data; None for what is absent or malformed."""
    realtime_ns: int | None = None
    drops: int | None = None
    for level, kind, data in ancdata:
        if level != socket.SOL_SOCKET:
            continue
        if kind == SCM_TIMESTAMPNS:
            fmt = _TIMESPEC_FORMATS.get(len(data))
            if fmt is None:
                continue
            sec, nsec = struct.unpack(fmt, data)
            if sec >= 0 and 0 <= nsec < _NSEC_PER_SEC:
                realtime_ns = sec * _NSEC_PER_SEC + nsec
        elif kind == SO_RXQ_OVFL and len(data) == _U32.size:
            (drops,) = _U32.unpack(data)
    return realtime_ns, drops


def read_meminfo_drops(sock: socket.socket) -> int | None:
    """Cumulative SK_MEMINFO_DROPS via getsockopt(SOL_SOCKET, SO_MEMINFO); None when unsupported."""
    try:
        raw = sock.getsockopt(socket.SOL_SOCKET, SO_MEMINFO, _U32.size * SK_MEMINFO_VARS)
    except OSError:
        return None
    offset = _U32.size * SK_MEMINFO_DROPS
    if len(raw) < offset + _U32.size:
        return None
    (drops,) = _U32.unpack_from(raw, offset)
    return int(drops)


class DropCounter:
    """Prefers the cmsg count once one has been seen; otherwise polls SO_MEMINFO (every 250 ms).

    The caller drives it: :meth:`on_cmsg` with each record's SO_RXQ_OVFL value (``None`` when the
    record carried none) and :meth:`tick` on its 250 ms cadence. Both counts are cumulative socket
    drops; once a cmsg count has been seen, polling stops. A failed poll keeps the last value.
    """

    def __init__(self, *, poll: Callable[[], int | None]) -> None:
        self._poll = poll
        self._drops: int | None = None
        self._source: Literal["cmsg", "meminfo", "none"] = "none"

    def on_cmsg(self, drops: int | None) -> None:
        if drops is None:
            return
        self._drops = drops
        self._source = "cmsg"

    def tick(self) -> None:
        if self._source == "cmsg":
            return
        drops = self._poll()
        if drops is None:
            return
        self._drops = drops
        self._source = "meminfo"

    @property
    def drops(self) -> int | None:
        return self._drops

    @property
    def source(self) -> Literal["cmsg", "meminfo", "none"]:
        return self._source


# struct sockaddr_hci { sa_family_t hci_family; uint16_t hci_dev;
#                       uint16_t hci_channel; }
class _SocketAddr(ctypes.Structure):
    _fields_ = [
        ("hci_family", ctypes.c_ushort),
        ("hci_dev", ctypes.c_ushort),
        ("hci_channel", ctypes.c_ushort),
    ]


def open_hci_socket(*, hci_dev: int, channel: int) -> socket.socket:
    """Open ``AF_BLUETOOTH / SOCK_RAW / BTPROTO_HCI`` on (*hci_dev*, *channel*).

    Raises ``OSError`` if the platform isn't Linux, the kernel rejects
    the bind, or the libc fd cannot be wrapped. Caller is responsible
    for closing the returned socket.
    """
    if sys.platform != "linux":
        raise OSError("HCI raw sockets require Linux")

    libc = ctypes.CDLL("libc.so.6", use_errno=True)
    libc.socket.argtypes = (ctypes.c_int, ctypes.c_int, ctypes.c_int)
    libc.socket.restype = ctypes.c_int
    libc.bind.argtypes = (ctypes.c_int, ctypes.POINTER(_SocketAddr), ctypes.c_int)
    libc.bind.restype = ctypes.c_int

    fd = libc.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
    if fd < 0:
        raise OSError(f"Unable to open AF_BLUETOOTH HCI socket: errno={ctypes.get_errno()}")

    try:
        addr = _SocketAddr(
            hci_family=AF_BLUETOOTH,
            hci_dev=hci_dev & 0xFFFF,
            hci_channel=channel & 0xFFFF,
        )
        rc = libc.bind(fd, ctypes.pointer(addr), ctypes.sizeof(addr))
        if rc < 0:
            raise OSError(
                f"Unable to bind HCI socket (hci_dev={hci_dev}, channel={channel}): errno={ctypes.get_errno()}"
            )
        return socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI, fileno=fd)
    except BaseException:
        # Close the raw fd on any failure between socket() and the
        # final socket.socket() wrap, so we don't leak when bind fails.
        import os

        try:
            os.close(fd)
        except OSError:
            pass
        raise
