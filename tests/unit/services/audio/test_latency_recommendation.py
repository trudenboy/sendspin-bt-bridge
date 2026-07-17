from sendspin_bridge.services.audio.latency_recommendation import build_latency_recommendation


def test_bluez_report_has_precedence_over_codec_fallback():
    result = build_latency_recommendation(reported_bt_delay_ms=187.4, codec_name="sbc")

    assert result.value_ms == 187
    assert result.source == "bluez_delay_report"
    assert result.confidence == "medium"
    assert result.requires_confirmation is True


def test_codec_fallback_is_low_confidence():
    result = build_latency_recommendation(reported_bt_delay_ms=None, codec_name="aptx_ll")

    assert result.value_ms == 40
    assert result.source == "codec_fallback"
    assert result.confidence == "low"


def test_unknown_codec_does_not_invent_recommendation():
    result = build_latency_recommendation(reported_bt_delay_ms=None, codec_name="mystery")

    assert result.value_ms is None
    assert result.source == "unavailable"


def test_manual_calibration_has_highest_precedence():
    result = build_latency_recommendation(
        reported_bt_delay_ms=125,
        codec_name="sbc",
        calibrated_delay_ms=211,
        calibration_source="microphone_calibration",
    )

    assert result.value_ms == 211
    assert result.source == "microphone_calibration"
    assert result.confidence == "high"
