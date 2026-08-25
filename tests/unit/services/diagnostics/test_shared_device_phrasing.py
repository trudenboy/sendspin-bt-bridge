"""Two screens, one device, one answer.

The recovery card and the operator-guidance card both tell you how a
reconnect is going, and each had its own copy of the sentence.  They read
different sources — one the normalised state, one the raw extras — and
punctuated differently, so the same speaker could be described two ways at
once, and could be given two different attempt counts if the two
representations disagreed.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from sendspin_bridge.services.diagnostics.device_phrasing import reconnect_attempt_summary


def _device(*, extra=None, state_model=None) -> SimpleNamespace:
    return SimpleNamespace(extra=extra or {}, state_model=state_model or {})


# ── what the sentence says ───────────────────────────────────────────────


def test_no_attempt_in_progress_says_nothing():
    assert reconnect_attempt_summary(_device(extra={"reconnect_attempt": 0})) == ""


def test_an_attempt_without_a_limit_is_reported_plainly():
    summary = reconnect_attempt_summary(_device(extra={"reconnect_attempt": 2}))

    assert summary == "Reconnect attempt 2 is in progress."


def test_an_attempt_against_a_limit_reports_what_is_left():
    summary = reconnect_attempt_summary(_device(extra={"reconnect_attempt": 3, "max_reconnect_fails": 10}))

    assert "3/10" in summary
    assert "7 attempts remain" in summary


def test_the_last_attempt_reports_none_remaining():
    summary = reconnect_attempt_summary(_device(extra={"reconnect_attempt": 10, "max_reconnect_fails": 10}))

    assert "0 attempts remain" in summary


def test_an_overshooting_count_does_not_go_negative():
    summary = reconnect_attempt_summary(_device(extra={"reconnect_attempt": 12, "max_reconnect_fails": 10}))

    assert "-2" not in summary
    assert "0 attempts remain" in summary


# ── where it reads from ──────────────────────────────────────────────────


def test_the_normalised_state_is_preferred_when_present():
    device = _device(
        extra={"reconnect_attempt": 1, "max_reconnect_fails": 5},
        state_model={"bluetooth": {"reconnect_attempt": 4, "max_reconnect_fails": 10}},
    )

    assert "4/10" in reconnect_attempt_summary(device)


def test_the_extras_are_used_when_there_is_no_normalised_state():
    device = _device(extra={"reconnect_attempt": 4, "max_reconnect_fails": 10})

    assert "4/10" in reconnect_attempt_summary(device)


@pytest.mark.parametrize("missing", [{}, {"bluetooth": {}}, {"bluetooth": None}])
def test_an_empty_normalised_state_falls_back_rather_than_reporting_nothing(missing):
    device = _device(extra={"reconnect_attempt": 2, "max_reconnect_fails": 5}, state_model=missing)

    assert "2/5" in reconnect_attempt_summary(device)


# ── the property that was broken ─────────────────────────────────────────


def test_both_cards_describe_a_device_identically():
    """The recovery card and the guidance card must not disagree."""
    from sendspin_bridge.services.diagnostics import operator_guidance, recovery_assistant

    device = _device(
        extra={"reconnect_attempt": 1, "max_reconnect_fails": 5},
        state_model={"bluetooth": {"reconnect_attempt": 4, "max_reconnect_fails": 10}},
    )

    assert operator_guidance._reconnect_attempt_summary(device) == recovery_assistant._reconnect_attempt_summary(device)
