"""B6a regression: reading noise must not look like a pulse step to the air bias.

Found by the closed-loop simulation: with sigma = 4 ms reading noise (PulseAudio 17) one
reading in ~125 differs from its predecessor by more than ``PA_STEP_US`` (15 ms), and every
such outlier broke the air bias' readings (reseed, frozen controller), delaying the seed past
10 s. A step now has to persist into the next reading before the bias hears of it.
"""

import pytest

from sendspin_bridge.services.audio.playout.air_bias import AirFeed
from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import InputFormat
from tests.unit.services.audio.playout.sim import FakeRaw, SimulatedPort, drive

FS = 48000


def engine(seed: int):
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw, buffer_ms=800.0, noise_us=4000.0, seed=seed)
    feed = AirFeed()
    feed.set_target(latency_ms=228.7, d_ref_ms=184.0)
    eng = PlayoutEngine(settings=PlayoutSettings(mode="loop"), port=port, air_feed=feed)
    eng.set_format(InputFormat(sample_rate=FS))
    eng.submit(int(raw.t_us + 1e6), b"\x10\x00\x10\x00" * FS * 150)
    breaks: list[tuple[str, float]] = []
    orig = eng._air_break

    def spy(kind, x_ns, t_s):
        breaks.append((kind, t_s))
        orig(kind, x_ns, t_s)

    eng._air_break = spy  # type: ignore[method-assign]
    return eng, port, raw, breaks


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_reading_noise_is_not_a_pa_step_for_the_bias(seed):
    eng, port, raw, breaks = engine(seed)
    drive(eng, port, raw, 120.0, step_ms=2.0)
    assert eng.snapshot()["pa_steps"] >= 1, "the harness must produce single-reading outliers"
    assert [b for b in breaks if b[0] == "pa_step"] == []


def test_a_real_pa_step_still_breaks_the_readings_once():
    eng, port, raw, breaks = engine(1)
    drive(eng, port, raw, 20.0, step_ms=2.0)
    t_skip = raw.t_us / 1e6
    port.skip(40.0)
    drive(eng, port, raw, 10.0, step_ms=2.0)
    steps = [b for b in breaks if b[0] == "pa_step" and b[1] >= t_skip]
    assert len(steps) == 1
    assert steps[0][1] - t_skip < 0.5  # dated at the first reading that saw it
