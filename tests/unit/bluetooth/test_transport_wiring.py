"""The shared bluetoothctl transport must resolve ``hciN`` through D-Bus.

Live rc.1 finding on the two-controller stand: this kernel exposes
``/sys/class/bluetooth/hciN`` **without** an ``address`` file, so the
transport's sysfs step comes back empty.  Without the D-Bus resolver wired
into the shared transport, resolution falls through to positional indexing
into ``bluetoothctl list`` — registration order, not kernel numbering — and
``select`` lands on the other controller.  That is how a power-cycle aimed
at hci0 powered hci1 instead.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.bluetooth.bluez import Adapter, BluezControl, get_bluez, set_bluez
from sendspin_bridge.bluetooth.controller import set_controller
from tests.support.fake_bluez import FakeBluez
from tests.support.fake_dbus import controller_knowing_adapters

HCI0_MAC = "C0:FB:F9:62:D7:D6"
HCI1_MAC = "00:02:72:0A:E4:3B"


@pytest.fixture
def reset_transport():
    yield
    set_bluez(None)
    set_controller(None)


def test_installed_transport_resolves_hci_via_dbus_when_sysfs_has_no_address(monkeypatch, tmp_path, reset_transport):
    from sendspin_bridge.bluetooth import manager as manager_mod

    fake = FakeBluez()
    # ``bluetoothctl list`` in BlueZ registration order: hci1's MAC first,
    # which is exactly what makes positional indexing pick the wrong one.
    fake.on("list", stdout=f"Controller {HCI1_MAC} HP-ProDesk #2 [default]\nController {HCI0_MAC} HP-ProDesk\n")
    set_controller(controller_knowing_adapters({"hci0": HCI0_MAC, "hci1": HCI1_MAC}))

    manager_mod.install_dbus_hci_resolver(
        # No sysfs address files, as on the live stand.
        transport_factory=lambda resolver: BluezControl(spawner=fake, hci_resolver=resolver, sysfs_dir=tmp_path)
    )

    # Through the transport itself, so the scoping ladder is the one under test.
    get_bluez().run(["power on"], adapter=Adapter.of("hci0"))

    selected = [c.adapter_selected for c in fake.commands if c.adapter_selected]
    assert selected == [HCI0_MAC], f"hci0 resolved to {selected}, not the controller the kernel calls hci0"
