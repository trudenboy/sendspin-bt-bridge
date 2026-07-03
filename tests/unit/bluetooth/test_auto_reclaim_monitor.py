"""Monitor-side auto-reclaim flow (issues #349/#350).

``_finish_auto_reclaim`` must mirror the external-reconnect path after
the state flip: configure audio, restart the player subprocess.  The
polling variant additionally polls the live connected state (the cached
``mgr.connected`` is only maintained by the D-Bus handler) at the
regular ``check_interval`` cadence.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from sendspin_bridge.bluetooth.monitor import _finish_auto_reclaim, _poll_auto_reclaim


def _make_mgr(*, reclaims: bool) -> MagicMock:
    mgr = MagicMock()
    mgr.device_name = "TestSpeaker"
    mgr.maybe_auto_reclaim = MagicMock(return_value=reclaims)
    mgr.configure_bluetooth_audio = MagicMock()
    mgr.host = MagicMock()
    mgr.host.start_subprocess = AsyncMock()
    return mgr


async def test_finish_auto_reclaim_configures_audio_and_starts_player():
    mgr = _make_mgr(reclaims=True)
    loop = asyncio.get_running_loop()

    assert await _finish_auto_reclaim(mgr, loop) is True

    mgr.configure_bluetooth_audio.assert_called_once()
    mgr._record_reconnect.assert_called_once()
    mgr.host.start_subprocess.assert_awaited_once()
    status_update = mgr.host.update_status.call_args[0][0]
    assert status_update["bluetooth_connected"] is True


async def test_finish_auto_reclaim_noop_when_manager_declines():
    mgr = _make_mgr(reclaims=False)
    loop = asyncio.get_running_loop()

    assert await _finish_auto_reclaim(mgr, loop) is False

    mgr.configure_bluetooth_audio.assert_not_called()
    mgr.host.start_subprocess.assert_not_awaited()


async def test_poll_auto_reclaim_skips_user_released_without_polling():
    mgr = _make_mgr(reclaims=True)
    mgr.host.get_status_value = lambda key: {"bt_released_by": "user"}.get(key)
    mgr.is_device_connected = MagicMock()
    loop = asyncio.get_running_loop()

    assert await _poll_auto_reclaim(mgr, loop) is False
    mgr.is_device_connected.assert_not_called()


async def test_poll_auto_reclaim_respects_check_interval_cadence():
    import time as _time

    mgr = _make_mgr(reclaims=True)
    mgr.host.get_status_value = lambda key: {"bt_released_by": "auto"}.get(key)
    mgr.check_interval = 15
    mgr.last_check = _time.time()  # checked just now
    mgr.is_device_connected = MagicMock(return_value=True)
    loop = asyncio.get_running_loop()

    assert await _poll_auto_reclaim(mgr, loop) is False
    mgr.is_device_connected.assert_not_called()


async def test_poll_auto_reclaim_reclaims_when_speaker_is_back():
    mgr = _make_mgr(reclaims=True)
    mgr.host.get_status_value = lambda key: {"bt_released_by": "auto"}.get(key)
    mgr.check_interval = 15
    mgr.last_check = 0.0  # long overdue
    mgr.is_device_connected = MagicMock(return_value=True)
    loop = asyncio.get_running_loop()

    assert await _poll_auto_reclaim(mgr, loop) is True
    mgr.maybe_auto_reclaim.assert_called_once_with(connected=True)
    mgr.host.start_subprocess.assert_awaited_once()


async def test_poll_auto_reclaim_stays_released_while_speaker_off():
    mgr = _make_mgr(reclaims=True)
    mgr.host.get_status_value = lambda key: {"bt_released_by": "auto"}.get(key)
    mgr.check_interval = 15
    mgr.last_check = 0.0
    mgr.is_device_connected = MagicMock(return_value=False)
    loop = asyncio.get_running_loop()

    assert await _poll_auto_reclaim(mgr, loop) is False
    mgr.maybe_auto_reclaim.assert_not_called()
