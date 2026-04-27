"""Tests for services/hci_avrcp_monitor.py — HCI AVRCP source monitor."""

from __future__ import annotations

import struct

import pytest

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


class TestProcessPacket:
    def _full_acl_frame(self) -> bytes:
        """Build ACL_RX_PKT monitor frame for handle 0x0042 carrying AVRCP Play."""
        avctp = AVRCP_PLAY_PRESSED
        l2cap = struct.pack("<HH", len(avctp), 0x0045) + avctp
        acl = struct.pack("<HH", 0x0042, len(l2cap)) + l2cap
        return _mon_frame(0x0005, acl)

    def test_connection_complete_adds_to_handle_map(self):
        from unittest.mock import MagicMock

        from services.hci_avrcp_monitor import _OPCODE_EVENT_PKT, _process_packet

        event_payload = bytes([0x03, len(CONN_COMPLETE_ACL)]) + CONN_COMPLETE_ACL
        frame = _mon_frame(_OPCODE_EVENT_PKT, event_payload)
        h2m: dict = {}
        _process_packet(frame, h2m, MagicMock())
        assert h2m == {0x0042: "FC:58:FA:EB:08:6C"}

    def test_disconnect_complete_removes_from_handle_map(self):
        from unittest.mock import MagicMock

        from services.hci_avrcp_monitor import _OPCODE_EVENT_PKT, _process_packet

        event_payload = bytes([0x05, len(DISC_COMPLETE)]) + DISC_COMPLETE
        frame = _mon_frame(_OPCODE_EVENT_PKT, event_payload)
        h2m = {0x0042: "FC:58:FA:EB:08:6C"}
        _process_packet(frame, h2m, MagicMock())
        assert 0x0042 not in h2m

    def test_avrcp_play_calls_note_activity(self):
        from unittest.mock import MagicMock

        from services.hci_avrcp_monitor import _process_packet

        h2m = {0x0042: "FC:58:FA:EB:08:6C"}
        tracker = MagicMock()
        _process_packet(self._full_acl_frame(), h2m, tracker)
        tracker.note_activity.assert_called_once_with("FC:58:FA:EB:08:6C")

    def test_avrcp_unknown_handle_does_not_call_tracker(self):
        from unittest.mock import MagicMock

        from services.hci_avrcp_monitor import _process_packet

        tracker = MagicMock()
        _process_packet(self._full_acl_frame(), {}, tracker)
        tracker.note_activity.assert_not_called()

    def test_too_short_frame_does_not_raise(self):
        from unittest.mock import MagicMock

        from services.hci_avrcp_monitor import _process_packet

        _process_packet(b"\x03\x00", {}, MagicMock())  # must not raise

    def test_unknown_opcode_is_ignored(self):
        from unittest.mock import MagicMock

        from services.hci_avrcp_monitor import _process_packet

        frame = _mon_frame(0x0099, b"\x00" * 8)
        tracker = MagicMock()
        _process_packet(frame, {}, tracker)
        tracker.note_activity.assert_not_called()


class TestHciAvrcpMonitorLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_background_task(self):
        from unittest.mock import MagicMock, patch

        from services.hci_avrcp_monitor import HciAvrcpMonitor

        mon = HciAvrcpMonitor()
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = OSError("closed")
        with patch(
            "services.hci_avrcp_monitor._open_hci_monitor_socket",
            return_value=mock_sock,
        ):
            await mon.start()
            assert mon._task is not None
            await mon.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_and_clears_task(self):
        import asyncio
        from unittest.mock import MagicMock, patch

        from services.hci_avrcp_monitor import HciAvrcpMonitor

        mon = HciAvrcpMonitor()
        mock_sock = MagicMock()

        async def _block(*_):
            await asyncio.sleep(9999)

        with (
            patch(
                "services.hci_avrcp_monitor._open_hci_monitor_socket",
                return_value=mock_sock,
            ),
            patch("asyncio.to_thread", new=_block),
        ):
            await mon.start()
            await mon.stop()
        assert mon._task is None

    @pytest.mark.asyncio
    async def test_oserror_on_socket_open_disables_monitor(self):
        import asyncio
        from unittest.mock import patch

        from services.hci_avrcp_monitor import HciAvrcpMonitor

        mon = HciAvrcpMonitor()
        with patch(
            "services.hci_avrcp_monitor._open_hci_monitor_socket",
            side_effect=OSError("EPERM"),
        ):
            await mon.start()
            await asyncio.sleep(0.05)
        assert mon._task is None or mon._task.done()

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self):
        from unittest.mock import patch

        from services.hci_avrcp_monitor import HciAvrcpMonitor

        mon = HciAvrcpMonitor()
        with patch(
            "services.hci_avrcp_monitor._open_hci_monitor_socket",
            side_effect=OSError,
        ):
            await mon.start()
            task1 = mon._task
            await mon.start()
            assert mon._task is task1

    def test_get_monitor_returns_singleton(self):
        from services.hci_avrcp_monitor import get_monitor

        assert get_monitor() is get_monitor()
