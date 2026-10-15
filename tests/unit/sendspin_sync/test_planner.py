import random

import pytest

from sendspin_sync import planner
from sendspin_sync.models import Measurement, Member


def mem(i, delay=0, adjustable=True, origin="bridge"):
    return Member(ma_player_id=i, name=i, adjustable=adjustable, origin=origin, delay_ms=delay)


def ok(i, phase):
    return Measurement(member=i, status="ok", phase_ms=phase, confidence=0.9, clicks=3, spread_ms=0.5)


def test_later_player_gets_more_delay_and_minimum_is_normalised_to_zero():
    p = planner.plan([ok("a", 100.0), ok("b", 130.0)], [mem("a", 50), mem("b", 50)])
    rows = {r.member: r for r in p.rows}
    assert p.reference == "a"
    assert (rows["a"].after_ms, rows["b"].after_ms) == (0, 30)  # 50->0 and 80->30 after normalising
    assert rows["b"].delta_ms == -20 and not rows["b"].clamped and p.unverified == ()


def test_lag_unwraps_across_the_cycle_edge():
    assert planner.unwrap_lag_ms(9800.0, 100.0) == pytest.approx(-300.0)
    assert planner.unwrap_lag_ms(100.0, 9800.0) == pytest.approx(300.0)
    p = planner.plan([ok("a", 100.0), ok("b", 9800.0)], [mem("a"), mem("b")])  # b is 300 ms ahead
    rows = {r.member: r for r in p.rows}
    assert (rows["a"].after_ms, rows["b"].after_ms) == (300, 0)


def test_clamping_is_marked_and_residual_predicted_with_warning():
    p = planner.plan([ok("a", 4900.0), ok("b", 0.0)], [mem("a", 300), mem("b", 0)])  # a is 4.9 s late
    rows = {r.member: r for r in p.rows}
    assert rows["a"].after_ms == 5000 and rows["a"].clamped
    assert p.predicted_spread_ms == pytest.approx(200.0)  # wanted 5200, got 5000
    assert any("2000" in w for w in p.warnings)


def test_not_adjustable_and_failed_members_are_left_out_and_listed():
    ms = [ok("a", 0.0), Measurement(member="b", status="not_heard"), ok("c", 20.0)]
    p = planner.plan(ms, [mem("a"), mem("b"), mem("c"), mem("cast", adjustable=False, origin="other")])
    assert {r.member for r in p.rows} == {"a", "c"}
    assert set(p.unverified) == {"b", "cast"}
    rows = {r.member: r for r in p.rows}
    assert (rows["a"].after_ms, rows["c"].after_ms) == (0, 20)  # c is 20 ms late; b unmeasured -> no normalisation


def test_no_normalisation_when_a_member_was_skipped():
    ms = [ok("a", 0.0), Measurement(member="b", status="skipped"), ok("c", 20.0)]
    p = planner.plan(ms, [mem("a", 100), mem("b", 100), mem("c", 100)])
    rows = {r.member: r for r in p.rows}
    assert (rows["a"].after_ms, rows["c"].after_ms) == (100, 120)  # c rises, a keeps 100; nothing shifted down


def test_reference_can_be_chosen_and_must_be_measured():
    p = planner.plan([ok("a", 0.0), ok("b", 10.0)], [mem("a"), mem("b")], reference="b")
    assert p.reference == "b"
    with pytest.raises(planner.PlanError):
        planner.plan([Measurement(member="a", status="not_heard")], [mem("a")])


def test_property_alignment_preserved_and_values_in_range():
    rnd = random.Random(7)
    for _ in range(200):
        n = rnd.randint(2, 6)
        members = [mem(f"m{i}", rnd.randint(0, 4000)) for i in range(n)]
        lags = [rnd.uniform(-2000, 2000) for _ in range(n)]
        ms = [ok(m.ma_player_id, (lag + 5000.0) % 10000.0) for m, lag in zip(members, lags, strict=True)]
        p = planner.plan(ms, members)
        rows = {r.member: r for r in p.rows}
        assert all(0 <= r.after_ms <= 5000 for r in p.rows)
        assert min(r.after_ms for r in p.rows) == 0 or any(r.clamped for r in p.rows)
        # alignment: lag_i - lag_j == (after_i - before_i) - (after_j - before_j) wherever nothing clamped
        if not any(r.clamped for r in p.rows):
            for i in range(n):
                for j in range(n):
                    di = rows[f"m{i}"].after_ms - members[i].delay_ms
                    dj = rows[f"m{j}"].after_ms - members[j].delay_ms
                    assert abs((lags[i] - lags[j]) - (di - dj)) <= 1.0
