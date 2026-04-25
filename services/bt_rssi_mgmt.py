"""Live-RSSI source for connected BR/EDR speakers.

Wraps BlueZ kernel mgmt opcode 0x0031
(``MGMT_OP_GET_CONN_INFO``) via the ``btsocket`` library.  This is the
*only* path on Linux that exposes RSSI for an already-connected peer:

- ``bluetoothctl scan bredr`` only emits ``[CHG] Device <MAC> RSSI:``
  for *advertising* devices; connected peers stop responding to inquiry.
- ``bluetoothctl info <MAC>`` does not include an RSSI line for
  connected peers — BlueZ never queries the link.
- ``org.bluez.Device1.RSSI`` D-Bus property is populated only during
  active discovery and only for advertising devices.

The mgmt socket asks the kernel HCI layer to issue ``HCI_Read_RSSI``
on the established ACL link and returns the controller-measured value.
Requires ``CAP_NET_ADMIN`` (the bridge already has it).

The wrapper is deliberately defensive: every failure path collapses to
``None`` so the caller's contract is "fresh value or keep last known —
never propagate an exception into the asyncio refresh loop".
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# BlueZ mgmt-api.txt § Address Type:
#   0 = BR/EDR, 1 = LE Public, 2 = LE Random.
# Sendspin protocol is BR/EDR-only via BlueZ A2DP Sink, so this is fixed.
_ADDR_TYPE_BREDR = 0

# mgmt-api.txt § GetConnectionInformation: a value of 127 in the rssi
# field means "RSSI not available".  Surfacing it as a number would be
# wildly wrong (Bluetooth tx power tops out around +20 dBm).
_RSSI_UNAVAILABLE_SENTINEL = 127


def _get_btmgmt_sync():
    """Indirection seam so tests can monkey-patch without installing
    btsocket and without touching ``sys.modules``.  Production calls
    flow through this exactly once per ``read_conn_info`` invocation."""
    from btsocket import btmgmt_sync  # type: ignore[import-untyped]

    return btmgmt_sync


def read_conn_info(adapter_index: int, mac: str) -> int | None:
    """Return the live RSSI (signed dBm) of a connected BR/EDR peer.

    ``adapter_index`` is the integer the kernel uses for the controller
    (``hci0`` → 0, ``hci1`` → 1).  ``BluetoothManager`` resolves it
    from sysfs at startup.

    Returns ``None`` for *every* failure mode:

    - btsocket not installed (non-Linux dev env)
    - adapter index unresolvable (-1 sentinel)
    - mgmt socket EPERM / ENODEV
    - peer not currently connected
    - mgmt response status != Success
    - response sentinel 127 ("RSSI not available")
    - any other unexpected exception during send/parse

    Never raises.  Logs at DEBUG so the periodic refresh doesn't spam
    INFO when a speaker is briefly down.
    """
    if adapter_index < 0:
        return None

    try:
        sync = _get_btmgmt_sync()
    except ImportError:
        logger.debug("btsocket unavailable — RSSI read skipped for %s", mac)
        return None

    try:
        rsp = sync.send("GetConnectionInformation", adapter_index, mac, _ADDR_TYPE_BREDR)
    except Exception as exc:
        logger.debug("MGMT GetConnectionInformation send failed for %s: %s", mac, exc)
        return None

    status = getattr(getattr(rsp, "event_frame", None), "status", None)
    if status != "Success":
        logger.debug("MGMT GetConnectionInformation status=%s for %s", status, mac)
        return None

    raw = getattr(getattr(rsp, "cmd_response_frame", None), "rssi", None)
    if raw is None:
        return None

    # btsocket reports the wire byte unsigned; mgmt-api.txt declares it signed.
    if raw > 127:
        raw -= 256
    if raw == _RSSI_UNAVAILABLE_SENTINEL or raw == -_RSSI_UNAVAILABLE_SENTINEL - 1:
        return None
    return int(raw)
