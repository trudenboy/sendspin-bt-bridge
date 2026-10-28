"""The monitor asks an Echo-style speaker for its sink on links it did not make (#476).

An Echo often reconnects on its own, and after pairing the link may come up
outside the connect loop. The monitor sees those links first — through the
``Connected`` signal or because a link was already up with no player — so it
runs the manager's early role flip there, and once more when BlueZ learns
the speaker's services (``UUIDs`` / ``ServicesResolved``).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from sendspin_bridge.bluetooth.uuids import A2DP_SINK_UUID, A2DP_SOURCE_UUID
from tests.support.fake_dbus import attach, bluez_knowing


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")


def _manager(installed_bluez, *, enable_a2dp_dance: bool = True):
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    installed_bluez.on("show", stdout="")
    mgr = BluetoothManager(mac_address="AA:BB:CC:DD:EE:FF", device_name="Echo", enable_a2dp_dance=enable_a2dp_dance)
    mgr._running = True
    mgr.management_enabled = True
    return mgr


async def _run_monitor_while(mgr, signals):
    """Run one monitor cycle that fires *signals* on the fake bus, then stop."""
    from sendspin_bridge.bluetooth.monitor import _monitor_dbus

    async def _inner(*_args, **_kwargs):
        for fire in signals:
            fire()
        for _ in range(20):  # let the scheduled flip run in the executor
            await asyncio.sleep(0.01)
        mgr.shutdown()

    with (
        patch("sendspin_bridge.bluetooth.monitor._inner_dbus_monitor", side_effect=_inner),
        patch("sendspin_bridge.bluetooth.monitor._correct_other_devices_routing", new_callable=AsyncMock),
    ):
        await _monitor_dbus(mgr)


@pytest.mark.asyncio
async def test_a_link_the_speaker_brings_up_itself_gets_the_early_flip(installed_bluez):
    mgr = _manager(installed_bluez)
    bluez = bluez_knowing(mgr, connected=False, uuids=(A2DP_SOURCE_UUID, A2DP_SINK_UUID))
    attach(mgr, bluez)
    path = next(iter(bluez.objects))

    with patch.object(mgr, "early_a2dp_role_flip", return_value=True) as flip:
        await _run_monitor_while(mgr, [lambda: bluez.set_property(path, "Connected", True)])

    flip.assert_called_once()


@pytest.mark.asyncio
async def test_the_flip_is_retried_when_bluez_learns_the_services(installed_bluez):
    mgr = _manager(installed_bluez)
    bluez = bluez_knowing(mgr, connected=True, uuids=())
    attach(mgr, bluez)
    path = next(iter(bluez.objects))

    with patch.object(mgr, "early_a2dp_role_flip", return_value=True) as flip:
        await _run_monitor_while(
            mgr,
            [
                lambda: bluez.set_property(path, "UUIDs", [A2DP_SOURCE_UUID, A2DP_SINK_UUID]),
                lambda: bluez.set_property(path, "ServicesResolved", True),
            ],
        )

    assert flip.call_count >= 1


@pytest.mark.asyncio
async def test_no_retry_once_the_flip_was_made_for_this_link(installed_bluez):
    mgr = _manager(installed_bluez)
    bluez = bluez_knowing(mgr, connected=True, uuids=(A2DP_SOURCE_UUID, A2DP_SINK_UUID))
    attach(mgr, bluez)
    path = next(iter(bluez.objects))
    mgr._apply_connected_state(True)
    mgr._early_flip_seq = mgr._transition_seq  # the connect loop already asked on this link

    with patch.object(mgr, "early_a2dp_role_flip", return_value=True) as flip:
        await _run_monitor_while(mgr, [lambda: bluez.set_property(path, "ServicesResolved", True)])

    flip.assert_not_called()


@pytest.mark.asyncio
async def test_no_flip_from_the_monitor_without_the_experimental_recovery(installed_bluez):
    mgr = _manager(installed_bluez, enable_a2dp_dance=False)
    bluez = bluez_knowing(mgr, connected=False, uuids=(A2DP_SOURCE_UUID, A2DP_SINK_UUID))
    attach(mgr, bluez)
    path = next(iter(bluez.objects))

    with patch.object(mgr, "early_a2dp_role_flip", return_value=False) as flip:
        await _run_monitor_while(
            mgr,
            [
                lambda: bluez.set_property(path, "Connected", True),
                lambda: bluez.set_property(path, "ServicesResolved", True),
            ],
        )

    flip.assert_not_called()


@pytest.mark.asyncio
async def test_a_link_found_already_up_gets_the_early_flip_before_audio_is_configured(installed_bluez):
    from sendspin_bridge.bluetooth.monitor import _start_player_on_established_link

    mgr = _manager(installed_bluez)
    attach(mgr, bluez_knowing(mgr, connected=True, uuids=(A2DP_SOURCE_UUID, A2DP_SINK_UUID)))
    order: list[str] = []

    with (
        patch.object(
            mgr, "early_a2dp_role_flip", side_effect=lambda **kw: order.append(f"flip wait={kw.get('wait')}") or True
        ),
        patch.object(mgr, "configure_bluetooth_audio", side_effect=lambda: order.append("audio") or True),
    ):
        assert await _start_player_on_established_link(mgr, asyncio.get_running_loop()) is True

    # May wait (bounded) for a flip another path has in flight.
    assert order == ["flip wait=2.0", "audio"]


@pytest.mark.asyncio
async def test_the_monitor_leaves_the_flip_to_a_bridge_connect_that_is_running(installed_bluez):
    mgr = _manager(installed_bluez)
    bluez = bluez_knowing(mgr, connected=False, uuids=(A2DP_SOURCE_UUID, A2DP_SINK_UUID))
    attach(mgr, bluez)
    path = next(iter(bluez.objects))

    mgr._connect_lock.acquire()
    try:
        with patch.object(mgr, "early_a2dp_role_flip", return_value=True) as flip:
            await _run_monitor_while(
                mgr,
                [
                    lambda: bluez.set_property(path, "Connected", True),
                    lambda: bluez.set_property(path, "ServicesResolved", True),
                ],
            )
    finally:
        mgr._connect_lock.release()

    flip.assert_not_called()
