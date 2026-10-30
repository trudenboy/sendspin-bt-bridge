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
