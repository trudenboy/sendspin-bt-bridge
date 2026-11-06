"""The bridge status carries the phone source only when it is configured (#563)."""

from __future__ import annotations

import time

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


def test_a_phone_pairing_window_is_reported_while_the_source_is_off():
    """The Hardware page shows the window the operator opened; capture stays off."""
    source_state.publish(
        pairing_until=time.time() + 60,
        pairing_adapters=("C0:FB:F9:62:D6:9D",),
    )

    block = status._sendspin_source({"SENDSPIN_SOURCE": False})

    assert block is not None
    assert block.enabled is False
    assert block.state == "off"
    assert block.pairing_seconds_left > 0
    assert block.pairing_adapters == ["C0:FB:F9:62:D6:9D"]


def test_a_phone_paired_result_is_reported_while_the_source_is_off():
    """The bond outlives the window: the picker offers it again on the next visit."""
    source_state.publish(paired_phone_mac="34:C7:E9:25:A3:A3")

    block = status._sendspin_source({})

    assert block is not None
    assert block.enabled is False
    assert block.paired_phone_mac == "34:C7:E9:25:A3:A3"
    assert block.pairing_seconds_left == 0
