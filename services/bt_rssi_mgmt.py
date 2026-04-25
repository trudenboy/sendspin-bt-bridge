"""Live-RSSI source for connected BR/EDR speakers.

Sends BlueZ kernel mgmt opcode 0x0031 (``MGMT_OP_GET_CONN_INFO``)
directly on an ``AF_BLUETOOTH`` raw socket and parses the
``CommandComplete`` event ourselves.

Note on BR/EDR semantics: ``HCI_Read_RSSI`` for a BR/EDR ACL link
does NOT return absolute dBm — it returns a *delta* from the
controller's Golden Receive Power Range:

- ``0``    : signal is in the desired range (good link)
- ``< 0``  : weaker than the golden range
- ``> 0``  : stronger than the golden range

LE links get absolute dBm.  We don't currently address LE peers
(Sendspin is BR/EDR-only), so callers should interpret the returned
integer as "delta from golden range" — the existing UI chip
(``_renderRssiChip``) happens to colour 0 as green which matches
"healthy" semantically; we don't try to reinterpret here.

This is the *only* path on Linux that exposes RSSI for an
already-connected peer:

- ``bluetoothctl scan bredr`` only emits ``[CHG] Device <MAC> RSSI:``
  for *advertising* devices; connected peers stop responding to inquiry.
- ``bluetoothctl info <MAC>`` does not include an RSSI line for
  connected peers — BlueZ never queries the link.
- ``org.bluez.Device1.RSSI`` D-Bus property is populated only during
  active discovery and only for advertising devices.

The mgmt socket asks the kernel HCI layer to issue ``HCI_Read_RSSI``
on the established ACL link and returns the controller-measured value.
Requires ``CAP_NET_ADMIN`` (the bridge already has it).

Why hand-rolled instead of using ``btsocket.btmgmt_sync``: the
library's ``AddressTypeField`` encoder treats the address-type byte
as a *bitmask* (``1 << AddressType.BREDR.value`` ⇒ wire ``0x01``)
which is correct for some scan/filter opcodes but wrong for
``GetConnectionInformation``, where the byte is a *discriminator*
(BR/EDR ⇒ wire ``0x00``).  Passing a plain int explodes the encoder
with ``'int' object is not iterable``; passing the enum produces a
wrong wire byte and the kernel rejects the command.  Bypassing the
encoder is much simpler than monkey-patching the library.

We still use ``btsocket.btmgmt_socket.open()`` for the socket setup —
it works around `Python bug #36132 <https://bugs.python.org/issue36132>`_
(stdlib can't bind ``HCI_CHANNEL_CONTROL`` directly) via a libc-bind
shim that's tedious to inline.

The wrapper is deliberately defensive: every failure path collapses to
``None`` so the caller's contract is "fresh value or keep last known —
never propagate an exception into the asyncio refresh loop".
"""

from __future__ import annotations

import logging
import struct
import time

logger = logging.getLogger(__name__)

# BlueZ mgmt-api.txt § Address Type:
#   0 = BR/EDR, 1 = LE Public, 2 = LE Random.
# Sendspin protocol is BR/EDR-only via BlueZ A2DP Sink, so this is fixed.
_ADDR_TYPE_BREDR = 0

# mgmt-api.txt § GetConnectionInformation: a value of 127 in the rssi
# field means "RSSI not available".  Surfacing it as a number would be
# wildly wrong (Bluetooth tx power tops out around +20 dBm).
_RSSI_UNAVAILABLE_SENTINEL = 127

# Mgmt protocol constants (mgmt-api.txt § Packet Structures + Commands)
_MGMT_OP_GET_CONN_INFO = 0x0031
_MGMT_EV_CMD_COMPLETE = 0x0001
_MGMT_EV_CMD_STATUS = 0x0002

# Maximum wall-clock budget for a single read.  Bluetoothd typically
# answers in well under 100 ms; 2 s is generous and bounds how long a
# stuck controller can block one refresh tick.
_MGMT_DEADLINE_S = 2.0


def _open_mgmt_socket():
    """Open a bound HCI control-channel socket.

    Indirection seam so the syscall stays mockable without touching
    the real kernel interface in tests.  ``btsocket`` is imported
    lazily so non-Linux dev installs (which can't bind the socket
    anyway) don't fail at module load.
    """
    from btsocket import btmgmt_socket  # type: ignore[import-untyped]

    return btmgmt_socket.open()


def _close_mgmt_socket(sock) -> None:
    try:
        from btsocket import btmgmt_socket  # type: ignore[import-untyped]

        btmgmt_socket.close(sock)
    except Exception:
        # Best-effort close; never re-raise during cleanup.
        pass


def _mac_to_le_bytes(mac: str) -> bytes:
    """``'AA:BB:CC:DD:EE:FF'`` → 6-byte little-endian BD_ADDR.

    The mgmt API encodes BD_ADDR with the lowest-order byte first
    (matches the HCI wire format), so the human-readable MAC string
    is reversed before packing.

    Raises ``ValueError`` if the input doesn't have exactly 6 colon-
    separated octets — silently truncating to a shorter BD_ADDR would
    produce a malformed mgmt payload (the kernel would either reject
    with InvalidParameters or, worse, slide the addr_type byte into a
    BD_ADDR slot and address an unintended peer).
    """
    parts = mac.split(":")
    if len(parts) != 6:
        raise ValueError(f"MAC must have 6 octets, got {len(parts)}: {mac!r}")
    return bytes(int(b, 16) for b in reversed(parts))


def _query_rssi_byte(adapter_index: int, mac: str) -> int | None:
    """Return the **unsigned wire byte** of the connection's RSSI, or ``None``.

    Issues a single mgmt command and reads events back until either
    our matching ``CommandComplete``/``CommandStatus`` is seen or the
    deadline expires.  Bluetoothd shares the control channel — we
    must skip unrelated events (index-added/removed, settings
    changes from other clients, etc.) instead of treating the first
    incoming packet as our reply.

    Caller does the sentinel check (127) and unsigned→signed fold.
    """
    try:
        sock = _open_mgmt_socket()
    except ImportError:
        logger.debug("btsocket unavailable — RSSI read skipped for %s", mac)
        return None
    except Exception as exc:
        logger.debug("mgmt socket open failed for %s: %s", mac, exc)
        return None

    try:
        try:
            addr_bytes = _mac_to_le_bytes(mac)
        except (ValueError, AttributeError):
            return None
        payload = addr_bytes + bytes([_ADDR_TYPE_BREDR])
        header = struct.pack("<HHH", _MGMT_OP_GET_CONN_INFO, adapter_index, len(payload))
        try:
            sock.send(header + payload)
        except Exception as exc:
            logger.debug("mgmt send failed for %s: %s", mac, exc)
            return None

        deadline = time.monotonic() + _MGMT_DEADLINE_S
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.debug("mgmt read timeout for %s", mac)
                return None
            try:
                sock.settimeout(remaining)
                data = sock.recv(512)
            except TimeoutError:
                logger.debug("mgmt read timeout for %s", mac)
                return None
            except Exception as exc:
                logger.debug("mgmt recv failed for %s: %s", mac, exc)
                return None
            if len(data) < 6:
                continue
            ev_code, ev_idx = struct.unpack_from("<HH", data, 0)
            # Multi-adapter hosts: bluetoothd and other mgmt clients
            # share the control channel.  Skip events whose controller
            # index doesn't match what we asked for — otherwise we'd
            # treat hci1's reply as ours when querying hci0 (or
            # falsely return None when the actual reply comes next).
            if ev_idx != adapter_index:
                continue
            if len(data) < 9:
                continue
            cmd_op, status = struct.unpack_from("<HB", data, 6)
            if ev_code == _MGMT_EV_CMD_STATUS:
                if cmd_op == _MGMT_OP_GET_CONN_INFO:
                    logger.debug("mgmt CmdStatus=%d for %s", status, mac)
                    return None
                continue
            if ev_code != _MGMT_EV_CMD_COMPLETE or cmd_op != _MGMT_OP_GET_CONN_INFO:
                continue
            if status != 0:
                logger.debug("mgmt status=%d for %s", status, mac)
                return None
            # CommandComplete payload after the status byte:
            #   6 octets BD_ADDR + 1 octet Address_Type + 1 octet RSSI ...
            # The header is 6 bytes, then we wrote opcode (2) + status (1)
            # = byte 9, then echoed addr (6) + addr_type (1) = bytes 9..15,
            # then the RSSI byte at offset 16.
            if len(data) < 17:
                return None
            # Verify the kernel is replying about *our* peer.  Another
            # client could have issued GetConnectionInformation for a
            # different MAC moments earlier; matching only on opcode
            # would let us steal that reading and label it ours.
            if data[9:15] != addr_bytes or data[15] != _ADDR_TYPE_BREDR:
                logger.debug("mgmt reply addr mismatch for %s — skipping", mac)
                continue
            return data[16]
    finally:
        _close_mgmt_socket(sock)


def read_conn_info(adapter_index: int, mac: str) -> int | None:
    """Return the live RSSI (signed dBm) of a connected BR/EDR peer.

    ``adapter_index`` is the integer the kernel uses for the controller
    (``hci0`` → 0, ``hci1`` → 1).  ``BluetoothManager`` resolves it
    from sysfs at startup.

    Returns ``None`` for *every* failure mode:

    - btsocket not installed (non-Linux dev env)
    - adapter index unresolvable (-1 sentinel)
    - mgmt socket EPERM / ENODEV
    - peer not currently connected (mgmt status != Success)
    - response sentinel 127 ("RSSI not available")
    - any other unexpected exception during send/recv/parse

    Never raises.  Logs at DEBUG so the periodic refresh doesn't spam
    INFO when a speaker is briefly down.
    """
    if adapter_index < 0:
        return None
    raw = _query_rssi_byte(adapter_index, mac)
    if raw is None:
        return None
    # mgmt-api.txt names ONLY the raw wire value 127 as "RSSI not
    # available" — check it BEFORE folding so the signed -128 reading
    # (raw byte 0x80, a real very-weak signal) is preserved.
    if raw == _RSSI_UNAVAILABLE_SENTINEL:
        return None
    # Wire byte is unsigned; mgmt-api.txt declares the field as signed int8.
    if raw > 127:
        raw -= 256
    return int(raw)
