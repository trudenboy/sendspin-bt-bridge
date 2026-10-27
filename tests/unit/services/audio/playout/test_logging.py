import logging

from sendspin_bridge.services.audio.playout.engine import PlayoutEngine, PlayoutSettings
from sendspin_bridge.services.audio.playout.scheduler import InputFormat
from sendspin_bridge.services.audio.playout.stats import RateLimitedLog
from tests.unit.services.audio.playout.sim import FakeRaw, SimulatedPort, drive

ENGINE_LOGGER = "sendspin_bridge.services.audio.playout.engine"


def test_a_warning_kind_is_logged_once_per_ten_minutes(caplog):
    now = [0.0]
    rl = RateLimitedLog(logging.getLogger("t.rl"), clock=lambda: now[0])
    with caplog.at_level(logging.WARNING, logger="t.rl"):
        assert rl.warning("underruns", "first %d", 1)
        now[0] = 599.0
        assert not rl.warning("underruns", "second")
        assert rl.warning("other", "other kind")  # kinds are independent
        now[0] = 600.0
        assert rl.warning("underruns", "third")
    assert [r.getMessage() for r in caplog.records] == ["first 1", "other kind", "third"]


def make(monkeypatch, debug_env=None, **port_kw):
    if debug_env is None:
        monkeypatch.delenv("SENDSPIN_PLAYOUT_DEBUG", raising=False)
    else:
        monkeypatch.setenv("SENDSPIN_PLAYOUT_DEBUG", debug_env)
    raw = FakeRaw(1_000_000.0)
    port = SimulatedPort(raw, **port_kw)
    eng = PlayoutEngine(settings=PlayoutSettings(mode="measure"), port=port)
    eng.set_format(InputFormat(sample_rate=48000))
    return eng, port, raw


def summaries(caplog):
    return [r for r in caplog.records if r.levelno == logging.DEBUG and "summary" in r.getMessage()]


def test_a_jump_logs_info_with_size_and_reason(monkeypatch, caplog):
    eng, port, raw = make(monkeypatch)
    eng.submit(int(raw.t_us + 1e6), b"\x10\x00" * 2 * 48000 * 5)
    with caplog.at_level(logging.INFO, logger=ENGINE_LOGGER):
        drive(eng, port, raw, 3.0)
    jumps = [r for r in caplog.records if r.levelno == logging.INFO and "jump" in r.getMessage()]
    assert jumps
    msg = jumps[0].getMessage()
    assert "start" in msg and " ms" in msg


def test_debug_summary_every_ten_seconds(monkeypatch, caplog):
    eng, port, raw = make(monkeypatch)
    with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
        drive(eng, port, raw, 35.0)
    assert len(summaries(caplog)) == 3


def test_debug_summary_every_second_with_the_debug_env(monkeypatch, caplog):
    eng, port, raw = make(monkeypatch, debug_env="1")
    with caplog.at_level(logging.DEBUG, logger=ENGINE_LOGGER):
        drive(eng, port, raw, 10.5)
    assert 9 <= len(summaries(caplog)) <= 11


def test_repeated_underruns_warn_once_per_ten_minutes(monkeypatch, caplog):
    eng, port, raw = make(monkeypatch)
    eng.submit(int(raw.t_us + 1e6), b"\x10\x00" * 2 * 48000 * 5)
    drive(eng, port, raw, 2.0)
    with caplog.at_level(logging.WARNING, logger=ENGINE_LOGGER):
        for _ in range(6):
            eng._stats.underrun()
            eng._check_warnings(raw.t_us / 1e6)
    warns = [r for r in caplog.records if r.levelno == logging.WARNING and "underrun" in r.getMessage()]
    assert len(warns) == 1
