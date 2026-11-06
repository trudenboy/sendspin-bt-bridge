"""Source views join one immutable runtime and hardware read, without host IO."""

import pytest

from sendspin_bridge.application import source as source_uc
from sendspin_bridge.services.hardware import inventory
from sendspin_bridge.services.source import devices, state

PHONE = "34:C7:E9:25:A3:A3"
UUID = "0000110a-0000-1000-8000-00805f9b34fb"


def read(*, with_phone=True, unavailable=()):
    device = inventory.DeviceFacts(
        mac=PHONE,
        name="Pixel",
        alias="Pixel",
        paired=True,
        bonded=True,
        trusted=True,
        blocked=False,
        connected=True,
        controller_macs=(),
        uuids=(UUID,),
        modalias=None,
        services_resolved=True,
        battery_pct=None,
        rssi_delta_db=None,
        rssi_at=None,
        transport_state=None,
        roles=(),
    )
    return inventory._assemble(
        [],
        [device] if with_phone else [],
        dbus=True,
        bluetooth=True,
        degraded=[],
        audio=inventory.AudioFacts(unavailable=unavailable),
        server_name="PulseAudio",
    )


@pytest.fixture(autouse=True)
def clean():
    inventory.clear_cache()
    state.reset()
    yield
    inventory.clear_cache()
    state.reset()


def test_empty_source_devices_share_the_inventory_ttl(monkeypatch):
    calls = []
    monkeypatch.setattr(inventory, "_refresh", lambda: (calls.append(1), read(with_phone=False))[1])
    monkeypatch.setattr(inventory, "_monotonic", lambda: 10.0)
    assert devices.list_source_devices() == ()
    assert devices.list_source_devices() == ()
    assert calls == [1]


def test_changed_player_exclusions_apply_to_the_cached_facts(monkeypatch):
    calls = []
    monkeypatch.setattr(inventory, "_refresh", lambda: (calls.append(1), read())[1])
    monkeypatch.setattr(inventory, "_monotonic", lambda: 10.0)
    assert [d.name for d in devices.list_source_devices()] == ["Pixel"]
    assert devices.list_source_devices(excluded_macs=["34_c7_e9_25_a3_a3"]) == ()
    assert calls == [1]


def test_invalidated_failed_refresh_keeps_last_good_facts_and_ttl(monkeypatch):
    first = read()
    monkeypatch.setattr(inventory, "_monotonic", lambda: 10.0)
    monkeypatch.setattr(inventory, "_refresh", lambda: first)
    assert inventory.read_inventory() is first
    calls = []

    def failed():
        calls.append(1)
        raise inventory.HardwareUnavailable("offline")

    monkeypatch.setattr(inventory, "_refresh", failed)
    devices.invalidate()
    stale = inventory.read_inventory()
    assert stale.stale and stale.devices == first.devices and stale.as_of == first.as_of
    assert any(d.code == "refresh_failed" for d in stale.degraded)
    assert inventory.read_inventory() is stale
    assert calls == [1]


def test_projection_uses_one_supplied_read_without_io(monkeypatch):
    from sendspin_bridge.services.source.read_model import project_status

    monkeypatch.setattr(inventory, "read_inventory", lambda: pytest.fail("projection read hardware"))
    current = state.SourceState(enabled=True, state="streaming", phone_mac=PHONE)
    view = project_status({}, current, read(), headsets=())
    assert [d.name for d in view.devices] == ["Pixel"]
    assert [(i.id, i.connected, i.active) for i in view.inputs] == [("bluetooth:" + PHONE, True, True)]


def test_projection_distinguishes_unknown_audio_from_a_known_empty_list():
    from sendspin_bridge.services.source.read_model import project_status

    config = {"SENDSPIN_SOURCES": [{"node": "alsa_input.line", "name": "Line"}]}
    current = state.SourceState()
    assert project_status(config, current, None).inputs[0].available is True
    assert project_status(config, current, read(with_phone=False)).inputs[0].available is False
    assert (
        project_status(config, current, read(with_phone=False, unavailable=("sources: offline",))).inputs[0].available
        is True
    )


def test_off_source_status_never_reads_hardware(monkeypatch):
    monkeypatch.setattr(inventory, "read_inventory", lambda: pytest.fail("off source read hardware"))
    assert source_uc.status({}).devices == ()


async def test_input_listing_reads_inventory_off_the_event_loop(monkeypatch):
    import threading

    from sendspin_bridge.application import hardware

    event_thread = threading.get_ident()
    calls = []

    def external_read():
        assert threading.get_ident() != event_thread
        calls.append(1)
        return read()

    monkeypatch.setattr(inventory, "read_inventory", external_read)
    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: {})
    monkeypatch.setattr(hardware, "snapshot", lambda: pytest.fail("source called full hardware snapshot"))
    result = await source_uc.inputs()
    assert [i["name"] for i in result["inputs"]] == ["Pixel"]
    assert calls == [1]


async def test_hardware_status_and_inputs_share_one_refresh_then_expire(monkeypatch):
    import asyncio
    import threading

    from sendspin_bridge.application import hardware
    from sendspin_bridge.services.source import manager

    now = [10.0]
    calls = []
    event_thread = threading.get_ident()

    def refresh():
        assert threading.get_ident() != event_thread
        calls.append(1)
        return read()

    monkeypatch.setattr(inventory, "_refresh", refresh)
    monkeypatch.setattr(inventory, "_monotonic", lambda: now[0])
    config = {"SENDSPIN_SOURCE": True}
    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: config)
    monkeypatch.setattr(hardware, "load_config", lambda: config)
    monkeypatch.setattr(manager, "running", lambda: False)
    state.publish(enabled=True, state="waiting")
    hardware_view = await hardware.snapshot()
    source_view = await asyncio.to_thread(source_uc.status, config)
    listed = await source_uc.inputs()
    assert hardware_view.source is not None
    assert [(d.mac, d.name, d.connected) for d in hardware_view.source.devices] == [(PHONE, "Pixel", True)]
    assert [(d.mac, d.name, d.connected) for d in source_view.devices] == [(PHONE, "Pixel", True)]
    assert [(d["mac"], d["name"], d["connected"]) for d in listed["inputs"]] == [(PHONE, "Pixel", True)]
    assert calls == [1]
    now[0] = 12.0
    await source_uc.inputs()
    assert calls == [1, 1]


def test_shared_inventory_failure_never_falls_back_to_source_bluez(monkeypatch):
    def unavailable():
        raise inventory.HardwareUnavailable("offline")

    monkeypatch.setattr(inventory, "read_inventory", unavailable)
    monkeypatch.setattr(
        "sendspin_bridge.bluetooth.controller.get_controller", lambda: pytest.fail("direct BlueZ fallback")
    )
    assert devices.list_source_devices() == ()
    state.publish(enabled=True, state="error", error="capture_failed")
    view = source_uc.status({"SENDSPIN_SOURCES": [{"node": "line", "name": "Line"}]})
    assert view.error == "capture_failed" and view.inputs[0].available is True


def test_active_source_identity_uses_canonical_device_address():
    from sendspin_bridge.services.source.read_model import project_status

    current = state.SourceState(enabled=True, phone_mac="34_c7_e9_25_a3_a3")
    view = project_status({}, current, read())
    assert view.active_input == "bluetooth:34:C7:E9:25:A3:A3"
    assert view.inputs[0].active is True


@pytest.mark.parametrize("command", ["select", "remove"])
async def test_bluetooth_commands_validate_shared_facts_off_the_event_loop(monkeypatch, command):
    import threading

    event_thread = threading.get_ident()
    calls = []

    def external_read():
        assert threading.get_ident() != event_thread
        calls.append(1)
        return read()

    saved = {}
    removed = []
    monkeypatch.setattr(inventory, "read_inventory", external_read)
    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: {})
    monkeypatch.setattr("sendspin_bridge.config.update_config", lambda mutate: mutate(saved))
    monkeypatch.setattr("sendspin_bridge.services.bluetooth.bt_remove_device", lambda mac: removed.append(mac))
    monkeypatch.setattr(source_uc.manager, "client", lambda: None)
    if command == "select":
        result = await source_uc.select_input("bluetooth:" + PHONE)
        assert result["active_input"] == "bluetooth:" + PHONE
        assert saved["SENDSPIN_SOURCE_ACTIVE"] == "bluetooth:" + PHONE
    else:
        await source_uc.remove_input("bluetooth:" + PHONE)
        assert removed == [PHONE]
    assert calls == [1]


def test_no_audio_server_is_an_unknown_list_not_known_empty():
    from dataclasses import replace

    from sendspin_bridge.services.source.read_model import project_status

    known = read(with_phone=False)
    unavailable = replace(known, capabilities=replace(known.capabilities, audio_server="none"))
    view = project_status({"SENDSPIN_SOURCES": [{"node": "line", "name": "Line"}]}, state.SourceState(), unavailable)
    assert view.inputs[0].available is True
