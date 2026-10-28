"""``scripts/air_envelope_corr.py``: legacy and implementation PSR on synthetic pairs and the click capture."""

from __future__ import annotations

import importlib.util
import math
import sys
import wave
from pathlib import Path

import pytest

from sendspin_bridge.services.audio.playout import air_correlator
from sendspin_sync import click_track as ct

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "scripts" / "air_envelope_corr.py"
FIX = ROOT / "tests" / "fixtures" / "a2dp_air"
CLICKS = FIX / "eneby_clicks.btsnoop.gz"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("air_envelope_corr", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["air_envelope_corr"] = module
    spec.loader.exec_module(module)
    return module


def write_wav(path: Path, pcm: bytes, rate: int, width: int = 2) -> Path:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(pcm)
    return path


@pytest.fixture(scope="module")
def click_wav(tmp_path_factory):
    pcm = ct.render_pcm(duration_s=14.0, sample_rate=44100)
    return write_wav(tmp_path_factory.mktemp("clicks") / "clicks.wav", pcm, 44100)


def test_legacy_definition_reproduces_the_scripts_ratio_on_a_synthetic_pair(tool):
    air, src = tool.synthetic_pair(lag_frames=7, seed=3)
    w = tool.legacy_windows(air, src, window=600, hop=300, search=20)
    confirmed = [x for x in w if x["psr"] >= 1.15]
    assert len(confirmed) >= 3
    assert all(abs(x["lag"] - 7) < 0.3 for x in confirmed)


def test_legacy_side_lobe_is_the_best_correlation_three_cells_from_the_peak(tool):
    # air = src shifted by 7 plus a second, weaker copy 12 cells later: the ratio is peak / that copy.
    air, src = tool.synthetic_pair(lag_frames=7, seed=5, echo_frames=12, echo_gain=0.5)
    w = tool.legacy_windows(air, src, window=600, hop=600, search=20)
    assert w and all(abs(x["lag"] - 7) < 0.3 for x in w)
    assert all(1.2 < x["psr"] < 3.0 for x in w)


def test_impl_definition_uses_the_daemon_correlator(tool, monkeypatch):
    called = []
    monkeypatch.setattr(
        air_correlator,
        "correlate_window",
        lambda *a, **k: called.append((a, k)) or air_correlator.WindowResult("rejected", None, 0.1, 1.0),
    )
    out = tool.impl_windows([1.0] * 1100, [1.0] * 1200, b"\x01" * 1200, window=1034, hop=344, search=20)
    assert called and out and out[0]["status"] == "rejected" and out[0]["psr"] == 1.0
    assert called[0][1]["margin"] == air_correlator.SIDE_SCAN


def test_impl_and_legacy_agree_on_lag_for_a_clean_pair(tool):
    air, src = tool.synthetic_pair(lag_frames=7, seed=3)
    program = bytes([1]) * len(src)
    impl = tool.impl_windows(air, src, program, window=600, hop=300, search=20)
    confirmed = [x for x in impl if x["status"] == "confirmed"]
    assert len(confirmed) >= 3 and all(abs(x["lag"] - 7) < 0.6 for x in confirmed)


def test_air_cells_follow_the_rtp_clock_and_leave_skips_empty(tool):
    from sendspin_bridge.services.bluetooth import a2dp_air as air

    packets = air.parse_records(air.read_btsnoop(CLICKS))
    cells, meta = tool.air_cells(packets, tool.impl_air_value)
    assert len(cells) == sum(len(p.frames) for p in packets) and meta.rate == 44100
    assert all(c is not None for c in cells)
    shifted = [p for p in packets[:50]] + [p for p in packets[60:110]]
    cells, _ = tool.air_cells(shifted, tool.impl_air_value)
    assert len(cells) == 100 * 7 + 10 * 7 and cells.count(None) == 10 * 7


def test_local_anchor_segments_at_skips(tool):
    # Two segments with different t_send - u offsets: the local anchor takes the cell's own segment only.
    pk = [
        tool.PacketTime(seg=0 if i < 20 else 1, u_s=i * 0.02, t_send=100.0 + i * 0.02 + (0 if i < 20 else 0.5))
        for i in range(40)
    ]
    assert tool.local_anchor(pk, 5) == pytest.approx(100.0)
    assert tool.local_anchor(pk, 30) == pytest.approx(100.5)


def test_cli_on_the_click_fixture_prints_both_definitions(capsys, tool, click_wav):
    rc = tool.main(
        [str(CLICKS), str(click_wav), "--definition", "both", "--window-frames", "600", "--hop-frames", "300"]
    )
    out = capsys.readouterr().out
    assert rc == 0 and "legacy" in out and "impl" in out and "psr" in out.lower()
    assert "summary" in out.lower()


def test_cli_writes_csv_with_offline_h(tmp_path, capsys, tool, click_wav):
    csv_path = tmp_path / "o.csv"
    rc = tool.main(
        [
            str(CLICKS),
            str(click_wav),
            "--window-frames",
            "600",
            "--hop-frames",
            "300",
            "--offline-h",
            "--csv",
            str(csv_path),
        ]
    )
    assert rc == 0
    lines = csv_path.read_text().splitlines()
    assert lines[0].startswith("definition,") and "offline_h_s" in lines[0] and len(lines) > 2


def test_cli_accepts_24_bit_wav_and_bandpass(tmp_path, capsys, tool):
    pcm16 = ct.render_pcm(duration_s=12.0, sample_rate=44100)
    pcm24 = b"".join(b"\x00" + pcm16[i : i + 2] for i in range(0, len(pcm16), 2))
    wav = write_wav(tmp_path / "c24.wav", pcm24, 44100, width=3)
    rc = tool.main(
        [
            str(CLICKS),
            str(wav),
            "--definition",
            "impl",
            "--envelope",
            "bandpass",
            "--window-frames",
            "600",
            "--hop-frames",
            "600",
        ]
    )
    assert rc == 0 and "impl" in capsys.readouterr().out


def test_selftest_passes(capsys, tool):
    assert tool.main(["--selftest"]) == 0
    assert "ok" in capsys.readouterr().out


def test_rejects_a_source_whose_rate_differs_from_the_capture(tmp_path, capsys, tool):
    wav = write_wav(tmp_path / "c48.wav", ct.render_pcm(duration_s=3.0, sample_rate=48000), 48000)
    assert tool.main([str(CLICKS), str(wav)]) == 2
    assert "rate" in capsys.readouterr().err.lower()


def test_percentile_helper(tool):
    assert tool.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 50) == 3.0
    assert math.isnan(tool.percentile([], 50))


def test_cli_finds_the_click_alignment_with_both_definitions(tmp_path, tool, click_wav):
    csv_path = tmp_path / "o.csv"
    assert (
        tool.main(
            [str(CLICKS), str(click_wav), "--window-frames", "600", "--hop-frames", "300", "--csv", str(csv_path)]
        )
        == 0
    )
    rows = [line.split(",") for line in csv_path.read_text().splitlines()[1:]]
    for definition in ("legacy", "impl"):
        lags = [float(r[6]) for r in rows if r[0] == definition and r[3] == "confirmed"]
        assert len(lags) >= 3 and max(lags) - min(lags) < 0.5  # one constant clock offset, no jumps


def test_impl_cells_span_the_sbc_look_back_like_the_daemon_tap(tool):
    """The offline written cells must be the daemon's: each spans its 128 frames and the 72 before."""
    frames = [0] * 640
    frames[3 * 128 - 60] = 4000
    data = [v for f in frames for v in (f, f)]
    cells = tool.impl_source_cells(data, 2, 44100, 2, "comb")
    assert cells[3] == pytest.approx(math.log2(4001), abs=1e-4) and cells[4] == 0.0


def test_offline_h_credits_the_comb_and_the_look_back(tool):
    """Pinned: ``offline_h = α + u_mid − src_mid − (d_enc − d_comb)`` with
    ``d_comb = (comb lag + look-back)/2/rate``; at M = 8 and 44.1 kHz that is +1.5 samples."""
    rate = 44100
    pk = tuple(tool.PacketTime(seg=0, u_s=k * 896 / rate, t_send=100.0 + k * 896 / rate) for k in range(10))
    meta = tool.CellMeta(rate=rate, subbands=8, cell0_ticks=0, packet_of=(0,) * 8, packets=pk)
    rows = [{"k": 0, "lag": 0.0}]
    tool.annotate(rows, definition="impl", offset=0, meta=meta, window=4, offline_h=True, comb=True)
    assert rows[0]["offline_h_s"] == pytest.approx(100.0 - (36 - (3 + 72) / 2) / rate, abs=1e-9)


def test_offline_h_on_the_click_capture_agrees_with_the_phase_tool(tmp_path, tool, click_wav):
    """The offline H of every confirmed window, folded onto the click cycle, lands within 1 ms of the
    phase tool's air phase (the clicks start at the WAV's LEAD_IN_S); a flipped look-back credit
    (1.6 ms) does not."""
    from scripts.a2dp_air_phase import analyze

    csv_path = tmp_path / "o.csv"
    args = [str(CLICKS), str(click_wav), "--definition", "impl", "--window-frames", "600", "--hop-frames", "300"]
    assert tool.main([*args, "--offline-h", "--csv", str(csv_path)]) == 0
    head, *rows = [line.split(",") for line in csv_path.read_text().splitlines()]
    col, status = head.index("offline_h_s"), head.index("status")
    phi = analyze(CLICKS, 1, 70)[0][0].phase_send_ms
    cycle = ct.CYCLE_S * 1000.0
    diffs = [
        (float(r[col]) * 1000.0 - phi + cycle / 2) % cycle - cycle / 2
        for r in rows
        if r[status] == "confirmed" and r[col]
    ]
    assert len(diffs) >= 3 and max(abs(d) for d in diffs) <= 1.0


def test_offline_look_back_follows_the_sub_bands(tool):
    """M = 4: the cells look back 36 frames and the credit is (3 + 36)/2 samples."""
    rate = 44100
    pk = tuple(tool.PacketTime(seg=0, u_s=k * 896 / rate, t_send=100.0 + k * 896 / rate) for k in range(10))
    meta = tool.CellMeta(rate=rate, subbands=4, cell0_ticks=0, packet_of=(0,) * 8, packets=pk)
    rows = [{"k": 0, "lag": 0.0}]
    tool.annotate(rows, definition="impl", offset=0, meta=meta, window=4, offline_h=True, comb=True)
    assert rows[0]["offline_h_s"] == pytest.approx(100.0 - (18 - (3 + 36) / 2) / rate, abs=1e-9)
    frames = [0] * 640
    frames[3 * 128 - 45] = 4000
    data = [v for f in frames for v in (f, f)]
    assert tool.impl_source_cells(data, 2, rate, 2, "comb", lookback=36)[3] == 0.0
    assert tool.impl_source_cells(data, 2, rate, 2, "comb", lookback=72)[3] > 0.0


# ── --anchor: the live sensor's air times, media vs count anchor (R-18) ─────────────────────


def _late_skip_stream():
    """On time, then sends falling 60 ms behind, a PA skip of that much media, sends staying late."""
    from dataclasses import replace

    from tests.unit.services.bluetooth.test_a2dp_air_envelope import PKT_S, packets

    late = [
        replace(p, t_send=10.0 + (80 + j) * PKT_S + 0.003 * (j + 1))
        for j, p in enumerate(packets(20, seq0=80, ts0=80 * 896))
    ]
    after = packets(120, seq0=100, ts0=100 * 896 + 2646, t0=10.0 + 100 * PKT_S + 0.060)
    return packets(80) + late + after


def test_count_anchor_times_are_the_trackers_batches_on_the_media_grid(tool):
    from sendspin_bridge.services.bluetooth.a2dp_air_envelope import AirLinkTracker

    pk = _late_skip_stream()
    times = tool.live_air_times(pk, "count")
    tr = AirLinkTracker()
    batches, next_take = [], pk[0].t_send + tool.BATCH_S
    for p in pk:
        tr.on_packet(p, timestamp="kernel", in_step=False)
        if p.t_send >= next_take:
            batches += tr.take_batches(p.t_send * 1e6)
            next_take += tool.BATCH_S
    batches += tr.take_batches(1e18, force=True)
    want = {
        (round(b.u0_us * 44100 / 1e6) + k * 128) // 128: (b.t0_us + k * b.frame_us) / 1e6
        for b in batches
        for k in range(len(b.e))
    }  # the cell index of air_cells: media ticks // 128
    assert times.keys() == want.keys() and all(times[i] == pytest.approx(want[i], abs=1e-9) for i in want)
    assert 699 in times and 720 in times and not any(i in times for i in range(700, 720))  # the skip holds no cell


def test_media_anchor_with_its_reseed_gives_the_count_anchor_times(tool):
    """The pre-R-18 media anchor reseeded by −Δu at every RTP step is the count anchor in another
    frame: median(t − u) shifted by the skips is median(t − count·frame_us) − skips. Pinned, so the
    offline comparison of the two is known to be like for like."""
    pk = _late_skip_stream()
    media, count = tool.live_air_times(pk, "media"), tool.live_air_times(pk, "count")
    assert media.keys() == count.keys()
    assert all(media[i] == pytest.approx(count[i], abs=1e-9) for i in count)


def test_anchor_h_series_and_its_stability(tool):
    rate = 44100
    f = 128 / rate
    times = {i: 100.0 + i * f for i in range(400)}
    rows = [
        {"k": 0, "lag": 0.0, "status": "confirmed"},
        {"k": 100, "lag": 0.0, "status": "confirmed"},
        {"k": 200, "lag": 1.0, "status": "confirmed"},
        {"k": 300, "lag": None, "status": "rejected"},
    ]
    tool.annotate_anchor_h(rows, times, offset=0, rate=rate, subbands=8, window=10, comb=True)
    credit = (36 - (3 + 72) / 2) / rate
    assert rows[0]["anchor_h_s"] == pytest.approx(100.0 - credit, abs=1e-9)
    assert rows[2]["anchor_h_s"] == pytest.approx(100.0 - f - credit, abs=1e-9)
    assert rows[3]["anchor_h_s"] is None
    st = tool.h_stability(rows)
    assert st["n"] == 3 and st["max_jump_ms"] == pytest.approx(f * 1000, abs=1e-6)
    assert st["median_ms"] == pytest.approx((100.0 - credit) * 1000, abs=1e-6)


def test_anchor_h_interpolates_air_time_across_a_skip_like_the_daemon(tool):
    rate = 44100
    f = 128 / rate
    times = {i: 100.0 + i * f for i in range(10)} | {i: 100.0 + (i - 10) * f for i in range(20, 40)}
    rows = [{"k": 10, "lag": 0.0, "status": "confirmed"}]  # middle at cell 15, in the gap 9 … 20
    tool.annotate_anchor_h(rows, times, offset=0, rate=rate, subbands=8, window=10, comb=False)
    t_mid = times[9] + (15 - 9) / (20 - 9) * (times[20] - times[9])
    assert rows[0]["anchor_h_s"] == pytest.approx(t_mid - 15 * f - 36 / rate, abs=1e-9)


def test_cli_anchor_prints_the_h_stability(capsys, tool, click_wav):
    rc = tool.main(
        [
            str(CLICKS),
            str(click_wav),
            "--definition",
            "impl",
            "--window-frames",
            "600",
            "--hop-frames",
            "300",
            "--anchor",
            "count",
        ]
    )
    out = capsys.readouterr().out
    assert rc == 0 and "anchor count" in out and "max jump" in out
