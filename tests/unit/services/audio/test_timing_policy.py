"""An absolute Bluetooth report must never replace a group-relative or air-targeted setting."""

import pytest

from sendspin_bridge.services.audio.latency_recommendation import build_latency_recommendation


@pytest.mark.parametrize("source", ["manual", "recommendation", "music_assistant", "group_sync"])
def test_confirmed_compensation_is_not_replaced_by_an_absolute_report(source):
    result = build_latency_recommendation(
        reported_bt_delay_ms=150,
        codec_name="sbc",
        current_delay_ms=0,
        current_source=source,
    )
    assert result.value_ms is None
    assert result.source == "already_tuned"


@pytest.mark.parametrize("context", [{"air_target_active": True}, {"grouped": True}])
def test_auto_pending_is_not_permission_to_compensate_a_second_time(context):
    result = build_latency_recommendation(
        reported_bt_delay_ms=150,
        codec_name="sbc",
        current_delay_ms=0,
        current_source="auto_pending",
        **context,
    )
    assert result.value_ms is None


def test_an_untuned_standalone_speaker_still_has_a_starting_suggestion():
    result = build_latency_recommendation(
        reported_bt_delay_ms=150,
        codec_name="sbc",
        current_delay_ms=0,
        current_source="auto_pending",
    )
    assert result.value_ms == 150


def test_unknown_group_membership_is_not_treated_as_a_standalone_speaker():
    result = build_latency_recommendation(
        reported_bt_delay_ms=150,
        codec_name="sbc",
        current_delay_ms=0,
        current_source="auto_pending",
        group_membership_known=False,
    )
    assert result.value_ms is None and result.source == "group_unknown"
