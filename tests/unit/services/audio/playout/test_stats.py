import pytest

from sendspin_bridge.services.audio.playout.stats import ClockSlewEstimator, LinearTrend, PlayoutStats


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


def test_stats_p95_is_nearest_rank():
    st = PlayoutStats()
    for i in range(100):
        st.error(i * 0.1, (0.1 if i < 95 else 5.0) * 1000.0)
    snap = st.snapshot()
    assert 0.1 <= snap["error_p95_ms"] <= 5.0
    assert snap["error_p95_ms"] == pytest.approx(0.1)
    assert snap["error_ms"] == 5.0


def test_stats_error_slope_of_a_ramp():
    st = PlayoutStats()
    for i in range(61):
        st.error(float(i), 0.2 * i * 1000.0)
    assert st.snapshot()["error_slope_ms_per_s"] == pytest.approx(0.2)


def test_stats_corrections_per_second_over_rate_window():
    st = PlayoutStats()
    for i in range(10):
        st.correction(float(i), 0, 3)
    snap = st.snapshot()
    assert snap["corrections_per_s"] == pytest.approx(3.0)
    assert snap["dropped"] == 30
    assert snap["inserted"] == 0


def test_stats_reset_window_keeps_counters():
    st = PlayoutStats()
    for i in range(61):
        st.error(float(i), 200.0 * i)
    st.correction(1.0, 2, 3)
    st.jump("2026-10-27T00:00:00Z", 12345.0, "placement")
    st.underrun()
    st.pa_step()
    st.rejected()
    st.reset_window()
    snap = st.snapshot()
    assert snap["error_p95_ms"] is None
    assert snap["error_slope_ms_per_s"] is None
    assert (snap["inserted"], snap["dropped"], snap["jumps"]) == (2, 3, 1)
    assert (snap["underruns"], snap["pa_steps"], snap["rejected_samples"]) == (1, 1, 1)
    assert snap["last_jump_ms"] == 12.345
    assert snap["last_jump_reason"] == "placement"
    assert snap["last_jump_at"] == "2026-10-27T00:00:00Z"


def test_stats_sink_rate_ppm():
    st = PlayoutStats()
    for i in range(61):
        st.port(float(i), 100.0 * i)
    assert st.snapshot()["sink_rate_ppm"] == pytest.approx(100.0)


def test_stats_empty_snapshot():
    snap = PlayoutStats().snapshot()
    assert snap["error_ms"] is None
    assert snap["sink_rate_ppm"] is None
    assert snap["jumps"] == 0
    assert snap["corrections_per_s"] == 0.0


def test_stats_sink_rate_survives_reset_window():
    st = PlayoutStats()
    for i in range(61):
        st.port(float(i), 100.0 * i)
    st.reset_window()
    assert st.snapshot()["sink_rate_ppm"] == pytest.approx(100.0)
