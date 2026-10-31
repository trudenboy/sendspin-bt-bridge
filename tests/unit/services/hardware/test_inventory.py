"""The one hardware reader: what it reads, how long it keeps it, and how it fails."""

from __future__ import annotations

import threading

import pytest

from sendspin_bridge.bluetooth.bluez import (
    Adapter,
    AdapterInfo,
    AdapterRef,
    BluezControl,
    DeviceEntry,
    DeviceInfo,
    set_bluez,
)
from sendspin_bridge.services.bluetooth import device_registry
from sendspin_bridge.services.hardware import inventory
from tests.support.fake_bluez import FakeBluez
from tests.support.fake_lease import FakeLease

ADAPTER = "C0:FB:F9:62:D7:D6"
DEVICE = "6C:5C:3D:35:17:99"
DEVICE_B = "30:21:0E:0A:AE:5A"

SHOW_A = "Controller C0:FB:F9:62:D7:D6 (public)\n\tName: HP-ProDesk\n\tAlias: Desk radio\n\tPowered: yes\n"
SHOW_B = "Controller C0:FB:F9:62:D7:D7 (public)\n\tName: Dongle\n\tAlias: Dongle\n\tPowered: no\n"
INFO = (
    f"Device {DEVICE} (public)\n"
    "\tName: ENEBY Portable\n"
    "\tAlias: ENEBY Portable\n"
    "\tPaired: yes\n"
    "\tBonded: yes\n"
    "\tTrusted: yes\n"
    "\tBlocked: no\n"
    "\tConnected: yes\n"
    "\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)\n"
    "\tModalias: bluetooth:v0059p0001d0001\n"
)
INFO_B = (
    f"Device {DEVICE_B} (public)\n"
    "\tName: Lenco LS-500\n"
    "\tAlias: Lenco LS-500\n"
    "\tPaired: yes\n"
    "\tBonded: yes\n"
    "\tTrusted: no\n"
    "\tBlocked: no\n"
    "\tConnected: no\n"
)


class _RecordingLease(FakeLease):
    def __init__(self, acquired: list[str], reason: str = "test"):
        super().__init__(reason=reason)
        self._acquired = acquired

    def __enter__(self):
        self._acquired.append(self.reason)
        return self


@pytest.fixture(autouse=True)
def _fresh_reader(monkeypatch):
    inventory.clear_cache()
    monkeypatch.setattr(inventory, "probe_audio_server", lambda: "PulseAudio")
    monkeypatch.setattr(inventory, "probe_capture", lambda: True)
    monkeypatch.setattr(inventory, "_take_read_lease", lambda: _RecordingLease([]))
    yield
    inventory.clear_cache()


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(inventory, "_monotonic", lambda: now[0])
    return now


def _read() -> inventory.InventoryRead:
    return inventory.read_inventory()


def test_reads_controllers_and_paired_devices_from_bluetoothctl(installed_bluez):
    installed_bluez.on(lambda text: f"info {DEVICE}" in text, stdout=INFO)
    installed_bluez.on(lambda text: f"info {DEVICE_B}" in text, stdout=INFO_B)
    read = _read()

    controller = read.controllers[0]
    assert controller.mac == ADAPTER
    assert controller.id == "hci0"
    assert controller.id_synthetic is True
    assert controller.name == "HP-ProDesk"
    assert controller.alias == "HP-ProDesk"
    assert controller.powered is True
    assert controller.present is True
    assert controller.device_macs == (DEVICE, DEVICE_B)

    by_mac = {device.mac: device for device in read.devices}
    assert set(by_mac) == {DEVICE, DEVICE_B}
    assert by_mac[DEVICE].paired is True
    assert by_mac[DEVICE].bonded is True
    assert by_mac[DEVICE].trusted is True
    assert by_mac[DEVICE].blocked is False
    assert by_mac[DEVICE].connected is True
    assert by_mac[DEVICE].controller_macs == (ADAPTER,)
    assert by_mac[DEVICE].modalias == "bluetooth:v0059p0001d0001"
    assert by_mac[DEVICE].uuids == ("0000110b-0000-1000-8000-00805f9b34fb",)
    assert read.capabilities.audio_server == "pulseaudio"
    assert read.stale is False
    assert read.stale_after_s == 2


def test_the_bluetoothctl_fallback_runs_under_the_adapter_lease(installed_bluez, monkeypatch):
    acquired: list[str] = []
    monkeypatch.setattr(inventory, "_take_read_lease", lambda: _RecordingLease(acquired, inventory._READ_LEASE_REASON))

    _read()

    assert acquired == [inventory._READ_LEASE_REASON]


def test_dbus_reads_do_not_take_the_lease(tmp_path, monkeypatch):
    class _Backend:
        def list_adapters(self, *, timeout=None):
            return [AdapterRef(mac=ADAPTER, name="Desk radio", is_default=True)]

        def show(self, adapter=Adapter.DEFAULT, *, timeout=None):
            return AdapterInfo(mac=ADAPTER, name="HP-ProDesk", alias="Desk radio", powered=True, present=True)

        def list_devices(self, adapter=Adapter.DEFAULT, *, filter="Paired", timeout=None):
            return [DeviceEntry(mac=DEVICE, name="ENEBY Portable")]

        def device_info(self, mac, adapter=Adapter.DEFAULT, *, timeout=None):
            return DeviceInfo(
                mac=mac,
                present=True,
                fields={"name": "ENEBY Portable", "alias": "ENEBY Portable", "paired": "yes", "connected": "no"},
                raw=(f"Device {mac} (public)", "Paired: yes", "Connected: no"),
            )

    fake = FakeBluez()
    set_bluez(BluezControl(spawner=fake, sysfs_dir=tmp_path, query_backend=_Backend()))
    monkeypatch.setattr(inventory, "_take_read_lease", lambda: pytest.fail("a D-Bus read must not take the lease"))
    try:
        read = _read()
    finally:
        set_bluez(None)

    assert [controller.mac for controller in read.controllers] == [ADAPTER]
    assert [device.mac for device in read.devices] == [DEVICE]
    assert read.capabilities.dbus.available is True
    assert not {"list", "show", "info", "devices"}.intersection(fake.verbs())


def test_a_read_is_reused_for_two_seconds(installed_bluez, clock):
    _read()
    commands_after_first = len(installed_bluez.commands)
    assert commands_after_first > 0

    clock[0] += 1.9
    _read()
    assert len(installed_bluez.commands) == commands_after_first

    clock[0] += 0.2
    _read()
    assert len(installed_bluez.commands) > commands_after_first


def test_concurrent_reads_share_one_refresh(installed_bluez, clock):
    _read()  # warm the cache
    commands_after_first = len(installed_bluez.commands)
    results: list[inventory.InventoryRead] = []

    def _worker() -> None:
        results.append(_read())

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 4
    assert all(read.controllers[0].mac == ADAPTER for read in results)
    assert len(installed_bluez.commands) == commands_after_first


def test_a_failed_refresh_returns_the_last_read_as_stale(installed_bluez, clock):
    first = _read()
    clock[0] += 5.0
    installed_bluez.fail("list")
    installed_bluez.fail("--version")

    stale = _read()

    assert stale.stale is True
    assert stale.as_of == first.as_of
    assert [controller.mac for controller in stale.controllers] == [ADAPTER]
    assert any(section.code == "refresh_failed" for section in stale.degraded)


def test_nothing_answering_is_an_unavailable_host(installed_bluez):
    installed_bluez.fail("list")
    installed_bluez.fail("--version")

    with pytest.raises(inventory.HardwareUnavailable):
        _read()


def test_no_dbus_is_a_degraded_section_not_an_error(installed_bluez):
    read = _read()

    assert read.capabilities.dbus.available is False
    assert any(section.section == "controllers" and section.code == "dbus_unavailable" for section in read.degraded)


def test_no_audio_server_is_a_degraded_section(installed_bluez, monkeypatch):
    monkeypatch.setattr(inventory, "probe_audio_server", lambda: "not available")
    read = _read()

    assert read.capabilities.audio_server == "none"
    assert any(section.section == "audio" and section.code == "pulse_unavailable" for section in read.degraded)


def test_a_bond_on_two_adapters_lists_both_controllers(monkeypatch):
    fake = FakeBluez()
    fake.on("list", stdout="Controller AA:BB:CC:DD:EE:01 Alpha [default]\nController AA:BB:CC:DD:EE:02 Beta\n")
    fake.on(lambda text: "show AA:BB:CC:DD:EE:01" in text, stdout=SHOW_A)
    fake.on(lambda text: "show AA:BB:CC:DD:EE:02" in text, stdout=SHOW_B)
    fake.on_adapter("AA:BB:CC:DD:EE:01").on("devices", stdout=f"Device {DEVICE} ENEBY Portable\n")
    fake.on_adapter("AA:BB:CC:DD:EE:02").on(
        "devices", stdout=f"Device {DEVICE} ENEBY Portable\nDevice {DEVICE_B} Lenco LS-500\n"
    )
    fake.on(lambda text: f"info {DEVICE}" in text, stdout=INFO)
    fake.on(lambda text: f"info {DEVICE_B}" in text, stdout=INFO_B)
    set_bluez(fake.control)
    try:
        read = _read()
    finally:
        set_bluez(None)

    by_mac = {device.mac: device for device in read.devices}
    assert by_mac[DEVICE].controller_macs == ("AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02")
    assert by_mac[DEVICE_B].controller_macs == ("AA:BB:CC:DD:EE:02",)
    assert [controller.id for controller in read.controllers] == ["hci0", "hci1"]
    assert all(controller.id_synthetic for controller in read.controllers)


def test_controller_id_and_class_come_from_sysfs(tmp_path, bluez_sysfs, monkeypatch):
    (tmp_path / "hci3").mkdir()
    (tmp_path / "hci3" / "address").write_text(ADAPTER)
    bluez_sysfs(tmp_path)
    monkeypatch.setattr(inventory, "read_device_class", lambda index: 0x240404)

    read = _read()

    controller = read.controllers[0]
    assert controller.id == "hci3"
    assert controller.id_synthetic is False
    assert controller.class_of_device == "0x240404"


def test_a_synthetic_controller_id_never_collides_with_a_real_one(tmp_path):
    """A partial sysfs map must not mint a name a real adapter already owns."""
    (tmp_path / "hci1").mkdir()
    (tmp_path / "hci1" / "address").write_text("AA:BB:CC:DD:EE:02")

    fake = FakeBluez()
    fake.on("list", stdout="Controller AA:BB:CC:DD:EE:02 Beta\nController AA:BB:CC:DD:EE:01 Alpha\n")
    fake.on(lambda text: "show AA:BB:CC:DD:EE:02" in text, stdout=SHOW_B)
    fake.on(lambda text: "show AA:BB:CC:DD:EE:01" in text, stdout=SHOW_A)
    fake.on_adapter("AA:BB:CC:DD:EE:02").on("devices", stdout="")
    fake.on_adapter("AA:BB:CC:DD:EE:01").on("devices", stdout="")
    set_bluez(BluezControl(spawner=fake, sysfs_dir=tmp_path))
    try:
        read = _read()
    finally:
        set_bluez(None)

    by_mac = {controller.mac: controller for controller in read.controllers}
    assert by_mac["AA:BB:CC:DD:EE:02"].id == "hci1"
    assert by_mac["AA:BB:CC:DD:EE:02"].id_synthetic is False
    assert by_mac["AA:BB:CC:DD:EE:01"].id == "hci0"
    assert by_mac["AA:BB:CC:DD:EE:01"].id_synthetic is True


def test_the_runtime_status_bag_fills_battery_rssi_and_the_output_role(installed_bluez):
    class _Client:
        class bt_manager:
            mac_address = DEVICE

        status = {
            "battery_level": 77,
            "rssi_dbm": -42,
            "rssi_at_ts": 1_700_000_000.0,
            "audio_streaming": True,
        }

    device_registry.set_active_clients([_Client()])
    try:
        device = {item.mac: item for item in _read().devices}[DEVICE]
    finally:
        device_registry.set_active_clients([])

    assert device.battery_pct == 77
    assert device.rssi_delta_db == -42
    assert device.rssi_at == "2023-11-14T22:13:20+00:00"
    assert device.transport_state == "active"
    assert device.roles == (inventory.RoleEvidence(role="output", evidence="transport:active"),)


# -- the transports' answers, not just their exceptions ------------------------------------


def _installed_with_backend(tmp_path, fake_bluez, controller):
    from sendspin_bridge.bluetooth.bluez import BluezControl

    set_bluez(
        BluezControl(
            spawner=fake_bluez,
            sysfs_dir=tmp_path,
            time_source=fake_bluez.now,
            sleeper=fake_bluez.sleep,
            query_backend=controller,
        )
    )


def test_a_silent_dbus_falls_back_to_bluetoothctl_under_the_lease(tmp_path, monkeypatch, bridge_loop):
    from sendspin_bridge.bluetooth.controller import DbusController
    from tests.support.fake_dbus import FakeBlueZ as FakeDbusBlueZ

    bus = FakeDbusBlueZ()
    bus.connected = False
    fake = FakeBluez()
    _installed_with_backend(tmp_path, fake, DbusController(bus_factory=bus.bus))
    acquired: list[str] = []
    monkeypatch.setattr(inventory, "_take_read_lease", lambda: _RecordingLease(acquired, inventory._READ_LEASE_REASON))
    try:
        read = _read()
    finally:
        set_bluez(None)

    assert acquired == [inventory._READ_LEASE_REASON]
    assert read.capabilities.dbus.available is False
    assert [controller.mac for controller in read.controllers] == [ADAPTER]
    assert any(section.section == "controllers" and section.code == "dbus_unavailable" for section in read.degraded)


def test_a_silent_dbus_and_a_silent_bluetoothctl_is_hardware_unavailable(tmp_path, bridge_loop):
    from sendspin_bridge.bluetooth.controller import DbusController
    from tests.support.fake_dbus import FakeBlueZ as FakeDbusBlueZ

    bus = FakeDbusBlueZ()
    bus.fail["GetManagedObjects"] = RuntimeError("the object tree exploded")
    fake = FakeBluez()
    fake.fail("list")
    fake.fail("--version")
    _installed_with_backend(tmp_path, fake, DbusController(bus_factory=bus.bus))
    try:
        with pytest.raises(inventory.HardwareUnavailable):
            _read()
    finally:
        set_bluez(None)


def test_a_failing_list_is_unavailable_even_when_version_answers(installed_bluez):
    """``--version`` only proves the binary exists; a dead bluetoothd is not an empty host."""
    installed_bluez.fail("list")

    with pytest.raises(inventory.HardwareUnavailable):
        _read()


def test_a_dbus_read_is_one_object_tree_round_trip(bridge_loop, tmp_path):
    from sendspin_bridge.bluetooth.controller import DbusController
    from tests.support.fake_dbus import FakeBlueZ as FakeDbusBlueZ

    bus = FakeDbusBlueZ()
    bus.add_adapter("/org/bluez/hci0", ADAPTER, powered=True, Name="HP-ProDesk", Alias="Desk radio")
    for index in range(1, 6):
        mac = f"AA:BB:CC:DD:EE:{index:02X}"
        bus.add_device(f"/org/bluez/hci0/dev_AA_BB_CC_DD_EE_{index:02X}", mac, paired=True)
    fake = FakeBluez()
    _installed_with_backend(tmp_path, fake, DbusController(bus_factory=bus.bus))
    try:
        read = _read()
    finally:
        set_bluez(None)

    assert len(read.controllers) == 1
    assert len(read.devices) == 5
    assert bus.managed_object_reads == 1


def test_the_bluetoothctl_fallback_uses_one_inventory_session(installed_bluez):
    _read()

    spawned = [
        command
        for command in installed_bluez.commands
        if command.kind in ("run", "popen") and "bluetoothctl" in command.argv
    ]
    assert sum(1 for command in spawned if command.kind == "popen") == 1
    assert len(spawned) == 2  # the list that decides what to ask, then one session


def test_the_ttl_starts_when_the_refresh_finishes(monkeypatch, clock):
    refreshes: list[int] = []

    def _slow_refresh():
        refreshes.append(1)
        clock[0] += 3.0
        return inventory._assemble([], [], dbus=True, bluetooth=True, degraded=[])

    monkeypatch.setattr(inventory, "_refresh", _slow_refresh)

    first = _read()
    assert len(refreshes) == 1

    clock[0] += 1.9
    second = _read()

    assert second is first
    assert len(refreshes) == 1
