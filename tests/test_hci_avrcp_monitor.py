"""Tests for services/hci_avrcp_monitor.py — HCI AVRCP source monitor."""

from __future__ import annotations

import struct

# ── Concrete test bytes ────────────────────────────────────────────────────
CONN_COMPLETE_ACL = bytes([0x00, 0x42, 0x00, 0x6C, 0x08, 0xEB, 0xFA, 0x58, 0xFC, 0x01, 0x00])
CONN_COMPLETE_SCO = bytes([0x00, 0x42, 0x00, 0x6C, 0x08, 0xEB, 0xFA, 0x58, 0xFC, 0x00, 0x00])
CONN_COMPLETE_FAIL = bytes([0x04, 0x42, 0x00, 0x6C, 0x08, 0xEB, 0xFA, 0x58, 0xFC, 0x01, 0x00])
DISC_COMPLETE = bytes([0x00, 0x42, 0x00, 0x13])
DISC_COMPLETE_FAIL = bytes([0x04, 0x42, 0x00, 0x13])
AVRCP_PLAY_PRESSED = bytes([0x00, 0x11, 0x0E, 0x00, 0x48, 0x7C, 0x44, 0x00])
AVRCP_NEXT_PRESSED = bytes([0x00, 0x11, 0x0E, 0x00, 0x48, 0x7C, 0x4B, 0x00])
AVRCP_PLAY_RELEASED = bytes([0x00, 0x11, 0x0E, 0x00, 0x48, 0x7C, 0xC4, 0x00])
AVCTP_WRONG_PID = bytes([0x00, 0x11, 0x0B, 0x00, 0x48, 0x7C, 0x44, 0x00])
AVCTP_RESPONSE = bytes([0x02, 0x11, 0x0E, 0x00, 0x48, 0x7C, 0x44, 0x00])
AVCTP_INVALID_PID = bytes([0x01, 0x11, 0x0E, 0x00, 0x48, 0x7C, 0x44, 0x00])
AVC_WRONG_SUBUNIT = bytes([0x00, 0x11, 0x0E, 0x00, 0x20, 0x7C, 0x44, 0x00])
AVC_WRONG_OPCODE = bytes([0x00, 0x11, 0x0E, 0x00, 0x48, 0x11, 0x44, 0x00])


def _mon_frame(opcode: int, payload: bytes) -> bytes:
    return struct.pack("<HHH", opcode, 0, len(payload)) + payload


class TestParseConnectionComplete:
    def test_acl_link_success_returns_handle_and_mac(self):
        from services.hci_avrcp_monitor import _parse_connection_complete

        result = _parse_connection_complete(CONN_COMPLETE_ACL)
        assert result == (0x0042, "FC:58:FA:EB:08:6C")

    def test_sco_link_returns_none(self):
        from services.hci_avrcp_monitor import _parse_connection_complete

        assert _parse_connection_complete(CONN_COMPLETE_SCO) is None

    def test_nonzero_status_returns_none(self):
        from services.hci_avrcp_monitor import _parse_connection_complete

        assert _parse_connection_complete(CONN_COMPLETE_FAIL) is None

    def test_too_short_returns_none(self):
        from services.hci_avrcp_monitor import _parse_connection_complete

        assert _parse_connection_complete(b"\x00\x42") is None


class TestParseDisconnectComplete:
    def test_success_returns_handle(self):
        from services.hci_avrcp_monitor import _parse_disconnect_complete

        assert _parse_disconnect_complete(DISC_COMPLETE) == 0x0042

    def test_nonzero_status_returns_none(self):
        from services.hci_avrcp_monitor import _parse_disconnect_complete

        assert _parse_disconnect_complete(DISC_COMPLETE_FAIL) is None

    def test_too_short_returns_none(self):
        from services.hci_avrcp_monitor import _parse_disconnect_complete

        assert _parse_disconnect_complete(b"\x00") is None


class TestParseAvrcpPassthrough:
    def test_play_pressed_returns_op_id(self):
        from services.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVRCP_PLAY_PRESSED) == 0x44

    def test_next_pressed_returns_op_id(self):
        from services.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVRCP_NEXT_PRESSED) == 0x4B

    def test_released_returns_none(self):
        from services.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVRCP_PLAY_RELEASED) is None

    def test_wrong_pid_returns_none(self):
        from services.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVCTP_WRONG_PID) is None

    def test_response_cr_bit_returns_none(self):
        from services.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVCTP_RESPONSE) is None

    def test_invalid_pid_flag_returns_none(self):
        from services.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVCTP_INVALID_PID) is None

    def test_wrong_subunit_returns_none(self):
        from services.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVC_WRONG_SUBUNIT) is None

    def test_wrong_opcode_returns_none(self):
        from services.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVC_WRONG_OPCODE) is None

    def test_too_short_returns_none(self):
        from services.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(b"\x00\x11\x0e") is None
