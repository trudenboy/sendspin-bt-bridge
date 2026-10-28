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
