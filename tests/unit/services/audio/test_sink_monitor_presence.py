"""SinkMonitor: presence of local (non-Bluetooth) sinks — unplug, replug, rename.

Driven with fake PulseAudio events and a fake inventory; no real sink is touched.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from sendspin_bridge.services.audio.sink_inventory import LocalSink, SinkInventoryUnavailable
from sendspin_bridge.services.audio.sink_monitor import SinkMonitor

SINK = "alsa_output.usb-Generic_USB_DAC_0000A1-00.analog-stereo"
RENAMED = "alsa_output.usb-Generic_USB_DAC_0000A1-00.analog-stereo.2"
MATCH = {"serial": "Generic_USB_DAC_0000A1"}


def _sink(name: str, state: str = "idle", **ids: str) -> LocalSink:
    return LocalSink(name, name, state, "s16le 2ch 48000Hz", False, 100, None, None, dict(ids))


class _Inventory:
    """A fake ``list_local_sinks(strict=True)``: what is plugged in right now."""

    def __init__(self, *sinks: LocalSink) -> None:
        self.sinks = list(sinks)
        self.fail = False
        self.calls = 0

    async def __call__(self) -> list[LocalSink]:
        self.calls += 1
        if self.fail:
            raise SinkInventoryUnavailable("no pulse")
        return list(self.sinks)


def _pulse_with(name: str, state: int = 1) -> AsyncMock:
    pulse = AsyncMock()
    info = MagicMock()
    info.name = name
    info.state = state
    pulse.sink_info.return_value = info
    return pulse


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    def present(self, sink: str) -> None:
        self.calls.append(("present", sink))

    def absent(self) -> None:
        self.calls.append(("absent", None))


def _monitor(inventory: _Inventory, rec: _Recorder, *, current: str | None) -> SinkMonitor:
    mon = SinkMonitor(inventory=inventory)
    mon.register_presence("dac-1", MATCH, SINK, rec.present, rec.absent, current_sink=current)
    return mon


async def test_removing_the_matched_sink_reports_it_absent():
    inv = _Inventory(_sink(SINK, serial=MATCH["serial"]))
    rec = _Recorder()
    mon = _monitor(inv, rec, current=SINK)
    mon._sink_index_to_name[7] = SINK

    inv.sinks = []
    await mon._dispatch_event(AsyncMock(), "remove", 7)

    assert rec.calls == [("absent", None)]
    assert mon.presence_sink("dac-1") is None


async def test_removing_another_sink_changes_nothing():
    inv = _Inventory(_sink(SINK, serial=MATCH["serial"]))
    rec = _Recorder()
    mon = _monitor(inv, rec, current=SINK)
    mon._sink_index_to_name[8] = "alsa_output.pci.analog-stereo"

    await mon._dispatch_event(AsyncMock(), "remove", 8)

    assert rec.calls == []
    assert mon.presence_sink("dac-1") == SINK


async def test_replug_under_the_same_name_reports_it_present():
    inv = _Inventory()
    rec = _Recorder()
    mon = _monitor(inv, rec, current=None)

    inv.sinks = [_sink(SINK, serial=MATCH["serial"])]
    await mon._dispatch_event(_pulse_with(SINK), "new", 9)

    assert rec.calls == [("present", SINK)]
    assert mon.presence_sink("dac-1") == SINK


async def test_replug_under_a_new_name_is_matched_by_serial():
    inv = _Inventory()
    rec = _Recorder()
    mon = _monitor(inv, rec, current=None)

    inv.sinks = [_sink(RENAMED, serial=MATCH["serial"])]
    await mon._dispatch_event(_pulse_with(RENAMED), "new", 9)

    assert rec.calls == [("present", RENAMED)]


async def test_a_new_unrelated_sink_reports_nothing():
    inv = _Inventory()
    rec = _Recorder()
    mon = _monitor(inv, rec, current=None)

    inv.sinks = [_sink("alsa_output.pci.analog-stereo")]
    await mon._dispatch_event(_pulse_with("alsa_output.pci.analog-stereo"), "new", 9)

    assert rec.calls == []


async def test_a_new_bluetooth_sink_does_not_read_the_inventory():
    inv = _Inventory()
    rec = _Recorder()
    mon = _monitor(inv, rec, current=None)

    await mon._dispatch_event(_pulse_with("bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"), "new", 9)

    assert inv.calls == 0
    assert rec.calls == []


async def test_a_sink_another_player_holds_is_not_given_out_twice():
    inv = _Inventory()
    first, second = _Recorder(), _Recorder()
    mon = SinkMonitor(inventory=inv)
    mon.register_presence("dac-1", MATCH, SINK, first.present, first.absent)
    mon.register_presence("dac-2", MATCH, SINK, second.present, second.absent)

    inv.sinks = [_sink(SINK, serial=MATCH["serial"])]
    await mon._dispatch_event(_pulse_with(SINK), "new", 9)

    assert first.calls == [("present", SINK)]
    assert second.calls == []


async def test_an_unreadable_inventory_reports_nothing():
    inv = _Inventory(_sink(SINK, serial=MATCH["serial"]))
    rec = _Recorder()
    mon = _monitor(inv, rec, current=SINK)

    inv.fail = True
    await mon.refresh_presence()

    assert rec.calls == []
    assert mon.presence_sink("dac-1") == SINK


# ── Polling fallback ────────────────────────────────────────────────────


async def test_polling_detects_unplug_and_replug():
    inv = _Inventory(_sink(SINK, serial=MATCH["serial"]))
    rec = _Recorder()
    mon = _monitor(inv, rec, current=SINK)

    inv.sinks = []
    await mon.refresh_presence()
    inv.sinks = [_sink(RENAMED, serial=MATCH["serial"])]
    await mon.refresh_presence()

    assert rec.calls == [("absent", None), ("present", RENAMED)]


async def test_the_poll_loop_runs_while_the_event_monitor_is_unavailable():
    inv = _Inventory(_sink(SINK, serial=MATCH["serial"]))
    rec = _Recorder()
    mon = _monitor(inv, rec, current=SINK)
    inv.sinks = []

    mon.start_presence_polling(0.01)
    try:
        for _ in range(100):
            if rec.calls:
                break
            await asyncio.sleep(0.01)
    finally:
        await mon.stop()

    assert rec.calls == [("absent", None)]


async def test_the_poll_loop_stays_quiet_while_events_arrive():
    inv = _Inventory(_sink(SINK, serial=MATCH["serial"]))
    rec = _Recorder()
    mon = _monitor(inv, rec, current=SINK)
    inv.sinks = []

    async def _forever():
        await asyncio.sleep(3600)

    mon._task = asyncio.create_task(_forever())  # the event subscription is live
    mon.start_presence_polling(0.01)
    await asyncio.sleep(0.1)
    await mon.stop()

    assert inv.calls == 0
    assert rec.calls == []


# ── Sink state (power_save idle detection) ─────────────────────────────


async def test_a_present_local_sink_reports_running_and_idle():
    inv = _Inventory(_sink(SINK, state="running", serial=MATCH["serial"]))
    rec = _Recorder()
    on_active, on_idle = MagicMock(), MagicMock()
    mon = SinkMonitor(inventory=inv)
    mon.register_presence(
        "dac-1", MATCH, SINK, rec.present, rec.absent, current_sink=SINK, on_active=on_active, on_idle=on_idle
    )
    await mon.refresh_presence()  # learns the sink's state from the inventory
    on_active.assert_called_once()

    await mon._dispatch_event(_pulse_with(SINK, state=1), "change", 7)

    on_idle.assert_called_once()


async def test_state_tracking_for_a_local_sink_stops_when_it_is_unplugged():
    inv = _Inventory(_sink(SINK, serial=MATCH["serial"]))
    rec = _Recorder()
    on_active, on_idle = MagicMock(), MagicMock()
    mon = SinkMonitor(inventory=inv)
    mon.register_presence(
        "dac-1", MATCH, SINK, rec.present, rec.absent, current_sink=SINK, on_active=on_active, on_idle=on_idle
    )
    mon._sink_index_to_name[7] = SINK
    await mon._dispatch_event(AsyncMock(), "remove", 7)

    await mon._dispatch_event(_pulse_with(SINK, state=0), "change", 7)

    on_active.assert_not_called()


# ── Rematch and ownership ──────────────────────────────────────────────


async def test_rematch_returns_the_current_sink_and_remembers_it():
    inv = _Inventory(_sink(RENAMED, serial=MATCH["serial"]))
    rec = _Recorder()
    mon = _monitor(inv, rec, current=SINK)

    found = await mon.rematch("dac-1")

    assert found.status == "ok"
    assert found.sink is not None and found.sink.name == RENAMED
    assert mon.presence_sink("dac-1") == RENAMED
    assert rec.calls == []  # the caller acts on the result itself


async def test_rematch_of_a_gone_sink_forgets_it():
    inv = _Inventory()
    rec = _Recorder()
    mon = _monitor(inv, rec, current=SINK)

    found = await mon.rematch("dac-1")

    assert found.status == "sink_missing"
    assert mon.presence_sink("dac-1") is None


async def test_rematch_raises_when_the_inventory_is_unreadable():
    inv = _Inventory()
    inv.fail = True
    mon = _monitor(inv, _Recorder(), current=SINK)

    with pytest.raises(SinkInventoryUnavailable):
        await mon.rematch("dac-1")


def test_unregister_by_another_owner_keeps_the_registration():
    mon = SinkMonitor(inventory=_Inventory())
    rec = _Recorder()
    old, new = object(), object()
    mon.register_presence("dac-1", MATCH, SINK, rec.present, rec.absent, owner=new)

    mon.unregister_presence("dac-1", owner=old)
    assert mon.has_presence("dac-1")

    mon.unregister_presence("dac-1", owner=new)
    assert not mon.has_presence("dac-1")
