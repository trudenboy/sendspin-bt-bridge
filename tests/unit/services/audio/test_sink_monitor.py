"""Tests for services/sink_monitor.py — PA sink state monitoring."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sendspin_bridge.services.audio.sink_monitor import SinkMonitor, extract_mac_from_sink

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
        with patch("sendspin_bridge.services.audio.sink_monitor._PULSECTL_AVAILABLE", False):
            await mon.start()
        assert mon._task is None

    @pytest.mark.asyncio
    async def test_stop_without_start_is_noop(self):
        mon = SinkMonitor()
        await mon.stop()  # should not raise

    def test_available_property(self):
        mon = SinkMonitor()
        with patch("sendspin_bridge.services.audio.sink_monitor._PULSECTL_AVAILABLE", True):
            # Not started yet — not available even if pulsectl exists
            assert mon.available is False
        with patch("sendspin_bridge.services.audio.sink_monitor._PULSECTL_AVAILABLE", False):
            assert mon.available is False


# ── Monitor loop ──────────────────────────────────────────────────────────


class TestMonitorLoop:
    """Test the main monitoring loop lifecycle."""

    @pytest.mark.asyncio
    async def test_start_creates_task(self):
        mon = SinkMonitor()
        with (
            patch("sendspin_bridge.services.audio.sink_monitor._PULSECTL_AVAILABLE", True),
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


# ── Failure diagnosis & self-disable ─────────────────────────────────────


class _ConnectFail:
    """Context manager stub that raises the given exception on __aenter__."""

    def __init__(self, exc: BaseException):
        self._exc = exc

    async def __aenter__(self):
        raise self._exc

    async def __aexit__(self, *args):
        return False


class _ConnectSuccessThenCancel:
    """Stub that enters the body once, then cancels via CancelledError."""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def subscribe_events(self, *_args, **_kwargs):
        async def _agen():
            raise asyncio.CancelledError()
            yield  # pragma: no cover

        return _agen()

    async def sink_list(self):
        return []


class _ConnectSuccessThenDrop:
    """Stub that enters the body, then the event loop raises a transient error."""

    def __init__(self, drop_exc: BaseException | None = None):
        self._drop_exc = drop_exc or ConnectionResetError("dropped")

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def subscribe_events(self, *_args, **_kwargs):
        drop = self._drop_exc

        async def _agen():
            raise drop
            yield  # pragma: no cover

        return _agen()

    async def sink_list(self):
        return []


class TestMonitorLoopFailureBehavior:
    """Diagnose-then-disable semantics for the reconnect loop."""

    @pytest.mark.asyncio
    async def test_initial_connect_failures_disable_after_threshold(self, caplog):
        """After _INITIAL_FAILURE_THRESHOLD initial failures, the loop returns."""
        import sendspin_bridge.services.audio.sink_monitor as sm_mod

        mon = SinkMonitor()
        exc = ConnectionRefusedError("no server")

        def _factory(*_args, **_kwargs):
            return _ConnectFail(exc)

        with (
            patch("sendspin_bridge.services.audio.sink_monitor._PULSECTL_AVAILABLE", True),
            patch.object(sm_mod, "pulsectl_asyncio", MagicMock(PulseAsync=_factory), create=True),
            patch("sendspin_bridge.services.audio.sink_monitor.asyncio.sleep", new_callable=AsyncMock),
            caplog.at_level("DEBUG", logger="sendspin_bridge.services.audio.sink_monitor"),
        ):
            await mon.start()
            await asyncio.wait_for(mon._task, timeout=2.0)

        assert mon.available is False
        # _task must be cleared after self-disable so a later start() can retry
        # without requiring a full bridge restart.
        assert mon._task is None
        assert mon._consecutive_connect_failures >= sm_mod._INITIAL_FAILURE_THRESHOLD
        warnings = [r for r in caplog.records if r.levelname == "WARNING"]
        # One diagnostic on first failure + one "disabled after" final.
        assert len(warnings) == 2
        assert "cannot connect to PulseAudio" in warnings[0].getMessage()
        assert "server-not-listening" in warnings[0].getMessage()
        assert "SinkMonitor disabled after" in warnings[1].getMessage()

    @pytest.mark.asyncio
    async def test_diagnose_file_not_found_reports_socket_missing(self, caplog):
        import sendspin_bridge.services.audio.sink_monitor as sm_mod

        mon = SinkMonitor()
        exc = FileNotFoundError(2, "No such file")

        def _factory(*_args, **_kwargs):
            return _ConnectFail(exc)

        with (
            patch("sendspin_bridge.services.audio.sink_monitor._PULSECTL_AVAILABLE", True),
            patch.object(sm_mod, "pulsectl_asyncio", MagicMock(PulseAsync=_factory), create=True),
            patch("sendspin_bridge.services.audio.sink_monitor.asyncio.sleep", new_callable=AsyncMock),
            caplog.at_level("WARNING", logger="sendspin_bridge.services.audio.sink_monitor"),
        ):
            await mon.start()
            await asyncio.wait_for(mon._task, timeout=2.0)

        msgs = " ".join(r.getMessage() for r in caplog.records)
        assert "socket-missing" in msgs
        assert "/run/user" in msgs

    @pytest.mark.asyncio
    async def test_diagnose_permission_error_reports_uid_mismatch(self, caplog):
        import sendspin_bridge.services.audio.sink_monitor as sm_mod

        mon = SinkMonitor()
        exc = PermissionError(13, "Permission denied")

        def _factory(*_args, **_kwargs):
            return _ConnectFail(exc)

        with (
            patch("sendspin_bridge.services.audio.sink_monitor._PULSECTL_AVAILABLE", True),
            patch.object(sm_mod, "pulsectl_asyncio", MagicMock(PulseAsync=_factory), create=True),
            patch("sendspin_bridge.services.audio.sink_monitor.asyncio.sleep", new_callable=AsyncMock),
            caplog.at_level("WARNING", logger="sendspin_bridge.services.audio.sink_monitor"),
        ):
            await mon.start()
            await asyncio.wait_for(mon._task, timeout=2.0)

        msgs = " ".join(r.getMessage() for r in caplog.records)
        assert "permission-denied" in msgs
        assert "AUDIO_UID" in msgs

    @pytest.mark.asyncio
    async def test_diagnose_unknown_exception_falls_back_to_generic(self, caplog):
        import sendspin_bridge.services.audio.sink_monitor as sm_mod

        mon = SinkMonitor()
        exc = RuntimeError("weird")

        def _factory(*_args, **_kwargs):
            return _ConnectFail(exc)

        with (
            patch("sendspin_bridge.services.audio.sink_monitor._PULSECTL_AVAILABLE", True),
            patch.object(sm_mod, "pulsectl_asyncio", MagicMock(PulseAsync=_factory), create=True),
            patch("sendspin_bridge.services.audio.sink_monitor.asyncio.sleep", new_callable=AsyncMock),
            caplog.at_level("WARNING", logger="sendspin_bridge.services.audio.sink_monitor"),
        ):
            await mon.start()
            await asyncio.wait_for(mon._task, timeout=2.0)

        msgs = " ".join(r.getMessage() for r in caplog.records)
        assert "RuntimeError" in msgs
        assert "weird" in msgs

    @pytest.mark.asyncio
    async def test_successful_connect_resets_counter(self, caplog):
        """First two attempts fail, third succeeds → no 'disabled' WARNING."""
        import sendspin_bridge.services.audio.sink_monitor as sm_mod

        mon = SinkMonitor()
        attempts: list[int] = []

        def _factory(*_args, **_kwargs):
            attempts.append(1)
            if len(attempts) < 3:
                return _ConnectFail(ConnectionRefusedError("transient"))
            return _ConnectSuccessThenCancel()

        with (
            patch("sendspin_bridge.services.audio.sink_monitor._PULSECTL_AVAILABLE", True),
            patch.object(sm_mod, "pulsectl_asyncio", MagicMock(PulseAsync=_factory), create=True),
            patch("sendspin_bridge.services.audio.sink_monitor.asyncio.sleep", new_callable=AsyncMock),
            caplog.at_level("WARNING", logger="sendspin_bridge.services.audio.sink_monitor"),
        ):
            await mon.start()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(mon._task, timeout=2.0)

        assert mon._ever_connected is True
        assert mon._consecutive_connect_failures == 0
        msgs = " ".join(r.getMessage() for r in caplog.records)
        assert "SinkMonitor disabled after" not in msgs

    @pytest.mark.asyncio
    async def test_post_success_transient_uses_backoff_and_demotes_log(self, caplog):
        """After one success, transient failures log WARNING once, then DEBUG."""
        import sendspin_bridge.services.audio.sink_monitor as sm_mod

        mon = SinkMonitor()
        state = {"count": 0}

        def _factory(*_args, **_kwargs):
            state["count"] += 1
            if state["count"] == 1:
                return _ConnectSuccessThenDrop(ConnectionResetError("dropped"))
            return _ConnectFail(ConnectionRefusedError("transient"))

        sleeps: list[float] = []

        async def _record_sleep(delay: float) -> None:
            sleeps.append(delay)
            if len(sleeps) >= 3:
                raise asyncio.CancelledError()

        with (
            patch("sendspin_bridge.services.audio.sink_monitor._PULSECTL_AVAILABLE", True),
            patch.object(sm_mod, "pulsectl_asyncio", MagicMock(PulseAsync=_factory), create=True),
            patch("sendspin_bridge.services.audio.sink_monitor.asyncio.sleep", side_effect=_record_sleep),
            caplog.at_level("DEBUG", logger="sendspin_bridge.services.audio.sink_monitor"),
        ):
            await mon.start()
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(mon._task, timeout=2.0)

        # Expect exactly one WARNING for the first post-success reconnect.
        reconnect_warnings = [
            r for r in caplog.records if r.levelname == "WARNING" and "PA connection lost" in r.getMessage()
        ]
        assert len(reconnect_warnings) == 1
        # Subsequent reconnect failures must be DEBUG.
        debugs = [r for r in caplog.records if r.levelname == "DEBUG"]
        assert any("reconnect attempt failed" in r.getMessage() for r in debugs)
        # Backoff schedule: 5, 10, 20 (doubles per miss).
        assert sleeps[0] == pytest.approx(sm_mod._BACKOFF_BASE)
        assert sleeps[1] == pytest.approx(sm_mod._BACKOFF_BASE * sm_mod._BACKOFF_FACTOR)
        assert sleeps[2] == pytest.approx(sm_mod._BACKOFF_BASE * sm_mod._BACKOFF_FACTOR**2)

    @pytest.mark.asyncio
    async def test_start_after_self_disable_resets_counters_and_restarts(self):
        """After self-disable, calling start() again must reset counters and run a fresh loop."""
        import sendspin_bridge.services.audio.sink_monitor as sm_mod

        mon = SinkMonitor()
        state = {"attempt": 0}

        def _factory(*_args, **_kwargs):
            state["attempt"] += 1
            # First 3 attempts (initial disable path) fail; the 4th (after
            # manual restart) succeeds to prove the loop is live again.
            if state["attempt"] <= 3:
                return _ConnectFail(ConnectionRefusedError("down"))
            return _ConnectSuccessThenCancel()

        with (
            patch("sendspin_bridge.services.audio.sink_monitor._PULSECTL_AVAILABLE", True),
            patch.object(sm_mod, "pulsectl_asyncio", MagicMock(PulseAsync=_factory), create=True),
            patch("sendspin_bridge.services.audio.sink_monitor.asyncio.sleep", new_callable=AsyncMock),
        ):
            await mon.start()
            await asyncio.wait_for(mon._task, timeout=2.0)
            assert mon._task is None
            assert mon._consecutive_connect_failures >= sm_mod._INITIAL_FAILURE_THRESHOLD

            # Operator fixed PA and restarts the monitor.
            await mon.start()
            assert mon._task is not None
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait_for(mon._task, timeout=2.0)

        assert mon._ever_connected is True
        assert mon._consecutive_connect_failures == 0

    @pytest.mark.asyncio
    async def test_cancel_during_reconnect_exits_cleanly(self):
        """Cancelling during backoff sleep must not raise."""
        import sendspin_bridge.services.audio.sink_monitor as sm_mod

        mon = SinkMonitor()

        def _factory(*_args, **_kwargs):
            return _ConnectFail(ConnectionRefusedError("nope"))

        with (
            patch("sendspin_bridge.services.audio.sink_monitor._PULSECTL_AVAILABLE", True),
            patch.object(sm_mod, "pulsectl_asyncio", MagicMock(PulseAsync=_factory), create=True),
            patch("sendspin_bridge.services.audio.sink_monitor.asyncio.sleep", new_callable=AsyncMock),
        ):
            await mon.start()
            # Cancel almost immediately; should not raise.
            await asyncio.sleep(0)
            await mon.stop()
        assert mon._task is None


class TestDescribePAFailure:
    """Direct unit tests for _describe_pa_failure."""

    def test_file_not_found(self):
        from sendspin_bridge.services.audio.sink_monitor import _describe_pa_failure

        label, hint = _describe_pa_failure(FileNotFoundError(2, "x"))
        assert label == "socket-missing"
        assert "PULSE_SERVER" in hint

    def test_permission_error(self):
        from sendspin_bridge.services.audio.sink_monitor import _describe_pa_failure

        label, hint = _describe_pa_failure(PermissionError(13, "x"))
        assert label == "permission-denied"
        assert "AUDIO_UID" in hint

    def test_connection_refused(self):
        from sendspin_bridge.services.audio.sink_monitor import _describe_pa_failure

        label, hint = _describe_pa_failure(ConnectionRefusedError("x"))
        assert label == "server-not-listening"
        assert "pactl info" in hint

    def test_protocol_error_econnreset(self):
        import errno as _errno

        from sendspin_bridge.services.audio.sink_monitor import _describe_pa_failure

        exc = OSError(_errno.ECONNRESET, "reset")
        label, hint = _describe_pa_failure(exc)
        assert label == "protocol-error"
        assert "pipewire-pulse version" in hint

    def test_unknown_exception(self):
        from sendspin_bridge.services.audio.sink_monitor import _describe_pa_failure

        label, hint = _describe_pa_failure(RuntimeError("weird"))
        assert label == "unknown"
        assert "RuntimeError" in hint
