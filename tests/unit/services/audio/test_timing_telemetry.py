from sendspin_bridge.services.audio.timing_telemetry import collect_timing_snapshot


class _Audio:
    _sync_error_filtered_us = -12_500

    def get_timing_metrics(self):
        return {
            "output_latency_us": 48_500,
            "buffered_audio_us": 300_000,
            "playback_position_us": 123,
            "dac_samples_recorded": 2,
        }


class _Filter:
    offset = 2_500
    error = 750


class _Client:
    _time_filter = _Filter()

    def is_time_synchronized(self):
        return True


def test_collects_public_and_compatibility_metrics_with_correct_names():
    result = collect_timing_snapshot(_Audio(), _Client())

    assert result["timing_metrics_available"] is True
    assert result["backend_output_latency_ms"] == 48.5
    assert result["buffered_audio_ms"] == 300.0
    assert result["playback_sync_error_ms"] == -12.5
    assert result["clock_offset_ms"] == 2.5
    assert result["clock_uncertainty_ms"] == 0.75
    assert result["clock_synchronized"] is True


def test_missing_private_metrics_degrades_to_none():
    result = collect_timing_snapshot(object(), object())

    assert result["timing_metrics_available"] is False
    assert result["playback_sync_error_ms"] is None
    assert result["clock_uncertainty_ms"] is None


def test_unsynchronized_clock_with_infinite_covariance_degrades_to_none():
    class _UnsynchronizedFilter:
        offset = 0

        @property
        def error(self):
            raise OverflowError("cannot convert float infinity to integer")

    class _UnsynchronizedClient:
        _time_filter = _UnsynchronizedFilter()

        def is_time_synchronized(self):
            return False

    result = collect_timing_snapshot(_Audio(), _UnsynchronizedClient())

    assert result["clock_synchronized"] is False
    assert result["clock_offset_ms"] is None
    assert result["clock_uncertainty_ms"] is None
