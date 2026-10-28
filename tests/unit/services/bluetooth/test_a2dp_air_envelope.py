"""Air envelope: SBC band and energy, send anchor, anchor segments, epochs and batches.

Packets are built directly as :class:`MediaPacket` with RAW-second send times (the hub feeds the
parser RAW seconds); the click-track fixture is folded through the tracker and compared with the
offline phase tool.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from sendspin_bridge.services.bluetooth.a2dp_air import (
    A2dpAirParser,
    ChannelMode,
    MediaPacket,
    SbcFrame,
    parse_records,
    parse_sbc_frames,
    read_btsnoop,
)
from sendspin_bridge.services.bluetooth.a2dp_air_envelope import (
    MISSING,
    AirLinkTracker,
    SendAnchor,
    envelope_subbands,
    frame_envelope,
)

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "a2dp_air"
RATE = 44100
PKT_S = 7 * 128 / RATE

# A real SBC frame (44.1 kHz, 16 blocks, joint stereo, 8 sub-bands, bitpool 53, 119 bytes); byte 3 is its CRC.
REAL_SBC_FRAME = bytes.fromhex(
    "9cbd357d0000000000000000007776db6eedb6dbbbb6db874dc69dddb6dbbb6db6eeedb6dddb6db7776db6f0e26dbbbb6db776db6e15e27"
    "1bbb6db6eeedb6dddb6db7776db6eedb6dbbbb6db776db6ddddb6db426d46eeedb6dddb6db7776db6eedb6dc4324dc776db6de1a26dbbb6d"
    "b6eeedb6dddb6db"
)


def bad_crc(frame: bytes) -> bytes:
    return frame[:3] + bytes([frame[3] ^ 0xFF]) + frame[4:]


# ── band, energy, parser CRC option ────────────────────────────────────────


@pytest.mark.parametrize(
    ("rate", "m", "bands"),
    [(44100, 8, (1, 2)), (48000, 8, (1, 2)), (44100, 4, (1,)), (48000, 4, (1,)), (32000, 8, (1, 2, 3))],
)
def test_band_selection(rate, m, bands):
    assert envelope_subbands(rate, m) == bands


def sbc(sf, m=8):
    return SbcFrame(RATE, 16, m, ChannelMode.JOINT_STEREO, False, 53, 119, (False,) * m, (tuple(sf), tuple(sf)))


def test_frame_envelope_is_log_peak_in_eighth_bits():
    assert frame_envelope(sbc([0, 4, 4, 0, 0, 0, 0, 0]), (1, 2)) == 48  # 8·log2(1 + 4·16)
    assert frame_envelope(sbc([15] * 8), (1, 2)) <= 254


def test_frame_envelope_never_reaches_the_missing_marker():
    many = SbcFrame(RATE, 16, 8, ChannelMode.STEREO, False, 53, 119, (), ((15,) * 8, (15,) * 8))
    assert 0 <= frame_envelope(many, tuple(range(8))) <= 254 < MISSING


def test_first_frame_crc_mode_checks_only_the_first_frame():
    two_bad_second = REAL_SBC_FRAME + bad_crc(REAL_SBC_FRAME)
    frames, complete = parse_sbc_frames(two_bad_second)
    assert (len(frames), complete) == (1, False)
    frames, complete = parse_sbc_frames(two_bad_second, check_crc="first")
    assert (len(frames), complete) == (2, True)
    frames, complete = parse_sbc_frames(bad_crc(REAL_SBC_FRAME) + REAL_SBC_FRAME, check_crc="first")
    assert (len(frames), complete) == (0, False)


def test_parser_first_frame_crc_mode_parses_the_fixture_identically():
    recs = list(read_btsnoop(FIX / "eneby_clicks.btsnoop.gz"))
    full = parse_records(recs, A2dpAirParser())
    first = parse_records(recs, A2dpAirParser(check_crc="first"))
    assert [(p.seq, len(p.frames)) for p in full] == [(p.seq, len(p.frames)) for p in first]


# ── send anchor ────────────────────────────────────────────────────────────


def test_send_anchor_is_a_median_robust_to_credit_jitter():
    a = SendAnchor()
    for k in range(64):
        a.add(1_000_000.0 + k * 20_000 + (9000.0 if k % 5 == 0 else 0.0), k * 20_000.0)
    assert a.value() == pytest.approx(1_000_000.0)


def test_send_anchor_follows_slew_with_small_lag():
    a = SendAnchor()
    for k in range(400):
        a.add(k * 20_000 * 1.0005, k * 20_000.0)
    assert a.value() == pytest.approx(399 * 20_000 * 0.0005, abs=330.0)


def test_send_anchor_reset_empties_it():
    a = SendAnchor()
    assert a.value() is None
    for k in range(64):
        a.add(500.0 + k * 10 + (300.0 if k % 4 == 0 else 0.0), k * 10.0)
    assert a.value() == pytest.approx(500.0)
    a.add(10_000.0 + 500.0 + 300.0, 10_000.0)  # one jittered sample does not move the median
    assert a.value() == pytest.approx(500.0)
    a.reset()
    assert a.value() is None


# ── tracker ────────────────────────────────────────────────────────────────


def packets(n, *, seq0=0, ts0=0, t0=10.0, ssrc=1, frames=7):
    out = []
    for k in range(n):
        fr = tuple(sbc([0, 3 + (k % 4), 2, 0, 0, 0, 0, 0]) for _ in range(frames))
        out.append(
            MediaPacket(
                t_send=t0 + k * PKT_S,
                t_complete=None,
                index=1,
                handle=70,
                cid=0x41,
                seq=(seq0 + k) & 0xFFFF,
                rtp_ts=(ts0 + k * frames * 128) & 0xFFFFFFFF,
                ssrc=ssrc,
                payload_type=96,
                codec="sbc",
                frames=fr,
                sbc_frame_count=frames,
            )
        )
    return out


def feed(tr, pk, *, timestamp="kernel", clock_epoch=None):
    for p in pk:
        tr.on_packet(p, timestamp=timestamp, in_step=False, clock_epoch=clock_epoch)


def frame_us():
    return 128 / RATE * 1e6


def test_batches_are_contiguous_and_bounded():
    tr = AirLinkTracker()
    feed(tr, packets(60))
    b = tr.take_batches(1e12, force=True)
    assert all(len(x.e) <= 128 for x in b) and sum(len(x.e) for x in b) == 420
    assert [x.seq for x in b] == [i * 128 for i in range(len(b))]
    assert b[1].t0_us == pytest.approx(b[0].t0_us + 128 * b[0].frame_us, abs=1.0)


def test_batch_fields_describe_the_stream():
    tr = AirLinkTracker()
    feed(tr, packets(40))
    b = tr.take_batches(1e12, force=True)
    x = b[0]
    assert (x.epoch, x.seq, x.u0_us) == (0, 0, 0.0)
    assert (x.rate, x.subbands, x.blocks, x.bands, x.timestamp) == (RATE, 8, 16, (1, 2), "kernel")
    assert x.frame_us == pytest.approx(frame_us())
    assert x.t0_us == pytest.approx(10.0 * 1e6, abs=1.0)  # u + alpha: the first frame left at its send time
    assert x.complete_lag_us is None and x.anchor_reset is False
    assert x.e[:2] == (frame_envelope(sbc([0, 3, 2, 0, 0, 0, 0, 0]), (1, 2)),) * 2


def test_take_batches_empties_the_queue():
    tr = AirLinkTracker()
    feed(tr, packets(40))
    assert tr.take_batches(1e12, force=True)
    assert tr.take_batches(1e12, force=True) == []


def test_partial_batch_waits_for_250ms_unless_forced():
    tr = AirLinkTracker()
    feed(tr, packets(40))  # 280 frames: two full batches and 24 left over
    last_send_us = (10.0 + 39 * PKT_S) * 1e6
    b = tr.take_batches(last_send_us)
    assert [len(x.e) for x in b] == [128, 128]
    assert tr.take_batches(last_send_us) == []
    first_pending_us = (10.0 + 256 * 128 / RATE) * 1e6  # air time of frame 256 (u + alpha)
    assert tr.take_batches(first_pending_us + 200_000.0) == []
    late = tr.take_batches(first_pending_us + 260_000.0)
    assert [(x.seq, len(x.e)) for x in late] == [(256, 24)]


def test_skip_is_an_anchor_reset_in_the_same_epoch():
    """PA bluez5 'Skipping': contiguous sequence, RTP timestamp jumps, send times continue."""
    tr = AirLinkTracker()
    first = packets(20)
    skipped = 882  # 20 ms of samples never sent
    later = packets(20, seq0=20, ts0=20 * 7 * 128 + skipped, t0=10.0 + 20 * PKT_S)
    feed(tr, first + later)
    b = tr.take_batches(1e12, force=True)
    assert {x.epoch for x in b} == {0}
    assert [x.anchor_reset for x in b].count(True) == 1
    reset = next(x for x in b if x.anchor_reset)
    assert reset.seq == 140  # sent-frame counter continues
    assert reset.u0_us == pytest.approx((140 * 128 + skipped) / RATE * 1e6)  # media time includes the skip


def test_anchor_is_carried_across_a_skip():
    tr = AirLinkTracker()
    skipped = 882
    feed(tr, packets(80) + packets(3, seq0=80, ts0=80 * 7 * 128 + skipped, t0=10.0 + 80 * PKT_S))
    b = tr.take_batches(1e12, force=True)
    reset = next(x for x in b if x.anchor_reset)
    # the frame right after the skip left at its send time, not 'send time + skip'
    assert reset.t0_us == pytest.approx((10.0 + 80 * PKT_S) * 1e6, abs=200.0)


def test_media_time_never_runs_behind_the_sent_frame_count():
    """The daemon's validation: u0_us ≥ seq · frame_us − 1 (skips only add media time)."""
    tr = AirLinkTracker()
    feed(tr, packets(30) + packets(30, seq0=30, ts0=30 * 896 + 441, t0=10.0 + 30 * PKT_S))
    feed(tr, packets(30, seq0=60, ts0=60 * 896 + 441 + 2000, t0=10.0 + 60 * PKT_S))
    b = tr.take_batches(1e12, force=True)
    assert [x.anchor_reset for x in b].count(True) == 2
    assert all(x.u0_us >= x.seq * x.frame_us - 1 for x in b)
    assert tr.state(1e12).anchor_segments == 3


def test_no_batch_before_32_packets_of_an_epoch():
    tr = AirLinkTracker()
    feed(tr, packets(31))
    assert tr.take_batches(1e12, force=True) == []
    feed(tr, packets(1, seq0=31, ts0=31 * 7 * 128, t0=10.0 + 31 * PKT_S))
    assert tr.take_batches(1e12, force=True)


def test_an_epoch_shorter_than_32_packets_sends_nothing():
    tr = AirLinkTracker()
    feed(tr, packets(10))
    tr.on_discontinuity()
    feed(tr, packets(40, seq0=10, ts0=10 * 896, t0=10.0 + 10 * PKT_S))
    b = tr.take_batches(1e12, force=True)
    assert {x.epoch for x in b} == {1}
    assert b[0].seq == 0 and b[0].u0_us == 0.0


def test_sequence_gap_is_a_new_epoch_without_any_time_gap():
    tr = AirLinkTracker()
    feed(tr, packets(40) + packets(40, seq0=41, ts0=41 * 7 * 128, t0=10.0 + 41 * PKT_S))  # seq 40 lost
    assert {x.epoch for x in tr.take_batches(1e12, force=True)} == {0, 1}


def test_new_epoch_restarts_frame_index_and_media_time():
    tr = AirLinkTracker()
    feed(tr, packets(40) + packets(40, seq0=41, ts0=41 * 7 * 128, t0=10.0 + 41 * PKT_S))
    second = [x for x in tr.take_batches(1e12, force=True) if x.epoch == 1]
    assert (second[0].seq, second[0].u0_us, second[0].anchor_reset) == (0, 0.0, False)
    assert second[0].t0_us == pytest.approx((10.0 + 41 * PKT_S) * 1e6, abs=1.0)


def test_send_gap_over_1s_is_a_new_epoch_with_contiguous_rtp():
    tr = AirLinkTracker()
    feed(tr, packets(40) + packets(40, seq0=40, ts0=40 * 7 * 128, t0=10.0 + 40 * PKT_S + 1.2))
    assert {x.epoch for x in tr.take_batches(1e12, force=True)} == {0, 1}


def test_rtp_restart_without_any_send_gap_is_a_new_epoch():
    """AVDTP restart: new sequence and timestamp origin; the sends continue at the normal pace."""
    tr = AirLinkTracker()
    feed(tr, packets(40, seq0=5000, ts0=900_000) + packets(40, seq0=0, ts0=0, t0=10.0 + 40 * PKT_S))
    assert {x.epoch for x in tr.take_batches(1e12, force=True)} == {0, 1}


def test_timestamp_going_back_with_contiguous_sequence_is_a_new_epoch():
    tr = AirLinkTracker()
    feed(tr, packets(40, ts0=900_000) + packets(40, seq0=40, ts0=0, t0=10.0 + 40 * PKT_S))
    b = tr.take_batches(1e12, force=True)
    assert {x.epoch for x in b} == {0, 1}
    assert not any(x.anchor_reset for x in b)


def test_ssrc_change_is_a_new_epoch():
    tr = AirLinkTracker()
    feed(tr, packets(40) + packets(40, seq0=40, ts0=40 * 896, t0=10.0 + 40 * PKT_S, ssrc=2))
    assert {x.epoch for x in tr.take_batches(1e12, force=True)} == {0, 1}


def test_rtp_timestamp_wraps_inside_an_epoch():
    tr = AirLinkTracker()
    ts0 = 0x100000000 - 10 * 896
    feed(tr, packets(40, ts0=ts0))
    b = tr.take_batches(1e12, force=True)
    assert {x.epoch for x in b} == {0} and not any(x.anchor_reset for x in b)
    assert sum(len(x.e) for x in b) == 280


def test_discontinuity_from_the_hub_is_a_new_epoch():
    tr = AirLinkTracker()
    feed(tr, packets(40))
    tr.on_discontinuity()
    feed(tr, packets(40, seq0=40, ts0=40 * 7 * 128, t0=10.0 + 40 * PKT_S))
    assert {x.epoch for x in tr.take_batches(1e12, force=True)} == {0, 1}


def test_link_reset_is_a_new_epoch():
    tr = AirLinkTracker()
    feed(tr, packets(40))
    tr.on_link_reset()
    feed(tr, packets(40, seq0=40, ts0=40 * 7 * 128, t0=10.0 + 40 * PKT_S))
    assert {x.epoch for x in tr.take_batches(1e12, force=True)} == {0, 1}


def test_recv_stamped_packet_inside_a_kernel_stream_is_a_new_epoch():
    tr = AirLinkTracker()
    feed(tr, packets(40))
    feed(tr, packets(40, seq0=40, ts0=40 * 896, t0=10.0 + 40 * PKT_S), timestamp="recv")
    b = tr.take_batches(1e12, force=True)
    assert {(x.epoch, x.timestamp) for x in b} == {(0, "kernel"), (1, "recv")}


def test_clock_epoch_change_across_a_step_is_a_new_epoch_not_an_anchor_reset():
    """A hub clock step lands on the same packet as a PA skip: the epoch rule wins."""
    tr = AirLinkTracker()
    feed(tr, packets(40), clock_epoch=3)
    skipped = 882
    feed(tr, packets(40, seq0=40, ts0=40 * 896 + skipped, t0=10.0 + 40 * PKT_S), clock_epoch=4)
    b = tr.take_batches(1e12, force=True)
    assert {x.epoch for x in b} == {0, 1}
    assert not any(x.anchor_reset for x in b)
    second = [x for x in b if x.epoch == 1]
    assert (second[0].seq, second[0].u0_us) == (0, 0.0)
    assert second[0].t0_us == pytest.approx((10.0 + 40 * PKT_S) * 1e6, abs=1.0)
    # a later skip inside the new clock epoch is an anchor reset again
    feed(tr, packets(10, seq0=80, ts0=80 * 896 + skipped * 2, t0=10.0 + 80 * PKT_S), clock_epoch=4)
    later = tr.take_batches(1e12, force=True)
    assert {x.epoch for x in later} == {1} and [x.anchor_reset for x in later].count(True) == 1


def test_sbc_parameter_change_is_a_new_epoch():
    tr = AirLinkTracker()
    other = [
        replace(p, frames=tuple(replace(f, sampling_rate=48000) for f in p.frames), t_send=p.t_send + 40 * PKT_S)
        for p in packets(40, seq0=40, ts0=40 * 896)
    ]
    feed(tr, packets(40) + other)
    b = tr.take_batches(1e12, force=True)
    assert {(x.epoch, x.rate) for x in b} == {(0, 44100), (1, 48000)}


def test_unparsable_frames_inside_a_contiguous_run_are_missing():
    tr = AirLinkTracker()
    pk = packets(40)
    pk[10] = replace(pk[10], frames=pk[10].frames[:4])  # three frames failed to parse
    pk[20] = replace(pk[20], frames=())  # the whole payload failed
    feed(tr, pk)
    b = tr.take_batches(1e12, force=True)
    e = [v for x in b for v in x.e]
    assert len(e) == 280 and {x.epoch for x in b} == {0} and not any(x.anchor_reset for x in b)
    assert e[74:77] == [MISSING] * 3 and e[140:147] == [MISSING] * 7
    assert e[73] != MISSING and e[147] != MISSING


def test_media_packet_carries_the_sbc_header_frame_count():
    recs = list(read_btsnoop(FIX / "eneby_clicks.btsnoop.gz"))
    pk = [p for p in parse_records(recs, A2dpAirParser()) if p.codec == "sbc" and not p.sbc_fragmented]
    assert pk and all(p.sbc_frame_count == len(p.frames) for p in pk)


def test_header_count_above_parsed_frames_is_missing_not_a_skip():
    tr = AirLinkTracker()
    pk = packets(40)
    pk[10] = replace(pk[10], frames=pk[10].frames[:6])  # header says 7, one frame failed
    feed(tr, pk)
    b = tr.take_batches(1e12, force=True)
    e = [v for x in b for v in x.e]
    assert len(e) == 280 and e[76] == MISSING and e[75] != MISSING
    assert not any(x.anchor_reset for x in b)


def test_whole_packet_skip_after_full_packets_is_an_anchor_reset():
    tr = AirLinkTracker()
    feed(tr, packets(40) + packets(40, seq0=40, ts0=41 * 896, t0=10.0 + 40 * PKT_S))  # one packet of samples skipped
    b = tr.take_batches(1e12, force=True)
    assert MISSING not in [v for x in b for v in x.e]
    reset = [x for x in b if x.anchor_reset]
    assert len(reset) == 1 and reset[0].seq == 280
    assert reset[0].t0_us == pytest.approx((10.0 + 40 * PKT_S) * 1e6, abs=200.0)


def test_whole_packet_skip_after_a_frames_per_packet_decrease_is_an_anchor_reset():
    """Bitpool changes shrink packets without a new epoch; a later skip must not read as missing frames."""
    tr = AirLinkTracker()
    eight = packets(20, frames=8)
    seven = packets(20, seq0=20, ts0=20 * 8 * 128, t0=10.0 + 20 * PKT_S, frames=7)
    skip = 896
    after = packets(20, seq0=40, ts0=20 * 8 * 128 + 20 * 896 + skip, t0=10.0 + 40 * PKT_S, frames=7)
    feed(tr, eight + seven + after)
    b = tr.take_batches(1e12, force=True)
    assert MISSING not in [v for x in b for v in x.e]
    assert [x.anchor_reset for x in b].count(True) == 1
    reset = next(x for x in b if x.anchor_reset)
    assert reset.seq == 300 and reset.u0_us == pytest.approx((300 * 128 + skip) / RATE * 1e6)


def test_sub_frame_step_reseeds_the_anchor_without_a_reset_flag():
    tr = AirLinkTracker()
    step = 100  # samples, less than one frame
    feed(tr, packets(40) + packets(40, seq0=40, ts0=40 * 896 + step, t0=10.0 + 40 * PKT_S))
    b = tr.take_batches(1e12, force=True)
    assert not any(x.anchor_reset for x in b) and {x.epoch for x in b} == {0}
    assert tr.state(1e12).anchor_segments == 1
    after = next(x for x in b if x.seq == 280)
    assert after.u0_us == pytest.approx((280 * 128 + step) / RATE * 1e6)
    assert after.t0_us == pytest.approx((10.0 + 40 * PKT_S) * 1e6, abs=50.0)


def _stamp(pk, t_sends):
    return [replace(p, t_send=t) for p, t in zip(pk, t_sends, strict=True)]


def _take_every_250ms(tr, pk):
    out, next_take = [], pk[0].t_send + 0.25
    for p in pk:
        tr.on_packet(p, timestamp="kernel", in_step=False)
        if p.t_send >= next_take:
            out += tr.take_batches(p.t_send * 1e6)
            next_take += 0.25
    return out + tr.take_batches(1e12, force=True)


def test_late_send_stall_then_pa_skip_keeps_t0_on_the_frame_count_axis():
    """R-18, the ENEBY20 pattern: sends fall behind by up to D over a few packets, PulseAudio then
    skips about D of media (contiguous sequence) and sends go on at the frame cadence. Every batch's
    air time stays ``t0 = count·frame_us + anchor`` — no spike at the stall or at the skip — while
    ``u0_us`` carries the content jump that moves ``H``."""
    tr = AirLinkTracker()
    d_s, skip = 0.040, 1764  # 40 ms late at the worst, then 40 ms of media skipped
    before = packets(80)
    stall = _stamp(
        packets(20, seq0=80, ts0=80 * 896, t0=10.0 + 80 * PKT_S),
        [10.0 + (80 + j) * PKT_S + d_s * (j + 1) / 20 for j in range(20)],
    )
    after = packets(100, seq0=100, ts0=100 * 896 + skip, t0=10.0 + 100 * PKT_S)
    b = _take_every_250ms(tr, before + stall + after)
    assert {x.epoch for x in b} == {0} and [x.anchor_reset for x in b].count(True) == 1
    assert sum(len(x.e) for x in b) == 1400
    for x in b:
        assert x.t0_us - x.seq * x.frame_us == pytest.approx(10.0e6, abs=1.0)
        assert x.u0_us - x.seq * x.frame_us == pytest.approx(0.0 if x.seq < 700 else skip / RATE * 1e6, abs=1.0)


def test_count_anchor_follows_the_arrival_cadence_across_bitpool_changes_and_skips():
    """The anchor is the median of ``t_send − count·frame_us`` over the last 64 packets, ``count``
    the frames sent in the epoch (9–15 per packet as the bitpool changes): sends that settle ``D``
    behind the frame cadence after a skip move it by ``D`` once a majority of the window is late,
    and RTP steps never move it by themselves."""
    tr = AirLinkTracker()
    sizes = [9, 12, 15, 10] * 50  # 200 packets
    pk, ts, t, n = [], 0, 10.0, 0
    late_s, skip = 0.030, 1323
    for k, fr in enumerate(sizes):
        if k == 100:
            ts += skip  # PA skip after the 100th packet
        p = packets(1, seq0=k, ts0=ts, frames=fr)[0]
        pk.append(replace(p, t_send=t + n * 128 / RATE + (late_s if k >= 100 else 0.0)))
        ts += fr * 128
        n += fr
    b = _take_every_250ms(tr, pk)
    assert [x.anchor_reset for x in b].count(True) == 1
    frames_before = sum(sizes[:100])
    early = [x for x in b if x.seq + len(x.e) <= frames_before]
    late = [x for x in b if x.seq >= sum(sizes[:170])]
    assert early and late
    assert all(x.t0_us - x.seq * x.frame_us == pytest.approx(10.0e6, abs=1.0) for x in early)
    assert all(x.t0_us - x.seq * x.frame_us == pytest.approx(10.0e6 + late_s * 1e6, abs=1.0) for x in late)
    assert all(x.u0_us - x.seq * x.frame_us == pytest.approx(skip / RATE * 1e6, abs=1.0) for x in late)


def test_fragmented_sbc_explains_the_silence():
    tr = AirLinkTracker()
    for k in range(10):
        p = replace(packets(1)[0], frames=(), sbc_fragmented=True, sbc_frame_count=None, t_send=10.0 + k * 0.02, seq=k)
        tr.on_packet(p, timestamp="kernel", in_step=False)
    s = tr.state((10.0 + 9 * 0.02) * 1e6)
    assert (s.state, s.reason) == ("unsupported_codec", "sbc_fragmented")
    assert tr.take_batches(1e12, force=True) == []


def test_completion_lag_is_a_2s_median_telemetry():
    tr = AirLinkTracker()
    pk = [replace(p, t_complete=p.t_send + (0.008 if k % 3 else 0.030)) for k, p in enumerate(packets(40))]
    for p in pk:
        tr.on_packet(p, timestamp="kernel", in_step=True)
    b = tr.take_batches(1e12, force=True)
    assert b[0].complete_lag_us == pytest.approx(8000.0, abs=1.0)
    assert b[0].t0_us == pytest.approx(10.0 * 1e6, abs=1.0)  # the send anchor stays canonical (R-5)


def test_too_many_frames_per_second_is_unsupported():
    tr = AirLinkTracker()
    tiny = [
        replace(p, frames=tuple(replace(f, blocks=4, subbands=4) for f in p.frames), rtp_ts=k * 7 * 16)
        for k, p in enumerate(packets(40))
    ]  # 44100 / 16 = 2756 frames/s
    feed(tr, tiny)
    assert tr.take_batches(1e12, force=True) == []
    s = tr.state((10.0 + 39 * PKT_S) * 1e6)
    assert (s.state, s.reason) == ("unsupported_codec", "sbc_params")


def test_non_sbc_is_unsupported_after_2s():
    tr = AirLinkTracker(codec_hint=lambda: "aac")
    for k in range(150):
        p = replace(packets(1)[0], codec="unknown", frames=(), t_send=10.0 + k * 0.02, seq=k)
        tr.on_packet(p, timestamp="kernel", in_step=False)
    s = tr.state((10.0 + 149 * 0.02) * 1e6)
    assert s.state == "unsupported_codec" and s.reason == "codec:aac"


def test_non_sbc_for_less_than_2s_is_not_yet_unsupported():
    tr = AirLinkTracker()
    for k in range(50):
        p = replace(packets(1)[0], codec="unknown", frames=(), t_send=10.0 + k * 0.02, seq=k)
        tr.on_packet(p, timestamp="kernel", in_step=False)
    assert tr.state((10.0 + 49 * 0.02) * 1e6).state == "streaming"


def test_state_reports_streaming_then_no_traffic():
    tr = AirLinkTracker()
    assert tr.state(0.0).state == "no_traffic"
    feed(tr, packets(40))
    last_us = (10.0 + 39 * PKT_S) * 1e6
    s = tr.state(last_us)
    assert (s.state, s.codec, s.epoch, s.anchor_segments, s.frames) == ("streaming", "sbc", 0, 1, 280)
    assert tr.state(last_us + 5e6).state == "no_traffic"


def test_pending_frames_are_bounded_without_takers():
    tr = AirLinkTracker()
    feed(tr, packets(3000))  # 21000 frames, nobody takes batches
    b = tr.take_batches(1e12, force=True)
    assert sum(len(x.e) for x in b) <= 4096
    assert all(x.u0_us >= x.seq * x.frame_us - 1 for x in b)
    assert b[-1].seq + len(b[-1].e) == 21000  # the newest frames are kept


def test_fixture_cells_fold_to_the_phase_tool_phase():
    from scripts.a2dp_air_phase import analyze, click_phase_ms, sbc_encoder_delay_samples

    reports, _ = analyze(FIX / "eneby_clicks.btsnoop.gz")
    pk = [p for p in parse_records(read_btsnoop(FIX / "eneby_clicks.btsnoop.gz"), A2dpAirParser()) if p.handle == 70]
    tr = AirLinkTracker()
    feed(tr, pk)
    rows = [
        (x.t0_us / 1e6 + j * x.frame_us / 1e6, float(2 ** (v / 8)))
        for x in tr.take_batches(1e18, force=True)
        for j, v in enumerate(x.e)
        if v != 255
    ]
    assert click_phase_ms(rows, 128 / RATE, 0.0, sbc_encoder_delay_samples(8) / RATE) == pytest.approx(
        reports[0].phase_send_ms, abs=0.5
    )
