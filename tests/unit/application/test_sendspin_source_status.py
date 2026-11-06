"""The bridge status carries the phone source only when it is configured (#563)."""

from __future__ import annotations

import time

import pytest

from sendspin_bridge.application import status
from sendspin_bridge.services.source import state as source_state

PHONE = "34:C7:E9:25:A3:A3"


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
    source_state.publish(paired_phone_mac=PHONE)

    block = status._sendspin_source({})

    assert block is not None
    assert block.enabled is False
    assert block.paired_phone_mac == PHONE
    assert block.pairing_seconds_left == 0


@pytest.mark.parametrize(
    "change",
    [
        pytest.param({"paired_phone_mac": PHONE}, id="bond"),
        pytest.param({"pairing_until": time.time() + 60}, id="window"),
    ],
)
def test_an_off_source_block_never_reads_the_hardware_inventory(monkeypatch, change):
    """The off-state block reports a window, bond or error — never host hardware.

    ``application/status`` builds the source block whenever a bond or a window
    exists, i.e. for the life of the process after any phone pairing.  Reading
    the shared inventory there made every status tick (and every SPA event
    frame) refresh PulseAudio/BlueZ twice a second with the feed off.
    """
    from sendspin_bridge.services.hardware import inventory

    source_state.publish(**change)
    monkeypatch.setattr(inventory, "read_inventory", lambda: pytest.fail("off-state block read hardware"))

    block = status._sendspin_source({"SENDSPIN_SOURCE": False})

    assert block is not None
    assert block.enabled is False


def test_a_failed_headset_restore_keeps_the_source_error_visible_while_the_feed_is_off(monkeypatch):
    """The operator turned the feed off; the hand-off back to A2DP failed.

    With no pairing window and no fresh bond the block used to be omitted
    entirely, so the only explanation for the silent speaker lived in an
    unpublished snapshot.
    """
    from sendspin_bridge.services.bluetooth import headset

    service = headset.HeadsetProfileService()
    service._set_handoff(PHONE, "restore_failed")
    monkeypatch.setattr(headset, "_service", service)
    source_state.publish(state="off", error="headset_a2dp_restore_failed", streaming=False)

    block = status._sendspin_source({"SENDSPIN_SOURCE": False})

    assert block is not None
    assert block.enabled is False
    assert block.error == "headset_a2dp_restore_failed"

    service._set_handoff(PHONE, "none")
    source_state.publish(error=None)
    assert status._sendspin_source({"SENDSPIN_SOURCE": False}) is None
