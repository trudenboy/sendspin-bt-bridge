"""``scripts/air_sensor_bench.py``: one pass over the fixture yields the live-path batches and a cost line."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "air_sensor_bench.py"
FIXTURE = ROOT / "tests" / "fixtures" / "a2dp_air" / "eneby_clicks.btsnoop.gz"


@pytest.fixture(scope="module")
def bench():
    spec = importlib.util.spec_from_file_location("air_sensor_bench", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["air_sensor_bench"] = module
    spec.loader.exec_module(module)
    return module


def test_one_pass_produces_the_link_batches(bench):
    records = bench.load(FIXTURE)
    frames = bench.run_once(records, check_crc="first")
    assert frames == {(1, 70): 2065}  # every SBC frame of the click link reaches a batch


def test_cli_prints_cost_per_record_and_core_fraction(bench, capsys):
    assert bench.main([str(FIXTURE), "--repeat", "1"]) == 0
    out = capsys.readouterr().out
    assert "us/record" in out and "core fraction at 150 records/s" in out


def test_hub_mode_replays_the_capture_through_the_monitor_hub(bench, capsys):
    assert bench.main([str(FIXTURE), "--repeat", "1", "--hub"]) == 0
    out = capsys.readouterr().out
    assert "hub path:" in out and "hub's own share" in out


def test_hub_replay_feeds_every_frame_to_the_air_service(bench):
    cpu_s, frames = bench.run_hub_once(bench.load(FIXTURE))
    assert cpu_s > 0
    assert frames == {(1, 70): 2065}  # the same frames as the direct pass: nothing lost in the hub


def test_hub_replay_follows_a_connection_complete_in_the_capture(bench):
    capture = FIXTURE.parent / "air_music.btsnoop.gz"  # starts with the link's Connection Complete
    _cpu_s, frames = bench.run_hub_once(bench.load(capture))
    # the direct pass's 6363 less the last packet, which waits for a drain the capture ends before
    # (the live path, like the hub replay, never flushes the parser)
    assert frames == {(0, 42): 6356}


@pytest.mark.parametrize(
    ("capture", "golden"),
    [
        ("eneby_clicks", (18, "c8a760b37c8d8b79")),
        ("air_music", (34, "5f9f23471a1d5db5")),
    ],
)
def test_hub_replay_batches_match_the_recorded_output(bench, monkeypatch, capture, golden):
    """The hub + service path gives the batches it gave before the hub's R-19 changes (recorded
    at 739d19cf): routing, the clock-map fast path and the bookkeeping changed nothing."""
    import hashlib

    from sendspin_bridge.services.bluetooth import air_sensor_service

    trackers = []

    class Recording(air_sensor_service.AirLinkTracker):
        def __init__(self, **kw):
            super().__init__(**kw)
            trackers.append(self)

    monkeypatch.setattr(air_sensor_service, "AirLinkTracker", Recording)
    bench.run_hub_once(bench.load(FIXTURE.parent / f"{capture}.btsnoop.gz"))
    lines = []
    for tr in trackers:
        lines += [repr(b) for b in tr.take_batches(float("inf"), force=True)]
        lines.append(repr(tr.state(1e18)))
    assert (len(lines), hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]) == golden
