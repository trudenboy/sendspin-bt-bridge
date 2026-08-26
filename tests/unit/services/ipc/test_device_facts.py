"""The facts a device state is built from, named once.

The normalised state read nineteen facts out of an untyped bag by string, and
the capability builder and the guidance read more of the same bag beside it.
Every one of those strings is a chance to ask for a key nobody writes — which
is how the sink name and the reconnect limit both came to be reported as
missing on devices that had them.

`DeviceFacts` is that bag with names: it takes a snapshot and answers the
questions, so a typo is an attribute error at the call rather than a `None`
on somebody's screen.
"""

from __future__ import annotations

from types import SimpleNamespace

from sendspin_bridge.services.ipc.device_facts import DeviceFacts


def _snapshot(**extra) -> SimpleNamespace:
    return SimpleNamespace(
        player_name="Kitchen",
        enabled=True,
        has_sink=True,
        sink_name="bluez_output.AA_BB_CC_DD_EE_FF.1",
        bluetooth_connected=True,
        extra=dict(extra),
    )


# ── reading what is there ────────────────────────────────────────────────


def test_it_answers_from_the_typed_field_before_the_bag():
    facts = DeviceFacts(_snapshot(sink_name="stale-from-the-bag"))

    assert facts.sink_name == "bluez_output.AA_BB_CC_DD_EE_FF.1"


def test_it_falls_back_to_the_bag_for_facts_with_no_field():
    facts = DeviceFacts(_snapshot(reconnect_attempt=3, max_reconnect_fails=10))

    assert facts.reconnect_attempt == 3
    assert facts.max_reconnect_fails == 10


def test_the_remaining_attempts_are_derived_not_stored():
    facts = DeviceFacts(_snapshot(reconnect_attempt=3, max_reconnect_fails=10))

    assert facts.reconnect_attempts_remaining == 7


def test_an_overshoot_does_not_report_negative_attempts():
    facts = DeviceFacts(_snapshot(reconnect_attempt=12, max_reconnect_fails=10))

    assert facts.reconnect_attempts_remaining == 0


def test_without_a_limit_there_is_no_remaining_count():
    facts = DeviceFacts(_snapshot(reconnect_attempt=3))

    assert facts.reconnect_attempts_remaining is None


# ── reading what is not ──────────────────────────────────────────────────


def test_an_absent_fact_answers_its_documented_default():
    facts = DeviceFacts(_snapshot())

    assert facts.never_paired is False
    assert facts.pair_failure_kind is None
    assert facts.reconnect_attempt is None


def test_a_fact_that_does_not_exist_is_an_error_rather_than_a_none():
    """The property this type exists for."""
    facts = DeviceFacts(_snapshot())

    try:
        facts.resolved_sink_name  # noqa: B018 — the point is that it raises
    except AttributeError:
        return
    raise AssertionError("a misspelled fact answered instead of failing")


# ── the shapes the state model publishes ─────────────────────────────────


def test_the_bluetooth_block_matches_what_the_state_model_publishes():
    from sendspin_bridge.services.ipc.bridge_state_model import build_normalized_device_state

    snapshot = _snapshot(reconnect_attempt=2, max_reconnect_fails=5, bluetooth_paired=True)
    snapshot.bluetooth_mac = "AA:BB:CC:DD:EE:FF"

    bluetooth = build_normalized_device_state(snapshot).to_dict()["bluetooth"]
    facts = DeviceFacts(snapshot)

    assert bluetooth["reconnect_attempt"] == facts.reconnect_attempt
    assert bluetooth["max_reconnect_fails"] == facts.max_reconnect_fails
    assert bluetooth["reconnect_attempts_remaining"] == facts.reconnect_attempts_remaining
    assert bluetooth["paired"] == facts.bluetooth_paired


# ── absent is not the same as false ──────────────────────────────────────


def test_a_fact_that_was_never_carried_is_distinguishable_from_a_false_one():
    """Callers with a fallback need to tell "no" from "nothing said"."""
    said_no = DeviceFacts(_snapshot(ma_connected=False))
    said_nothing = DeviceFacts(_snapshot())

    assert said_no.ma_connected is False
    assert said_nothing.ma_connected is False
    assert said_no.knows("ma_connected") is True
    assert said_nothing.knows("ma_connected") is False


def test_a_carried_true_is_known():
    assert DeviceFacts(_snapshot(ma_connected=True)).knows("ma_connected") is True
