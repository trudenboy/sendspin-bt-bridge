"""Click-track fold helpers as a library: the phase must survive spans shorter than one 10 s cycle."""

import pytest

from sendspin_bridge.services.bluetooth.a2dp_air_fold import click_fold_found, click_phase_ms
from sendspin_sync import click_track as ct

FRAME_S = 128 / 44100


def click_rows(phase_ms, start_s, span_s, frame_s=FRAME_S):
    """Energy rows of an ideal click track whose click 0 chirp starts at phase_ms on the fold axis."""
    rows, t = [], start_s
    while t < start_s + span_s:
        rel = ((t - phase_ms / 1000.0 - ct.LEAD_IN_S) % ct.CYCLE_S) * 1000.0
        k, within = divmod(rel, ct.PERIOD_S * 1000.0)
        hot = within < ct.CHIRP_MS or ct.gap_ms(int(k)) <= within < ct.gap_ms(int(k)) + ct.CHIRP_MS
        rows.append((t, 1000.0 if hot else 1.0))
        t += frame_s
    return rows


@pytest.mark.parametrize("span_s", [5.0, 6.0, 7.0, 11.9])
@pytest.mark.parametrize("start_s", [0.3, 4.1, 7.7])
@pytest.mark.parametrize("phase_ms", [3210.0, 120.0, 9870.0])
def test_partial_cycle_spans_still_give_the_phase(span_s, start_s, phase_ms):
    rows = click_rows(phase_ms, start_s, span_s)
    assert click_fold_found(rows, FRAME_S, 0.0, 0.0)
    got = click_phase_ms(rows, FRAME_S)
    assert got is not None
    # rows are dated at the frame's first sample; the fold's centre sits half a frame later
    err = (got - (phase_ms + FRAME_S * 500) + 5000.0) % 10000.0 - 5000.0
    assert abs(err) <= 1.5


def test_no_clicks_is_not_found():
    rows = [(i * 0.0029, 1.0) for i in range(2000)]
    assert not click_fold_found(rows, 0.0029, 0.0, 0.0)


def test_script_still_exports_the_helpers():
    import scripts.a2dp_air_phase as tool

    assert tool.click_phase_ms is click_phase_ms


@pytest.mark.parametrize("subbands", [8, 4])
@pytest.mark.parametrize("lead", [0, 64, 127])
@pytest.mark.parametrize("span_s", [5.0, 6.0, 7.0])
def test_libsbc_click_track_cut_to_wizard_spans(subbands, lead, span_s):
    """The real encoder's frames, cut to 5-7 s at several offsets: found, phase within 1 ms of the truth."""
    import gzip
    from pathlib import Path

    from sendspin_bridge.services.bluetooth import a2dp_air as air
    from sendspin_bridge.services.bluetooth.a2dp_air_fold import frame_energy, sbc_encoder_delay_samples

    path = Path(__file__).resolve().parents[4] / "tests/fixtures/a2dp_air/libsbc" / f"m{subbands}_lead{lead}.sbc.gz"
    frames, complete = air.parse_sbc_frames(gzip.decompress(path.read_bytes()))
    assert complete
    frame_s = frames[0].samples / frames[0].sampling_rate
    rows = [(i * frame_s, frame_energy(f)) for i, f in enumerate(frames)]
    delay_s = sbc_encoder_delay_samples(subbands) / frames[0].sampling_rate
    n, checked = int(span_s / frame_s), 0
    for offset_s in (0.0, 1.3, 2.9, 4.4):
        a = int(offset_s / frame_s)
        cut = rows[a : a + n]
        assert len(cut) == n
        assert click_fold_found(cut, frame_s, 0.0, delay_s)
        got = click_phase_ms(cut, frame_s, 0.0, delay_s)
        assert got is not None
        err = (got - lead / 44.1 + 5000.0) % 10000.0 - 5000.0
        assert abs(err) < 1.0, (offset_s, err)
        checked += 1
    assert checked == 4
