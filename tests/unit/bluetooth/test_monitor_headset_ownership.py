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
    service._set_handoff(MAC, "microphone")
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
        owned_link._set_handoff(MAC, "none")
        assert await asyncio.to_thread(mgr.request_sink_reconnect) is True
        disconnect.assert_called_once()


class _Host:
    """A device card that records what the monitor publishes."""

    def __init__(self) -> None:
        self.status: dict = {}
        self.updates: list[dict] = []
        self.start_subprocess = AsyncMock()
        self.bt_management_enabled = True

    def update_status(self, values: dict) -> None:
        self.status.update(values)
        self.updates.append(dict(values))

    def get_status_value(self, key: str, default=None):
        return self.status.get(key, default)

    def is_subprocess_running(self) -> bool:
        return False


async def test_a_reconnect_while_the_microphone_owns_the_link_is_not_a_failure(installed_bluez, owned_link):
    """The call link drops and comes back: no false error, no failed-attempt ladder.

    ``connect_device`` reports the link, but the capture-owned card keeps
    ``audio_sink_ready`` false on purpose and the manager's configure is
    refused.  Counting that as a missing sink published a false card error,
    lost the reconnect reset, and fed the churn/auto-release ladder until a
    working headset stopped being managed.
    """
    from sendspin_bridge.bluetooth.monitor import _inner_dbus_monitor

    host = _Host()
    mgr = _manager(installed_bluez)
    mgr.host = host
    mgr.connected = False
    device = MagicMock()
    device.is_connected = AsyncMock(return_value=False)
    device.battery_level = AsyncMock(return_value=None)

    def reconnect() -> bool:
        # The link is back, but the Source feed owns the card: no A2DP sink.
        mgr.apply_connected_state(True)
        mgr.audio_sink_ready = False
        return True

    mgr.connect_device = reconnect
    with (
        patch.object(mgr, "is_device_paired", return_value=True),
        patch.object(mgr, "disconnect_device") as disconnect,
        patch.object(mgr, "_a2dp_recovery_dance") as dance,
        patch.object(mgr, "_force_a2dp_sink_profile") as forced,
        patch("sendspin_bridge.bluetooth.monitor._correct_other_devices_routing", new=AsyncMock()),
    ):
        await _inner_dbus_monitor(
            mgr,
            device,
            asyncio.Event(),
            asyncio.Event(),
            asyncio.get_running_loop(),
        )

    assert all("last_error" not in update for update in host.updates), host.updates
    assert mgr.reconnect_attempt == 0
    assert host.status["reconnect_attempt"] == 0
    disconnect.assert_not_called()
    dance.assert_not_called()
    forced.assert_not_called()
    host.start_subprocess.assert_not_awaited()


async def test_connect_device_does_not_run_the_recovery_dance_while_the_microphone_owns_the_link(
    installed_bluez, owned_link
):
    """``configure_bluetooth_audio`` is fenced, but the connect path is not.

    With the experimental sink recovery on, the refused configure used to read
    as "no sink" and sent ``connect_device`` into the role flip and the
    disconnect/reconnect dance — dropping a live HFP call.
    """
    from unittest.mock import MagicMock

    from sendspin_bridge.bluetooth.manager import BluetoothManager

    mgr = BluetoothManager(mac_address=MAC, device_name="Headset", enable_a2dp_dance=True)
    mgr._running = True
    mgr.management_enabled = True
    mgr.host = _Host()
    mgr._adapter_handle = MagicMock()
    mgr._adapter_handle.hci_name = ""
    fake_device = MagicMock()
    fake_device.controller = ""
    fake_device.wait_for_services_blocking.return_value = True
    fake_device.uuids_blocking.return_value = ()
    mgr._device = fake_device
    link = {"connected": False}

    def wait(_duration: float, **_kwargs) -> bool:
        link["connected"] = True
        return True

    with (
        patch.object(mgr, "is_device_connected", side_effect=lambda: link["connected"]),
        patch.object(mgr, "_wait_with_cancel", side_effect=wait),
        patch.object(mgr, "is_device_paired", return_value=True),
        patch.object(mgr, "early_a2dp_role_flip") as early_flip,
        patch.object(mgr, "_force_a2dp_sink_profile") as force_sink,
        patch.object(mgr, "configure_bluetooth_audio", return_value=False) as configure,
        patch.object(mgr, "_a2dp_role_flip", return_value=False) as role_flip,
        patch.object(mgr, "_a2dp_recovery_dance", return_value=False) as dance,
    ):
        assert await asyncio.to_thread(mgr.connect_device) is True

    early_flip.assert_not_called()
    force_sink.assert_not_called()
    configure.assert_not_called()
    role_flip.assert_not_called()
    dance.assert_not_called()
