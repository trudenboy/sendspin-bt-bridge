"""Tests for services/sink_monitor.py — PA sink state monitoring."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.sink_monitor import SinkMonitor, extract_mac_from_sink

# ── MAC extraction ────────────────────────────────────────────────────────


class TestExtractMac:
    """Test MAC extraction from various bluez sink name patterns."""

    def test_pulseaudio_a2dp_sink(self):
        assert extract_mac_from_sink("bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink") == "FC:58:FA:EB:08:6C"

    def test_pulseaudio_bare(self):
        assert extract_mac_from_sink("bluez_sink.FC_58_FA_EB_08_6C") == "FC:58:FA:EB:08:6C"

    def test_pipewire_a2dp_sink(self):
        assert extract_mac_from_sink("bluez_output.FC_58_FA_EB_08_6C.a2dp-sink") == "FC:58:FA:EB:08:6C"

    def test_pipewire_numeric(self):
        assert extract_mac_from_sink("bluez_output.FC_58_FA_EB_08_6C.1") == "FC:58:FA:EB:08:6C"

    def test_lowercase_mac(self):
        assert extract_mac_from_sink("bluez_sink.fc_58_fa_eb_08_6c.a2dp_sink") == "FC:58:FA:EB:08:6C"

    def test_non_bluez_sink_returns_none(self):
        assert extract_mac_from_sink("alsa_output.pci-0000_00_1f.3.analog-stereo") is None

    def test_null_sink_returns_none(self):
        assert extract_mac_from_sink("sendspin_fallback") is None

    def test_empty_string(self):
        assert extract_mac_from_sink("") is None

    def test_partial_mac(self):
        assert extract_mac_from_sink("bluez_sink.FC_58_FA") is None


# ── Callback registration ────────────────────────────────────────────────


class TestRegistration:
    """Test device registration and unregistration."""

    def test_register_stores_callbacks(self):
        mon = SinkMonitor()
        cb_active = MagicMock()
        cb_idle = MagicMock()
        mon.register("FC:58:FA:EB:08:6C", "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", cb_active, cb_idle)
        assert "FC:58:FA:EB:08:6C" in mon._callbacks

    def test_unregister_removes_callbacks(self):
        mon = SinkMonitor()
        mon.register("FC:58:FA:EB:08:6C", "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", MagicMock(), MagicMock())
        mon.unregister("FC:58:FA:EB:08:6C")
        assert "FC:58:FA:EB:08:6C" not in mon._callbacks
        assert "FC:58:FA:EB:08:6C" not in mon._sink_names

    def test_unregister_nonexistent_is_noop(self):
        mon = SinkMonitor()
        mon.unregister("AA:BB:CC:DD:EE:FF")  # should not raise

    def test_register_replaces_existing(self):
        mon = SinkMonitor()
        cb1 = MagicMock()
        cb2 = MagicMock()
        mon.register("FC:58:FA:EB:08:6C", "sink1", cb1, cb1)
        mon.register("FC:58:FA:EB:08:6C", "sink2", cb2, cb2)
        assert mon._sink_names["FC:58:FA:EB:08:6C"] == "sink2"

    def test_register_dispatches_known_running_state(self):
        """If sink was already observed as running before register(), on_active fires immediately."""
        mon = SinkMonitor()
        # Simulate pre-registration state observation
        mon._sink_states["bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"] = "running"
        on_active = MagicMock()
        on_idle = MagicMock()
        mon.register("FC:58:FA:EB:08:6C", "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", on_active, on_idle)
        on_active.assert_called_once()
        on_idle.assert_not_called()

    def test_register_dispatches_known_idle_state(self):
        """If sink was already observed as idle before register(), on_idle fires immediately."""
        mon = SinkMonitor()
        mon._sink_states["bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"] = "idle"
        on_active = MagicMock()
        on_idle = MagicMock()
        mon.register("FC:58:FA:EB:08:6C", "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", on_active, on_idle)
        on_idle.assert_called_once()
        on_active.assert_not_called()

    def test_register_no_dispatch_when_no_known_state(self):
        """No callbacks fire on register() if sink state has not been observed yet."""
        mon = SinkMonitor()
        on_active = MagicMock()
        on_idle = MagicMock()
        mon.register("FC:58:FA:EB:08:6C", "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", on_active, on_idle)
        on_active.assert_not_called()
        on_idle.assert_not_called()

    def test_reverse_map_maintained(self):
        mon = SinkMonitor()
        mon.register("FC:58:FA:EB:08:6C", "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", MagicMock(), MagicMock())
        assert mon._sink_name_to_mac["bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"] == "FC:58:FA:EB:08:6C"
        mon.unregister("FC:58:FA:EB:08:6C")
        assert "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink" not in mon._sink_name_to_mac


# ── State classification ────────────────────────────────────────────────


class TestClassifyState:
    """Test PA sink state integer → string classification."""

    def test_running(self):
        assert SinkMonitor._classify_state(0) == "running"

    def test_idle(self):
        assert SinkMonitor._classify_state(1) == "idle"

    def test_suspended(self):
        assert SinkMonitor._classify_state(2) == "suspended"

    def test_unknown(self):
        assert SinkMonitor._classify_state(-1) == "unknown"

    def test_pulsectl_enum_value(self):
        """pulsectl returns EnumValue that supports == with strings but NOT int()."""

        class _EnumValue:
            """Mimics pulsectl's real EnumValue: _c_val (int), _value (str),
            __eq__ with strings, but no __int__."""

            def __init__(self, c_val: int, value: str):
                self._c_val = c_val
                self._value = value

            def __eq__(self, other: object) -> bool:
                if isinstance(other, str):
                    return self._value == other
                if isinstance(other, type(self)):
                    return self._c_val == other._c_val
                return NotImplemented

            def __repr__(self) -> str:
                return f"<EnumValue sink/source-state={self._value}>"

        assert SinkMonitor._classify_state(_EnumValue(0, "running")) == "running"
        assert SinkMonitor._classify_state(_EnumValue(1, "idle")) == "idle"
        assert SinkMonitor._classify_state(_EnumValue(2, "suspended")) == "suspended"
        assert SinkMonitor._classify_state(_EnumValue(99, "bogus")) == "unknown"


# ── Event handling (transition dispatch) ──────────────────────────────────


class TestHandleChange:
    """Test sink state change event handling and callback dispatch."""

    def _make_monitor(self):
        mon = SinkMonitor()
        self.on_active = MagicMock()
        self.on_idle = MagicMock()
        mon.register(
            "FC:58:FA:EB:08:6C",
            "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink",
            self.on_active,
            self.on_idle,
        )
        return mon

    @staticmethod
    def _make_sink_info(name: str, state: int):
        info = MagicMock()
        info.name = name
        info.state = state
        return info

    @pytest.mark.asyncio
    async def test_idle_to_running_fires_on_active(self):
        mon = self._make_monitor()
        pulse = AsyncMock()

        # Simulate: previously idle, now running
        mon._sink_states["bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"] = "idle"
        pulse.sink_info.return_value = self._make_sink_info(
            "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink",
            0,  # running
        )

        await mon._handle_sink_change(pulse, 42)
        self.on_active.assert_called_once()
        self.on_idle.assert_not_called()

    @pytest.mark.asyncio
    async def test_running_to_idle_fires_on_idle(self):
        mon = self._make_monitor()
        pulse = AsyncMock()

        mon._sink_states["bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"] = "running"
        pulse.sink_info.return_value = self._make_sink_info(
            "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink",
            1,  # idle
        )

        await mon._handle_sink_change(pulse, 42)
        self.on_idle.assert_called_once()
        self.on_active.assert_not_called()

    @pytest.mark.asyncio
    async def test_running_to_suspended_fires_on_idle(self):
        mon = self._make_monitor()
        pulse = AsyncMock()

        mon._sink_states["bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"] = "running"
        pulse.sink_info.return_value = self._make_sink_info(
            "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink",
            2,  # suspended
        )

        await mon._handle_sink_change(pulse, 42)
        self.on_idle.assert_called_once()

    @pytest.mark.asyncio
    async def test_same_state_no_duplicate_callback(self):
        mon = self._make_monitor()
        pulse = AsyncMock()

        mon._sink_states["bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"] = "idle"
        pulse.sink_info.return_value = self._make_sink_info(
            "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink",
            1,  # idle again
        )

        await mon._handle_sink_change(pulse, 42)
        self.on_active.assert_not_called()
        self.on_idle.assert_not_called()

    @pytest.mark.asyncio
    async def test_unregistered_sink_ignored(self):
        mon = self._make_monitor()
        pulse = AsyncMock()

        pulse.sink_info.return_value = self._make_sink_info(
            "alsa_output.pci.analog-stereo",
            0,
        )

        await mon._handle_sink_change(pulse, 99)
        self.on_active.assert_not_called()
        self.on_idle.assert_not_called()

    @pytest.mark.asyncio
    async def test_first_event_running_fires_on_active(self):
        """First event ever for a sink — no previous state. running should fire on_active."""
        mon = self._make_monitor()
        pulse = AsyncMock()

        pulse.sink_info.return_value = self._make_sink_info(
            "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink",
            0,  # running
        )

        await mon._handle_sink_change(pulse, 42)
        self.on_active.assert_called_once()

    @pytest.mark.asyncio
    async def test_first_event_idle_fires_on_idle(self):
        """First event ever for a sink — idle should fire on_idle."""
        mon = self._make_monitor()
        pulse = AsyncMock()

        pulse.sink_info.return_value = self._make_sink_info(
            "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink",
            1,  # idle
        )

        await mon._handle_sink_change(pulse, 42)
        self.on_idle.assert_called_once()

    @pytest.mark.asyncio
    async def test_sink_info_error_is_swallowed(self):
        """If sink_info raises, don't crash the monitor."""
        mon = self._make_monitor()
        pulse = AsyncMock()
        pulse.sink_info.side_effect = Exception("PA error")

        await mon._handle_sink_change(pulse, 42)
        self.on_active.assert_not_called()
        self.on_idle.assert_not_called()

    @pytest.mark.asyncio
    async def test_unregistered_bluez_sink_state_is_tracked(self):
        """State is stored for unregistered bluez sinks so register() can catch up."""
        mon = SinkMonitor()  # no registrations
        pulse = AsyncMock()

        pulse.sink_info.return_value = self._make_sink_info(
            "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink",
            0,  # running
        )

        await mon._handle_sink_change(pulse, 42)
        assert mon._sink_states["bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"] == "running"

    @pytest.mark.asyncio
    async def test_non_bluez_sink_state_not_tracked(self):
        """Non-bluez sinks are ignored entirely."""
        mon = SinkMonitor()
        pulse = AsyncMock()

        pulse.sink_info.return_value = self._make_sink_info(
            "alsa_output.pci.analog-stereo",
            0,
        )

        await mon._handle_sink_change(pulse, 42)
        assert "alsa_output.pci.analog-stereo" not in mon._sink_states


# ── Sink removal ──────────────────────────────────────────────────────────


class TestHandleRemove:
    """Test sink removal event handling."""

    @pytest.mark.asyncio
    async def test_remove_running_sink_fires_on_idle(self):
        mon = SinkMonitor()
        on_active = MagicMock()
        on_idle = MagicMock()
        mac = "FC:58:FA:EB:08:6C"
        sink = "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"
        mon.register(mac, sink, on_active, on_idle)
        mon._sink_states[sink] = "running"
        mon._sink_index_to_name[42] = sink

        mon._handle_sink_remove(42)
        on_idle.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_idle_sink_no_callback(self):
        mon = SinkMonitor()
        on_active = MagicMock()
        on_idle = MagicMock()
        mac = "FC:58:FA:EB:08:6C"
        sink = "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"
        mon.register(mac, sink, on_active, on_idle)
        mon._sink_states[sink] = "idle"
        mon._sink_index_to_name[42] = sink

        mon._handle_sink_remove(42)
        on_idle.assert_not_called()

    @pytest.mark.asyncio
    async def test_remove_unknown_index_is_noop(self):
        mon = SinkMonitor()
        mon._handle_sink_remove(999)  # should not raise


# ── Graceful degradation ─────────────────────────────────────────────────


class TestGracefulFallback:
    """Test behavior when pulsectl is unavailable."""

    @pytest.mark.asyncio
    async def test_start_without_pulsectl_is_noop(self):
        mon = SinkMonitor()
        with patch("services.sink_monitor._PULSECTL_AVAILABLE", False):
            await mon.start()
        assert mon._task is None

    @pytest.mark.asyncio
    async def test_stop_without_start_is_noop(self):
        mon = SinkMonitor()
        await mon.stop()  # should not raise

    def test_available_property(self):
        mon = SinkMonitor()
        with patch("services.sink_monitor._PULSECTL_AVAILABLE", True):
            # Not started yet — not available even if pulsectl exists
            assert mon.available is False
        with patch("services.sink_monitor._PULSECTL_AVAILABLE", False):
            assert mon.available is False


# ── Monitor loop ──────────────────────────────────────────────────────────


class TestMonitorLoop:
    """Test the main monitoring loop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        mon = SinkMonitor()
        with (
            patch("services.sink_monitor._PULSECTL_AVAILABLE", True),
            patch.object(mon, "_monitor_loop", new_callable=AsyncMock),
        ):
            await mon.start()
            assert mon._task is not None
            # Clean up
            await mon.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_task(self):
        mon = SinkMonitor()

        # Create a dummy long-running task
        async def _dummy():
            await asyncio.sleep(3600)

        mon._task = asyncio.create_task(_dummy())
        await mon.stop()
        assert mon._task is None


# ── Initial sink scan ────────────────────────────────────────────────────


class TestScanAllSinks:
    """Test the initial sink scan on PA connect/reconnect."""

    @staticmethod
    def _make_sink_info(name: str, state: int, index: int = 42):
        info = MagicMock()
        info.name = name
        info.state = state
        info.index = index
        return info

    @pytest.mark.asyncio
    async def test_scan_populates_sink_states(self):
        """Scan stores state for all bluez sinks."""
        mon = SinkMonitor()
        pulse = AsyncMock()
        pulse.sink_list.return_value = [
            self._make_sink_info("bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", 0, index=10),
            self._make_sink_info("alsa_output.pci.analog-stereo", 1, index=11),
            self._make_sink_info("bluez_output.AA_BB_CC_DD_EE_FF.1", 1, index=12),
        ]

        await mon._scan_all_sinks(pulse)
        assert mon._sink_states["bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"] == "running"
        assert mon._sink_states["bluez_output.AA_BB_CC_DD_EE_FF.1"] == "idle"
        assert "alsa_output.pci.analog-stereo" not in mon._sink_states

    @pytest.mark.asyncio
    async def test_scan_fires_callbacks_for_registered_sinks(self):
        """Scan fires on_active/on_idle for registered sinks with state changes."""
        mon = SinkMonitor()
        on_active = MagicMock()
        on_idle = MagicMock()
        mon.register("FC:58:FA:EB:08:6C", "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", on_active, on_idle)
        # Clear any immediate dispatch from register()
        on_active.reset_mock()
        on_idle.reset_mock()

        pulse = AsyncMock()
        pulse.sink_list.return_value = [
            self._make_sink_info("bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", 0, index=10),
        ]

        await mon._scan_all_sinks(pulse)
        on_active.assert_called_once()
        on_idle.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_no_duplicate_for_same_state(self):
        """Scan doesn't fire callback if state hasn't changed."""
        mon = SinkMonitor()
        on_active = MagicMock()
        on_idle = MagicMock()
        mon.register("FC:58:FA:EB:08:6C", "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", on_active, on_idle)
        on_active.reset_mock()
        on_idle.reset_mock()

        # Pre-set the state to idle
        mon._sink_states["bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"] = "idle"

        pulse = AsyncMock()
        pulse.sink_list.return_value = [
            self._make_sink_info("bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", 1, index=10),  # still idle
        ]

        await mon._scan_all_sinks(pulse)
        on_active.assert_not_called()
        on_idle.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_updates_stale_state_after_reconnect(self):
        """After PA reconnect, scan detects state change from stale cache."""
        mon = SinkMonitor()
        on_active = MagicMock()
        on_idle = MagicMock()
        mon.register("FC:58:FA:EB:08:6C", "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", on_active, on_idle)
        on_active.reset_mock()
        on_idle.reset_mock()

        # Stale state says idle, but sink is actually running now
        mon._sink_states["bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"] = "idle"

        pulse = AsyncMock()
        pulse.sink_list.return_value = [
            self._make_sink_info("bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", 0, index=10),  # running
        ]

        await mon._scan_all_sinks(pulse)
        on_active.assert_called_once()
        on_idle.assert_not_called()
        assert mon._sink_states["bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"] == "running"

    @pytest.mark.asyncio
    async def test_scan_error_is_swallowed(self):
        """If sink_list raises, scan doesn't crash the monitor."""
        mon = SinkMonitor()
        pulse = AsyncMock()
        pulse.sink_list.side_effect = Exception("PA error")

        await mon._scan_all_sinks(pulse)  # should not raise

    @pytest.mark.asyncio
    async def test_scan_populates_index_to_name(self):
        """Scan builds the index→name mapping for future events."""
        mon = SinkMonitor()
        pulse = AsyncMock()
        pulse.sink_list.return_value = [
            self._make_sink_info("bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink", 0, index=10),
        ]

        await mon._scan_all_sinks(pulse)
        assert mon._sink_index_to_name[10] == "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"
