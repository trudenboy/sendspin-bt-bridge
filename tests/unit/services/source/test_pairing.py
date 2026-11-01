"""The phone pairing window (#563)."""

from __future__ import annotations

import asyncio

from sendspin_bridge.services.source import manager, pairing


def test_no_window_is_open_by_default():
    state = pairing.pairing_status()
    assert state["pairing_seconds_left"] == 0
    assert state["pairing_adapters"] == []


def test_the_manager_reports_nothing_to_stop_before_a_start():
    asyncio.run(manager.stop())
    assert manager.running() is False


async def test_the_window_closes_as_soon_as_the_phone_bonds(monkeypatch):
    from sendspin_bridge.services.source import state

    state.reset()
    monkeypatch.setattr(pairing, "_known_devices", set())
    monkeypatch.setattr(pairing, "_devices", lambda: {"34:C7:E9:25:A3:A3"})
    trusted: list[set[str]] = []
    monkeypatch.setattr(pairing, "_trust", lambda macs: trusted.append(set(macs)))
    monkeypatch.setattr(pairing, "_set_discoverable", lambda *args: True)

    await pairing._watch_window(30, ["AA:BB:CC:DD:EE:FF"])

    snapshot = state.snapshot()
    assert trusted == [{"34:C7:E9:25:A3:A3"}]
    assert snapshot.pairing_until == 0
    assert snapshot.pairing_adapters == ()
    assert snapshot.paired_phone_mac == "34:C7:E9:25:A3:A3"
    state.reset()
