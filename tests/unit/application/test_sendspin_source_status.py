"""The bridge status carries the Bluetooth source only when it is configured (#563)."""

from __future__ import annotations

import pytest

from sendspin_bridge.application import status
from sendspin_bridge.services.source import state as source_state


@pytest.fixture(autouse=True)
def _clean_state():
    source_state.reset()
    yield
    source_state.reset()


def test_no_source_block_when_the_source_is_off():
    assert status._sendspin_source({"SENDSPIN_SOURCE": False}) is None
    assert status._sendspin_source({}) is None


def test_the_source_block_reports_the_state():
    source_state.publish(enabled=True, port=8940, phone="bluez_input.1", players=1, streaming=True)

    block = status._sendspin_source({"SENDSPIN_SOURCE": True})

    assert block is not None
    assert (block.enabled, block.port, block.players, block.streaming) == (True, 8940, 1, True)
    assert block.phone == "bluez_input.1"


def test_the_source_block_keeps_a_pairing_error_out_of_its_error():
    source_state.publish(enabled=True, port=8940, pairing_error="no_adapter")

    block = status._sendspin_source({"SENDSPIN_SOURCE": True})

    assert block is not None
    assert block.error is None
