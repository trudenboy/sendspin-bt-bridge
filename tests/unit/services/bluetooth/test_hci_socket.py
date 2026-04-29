"""Unit tests for the shared HCI socket helper.

Real ``socket(AF_BLUETOOTH, ...)`` calls only succeed on Linux, so the
tests here mostly verify the platform guard and the constants surface.
The actual ctypes-bind path is covered by integration tests on the
deployment side (no point reimplementing libc in a mock).
"""

from __future__ import annotations

import sys

import pytest

from sendspin_bridge.services.bluetooth import hci_socket


def test_constants_match_kernel_values():
    # Sanity: these must match net/bluetooth/hci.h. If they drift,
    # every caller of open_hci_socket breaks.
    assert hci_socket.AF_BLUETOOTH == 31
    assert hci_socket.BTPROTO_HCI == 1
    assert hci_socket.HCI_CHANNEL_RAW == 0
    assert hci_socket.HCI_CHANNEL_USER == 1
    assert hci_socket.HCI_CHANNEL_MONITOR == 2
    assert hci_socket.HCI_CHANNEL_CONTROL == 3
    assert hci_socket.HCI_DEV_NONE == 0xFFFF


def test_open_hci_socket_raises_oserror_on_non_linux(monkeypatch):
    """The helper must fail soft on macOS / Windows dev boxes."""
    monkeypatch.setattr(hci_socket.sys, "platform", "darwin")
    with pytest.raises(OSError, match="require Linux"):
        hci_socket.open_hci_socket(hci_dev=0, channel=hci_socket.HCI_CHANNEL_RAW)


@pytest.mark.skipif(sys.platform != "linux", reason="HCI sockets are Linux-only")
def test_open_hci_socket_bind_failure_propagates(monkeypatch):
    """Bind to a non-existent hci_dev must raise OSError, not segfault.

    This is a smoke test against a real libc on Linux — we ask for an
    obviously-bogus hci_dev and rely on the kernel to reject it.
    """
    # hci_dev=0xFFFE is a valid u16 value but unlikely to match a real
    # controller. On a test runner without bluetooth, even hci_dev=0
    # fails. Either way, we expect OSError to surface.
    with pytest.raises(OSError):
        hci_socket.open_hci_socket(hci_dev=0xFFFE, channel=hci_socket.HCI_CHANNEL_RAW)


def test_socket_addr_struct_layout():
    """The ctypes struct must be 6 bytes — sizeof(sockaddr_hci) on Linux."""
    import ctypes

    assert ctypes.sizeof(hci_socket._SocketAddr) == 6
