import pytest

from sendspin_bridge.services.audio.playout.controller import HAMPEL_FLOOR_US, LoopController


def run(ctrl, samples, t0=0.0):
    out = []
    for i, e in enumerate(samples):
        out.append(ctrl.update(t0 + 0.1 * i, e))
    return out


def test_measure_never_corrects_small_errors():
    d = run(LoopController(mode="measure"), [3000.0] * 50)
    assert all(x.rate == 0.0 and x.jump_us is None for x in d)
    assert d[-1].error_us == pytest.approx(3000.0, rel=0.05)


def test_single_outlier_is_rejected():
    d = run(LoopController(mode="measure"), [100.0] * 20 + [9000.0] + [100.0] * 5)
    assert d[20].rejected
    assert d[-1].error_us == pytest.approx(100.0, abs=20)


def test_persistent_step_over_the_threshold_jumps_once():
    ctrl = LoopController(mode="measure")
    d = run(ctrl, [0.0] * 20 + [25_000.0] * 10)
    jumps = [x.jump_us for x in d if x.jump_us is not None]
    assert jumps == [pytest.approx(25_000.0, rel=0.01)]


def test_second_steady_jump_within_10_s_is_refused_and_marks_degraded():
    ctrl = LoopController(mode="measure")
    run(ctrl, [0.0] * 20 + [25_000.0] * 10)
    ctrl.placed(3.0)
    d = run(ctrl, [-25_000.0] * 10, t0=3.1)
    assert all(x.jump_us is None for x in d) and d[-1].degraded


def test_hampel_floor_pins_the_gate_width():
    assert HAMPEL_FLOOR_US == 8000.0
    ok = run(LoopController(mode="measure"), [3000.0] * 20 + [10_000.0])
    assert not ok[-1].rejected
    bad = run(LoopController(mode="measure"), [3000.0] * 20 + [12_000.0])
    assert bad[-1].rejected


def test_negative_steady_error_jumps_negative_after_interval():
    ctrl = LoopController(mode="measure")
    ctrl.placed(0.0)
    d = run(ctrl, [-30_000.0] * 5, t0=10.0)
    assert d[-1].jump_us == pytest.approx(-30_000.0)


def test_step_of_five_outliers_is_accepted_and_reseeds():
    d = run(LoopController(mode="measure"), [0.0] * 20 + [15_000.0] * 8)
    assert [x.rejected for x in d[20:25]] == [True] * 4 + [False]
    assert d[-1].error_us > 5000.0
