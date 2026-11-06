"""The bridge status carries the phone source only when it is configured (#563)."""

from __future__ import annotations

import pytest

from sendspin_bridge.application import status
from sendspin_bridge.services.source import state as source_state


@pytest.fixture(autouse=True)
def _clean_state(installed_bluez):
    from sendspin_bridge.services.hardware import inventory

    inventory.clear_cache()
    source_state.reset()
    yield
    inventory.clear_cache()
    source_state.reset()


def test_no_source_block_when_the_source_is_off():
    assert status._sendspin_source({"SENDSPIN_SOURCE": False}) is None
    assert status._sendspin_source({}) is None


def test_the_source_block_reports_the_state():
    source_state.publish(
        enabled=True,
        state="streaming",
        phone="bluez_source.34_C7_E9_25_A3_A3",
        phone_mac="34:C7:E9:25:A3:A3",
        streaming=True,
        server="ws://ma:8927/sendspin",
        ma_connected=True,
        ma_paired=True,
    )

    block = status._sendspin_source({"SENDSPIN_SOURCE": True})

    assert block is not None
    assert (block.enabled, block.state, block.streaming) == (True, "streaming", True)
    assert block.phone == "bluez_source.34_C7_E9_25_A3_A3"


def test_the_source_block_keeps_a_pairing_error_out_of_its_error():
    source_state.publish(enabled=True, state="pairing", pairing_error="no_adapter", ma_error="ma_pairing_required")

    block = status._sendspin_source({"SENDSPIN_SOURCE": True})

    assert block is not None
    assert block.error is None
    assert block.ma_error == "ma_pairing_required"
