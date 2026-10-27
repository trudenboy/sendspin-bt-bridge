from sendspin_bridge.services.audio.playout.sensor import PortSample
from sendspin_bridge.services.audio.playout.timeline import PortObserver


def s(t_ms, a_ms):
    return PortSample(t_us=t_ms * 1000.0, a_ns=int(a_ms * 1e6), mono_us=t_ms * 1000.0, bracket_us=20.0)


def test_port_position_is_audio_clock_minus_origin():
    obs = PortObserver(k0_ns=2_000_000)
    r = obs.observe(s(1000, 500), delivered_ns=1_100_000_000)
    assert r.x_port_ns == 498_000_000
    assert r.fill_ns == 1_100_000_000 + 2_000_000 - 500_000_000


def test_underrun_moves_the_origin_by_the_deficit_once():
    obs = PortObserver()
    obs.observe(s(0, 0), delivered_ns=600_000_000)
    r = obs.observe(s(1000, 1000), delivered_ns=900_000_000)  # port passed our data by 100 ms
    assert r.underrun and r.x_port_ns == 900_000_000
    assert obs.k_ns == 100_000_000
    r2 = obs.observe(s(1100, 1100), delivered_ns=1_400_000_000)
    assert not r2.underrun and r2.recovered
    assert r2.x_port_ns == 1_000_000_000


def test_pa_skip_moves_the_port_forward_without_touching_the_origin():
    obs = PortObserver()
    obs.observe(s(0, 0), delivered_ns=600_000_000)
    r = obs.observe(s(100, 105), delivered_ns=700_000_000)  # 5 ms of our frames dropped by PA
    assert obs.k_ns == 0 and r.x_port_ns == 105_000_000 and not r.underrun


def test_stall_is_flagged_after_300_ms_without_progress():
    obs = PortObserver()
    obs.observe(s(0, 0), delivered_ns=600_000_000)
    assert not obs.observe(s(200, 0), delivered_ns=600_000_000).stalled
    assert obs.observe(s(350, 0), delivered_ns=600_000_000).stalled
    assert obs.observe(s(450, 100), delivered_ns=600_000_000).recovered


def test_a_continuing_underrun_is_one_episode():
    obs = PortObserver()
    obs.observe(s(0, 0), delivered_ns=100_000_000)
    first = obs.observe(s(200, 200), delivered_ns=150_000_000)
    second = obs.observe(s(300, 300), delivered_ns=150_000_000)
    assert first.underrun and not second.underrun and not second.recovered
    assert obs.k_ns == 150_000_000
