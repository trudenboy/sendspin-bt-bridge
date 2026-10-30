"""The bridge status carries the Bluetooth source only when it is configured (#563)."""

from __future__ import annotations

from sendspin_bridge.application import status


def test_no_source_block_when_the_source_is_off():
    assert status._sendspin_source({"SENDSPIN_SOURCE": False}) is None
    assert status._sendspin_source({}) is None


def test_the_source_block_reports_the_state(monkeypatch):
    from sendspin_bridge.services.source import bt_source

    monkeypatch.setattr(
        bt_source,
        "source_status",
        lambda: {
            "enabled": True,
            "port": 8940,
            "phone": "bluez_input.1",
            "players": 1,
            "streaming": True,
            "error": None,
        },
    )
    block = status._sendspin_source({"SENDSPIN_SOURCE": True})
    assert block is not None
    assert (block.enabled, block.port, block.players, block.streaming) == (True, 8940, 1, True)
    assert block.phone == "bluez_input.1"
