import math
import struct

import pytest

from sendspin_bridge.services.audio.latency_calibration import (
    build_calibration_pcm,
    build_metronome_beat_pcm,
    calculate_metronome_lead_frames,
    estimate_relative_delay_ms,
)


def test_calibration_pcm_warms_suspended_sink_before_audible_probe():
    sample_rate = 48000
    pcm = build_calibration_pcm(sample_rate=sample_rate, duration_seconds=2)
    samples = [frame[0] for frame in struct.iter_unpack("<hh", pcm)]

    assert max(abs(sample) for sample in samples[: sample_rate // 2]) == 0
    assert max(abs(sample) for sample in samples[sample_rate // 2 :]) >= 20000


def test_metronome_beat_uses_a_dsp_safe_audible_probe_followed_by_silence():
    sample_rate = 8000
    pcm = build_metronome_beat_pcm(sample_rate=sample_rate, bpm=120)
    samples = [frame[0] for frame in struct.iter_unpack("<hh", pcm)]

    assert len(samples) == sample_rate // 2
    assert max(abs(sample) for sample in samples[: sample_rate // 20]) >= 20000
    assert max(abs(sample) for sample in samples[sample_rate // 20 : sample_rate // 10]) >= 10000
    assert max(abs(sample) for sample in samples[sample_rate // 7 :]) == 0


def test_metronome_keepalive_carrier_prevents_digital_silence_between_probes():
    sample_rate = 8000
    pcm = build_metronome_beat_pcm(
        sample_rate=sample_rate,
        bpm=60,
        keepalive_amplitude=100,
    )
    samples = [frame[0] for frame in struct.iter_unpack("<hh", pcm)]
    quiet_tail = samples[sample_rate // 4 :]

    assert max(abs(sample) for sample in samples[: sample_rate // 10]) >= 20000
    assert any(sample != 0 for sample in quiet_tail)
    assert max(abs(sample) for sample in quiet_tail) <= 100


def test_manual_sync_click_is_a_short_woodblock_transient():
    sample_rate = 48000
    pcm = build_metronome_beat_pcm(
        sample_rate=sample_rate,
        bpm=120,
        click_duration_ms=40,
        keepalive_amplitude=100,
    )
    samples = [frame[0] for frame in struct.iter_unpack("<hh", pcm)]

    assert max(abs(sample) for sample in samples[: sample_rate // 100]) >= 18000
    assert max(abs(sample) for sample in samples[round(sample_rate * 0.05) :]) <= 100


def test_manual_sync_click_can_wake_a_speaker_gate_before_the_woodblock():
    sample_rate = 48000
    pcm = build_metronome_beat_pcm(
        sample_rate=sample_rate,
        bpm=120,
        gate_preroll_ms=80,
        click_duration_ms=40,
        keepalive_amplitude=100,
    )
    samples = [frame[0] for frame in struct.iter_unpack("<hh", pcm)]
    preroll = samples[round(sample_rate * 0.01) : round(sample_rate * 0.075)]
    woodblock = samples[round(sample_rate * 0.08) : round(sample_rate * 0.09)]
    quiet_tail = samples[round(sample_rate * 0.13) :]

    assert 4000 <= max(abs(sample) for sample in preroll) <= 12000
    assert max(abs(sample) for sample in woodblock) >= 18000
    assert max(abs(sample) for sample in quiet_tail) <= 100


def test_metronome_starts_at_shared_phase_for_speakers_started_at_different_times():
    sample_rate = 48000
    epoch = 100.0
    first_started_at = 101.13
    second_started_at = 101.41

    first_lead = calculate_metronome_lead_frames(
        first_started_at,
        sample_rate=sample_rate,
        bpm=120,
        epoch_seconds=epoch,
    )
    second_lead = calculate_metronome_lead_frames(
        second_started_at,
        sample_rate=sample_rate,
        bpm=120,
        epoch_seconds=epoch,
    )
    first_click_at = first_started_at + first_lead / sample_rate
    second_click_at = second_started_at + second_lead / sample_rate

    assert first_click_at == pytest.approx(second_click_at, abs=1 / sample_rate)
    assert first_click_at >= max(first_started_at, second_started_at) + 0.75


def test_metronome_shared_phase_accepts_per_speaker_delay_offset():
    sample_rate = 48000
    started_at = 101.13
    base_epoch = 100.0
    delay_seconds = 0.18

    base_frames = calculate_metronome_lead_frames(
        started_at,
        sample_rate=sample_rate,
        bpm=120,
        epoch_seconds=base_epoch,
    )
    delayed_frames = calculate_metronome_lead_frames(
        started_at,
        sample_rate=sample_rate,
        bpm=120,
        epoch_seconds=base_epoch - delay_seconds,
    )

    phase_delta = ((delayed_frames - base_frames) / sample_rate) % 0.5
    assert phase_delta == pytest.approx(0.5 - delay_seconds, abs=1 / sample_rate)


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
