"""A live microphone owns the link; the monitor must leave it alone.

While the Source feed captures a headset's microphone the card is on HFP and
the Player daemon is stopped, so the monitor's heartbeat sees "connected with
no player" and "no A2DP sink".  That is the expected shape of a call, not the
broken speaker of #476: reporting a missing sink and reconnecting would drop
the call.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sendspin_bridge.services.bluetooth import headset

MAC = "AA:BB:CC:DD:EE:FF"
UNRELATED_ERROR = "pulseaudio unavailable"


def _manager(installed_bluez, *, enable_a2dp_dance: bool = True):
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    installed_bluez.on("show", stdout="")
    mgr = BluetoothManager(mac_address=MAC, device_name="Headset", enable_a2dp_dance=enable_a2dp_dance)
    mgr._running = True
    mgr.management_enabled = True
    mgr.connected = True
    return mgr


@pytest.fixture
def owned_link(monkeypatch):
    """Publish a live microphone capture for MAC through the real service."""
    service = headset.HeadsetProfileService()
    service._publish(MAC, "microphone")
    monkeypatch.setattr(headset, "_service", service)
    return service


@pytest.fixture
def host():
    host = MagicMock()
    host.start_subprocess = AsyncMock()
    host.get_status_value = MagicMock(return_value=UNRELATED_ERROR)
    return host


async def test_a_link_the_microphone_owns_is_not_reported_as_sinkless(installed_bluez, owned_link, host):
    from sendspin_bridge.bluetooth.monitor import _start_player_on_established_link

    mgr = _manager(installed_bluez)
    mgr.host = host

    with (
        patch.object(mgr, "configure_bluetooth_audio") as configure,
        patch.object(mgr, "request_sink_reconnect") as reconnect,
        patch.object(mgr, "disconnect_device") as disconnect,
    ):
        assert await _start_player_on_established_link(mgr, asyncio.get_running_loop()) is True

    configure.assert_not_called()
    reconnect.assert_not_called()
    disconnect.assert_not_called()
    host.start_subprocess.assert_not_awaited()
    # The microphone owns the link: an unrelated card error must survive, and
    # no "missing sink" error may be published over it.
    reported = [call[0][0] for call in host.update_status.call_args_list]
    assert all("last_error" not in payload for payload in reported), reported


async def test_a_sinkless_link_the_microphone_owns_is_not_reconnected(installed_bluez, owned_link, host):
    from sendspin_bridge.bluetooth.monitor import _start_player_on_established_link

    mgr = _manager(installed_bluez)
    mgr.host = host

    with (
        patch.object(mgr, "configure_bluetooth_audio", return_value=False) as configure,
        patch.object(mgr, "disconnect_device") as disconnect,
    ):
        assert await _start_player_on_established_link(mgr, asyncio.get_running_loop()) is True

    # The refusal is expected, not "no sink": nothing is reported or dropped.
    configure.assert_not_called()
    disconnect.assert_not_called()
    assert all("last_error" not in payload for payload in (call[0][0] for call in host.update_status.call_args_list))


async def test_the_manager_refuses_a_sink_reconnect_while_the_microphone_owns_the_link(installed_bluez, owned_link):
    mgr = _manager(installed_bluez)

    with patch.object(mgr, "disconnect_device", return_value=True) as disconnect:
        assert await asyncio.to_thread(mgr.request_sink_reconnect) is False
        disconnect.assert_not_called()

        # The guard — not a spent retry budget — is what refused: once the
        # link is free again the same call does drop it.
        owned_link._publish(MAC, None)
        assert await asyncio.to_thread(mgr.request_sink_reconnect) is True
        disconnect.assert_called_once()
