"""A speaker moved to another controller follows it without a bridge restart.

Seen live: a speaker removed from the fleet and added back from a scan was
paired on hci0, but its old client — kept in the registry, released — was
reclaimed with the hci1 it had before, and reconnected on the wrong
controller forever. Changing a device's adapter in the config of a running
bridge had the same blind spot: the warm restart re-read every field but the
adapter.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from sendspin_bridge.bluetooth.manager import BluetoothManager
from sendspin_bridge.bridge.client import SendspinClient

SPEAKER = "6C:5C:3D:35:17:99"


def _manager(adapter: str) -> BluetoothManager:
    with patch("subprocess.check_output", return_value=""):
        return BluetoothManager(mac_address=SPEAKER, adapter=adapter)


def test_a_manager_retargeted_to_another_controller_addresses_it():
    mgr = _manager("hci1")

    changed = mgr.retarget_adapter("hci0")

    assert changed is True
    assert mgr.adapter == "hci0"
    assert mgr.adapter_hci_name == "hci0"
    assert mgr._dbus_device_path == "/org/bluez/hci0/dev_6C_5C_3D_35_17_99"
    assert mgr.device.controller == "hci0"


def test_retargeting_to_the_same_controller_changes_nothing():
    mgr = _manager("hci1")
    device = mgr.device

    assert mgr.retarget_adapter("hci1") is False
    assert mgr.device is device


@pytest.mark.asyncio
async def test_a_warm_restart_moves_the_speaker_to_its_new_adapter():
    client = SendspinClient("ENEBY Portable", "localhost", 9000)
    client.bt_manager = _manager("hci1")
    client.running = False

    async def _noop():
        return None

    client.stop_sendspin = _noop  # type: ignore[method-assign]
    client._start_sendspin_inner = _noop  # type: ignore[method-assign]

    await client.warm_restart({"adapter": "hci0"})

    assert client.bt_manager.adapter == "hci0"
