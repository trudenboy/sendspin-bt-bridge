from __future__ import annotations

import random

import pytest

from sendspin_bridge.services.audio.player.clock_diag import RateTracker


def test_rate_is_reported_in_ppm_against_the_reference():
    tracker = RateTracker(window_s=60.0)
    for i in range(31):
        ref_us = 1_000_000_000 + i * 1_000_000
        tracker.add(ref_us, 5_000 + int(i * 1_000_000 * (1 + 120e-6)))

    stats = tracker.stats()

    assert stats is not None
    assert stats.ppm == pytest.approx(120.0, abs=0.5)
    assert stats.sigma_us == pytest.approx(0.0, abs=1.0)
    assert stats.span_s == pytest.approx(30.0)
    assert stats.samples == 31


def test_residual_sigma_shows_the_jitter_around_the_fitted_rate():
    rng = random.Random(7)
    tracker = RateTracker(window_s=60.0)
    for i in range(200):
        ref_us = i * 250_000
        tracker.add(ref_us, ref_us + round(rng.gauss(0, 400)))

    stats = tracker.stats()

    assert stats is not None
    assert stats.ppm == pytest.approx(0.0, abs=20.0)
    assert 300 < stats.sigma_us < 500


def test_only_the_last_window_counts():
    tracker = RateTracker(window_s=10.0)
    for i in range(20):  # 1000 ppm fast for 20 s ...
        tracker.add(i * 1_000_000, int(i * 1_000_000 * 1.001))
    base = int(19 * 1_000_000 * 1.001)
    for i in range(1, 21):  # ... then exact for 20 s
        tracker.add((19 + i) * 1_000_000, base + i * 1_000_000)

    stats = tracker.stats()

    assert stats is not None
    assert stats.ppm == pytest.approx(0.0, abs=0.5)
    assert stats.span_s <= 10.0


def test_too_few_samples_give_no_rate():
    tracker = RateTracker(window_s=60.0)
    assert tracker.stats() is None
    tracker.add(0, 0)
    tracker.add(1_000_000, 1_000_000)
    assert tracker.stats() is None


def test_reset_forgets_everything():
    tracker = RateTracker(window_s=60.0)
    for i in range(10):
        tracker.add(i * 1_000_000, i * 1_000_000)
    tracker.reset()
    assert tracker.stats() is None


# ── ClockDiagnostics: what the daemon samples and logs while playing ─────────

import logging  # noqa: E402

from sendspin_bridge.services.audio.player.clock_diag import ClockDiagnostics  # noqa: E402


def _feed(diag: ClockDiagnostics, seconds: int, *, audio: bool, generation: int = 1, start_s: int = 0):
    lines = []
    for i in range(start_s, start_s + seconds + 1):
        raw = 1_000_000_000 + i * 1_000_000
        mono = 5_000_000_000 + int(i * 1_000_000 * (1 + 50e-6))
        audio_ns = int((i - start_s) * 1_000_000_000 * (1 - 30e-6)) if audio else None
        diag.sample(raw_us=raw, mono_us=mono, audio_ns=audio_ns, generation=generation)
        due = diag.due(raw_us=raw)
        if due is not None:
            lines.append(due)
    return lines


def test_debug_every_10_s_and_info_every_minute():
    diag = ClockDiagnostics(audio_clock_enabled=True)
    lines = _feed(diag, 120, audio=True)

    levels = [level for level, _ in lines]
    assert levels.count(logging.INFO) == 2
    assert levels.count(logging.DEBUG) == 10


def test_line_reports_both_rates_in_ppm():
    diag = ClockDiagnostics(audio_clock_enabled=True)
    lines = _feed(diag, 60, audio=True)
    _level, text = lines[-1]

    assert "MONOTONIC vs RAW +50.00 ppm" in text
    assert "audio clock vs RAW -30.00 ppm" in text


def test_line_says_how_to_enable_the_audio_clock_when_it_is_off():
    diag = ClockDiagnostics(audio_clock_enabled=False)
    _level, text = _feed(diag, 60, audio=False)[-1]

    assert "MONOTONIC vs RAW +50.00 ppm" in text
    assert "SENDSPIN_PLAYOUT_DIAG=1" in text


def test_a_new_output_starts_the_audio_clock_window_over():
    diag = ClockDiagnostics(audio_clock_enabled=True)
    _feed(diag, 30, audio=True, generation=1)
    # A new pipeline: its audio clock restarts from 0.
    lines = _feed(diag, 30, audio=True, generation=2, start_s=31)

    _level, text = lines[-1]
    assert "audio clock vs RAW -30.00 ppm" in text


def test_nothing_is_due_until_sampling_started():
    diag = ClockDiagnostics(audio_clock_enabled=False)
    assert diag.due(raw_us=10_000_000) is None


def test_a_long_pause_gives_one_line_not_a_burst_of_overdue_ones():
    diag = ClockDiagnostics(audio_clock_enabled=False)
    diag.sample(raw_us=0, mono_us=0, audio_ns=None, generation=1)
    late = 600_000_000  # 10 min later, nothing sampled meanwhile
    diag.sample(raw_us=late, mono_us=late, audio_ns=None, generation=1)

    assert diag.due(raw_us=late) is not None
    assert diag.due(raw_us=late + 1_000_000) is None
