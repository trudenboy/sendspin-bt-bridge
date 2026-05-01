"""Apply a per-adapter Class of Device override via raw HCI commands.

Workaround for the Samsung Q-series quirk documented in
`bluez/bluez#1025 <https://github.com/bluez/bluez/issues/1025>`_: the
soundbar's BR/EDR firmware filters incoming connection attempts by the
initiator's Class of Device. When the local adapter's CoD doesn't match
its allowlist (e.g. the bare ``0x0c0000`` that HAOS / RPi default to),
the soundbar replies ``LMP_not_accepted_ext: Limited Resources`` and
BlueZ surfaces the failure as ``HCI Connect Complete status=0x0d`` →
``MGMT Connect Failed: No Resources (0x07)`` →
``org.bluez.Error.AuthenticationCanceled``. Setting CoD to ``0x00010c``
(Computer / Laptop) is the documented fix from the BlueZ thread.

**Why raw HCI, not the kernel mgmt API.** The original v2.65.1-rc.1
implementation tried to use ``MGMT_OP_SET_DEV_CLASS`` (opcode
``0x000E``) on the mgmt control socket, the same channel
``services/bt_rssi_mgmt`` uses for ``GET_CONN_INFO``. Empirical tests
on a CSR8510 A10 (production HAOS reference) and on a Realtek RTL8761B
(reporter on issue #210) showed that path returns
``status 0x0d (Invalid Parameters)`` regardless of whether
``bluetoothd`` is running, and the same behaviour reproduces with the
official ``btmgmt class`` CLI. The mgmt-API path simply doesn't work
in this environment for the kernel/adapter combinations our users
have. Raw HCI ``Write_Class_Of_Device`` (OGF=0x03, OCF=0x0024) was
verified working on both adapters and is now the canonical applier.

The module sends two HCI commands on a ``BTPROTO_HCI`` socket bound to
``HCI_CHANNEL_RAW`` for the chosen controller:

- :func:`set_device_class` — ``HCI_Write_Class_Of_Device`` to set the
  CoD to the requested 24-bit value.
- :func:`read_device_class` — ``HCI_Read_Class_Of_Device`` to query
  the current live value (used by the UI to confirm the override
  landed).

Errors collapse to ``False`` / ``None`` so a missing capability or
non-Linux dev box never blocks startup; the warning is logged once and
the bridge moves on.
"""

from __future__ import annotations

import logging
import re
import struct
import time

logger = logging.getLogger(__name__)

# Raw HCI Write_Class_Of_Device + Read_Class_Of_Device opcodes.
# Spec: Bluetooth Core 5.3 Vol 4 Part E §7.3.26 / §7.3.25.
_HCI_OPCODE_WRITE_CLASS_OF_DEVICE = 0x0C24  # OGF=0x03 (Controller&Baseband), OCF=0x0024
_HCI_OPCODE_READ_CLASS_OF_DEVICE = 0x0C23  # OGF=0x03, OCF=0x0023

# HCI packet types and event codes.
_HCI_PKT_TYPE_COMMAND = 0x01
_HCI_PKT_TYPE_EVENT = 0x04
_HCI_EV_COMMAND_COMPLETE = 0x0E

# HCI socket filter. On HCI_CHANNEL_RAW the kernel's default filter is
# all-zero — every event is dropped before reaching userspace, so the
# Command Complete reply for our own command never arrives. We have to
# install an explicit filter that lets event packets and at least the
# command-status / command-complete opcodes through.
#
# struct hci_filter { __u32 type_mask; __u32 event_mask[2]; __le16 opcode; }
# The struct sizes to **16 bytes** on Linux because the trailing
# ``__le16`` aligns up to the ``__u32`` boundary — the kernel's
# ``bt_copy_from_sockptr`` rejects shorter buffers with ``EINVAL``,
# which is why a 14-byte ``=IIIH`` pack failed silently as a "timed
# out or failed" Command Complete read. ``=IIIH2x`` matches the real
# struct layout.
_SOL_HCI = 0
_HCI_FILTER = 2
_HCI_FILTER_TYPE_MASK_EVENT = 1 << _HCI_PKT_TYPE_EVENT  # 0x10
_HCI_FILTER_BYTES = struct.pack(
    "=IIIH2x",
    _HCI_FILTER_TYPE_MASK_EVENT,  # type_mask: HCI_EVENT_PKT only
    0xFFFFFFFF,  # event_mask[0]: events 0-31 (covers CMD_COMPLETE=0x0E, CMD_STATUS=0x0F)
    0xFFFFFFFF,  # event_mask[1]: events 32-63
    0x0000,  # opcode: 0 = match any
)

# Wall-clock budget for one HCI round-trip. Healthy controllers reply
# in single-digit milliseconds; 2 s is generous and bounds how long a
# stuck controller can stall the startup or pre-pair sequence.
_HCI_DEADLINE_S = 2.0

# Six-hex-digit form: ``0x00010c``. Anchored both ends so trailing
# whitespace or a stray ``L`` suffix is rejected at parse time.
_DEVICE_CLASS_RE = re.compile(r"^0x([0-9a-fA-F]{6})$")


def parse_class_hex(value: str) -> int | None:
    """Decode a 24-bit Class of Device hex string into an int.

    The HCI Write_Class_Of_Device command takes the 24-bit CoD as a
    single integer, packed as 3 bytes little-endian on the wire.
    Returns the integer value or ``None`` for malformed input so the
    caller can warn-and-skip instead of crashing the startup sequence.

    >>> parse_class_hex("0x00010c")
    268
    >>> hex(parse_class_hex("0x00010c"))
    '0x10c'
    >>> parse_class_hex("0x000100")
    256
    >>> parse_class_hex("not-hex") is None
    True
    """
    if not isinstance(value, str):
        return None
    match = _DEVICE_CLASS_RE.match(value.strip())
    if not match:
        return None
    return int(match.group(1), 16)


def _open_hci_socket(adapter_index: int):
    """Open a raw HCI socket bound to *hci<adapter_index>* on HCI_CHANNEL_RAW.

    Indirection seam so the syscall stays mockable in tests.
    """
    from sendspin_bridge.services.bluetooth.hci_socket import (
        HCI_CHANNEL_RAW,
        open_hci_socket,
    )

    return open_hci_socket(hci_dev=adapter_index, channel=HCI_CHANNEL_RAW)


def _close_hci_socket(sock) -> None:
    try:
        sock.close()
    except Exception:
        pass


def _send_and_read_command_complete(
    sock, opcode: int, payload: bytes, deadline_s: float = _HCI_DEADLINE_S
) -> tuple[bool, bytes]:
    """Send an HCI command, wait for the matching Command Complete event.

    Returns ``(success, return_params)``. ``success`` is ``True`` when
    the controller replied with a Command Complete event for *opcode*
    with HCI status ``0x00``. ``return_params`` is the bytes after the
    status field (empty for commands without return parameters; on
    failure, contains the status byte if available, empty otherwise).
    """
    # Install the HCI filter so the kernel actually delivers Command
    # Complete events to our socket. Without this the recv() call below
    # would always time out on HCI_CHANNEL_RAW. Failure here is fatal —
    # the operation cannot succeed without a working filter.
    try:
        sock.setsockopt(_SOL_HCI, _HCI_FILTER, _HCI_FILTER_BYTES)
    except OSError:
        return False, b""

    # Command packet: [01][opcode_lo][opcode_hi][plen][payload...]
    packet = struct.pack("<BHB", _HCI_PKT_TYPE_COMMAND, opcode, len(payload)) + payload
    try:
        sock.send(packet)
    except Exception:
        return False, b""

    deadline = time.monotonic() + deadline_s
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False, b""
        try:
            sock.settimeout(remaining)
            data = sock.recv(512)
        except (TimeoutError, OSError):
            return False, b""

        # Expect: [04][0e][plen][num_cmd_pkts][opcode_lo][opcode_hi][status][...]
        if len(data) < 7 or data[0] != _HCI_PKT_TYPE_EVENT:
            continue
        if data[1] != _HCI_EV_COMMAND_COMPLETE:
            continue
        cmd_op = int.from_bytes(data[4:6], "little")
        if cmd_op != opcode:
            continue
        status = data[6]
        if status != 0x00:
            return False, bytes([status])
        return True, data[7:]


def set_device_class(adapter_index: int, cod: int) -> bool:
    """Send ``HCI_Write_Class_Of_Device`` to *hci<adapter_index>*.

    ``cod`` is the full 24-bit Class of Device value (e.g. ``0x00010c``
    for Computer/Laptop). Returns ``True`` on Command Complete with
    status ``0x00``; ``False`` on any failure path (logged at WARNING).
    Never raises — the caller can keep iterating over the rest of the
    controllers.
    """
    if adapter_index < 0:
        logger.warning("CoD: adapter_index=%d is negative; skipping", adapter_index)
        return False
    if not (0 <= cod <= 0xFFFFFF):
        logger.warning("CoD: 0x%X out of 24-bit range; skipping", cod)
        return False

    try:
        sock = _open_hci_socket(adapter_index)
    except (ImportError, OSError) as exc:
        logger.warning("CoD: HCI socket open failed for hci%d: %s", adapter_index, exc)
        return False

    try:
        payload = cod.to_bytes(3, "little")
        ok, return_params = _send_and_read_command_complete(sock, _HCI_OPCODE_WRITE_CLASS_OF_DEVICE, payload)
        if not ok:
            if return_params:
                logger.warning(
                    "CoD: hci%d Write_Class_Of_Device returned HCI status=0x%02X",
                    adapter_index,
                    return_params[0],
                )
            else:
                # Most common cause: the controller didn't ACK because
                # bluetoothd is down or the adapter isn't powered.  This
                # warning is informational only — adapter detection
                # itself is independent of CoD writes; if the bridge
                # also reports "no Bluetooth controller", fix that
                # first and the CoD override will reapply on its own.
                logger.warning(
                    "CoD: hci%d no Command Complete event from controller "
                    "(controller may be unpowered or bluetoothd inactive — "
                    "fix adapter access first; CoD override is independent)",
                    adapter_index,
                )
            return False
        logger.info(
            "CoD: hci%d Write_Class_Of_Device(0x%06X) succeeded",
            adapter_index,
            cod,
        )
        return True
    finally:
        _close_hci_socket(sock)


def read_device_class(adapter_index: int) -> int | None:
    """Send ``HCI_Read_Class_Of_Device`` to *hci<adapter_index>*.

    Returns the live 24-bit CoD on success, ``None`` on any failure
    path (logged at DEBUG — this is a read used by the UI for
    confirmation, not a critical write). Never raises.
    """
    if adapter_index < 0:
        return None

    try:
        sock = _open_hci_socket(adapter_index)
    except (ImportError, OSError) as exc:
        logger.debug("CoD: HCI socket open failed for hci%d read: %s", adapter_index, exc)
        return None

    try:
        ok, return_params = _send_and_read_command_complete(sock, _HCI_OPCODE_READ_CLASS_OF_DEVICE, b"")
        if not ok or len(return_params) < 3:
            logger.debug(
                "CoD: hci%d Read_Class_Of_Device unsuccessful (params=%r)",
                adapter_index,
                return_params,
            )
            return None
        # Return params: [class_of_device (3 bytes LE)]
        return int.from_bytes(return_params[:3], "little")
    finally:
        _close_hci_socket(sock)


def apply_device_class_for_hex(adapter_index: int, hex_value: str) -> bool:
    """Apply a six-hex-digit CoD override to *hci<adapter_index>*.

    No-op (returns ``False``) when *hex_value* is empty or malformed —
    the caller can pass it unconditionally without first checking.
    """
    if not hex_value:
        return False
    parsed = parse_class_hex(hex_value)
    if parsed is None:
        logger.warning(
            "CoD: hci%d device_class=%r is not a 6-hex-digit value (e.g. '0x00010c'); skipping",
            adapter_index,
            hex_value,
        )
        return False
    return set_device_class(adapter_index, parsed)
