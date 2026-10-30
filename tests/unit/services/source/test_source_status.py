"""The source reports what the bridge status shows (#563)."""

from __future__ import annotations

import asyncio

from sendspin_bridge.services.source import bt_source


def test_the_source_is_off_until_it_starts():
    state = bt_source.source_status()
    assert state["enabled"] is False
    assert state["streaming"] is False


def test_stopping_resets_what_the_status_shows():
    bt_source._set_state(enabled=True, port=8940, phone="bluez_input.1", players=1, streaming=True)
    asyncio.run(bt_source.BtSendspinSource(bt_source.SourceConfig(), identity_path=None, pairing_path=None).stop())

    state = bt_source.source_status()
    # The port stays as configured; everything about the phone and the players is cleared.
    assert (state["enabled"], state["phone"], state["players"], state["streaming"]) == (False, None, 0, False)
