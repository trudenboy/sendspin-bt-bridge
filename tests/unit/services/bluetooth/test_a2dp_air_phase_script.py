"""``scripts/a2dp_air_phase.py`` on a real capture trimmed to 6 s, and its fold on synthetic clicks.

The fixture is rebuilt by ``tests/fixtures/a2dp_air/build_fixture.py`` from the stand capture
``hci_dcold2.btsnoop`` (66 s, hci1 handle 70 streaming the click track to an ENEBY20 over SBC).
"""

from __future__ import annotations

import importlib.util
import itertools
import subprocess
import sys
from pathlib import Path

import pytest

from sendspin_bridge.services.bluetooth import a2dp_air as air
from sendspin_sync import click_track as ct

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "a2dp_air_phase.py"
FIXTURE = ROOT / "tests" / "fixtures" / "a2dp_air" / "eneby_clicks.btsnoop.gz"

# What the script reports on the full 66 s capture (hci1 handle 70). The capture has no Connection
# Complete and no idle gap on that handle, so it has no trusted completion times either.
FULL_CAPTURE_PHASE_SEND_MS = 9829.8
TOLERANCE_MS = 3.0


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("a2dp_air_phase", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["a2dp_air_phase"] = module
    spec.loader.exec_module(module)
    return module


def circular_diff_ms(a: float, b: float) -> float:
    cycle = ct.CYCLE_S * 1000.0
    return abs((a - b + cycle / 2) % cycle - cycle / 2)


def test_fixture_is_small():
    assert FIXTURE.stat().st_size < 150_000


def test_fixture_media_packets_and_sbc_parameters():
    stats = air.ParseStats()
    parser = air.A2dpAirParser(stats=stats)
    packets = air.parse_records(air.read_btsnoop(FIXTURE, stats=stats), parser)
    assert {(p.index, p.handle, p.cid, p.codec) for p in packets} == {(1, 70, packets[0].cid, "sbc")}
    assert len(packets) == 295
    assert all(len(p.frames) == 7 for p in packets)
    frames = [f for p in packets for f in p.frames]
    assert {(f.sampling_rate, f.channel_mode, f.subbands, f.blocks, f.bitpool, f.length) for f in frames} == {
        (44100, air.ChannelMode.JOINT_STEREO, 8, 16, 53, 119)
    }
    # consecutive RTP sequence numbers, 7 x 128 samples apart
    assert all(b.seq == (a.seq + 1) & 0xFFFF for a, b in itertools.pairwise(packets))
    assert all(b.rtp_ts - a.rtp_ts == 896 for a, b in itertools.pairwise(packets))
    # the window starts mid-stream with no Connection Complete and no idle gap: nothing proves the
    # controller's queue empty, so no completion time is trusted (its first credits arrive as excess)
    assert stats.excess_completions > 0
    assert stats.sbc_errors == stats.dropped_malformed == 0
    assert all(p.t_complete is None for p in packets)


def test_default_link_is_the_busiest_tx_handle(tool):
    assert tool.busiest_tx_link(list(air.read_btsnoop(FIXTURE))) == (1, 70)


def test_phase_on_the_fixture_matches_the_full_capture(tool):
    (rep,), _stats = tool.analyze(FIXTURE)
    assert (rep.index, rep.handle, rep.packets, rep.frames) == (1, 70, 295, 2065)
    assert rep.nominal_hz == 44100
    assert rep.pacing_hz == pytest.approx(44100, rel=1e-3)
    assert circular_diff_ms(rep.phase_send_ms, FULL_CAPTURE_PHASE_SEND_MS) <= TOLERANCE_MS
    assert rep.completed == 0 and rep.phase_complete_ms is None  # no evidence of an empty controller queue


def test_cli_prints_the_report():
    out = subprocess.run(
        [sys.executable, str(SCRIPT), str(FIXTURE), "--index", "1", "--handle", "0x46"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "hci1 handle 70" in out
    assert "click phase at the air: send 98" in out


def test_cli_without_matching_traffic_fails(tool, capsys):
    assert tool.main([str(FIXTURE), "--index", "0"]) == 1


def test_sbc_encoder_delay(tool):
    # analysis window of 10*M taps, symmetric about (10M-1)/2 behind the newest sample of a block;
    # a block is placed at its oldest new sample, M-1 before the newest: (10M-1)/2 - (M-1) = 4M + 1/2
    assert tool.sbc_encoder_delay_samples(8) == 32.5
    assert tool.sbc_encoder_delay_samples(4) == 16.5


@pytest.mark.parametrize("phase_ms", [0.0, 1234.56, 5000.3, 9829.71, 3333.0])
def test_fold_returns_the_chirp_start(tool, phase_ms):
    """Per-frame energy shaped like the real proxy: the peak chirp amplitude the SBC analysis window sees.

    A frame placed at media sample m is computed from PCM delayed by the encoder (32.5 samples for
    8 sub-bands) and smeared by the 80-tap window (±40 samples); the proxy follows the peak amplitude."""
    rate, spf, delay, half_window = 44100, 128, 32.5, 40
    frame_s = spf / rate
    epoch = 1_700_000_000.0
    template = [abs(v) for v in ct.chirp(rate)]
    starts = []  # chirp starts in samples after *base*
    base = epoch - 1.0
    for k in range(13):
        t = epoch + ct.LEAD_IN_S + phase_ms / 1000.0 + k * ct.PERIOD_S
        for c in (t, t + ct.gap_ms(k) / 1000.0):
            starts.append((c - base) * rate)
    rows = []
    for n in range(round(28.0 / frame_s)):
        m = n * spf
        lo, hi = m - delay - half_window, m + spf - delay + half_window
        peak = 0.0
        for s0 in starts:
            if s0 + len(template) < lo or s0 > hi:
                continue
            i_lo, i_hi = max(0, int(lo - s0)), min(len(template), int(hi - s0) + 1)
            peak = max(peak, *template[i_lo:i_hi]) if i_hi > i_lo else peak
        rows.append((base + m / rate, 4.0 + 4096.0 * peak))
    got = tool.click_phase_ms(rows, frame_s, epoch_s=epoch, encoder_delay_s=delay / rate)
    assert got is not None
    assert circular_diff_ms(got, phase_ms) < 1.0


def test_fold_without_clicks_returns_none(tool):
    assert tool.click_phase_ms([(i * 0.01, 4.0) for i in range(5000)], 128 / 44100) is None
