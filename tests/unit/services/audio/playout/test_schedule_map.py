import pytest

from sendspin_bridge.services.audio.playout.timeline import ScheduleMap


def test_linear_inside_a_segment():
    m = ScheduleMap()
    m.append(0, 1_000_000.0, "silence")
    assert m.at(0) == 1_000_000.0
    assert m.at(10_000_000) == pytest.approx(1_010_000.0)


def test_lookup_picks_the_segment_containing_x():
    m = ScheduleMap()
    m.append(0, 0.0, "silence")
    m.append(20_000_000, 50_000.0, "program")  # placement: program starts 30 ms later than silence would
    assert m.at(19_999_000) == pytest.approx(19_999.0)
    assert m.at(20_000_000) == 50_000.0
    assert m.kind_at(25_000_000) == "program"


def test_before_first_segment_is_unknown():
    m = ScheduleMap()
    assert m.at(0) is None
    m.append(1000, 0.0, "program")
    assert m.at(0) is None


def test_x_cannot_go_backwards():
    m = ScheduleMap()
    m.append(1000, 0.0, "program")
    with pytest.raises(ValueError):
        m.append(999, 0.0, "program")


def test_prune_keeps_the_containing_segment_and_bounds_memory():
    m = ScheduleMap(max_segments=8)
    for i in range(100):
        m.append(i * 1_000_000, float(i), "program")
    assert len(m) <= 8
    m.prune_before(95_500_000)
    assert m.at(95_500_000) is not None
