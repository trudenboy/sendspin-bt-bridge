import threading

from sendspin_bridge.services.audio.playout.air_bias import FEED_MAX, AirFeed, AirSample, EHistory

ADAPTER = "C0:FB:F9:62:D7:D6"


def sample(i, *, gen=1, x0=10e9, h=-35_000.0, d_out=184_000.0, conf=1.0):
    return AirSample(gen, x0 + i * 1e9, x0 + i * 1e9 + 3e9, 1e6 * i, h, d_out, conf)


def test_take_drains_samples_in_order_and_keeps_the_latest_values():
    f = AirFeed()
    f.push(sample(0))
    f.push(sample(1))
    f.set_lock("locked", 0.9)
    f.set_target(latency_ms=228.7, d_ref_ms=184.0)
    f.set_key("sbc", 44100, ADAPTER)
    f.set_output_delay(184.0)
    v = f.take()
    assert [s.at_us for s in v.samples] == [0.0, 1e6]
    assert v.lock == ("locked", 0.9) and v.key == ("sbc", 44100, ADAPTER) and v.d_out_ms == 184.0
    assert (v.target.latency_ms, v.target.d_ref_ms, v.target.source) == (228.7, 184.0, "calibrated")
    again = f.take()
    assert again.samples == () and again.target.version == v.target.version


def test_target_sources():
    f = AirFeed()
    assert f.take().target.source is None
    f.set_target(latency_ms=None, learned_ms=226.0, learned_d_ref_ms=184.0, learned_starts=1)
    assert f.take().target.source is None  # active from the 3rd start: two samples needed
    f.set_target(latency_ms=None, learned_ms=226.0, learned_d_ref_ms=190.0, learned_starts=2)
    t = f.take().target
    assert (t.source, t.latency_ms, t.d_ref_ms) == ("learned", 226.0, 190.0)
    f.set_target(latency_ms=228.7, d_ref_ms=184.0, learned_ms=226.0, learned_d_ref_ms=190.0, learned_starts=2)
    t = f.take().target
    assert (t.source, t.latency_ms, t.d_ref_ms, t.learned_ms) == ("calibrated", 228.7, 184.0, 226.0)


def test_feed_is_bounded_drop_oldest():
    f = AirFeed()
    for i in range(FEED_MAX + 5):
        f.push(sample(i))
    got = f.take().samples
    assert len(got) == FEED_MAX and got[0].at_us == 5e6 and f.dropped == 5


def test_reopen_drops_queued_and_refuses_older_generations():
    f = AirFeed()
    f.push(sample(0, gen=1))
    f.reopen(min_tap_gen=2)
    f.push(sample(1, gen=1))  # computed on the old output, accepted after the reopen
    f.push(sample(2, gen=2))
    assert [s.at_us for s in f.take().samples] == [2e6]


def test_learned_seq_counts_per_feed():
    f = AirFeed()
    assert (f.next_learned_seq(), f.next_learned_seq()) == (1, 2)


def test_push_and_take_from_two_threads_smoke():
    """Smoke test only: no lost or reordered sample under contention (it cannot prove the absence of races)."""
    f = AirFeed()
    got: list[float] = []
    done = threading.Event()

    def consume():
        while not done.is_set():
            got.extend(s.at_us for s in f.take().samples)
        got.extend(s.at_us for s in f.take().samples)

    t = threading.Thread(target=consume)
    t.start()
    for i in range(10_000):
        f.push(sample(i))
    done.set()
    t.join()
    assert len(got) + f.dropped == 10_000 and got == sorted(got)


def test_history_median_over_a_span_uses_usable_readings_only():
    h = EHistory()
    for k in range(60):  # 6 s at 10 Hz
        h.add(1e6 + k * 1e5, k * 1e8, 1000.0 * (k % 3), usable=k != 20)
    med, n = h.median_over(0.0, 3e9)
    assert med == 1000.0 and n == 29


def test_history_below_half_coverage_is_none():
    h = EHistory()
    for k in range(14):  # 14 readings in a 3 s span: below 15
        h.add(1e6 + k * 1e5, k * 2e8, 500.0, usable=True)
    assert h.median_over(0.0, 3e9) is None


def test_history_forgets_readings_older_than_30_s():
    h = EHistory()
    for k in range(400):  # 40 s
        h.add(k * 1e5, k * 1e8, 100.0, usable=True)
    assert h.median_over(0.0, 3e9) is None and h.median_over(30e9, 33e9) is not None
