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

    assert (snapshot.enabled, snapshot.state, snapshot.phone, snapshot.streaming) == (False, "off", None, False)
    assert snapshot.status_dict() == {
        "enabled": False,
        "state": "off",
        "phone": None,
        "phone_mac": None,
        "paired_phone_mac": None,
        "streaming": False,
        "server": None,
        "ma_connected": False,
        "ma_paired": False,
        "ma_pairing_required": False,
        "ma_pairing_code": None,
        "ma_pairing_seconds_left": 0,
        "ma_error": None,
        "pairing_seconds_left": 0,
        "pairing_adapters": [],
        "devices": [],
        "target_player": None,
        "target_error": None,
        "active_input": None,
        "error": None,
    }


def test_publishing_replaces_the_snapshot_instead_of_mutating_it():
    before = state.snapshot()
    state.publish(enabled=True, phone="bluez_input.34_C7_E9_25_A3_A3.2", server="ws://ma:8927/sendspin")

    after = state.snapshot()

    assert dataclasses.is_dataclass(after)
    assert before.enabled is False and before.phone is None
    assert (after.enabled, after.phone) == (True, "bluez_input.34_C7_E9_25_A3_A3.2")


def test_the_phone_pairing_window_belongs_to_the_same_snapshot():
    state.publish(pairing_until=4_000_000_000.0, pairing_adapters=("AA:BB:CC:DD:EE:FF",))

    snapshot = state.snapshot()

    assert snapshot.pairing_adapters == ("AA:BB:CC:DD:EE:FF",)
    assert snapshot.status_dict()["pairing_adapters"] == ["AA:BB:CC:DD:EE:FF"]
    assert snapshot.pairing_seconds_left() > 0


def test_the_music_assistant_pairing_code_has_its_own_countdown():
    state.publish(ma_pairing_code="12345678", ma_pairing_until=4_000_000_000.0)

    model = state.SourceState(**dataclasses.asdict(state.snapshot()))
    assert model.ma_pairing_seconds_left() > 0
    assert state.snapshot().status_dict()["ma_pairing_code"] == "12345678"


def test_a_source_error_is_not_lost_because_pairing_has_none():
    state.publish(error="capture_unavailable")

    assert state.snapshot().status_dict()["error"] == "capture_unavailable"


def test_a_pairing_error_is_not_the_sources_error():
    state.publish(pairing_error="no_adapter")

    assert state.snapshot().status_dict()["error"] is None


def test_the_status_dict_constructs_the_bridge_status_model():
    from sendspin_bridge.application.models.bridge import SendspinSourceStatus

    model = SendspinSourceStatus(**state.snapshot().status_dict())

    assert model.state == "off"
    assert model.ma_pairing_required is False


def test_an_unknown_field_cannot_corrupt_the_snapshot():
    with pytest.raises(TypeError):
        state.publish(not_a_field=True)

    assert state.snapshot() == state.SourceState()


def test_a_publish_that_changes_a_visible_field_announces_the_status(monkeypatch):
    """A pairing window opening or closing must reach the event stream on its own.

    The status stream is only built when something announces a change; a source
    that closed its window silently left the SPA counting down a window the
    server had already ended.
    """
    from sendspin_bridge.services.lifecycle import bridge_runtime_state

    announced: list[int] = []
    monkeypatch.setattr(bridge_runtime_state, "notify_status_changed", lambda: announced.append(1))

    state.publish(pairing_until=1.0, pairing_adapters=("AA:BB:CC:DD:EE:FF",))
    state.publish(pairing_until=0.0, pairing_adapters=())

    assert announced == [1, 1]


def test_a_publish_that_changes_nothing_visible_stays_silent(monkeypatch):
    """Re-publishing an identical snapshot must not spend a status build."""
    from sendspin_bridge.services.lifecycle import bridge_runtime_state

    announced: list[int] = []
    monkeypatch.setattr(bridge_runtime_state, "notify_status_changed", lambda: announced.append(1))
    state.publish(enabled=True, state="waiting")

    state.publish(enabled=True, state="waiting")

    assert announced == [1]
