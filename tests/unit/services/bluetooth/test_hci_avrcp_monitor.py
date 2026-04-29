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
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_connection_complete

        result = _parse_connection_complete(CONN_COMPLETE_ACL)
        assert result == (0x0042, "FC:58:FA:EB:08:6C")

    def test_sco_link_returns_none(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_connection_complete

        assert _parse_connection_complete(CONN_COMPLETE_SCO) is None

    def test_nonzero_status_returns_none(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_connection_complete

        assert _parse_connection_complete(CONN_COMPLETE_FAIL) is None

    def test_too_short_returns_none(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_connection_complete

        assert _parse_connection_complete(b"\x00\x42") is None


class TestParseDisconnectComplete:
    def test_success_returns_handle(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_disconnect_complete

        assert _parse_disconnect_complete(DISC_COMPLETE) == 0x0042

    def test_nonzero_status_returns_none(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_disconnect_complete

        assert _parse_disconnect_complete(DISC_COMPLETE_FAIL) is None

    def test_too_short_returns_none(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_disconnect_complete

        assert _parse_disconnect_complete(b"\x00") is None


class TestParseAvrcpPassthrough:
    def test_play_pressed_returns_op_id(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVRCP_PLAY_PRESSED) == 0x44

    def test_next_pressed_returns_op_id(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVRCP_NEXT_PRESSED) == 0x4B

    def test_released_returns_none(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVRCP_PLAY_RELEASED) is None

    def test_wrong_pid_returns_none(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVCTP_WRONG_PID) is None

    def test_response_cr_bit_returns_none(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVCTP_RESPONSE) is None

    def test_invalid_pid_flag_returns_none(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVCTP_INVALID_PID) is None

    def test_wrong_subunit_returns_none(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVC_WRONG_SUBUNIT) is None

    def test_wrong_opcode_returns_none(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_avrcp_passthrough

        assert _parse_avrcp_passthrough(AVC_WRONG_OPCODE) is None

    def test_too_short_returns_none(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _parse_avrcp_passthrough

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

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _OPCODE_EVENT_PKT, _process_packet

        event_payload = bytes([0x03, len(CONN_COMPLETE_ACL)]) + CONN_COMPLETE_ACL
        frame = _mon_frame(_OPCODE_EVENT_PKT, event_payload)
        h2m: dict = {}
        _process_packet(frame, h2m, MagicMock())
        assert h2m == {0x0042: "FC:58:FA:EB:08:6C"}

    def test_disconnect_complete_removes_from_handle_map(self):
        from unittest.mock import MagicMock

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _OPCODE_EVENT_PKT, _process_packet

        event_payload = bytes([0x05, len(DISC_COMPLETE)]) + DISC_COMPLETE
        frame = _mon_frame(_OPCODE_EVENT_PKT, event_payload)
        h2m = {0x0042: "FC:58:FA:EB:08:6C"}
        _process_packet(frame, h2m, MagicMock())
        assert 0x0042 not in h2m

    def test_avrcp_play_calls_note_activity(self):
        from unittest.mock import MagicMock

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _process_packet

        h2m = {0x0042: "FC:58:FA:EB:08:6C"}
        tracker = MagicMock()
        _process_packet(self._full_acl_frame(), h2m, tracker)
        tracker.note_activity.assert_called_once_with("FC:58:FA:EB:08:6C")

    def test_avrcp_unknown_handle_does_not_call_tracker(self):
        from unittest.mock import MagicMock

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _process_packet

        tracker = MagicMock()
        _process_packet(self._full_acl_frame(), {}, tracker)
        tracker.note_activity.assert_not_called()

    def test_too_short_frame_does_not_raise(self):
        from unittest.mock import MagicMock

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _process_packet

        _process_packet(b"\x03\x00", {}, MagicMock())  # must not raise

    def test_unknown_opcode_is_ignored(self):
        from unittest.mock import MagicMock

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _process_packet

        frame = _mon_frame(0x0099, b"\x00" * 8)
        tracker = MagicMock()
        _process_packet(frame, {}, tracker)
        tracker.note_activity.assert_not_called()


class TestHciAvrcpMonitorLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_background_task(self):
        from unittest.mock import MagicMock, patch

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import HciAvrcpMonitor

        mon = HciAvrcpMonitor()
        mock_sock = MagicMock()
        mock_sock.recv.side_effect = OSError("closed")
        with patch(
            "sendspin_bridge.services.bluetooth.hci_avrcp_monitor._open_hci_monitor_socket",
            return_value=mock_sock,
        ):
            await mon.start()
            assert mon._task is not None
            await mon.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_and_clears_task(self):
        import asyncio
        from unittest.mock import MagicMock, patch

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import HciAvrcpMonitor

        mon = HciAvrcpMonitor()
        mock_sock = MagicMock()

        async def _block(*_):
            await asyncio.sleep(9999)

        with (
            patch(
                "sendspin_bridge.services.bluetooth.hci_avrcp_monitor._open_hci_monitor_socket",
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

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import HciAvrcpMonitor

        mon = HciAvrcpMonitor()
        with patch(
            "sendspin_bridge.services.bluetooth.hci_avrcp_monitor._open_hci_monitor_socket",
            side_effect=OSError("EPERM"),
        ):
            await mon.start()
            await asyncio.sleep(0.05)
        assert mon._task is None or mon._task.done()

    @pytest.mark.asyncio
    async def test_double_start_is_noop(self):
        from unittest.mock import patch

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import HciAvrcpMonitor

        mon = HciAvrcpMonitor()
        with patch(
            "sendspin_bridge.services.bluetooth.hci_avrcp_monitor._open_hci_monitor_socket",
            side_effect=OSError,
        ):
            await mon.start()
            task1 = mon._task
            await mon.start()
            assert mon._task is task1

    def test_get_monitor_returns_singleton(self):
        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import get_monitor

        assert get_monitor() is get_monitor()


class TestSeedHandleMapFromKernel:
    """At startup, connections that exist BEFORE the monitor binds are not
    replayed to HCI_CHANNEL_MONITOR — only future Connection Complete events
    are visible.  Without seeding from the kernel's existing connection
    list (HCIGETCONNLIST ioctl), every ACL packet from a pre-existing
    connection silently drops because handle_to_mac is empty, and inbound
    AVRCP commands silently fall back to default_client.
    """

    def test_seed_populates_handles_from_ioctl(self):
        from unittest.mock import patch

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _seed_handle_map_from_kernel

        # Fake ioctl returns 2 connections on hci0, no devices on hci1+
        def fake_ioctl(fd, request, req):
            # request 0x800448D2 = HCIGETDEVLIST → return one device (id=0)
            # request 0x800448D4 = HCIGETCONNLIST → fill conn_info for dev_id=0
            if request == 0x800448D2:
                # hci_dev_list_req: dev_num u16, [hci_dev_req {dev_id u16, dev_opt u32}]
                req.dev_num = 1
                req.dev_req[0].dev_id = 0
                return 0
            if request == 0x800448D4:
                if req.dev_id != 0:
                    return -1
                req.conn_num = 2
                req.conn_info[0].handle = 0x0047
                req.conn_info[0].bdaddr[:] = [0x99, 0x17, 0x35, 0x3D, 0x5C, 0x6C]  # LE reversed
                req.conn_info[0].type = 1  # ACL
                req.conn_info[1].handle = 0x0048
                req.conn_info[1].bdaddr[:] = [0xD3, 0x0B, 0xC2, 0xE7, 0x99, 0x80]
                req.conn_info[1].type = 1
                return 0
            return -1

        h2m: dict[int, str] = {}
        with (
            patch("sendspin_bridge.services.bluetooth.hci_avrcp_monitor.sys.platform", "linux"),
            patch("sendspin_bridge.services.bluetooth.hci_avrcp_monitor.socket.socket"),
            patch("sendspin_bridge.services.bluetooth.hci_avrcp_monitor._hci_ioctl", side_effect=fake_ioctl),
        ):
            _seed_handle_map_from_kernel(h2m)

        assert h2m == {
            0x0047: "6C:5C:3D:35:17:99",
            0x0048: "80:99:E7:C2:0B:D3",
        }

    def test_seed_skips_non_acl_connections(self):
        """SCO links (type=0) shouldn't pollute the table — AVRCP runs over
        L2CAP on ACL only."""
        from unittest.mock import patch

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _seed_handle_map_from_kernel

        def fake_ioctl(fd, request, req):
            if request == 0x800448D2:
                req.dev_num = 1
                req.dev_req[0].dev_id = 0
                return 0
            if request == 0x800448D4:
                req.conn_num = 1
                req.conn_info[0].handle = 0x0049
                req.conn_info[0].bdaddr[:] = [1, 2, 3, 4, 5, 6]
                req.conn_info[0].type = 0  # SCO, not ACL
                return 0
            return -1

        h2m: dict[int, str] = {}
        with (
            patch("sendspin_bridge.services.bluetooth.hci_avrcp_monitor.sys.platform", "linux"),
            patch("sendspin_bridge.services.bluetooth.hci_avrcp_monitor.socket.socket"),
            patch("sendspin_bridge.services.bluetooth.hci_avrcp_monitor._hci_ioctl", side_effect=fake_ioctl),
        ):
            _seed_handle_map_from_kernel(h2m)

        assert h2m == {}

    def test_seed_oserror_is_silent_noop(self):
        """If the ioctl path fails (no caps, kernel build w/o HCI mgmt, etc.),
        the seeder must not raise — the monitor should keep running and
        rely on Connection Complete events for new connections."""
        from unittest.mock import patch

        from sendspin_bridge.services.bluetooth.hci_avrcp_monitor import _seed_handle_map_from_kernel

        def fake_ioctl(fd, request, req):
            return -1

        h2m: dict[int, str] = {}
        with (
            patch("sendspin_bridge.services.bluetooth.hci_avrcp_monitor.sys.platform", "linux"),
            patch("sendspin_bridge.services.bluetooth.hci_avrcp_monitor.socket.socket"),
            patch("sendspin_bridge.services.bluetooth.hci_avrcp_monitor._hci_ioctl", side_effect=fake_ioctl),
        ):
            _seed_handle_map_from_kernel(h2m)  # must not raise

        assert h2m == {}
