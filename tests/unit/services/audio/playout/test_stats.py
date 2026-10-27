import pytest

from sendspin_bridge.services.audio.playout.stats import ClockSlewEstimator, LinearTrend


def test_trend_slope_of_a_line():
    tr = LinearTrend(window_s=60)
    for i in range(61):
        tr.add(float(i), 3.0 + 0.5 * i)
    assert tr.slope() == pytest.approx(0.5)


def test_trend_forgets_points_outside_the_window():
    tr = LinearTrend(window_s=10)
    for i in range(10):
        tr.add(float(i), 0.0)
    for i in range(10, 30):
        tr.add(float(i), float(i))
    assert tr.slope() == pytest.approx(1.0)


def test_trend_needs_enough_span():
    tr = LinearTrend(window_s=60)
    tr.add(0.0, 0.0)
    tr.add(1.0, 1.0)
    assert tr.slope() is None


def test_trend_reset_clears_points():
    tr = LinearTrend(window_s=60)
    for i in range(61):
        tr.add(float(i), float(i))
    tr.reset()
    assert tr.slope() is None


def test_slew_of_a_clock_running_500_ppm_fast():
    raw = [0]
    est = ClockSlewEstimator(raw_ns=lambda: raw[0], mono_ns=lambda: int(raw[0] * 1.0005))
    for _ in range(61):
        est.sample()
        raw[0] += 1_000_000_000
    assert est.ppm() == pytest.approx(500.0, abs=1.0)


def test_slew_is_unknown_before_enough_samples():
    est = ClockSlewEstimator(raw_ns=lambda: 0, mono_ns=lambda: 0)
    est.sample()
    assert est.ppm() is None
