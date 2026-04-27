"""HCI-level AVRCP passthrough command monitor.

Watches raw HCI_CHANNEL_MONITOR traffic to identify the source MAC for inbound
AVRCP passthrough commands. Updates AvrcpSourceTracker before BlueZ's D-Bus MPRIS
dispatch arrives — fixes Next/Previous mis-routing and timing race on Play/Pause.

Graceful degradation: if the socket can't be opened (non-Linux, missing CAP_NET_RAW,
no BT adapter), logs at INFO and does nothing. The D-Bus heuristic in
device_activation._subscribe_avrcp_source_tracker remains active as fallback.
"""

from __future__ import annotations

import logging
import struct
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
