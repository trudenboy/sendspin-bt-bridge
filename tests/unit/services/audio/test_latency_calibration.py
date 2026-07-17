import math
import struct

import pytest

from sendspin_bridge.services.audio.latency_calibration import build_calibration_pcm, estimate_relative_delay_ms


def test_calibration_pcm_warms_suspended_sink_before_audible_probe():
    sample_rate = 48000
    pcm = build_calibration_pcm(sample_rate=sample_rate, duration_seconds=2)
    samples = [frame[0] for frame in struct.iter_unpack("<hh", pcm)]

    assert max(abs(sample) for sample in samples[: sample_rate // 2]) == 0
    assert max(abs(sample) for sample in samples[sample_rate // 2 :]) >= 20000


def test_estimates_positive_relative_delay_from_two_recordings():
    sample_rate = 1000
    reference = [0.0] * 400
    target = [0.0] * 400
    for offset, value in enumerate((0.2, 0.7, 1.0, 0.7, 0.2)):
        reference[100 + offset] = value
        target[137 + offset] = value

    result = estimate_relative_delay_ms(reference, target, sample_rate=sample_rate, max_lag_ms=100)

    assert math.isclose(result.delay_ms, 37.0)
    assert result.confidence > 0.9


def test_silence_is_rejected():
    result = estimate_relative_delay_ms([0.0] * 100, [0.0] * 100, sample_rate=1000)

    assert result.valid is False


def test_long_recordings_are_bounded_without_losing_delay_direction():
    sample_rate = 48000
    reference = [0.0] * (sample_rate * 2)
    target = [0.0] * (sample_rate * 2)
    for index in range(24000, 24200):
        reference[index] = 1.0
        target[index + 4800] = 1.0

    result = estimate_relative_delay_ms(reference, target, sample_rate=sample_rate)

    assert result.valid is True
    assert result.delay_ms == pytest.approx(100.0, abs=2.0)


def test_estimates_envelope_delay_across_different_speaker_frequency_response():
    sample_rate = 8000
    reference = [0.0] * (sample_rate * 4)
    target = [0.0] * (sample_rate * 4)
    burst_frames = round(sample_rate * 0.35)
    delay_frames = round(sample_rate * 0.05)
    for index in range(burst_frames):
        envelope = math.sin(math.pi * index / (burst_frames - 1)) ** 2
        reference[6000 + index] = envelope * math.sin(2 * math.pi * 900 * index / sample_rate)
        target[6000 + delay_frames + index] = 0.4 * envelope * math.sin(2 * math.pi * 1500 * index / sample_rate + 0.7)

    result = estimate_relative_delay_ms(reference, target, sample_rate=sample_rate)

    assert result.valid is True
    assert result.delay_ms == pytest.approx(50.0, abs=5.0)
