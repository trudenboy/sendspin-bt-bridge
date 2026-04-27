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
