"""``list_bt_adapters()`` delegates to the BluezControl transport (batch 1).

The helper keeps its name and signature — it is a long-standing
monkeypatch seam across the route tests — but the inline
subprocess + regex body is replaced by ``get_bluez().list_adapters()``
so every ``bluetoothctl list`` invocation flows through the one deep
module.  These tests pin the delegation, the empty-on-transport-failure
contract, and the ``timeout`` pass-through.
"""

from __future__ import annotations

from sendspin_bridge.services.bluetooth import list_bt_adapters


def test_list_bt_adapters_returns_controller_macs_from_bluez(installed_bluez):
    installed_bluez.on(
        "list",
        stdout="Controller 11:22:33:44:55:66 FakeOne [default]\nController AA:BB:CC:DD:EE:00 FakeTwo\n",
    )

    assert list_bt_adapters() == ["11:22:33:44:55:66", "AA:BB:CC:DD:EE:00"]
    assert any(c.argv == ("bluetoothctl", "list") for c in installed_bluez.commands)


def test_list_bt_adapters_empty_when_bluetoothctl_unavailable(installed_bluez):
    installed_bluez.fail("list")

    assert list_bt_adapters() == []


def test_list_bt_adapters_empty_on_timeout(installed_bluez):
    installed_bluez.timeout("list")

    assert list_bt_adapters() == []


def test_list_bt_adapters_passes_timeout_override(installed_bluez):
    list_bt_adapters(timeout=7)

    runs = [c for c in installed_bluez.commands if c.kind == "run"]
    assert runs and runs[-1].timeout == 7.0
