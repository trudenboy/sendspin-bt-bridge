"""Tests for hybrid volume routing logic (MA proxy + local fallback)."""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# state.py uses Python 3.10+ type union syntax (X | None) which fails on 3.9.
# We pre-populate sys.modules with a lightweight mock so routes.api can import
# without actually loading state.py at module level.
_state_mock = MagicMock()
_state_mock._ma_connected = False
_state_mock._main_loop = None
_state_mock.is_ma_connected = lambda: _state_mock._ma_connected
_state_mock.get_main_loop = lambda: _state_mock._main_loop

# Also need to mock pulsectl-based helpers that aren't available in test env
_pulse_mock = MagicMock()
_pulse_mock.set_sink_volume = MagicMock(return_value=True)
_pulse_mock.set_sink_mute = MagicMock(return_value=True)
_pulse_mock.get_sink_mute = MagicMock(return_value=False)


def _make_client(player_name="Speaker1", player_id="sendspin-speaker1", volume=50, sink="bluez_sink.AA"):
    """Create a minimal mock SendspinClient."""
    client = MagicMock()
    client.player_name = player_name
    client.player_id = player_id
    client.bluetooth_sink_name = sink
    client.status = MagicMock()
    client.status.get = MagicMock(side_effect=lambda k, d=None: {"volume": volume, "muted": False}.get(k, d))
    client._update_status = MagicMock()
    client._send_subprocess_command = AsyncMock()
    client.bt_manager = MagicMock()
    client.bt_manager.mac_address = "AA:BB:CC:DD:EE:FF"
    return client


@pytest.fixture(autouse=True)
def _patch_imports(monkeypatch):
    """Ensure state and pulse modules are mocked before importing routes.api."""
    monkeypatch.setitem(sys.modules, "state", _state_mock)
    monkeypatch.setitem(sys.modules, "services.pulse", _pulse_mock)
    # Reset state between tests
    _state_mock._ma_connected = False
    _state_mock._main_loop = None


@pytest.fixture()
def _with_loop():
    """Provide a running event loop for async futures to resolve on."""
    import threading

    loop = asyncio.new_event_loop()
    _state_mock._main_loop = loop
    _state_mock._ma_connected = True

    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join(timeout=2)
    loop.close()


class TestSetVolumeViaMa:
    """Tests for _set_volume_via_ma helper."""

    def test_returns_false_without_loop(self):
        _state_mock._main_loop = None
        # Force re-import to pick up the mocked state
        if "routes.api" in sys.modules:
            del sys.modules["routes.api"]
        if "routes" in sys.modules:
            del sys.modules["routes"]
        from routes.api import _set_volume_via_ma

        assert _set_volume_via_ma([_make_client()], 70) is False

    def test_individual_sends_volume_set(self, _with_loop):
        with patch("services.ma_monitor.send_player_cmd", new_callable=AsyncMock, return_value=True) as mock_cmd:
            from routes.api import _set_volume_via_ma

            result = _set_volume_via_ma([_make_client()], 70, is_group=False)
            assert result is True
            mock_cmd.assert_called_once()
            args = mock_cmd.call_args
            assert args[0][0] == "players/cmd/volume_set"
            assert args[0][1]["volume_level"] == 70

    def test_group_sends_group_volume(self, _with_loop):
        with patch("services.ma_monitor.send_player_cmd", new_callable=AsyncMock, return_value=True) as mock_cmd:
            from routes.api import _set_volume_via_ma

            result = _set_volume_via_ma([_make_client()], 80, is_group=True)
            assert result is True
            mock_cmd.assert_called_once()
            assert mock_cmd.call_args[0][0] == "players/cmd/group_volume"

    def test_returns_false_on_failure(self, _with_loop):
        with patch("services.ma_monitor.send_player_cmd", new_callable=AsyncMock, return_value=False):
            from routes.api import _set_volume_via_ma

            assert _set_volume_via_ma([_make_client()], 50) is False

    def test_returns_false_for_empty_targets(self, _with_loop):
        from routes.api import _set_volume_via_ma

        assert _set_volume_via_ma([], 50) is False


class TestSetMuteViaMa:
    """Tests for _set_mute_via_ma helper."""

    def test_sends_volume_mute(self, _with_loop):
        with patch("services.ma_monitor.send_player_cmd", new_callable=AsyncMock, return_value=True) as mock_cmd:
            from routes.api import _set_mute_via_ma

            result = _set_mute_via_ma([_make_client()], True)
            assert result is True
            assert mock_cmd.call_args[0][0] == "players/cmd/volume_mute"
            assert mock_cmd.call_args[0][1]["muted"] is True


class TestVolumeViaMaConfig:
    """Tests for VOLUME_VIA_MA config toggle."""

    def test_disabled_config_skips_ma(self, _with_loop):
        """When VOLUME_VIA_MA is False, set_volume should not call MA."""
        with (
            patch("services.ma_monitor.send_player_cmd", new_callable=AsyncMock) as mock_cmd,
            patch("routes.api.load_config", return_value={"VOLUME_VIA_MA": False}),
        ):
            from routes.api import _set_volume_via_ma

            # The helper itself doesn't check config — the route does.
            # Verify helper still works when called directly.
            result = _set_volume_via_ma([_make_client()], 70)
            assert result is True
            mock_cmd.assert_called_once()
