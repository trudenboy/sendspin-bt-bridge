from sendspin_bridge.services.audio.playout.sensor import CLOCK_TIME_NONE, GstPortClock


class FakeClock:
    def __init__(self, t):
        self.t = t

    def get_internal_time(self):
        return self.t


class FakeSink:
    def __init__(self, clock):
        self.clock = clock

    def provide_clock(self):
        return self.clock


def test_takes_the_first_tight_bracket():
    raws = iter([0, 800_000, 1_000_000, 1_100_000])
    sensor = GstPortClock(FakeSink(FakeClock(5_000_000)), raw_ns=lambda: next(raws), mono_ns=lambda: 7)
    sample = sensor.read()
    assert sample is not None
    assert sample.t_us == 1050.0 and sample.a_ns == 5_000_000 and sample.bracket_us == 100.0
    assert sample.mono_us == 0.007
    assert sensor.rejected == 1


def test_gives_up_after_three_wide_brackets():
    raws = iter([0, 800_000] * 3)
    sensor = GstPortClock(FakeSink(FakeClock(1)), raw_ns=lambda: next(raws), mono_ns=lambda: 7)
    assert sensor.read() is None
    assert sensor.rejected == 3


def test_no_clock_means_no_sample():
    assert GstPortClock(FakeSink(None)).read() is None


def test_gst_clock_time_none_is_no_sample():
    assert GstPortClock(FakeSink(FakeClock(CLOCK_TIME_NONE))).read() is None
