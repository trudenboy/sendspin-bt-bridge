import statistics

import pytest

from sendspin_bridge.services.audio.playout.air_learned import learned_reference

STAGE_A_H = [-45.2, -32.4, -31.4, -44.5, -44.9, -36.3, -44.7, -36.4]


def test_robust_mean_of_the_raw_placements():
    xs = [184.0 - h for h in STAGE_A_H]
    latency, d_ref = learned_reference(xs, [184.0] * 8)
    assert d_ref == 184.0 and latency == pytest.approx(184.0 - statistics.mean(STAGE_A_H), abs=1e-9)


def test_an_outlier_start_is_left_out():
    xs = [184.0 - h for h in [-40.0, -41.0, -39.0, -60.0]]  # -60 is 20 ms from the median
    latency, _ = learned_reference(xs, [184.0] * 4)
    assert latency == pytest.approx(184.0 + 40.0, abs=1e-9)


def test_the_delay_of_a_sample_does_not_move_the_target():
    h = [-40.0, -41.0, -39.0]
    a = learned_reference([184.0 - x for x in h], [184.0] * 3)
    # each sample carries the delay it was taken at (X = d - h)
    b = learned_reference([214.0 - h[0], 214.0 - h[1], 184.0 - h[2]], [214.0, 214.0, 184.0])
    assert a[0] - a[1] == pytest.approx(b[0] - b[1])


@pytest.mark.parametrize(("xs", "ds"), [([], []), ([1.0], [1.0, 2.0]), ([1500.0], [184.0])])
def test_invalid_input_raises(xs, ds):
    with pytest.raises(ValueError):
        learned_reference(xs, ds)


def test_stage_a_sequence_moves_less_with_the_robust_mean_than_with_the_median():
    used_mean = [learned_reference([184.0 - h for h in STAGE_A_H[:k]], [184.0] * k)[0] for k in range(3, 8)]
    used_median = [184.0 - statistics.median(STAGE_A_H[:k]) for k in range(3, 8)]
    assert statistics.stdev(used_mean) == pytest.approx(1.44, abs=0.05)
    assert statistics.stdev(used_median) == pytest.approx(5.02, abs=0.05)
