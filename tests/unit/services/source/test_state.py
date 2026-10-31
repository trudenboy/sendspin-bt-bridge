"""The source's runtime state has one typed snapshot, read across threads."""

from __future__ import annotations

import dataclasses

import pytest

from sendspin_bridge.services.source import state


@pytest.fixture(autouse=True)
def _clean():
    state.reset()
    yield
    state.reset()


def test_a_fresh_snapshot_is_off_and_keeps_the_status_keys_unchanged():
    snapshot = state.snapshot()

    assert (snapshot.enabled, snapshot.port, snapshot.phone, snapshot.players, snapshot.streaming) == (
        False,
        None,
        None,
        0,
        False,
    )
    assert snapshot.status_dict() == {
        "enabled": False,
        "port": None,
        "phone": None,
        "players": 0,
        "streaming": False,
        "error": None,
        "pairing_seconds_left": 0,
        "pairing_adapters": [],
    }


def test_publishing_replaces_the_snapshot_instead_of_mutating_it():
    before = state.snapshot()
    state.publish(enabled=True, port=8940, phone="bluez_input.34_C7_E9_25_A3_A3.2")

    after = state.snapshot()

    assert dataclasses.is_dataclass(after)
    assert before.enabled is False and before.port is None
    assert (after.enabled, after.port, after.phone) == (True, 8940, "bluez_input.34_C7_E9_25_A3_A3.2")


def test_the_pairing_window_belongs_to_the_same_snapshot():
    state.publish(pairing_until=4_000_000_000.0, pairing_adapters=("AA:BB:CC:DD:EE:FF",))

    snapshot = state.snapshot()

    assert snapshot.pairing_adapters == ("AA:BB:CC:DD:EE:FF",)
    assert snapshot.status_dict()["pairing_adapters"] == ["AA:BB:CC:DD:EE:FF"]
    assert snapshot.pairing_seconds_left() > 0


def test_a_source_error_is_not_lost_because_pairing_has_none():
    state.publish(error="port_in_use")

    assert state.snapshot().status_dict()["error"] == "port_in_use"


def test_a_pairing_error_is_reported_when_the_source_has_none():
    state.publish(pairing_error="no_adapter")

    assert state.snapshot().status_dict()["error"] == "no_adapter"


def test_an_unknown_field_cannot_corrupt_the_snapshot():
    with pytest.raises(TypeError):
        state.publish(not_a_field=True)

    assert state.snapshot() == state.SourceState()
