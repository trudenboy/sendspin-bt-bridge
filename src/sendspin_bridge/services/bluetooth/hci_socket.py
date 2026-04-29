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
import sys

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
