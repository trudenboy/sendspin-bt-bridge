"""Tests for the A2DP-at-the-air parser (HCI monitor → ACL → L2CAP → RTP → SBC).

Byte strings are built by hand from the specs (Core v5.x Vol 4 Part E §5.4.2
ACL packets / §7.7.19 Number Of Completed Packets, L2CAP basic header, RFC 3550
RTP, A2DP v1.3 §12 SBC). Expected values are worked out in the comments, not
recomputed with the module's own helpers.
"""

from __future__ import annotations

import gzip
import random
import struct
from typing import TYPE_CHECKING

import pytest

from sendspin_bridge.services.bluetooth import a2dp_air as air
from sendspin_bridge.services.bluetooth.hci_socket import MONITOR_ACL_RX_PKT, MONITOR_ACL_TX_PKT, MONITOR_EVENT_PKT

if TYPE_CHECKING:
    from pathlib import Path

# A real SBC frame from a capture (44.1 kHz, 16 blocks, joint stereo, loudness, 8 sub-bands, bitpool 53):
# 4 + 4*8*2/8 + ceil((8 + 16*53) / 8) = 4 + 8 + 107 = 119 bytes, digital silence (all scale factors 0).
REAL_SBC_FRAME = bytes.fromhex(
    "9cbd357d0000000000000000007776db6eedb6dbbbb6db874dc69dddb6dbbb6db6eeedb6dddb6db7776db6f0e26dbbbb6db776db6e15e27"
    "1bbb6db6eeedb6dddb6db7776db6eedb6dbbbb6db776db6ddddb6db426d46eeedb6dddb6db7776db6eedb6dc4324dc776db6de1a26dbbb6d"
    "b6eeedb6dddb6db"
)
# The RTP header in front of it: V=2, PT=96, seq 0x31d5, ts 0x00ae6980, SSRC 1; then the SBC media header (7 frames).
REAL_RTP_HEADER = bytes.fromhex("806031d500ae698000000001")

CID = 0x0041
HANDLE = 0x046


def acl(handle: int, pb: int, data: bytes, bc: int = 0) -> bytes:
    return struct.pack("<HH", (handle & 0x0FFF) | (pb << 12) | (bc << 14), len(data)) + data


def l2cap(cid: int, payload: bytes) -> bytes:
    return struct.pack("<HH", len(payload), cid) + payload


def nocp(*pairs: tuple[int, int]) -> bytes:
    params = bytes([len(pairs)]) + b"".join(struct.pack("<HH", h, n) for h, n in pairs)
    return bytes([0x13, len(params)]) + params


def disconnect(handle: int) -> bytes:
    return bytes([0x05, 4, 0x00]) + struct.pack("<H", handle) + b"\x13"


def media_sdu(seq: int, ts: int, frames: int = 7) -> bytes:
    return struct.pack(">BBHII", 0x80, 0x60, seq, ts, 1) + bytes([frames]) + REAL_SBC_FRAME * frames


def fragments(sdu: bytes, cid: int = CID, sizes: tuple[int, ...] = (300, 300)) -> list[tuple[int, bytes]]:
    """Split an L2CAP PDU into (pb, chunk) ACL fragments: first-flushable then continuations."""
    pdu = l2cap(cid, sdu)
    out, pos = [], 0
    for size in sizes:
        out.append((0b10 if pos == 0 else 0b01, pdu[pos : pos + size]))
        pos += size
    if pos < len(pdu):
        out.append((0b10 if pos == 0 else 0b01, pdu[pos:]))
    return out


# ── SBC ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("mode", "subbands", "blocks", "bitpool", "expected"),
    [
        (air.ChannelMode.MONO, 8, 16, 32, 72),  # 4 + 4 + ceil(16*1*32/8)=64
        (air.ChannelMode.DUAL_CHANNEL, 8, 16, 32, 140),  # 4 + 8 + ceil(16*2*32/8)=128
        (air.ChannelMode.STEREO, 8, 16, 53, 118),  # 4 + 8 + ceil(16*53/8)=106
        (air.ChannelMode.JOINT_STEREO, 8, 16, 53, 119),  # 4 + 8 + ceil((8+16*53)/8)=107
        (air.ChannelMode.MONO, 4, 4, 5, 9),  # 4 + 2 + ceil(4*5/8)=3
        (air.ChannelMode.DUAL_CHANNEL, 4, 4, 5, 13),  # 4 + 4 + ceil(4*2*5/8)=5
        (air.ChannelMode.STEREO, 4, 4, 5, 11),  # 4 + 4 + ceil(4*5/8)=3
        (air.ChannelMode.JOINT_STEREO, 4, 4, 5, 11),  # 4 + 4 + ceil((4+20)/8)=3
        (air.ChannelMode.JOINT_STEREO, 4, 12, 3, 13),  # 4 + 4 + ceil((4+36)/8)=5
    ],
)
def test_sbc_frame_length_for_every_channel_mode(mode, subbands, blocks, bitpool, expected):
    assert air.sbc_frame_length(mode, subbands, blocks, bitpool) == expected


def test_parse_real_sbc_frame_header_and_crc():
    frame = air.parse_sbc_frame(REAL_SBC_FRAME)
    assert frame is not None
    assert (frame.sampling_rate, frame.blocks, frame.subbands) == (44100, 16, 8)
    assert frame.channel_mode is air.ChannelMode.JOINT_STEREO
    assert frame.channels == 2
    assert frame.allocation_snr is False
    assert frame.bitpool == 53
    assert frame.length == 119
    assert frame.samples == 128
    assert frame.scale_factors == ((0,) * 8, (0,) * 8)
    assert frame.join == (False,) * 8


def test_corrupted_scale_factor_fails_the_crc():
    bad = bytearray(REAL_SBC_FRAME)
    bad[6] ^= 0x10  # a scale-factor bit, covered by the CRC
    assert air.parse_sbc_frame(bytes(bad)) is None
    assert air.parse_sbc_frame(bytes(bad), check_crc=False) is not None


def test_joint_stereo_scale_factors_follow_the_join_bits():
    # 0xBD: 44.1 kHz, 16 blocks, joint stereo, loudness, 8 sub-bands; bitpool 2 -> 4 + 8 + ceil((8+32)/8) = 17 bytes
    frame_bytes = bytes(
        [0x9C, 0xBD, 0x02, 0x00, 0b1010_0000, 0x12, 0x34, 0x56, 0x78, 0xFE, 0xDC, 0xBA, 0x98, 0, 0, 0, 0]
    )
    frame = air.parse_sbc_frame(frame_bytes, check_crc=False)
    assert frame is not None
    assert frame.length == 17
    assert frame.join == (True, False, True, False, False, False, False, False)
    assert frame.scale_factors == ((1, 2, 3, 4, 5, 6, 7, 8), (15, 14, 13, 12, 11, 10, 9, 8))


def test_mono_four_subband_scale_factors_start_right_after_the_header():
    # 0x30 = 0b00_11_00_0_0: 16 kHz, 16 blocks, mono, loudness, 4 sub-bands (bit 0 clear)
    # bitpool 5 -> 4 + 2 + ceil(16*5/8) = 16 bytes
    frame_bytes = bytes([0x9C, 0x30, 0x05, 0x00, 0xA1, 0x0F]) + bytes(10)
    frame = air.parse_sbc_frame(frame_bytes, check_crc=False)
    assert frame is not None
    assert (frame.sampling_rate, frame.blocks, frame.subbands, frame.channels) == (16000, 16, 4, 1)
    assert frame.length == 16
    assert frame.join == ()
    assert frame.scale_factors == ((10, 1, 0, 15),)
    assert frame.samples == 64


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x9c",
        b"\x9c\xbd\x35",  # header cut
        REAL_SBC_FRAME[:60],  # frame shorter than its length
        b"\x9d" + REAL_SBC_FRAME[1:],  # wrong syncword
        bytes([0x9C, 0xBD, 0x01]) + REAL_SBC_FRAME[3:],  # bitpool 1 < 2
        bytes([0x9C, 0x30, 0x41]) + bytes(200),  # mono 4 sub-bands: bitpool 65 > 16 * 4
    ],
)
def test_bad_sbc_bytes_return_none(data):
    assert air.parse_sbc_frame(data, check_crc=False) is None


def test_parse_sbc_frames_walks_consecutive_frames_and_stops_at_garbage():
    frames, complete = air.parse_sbc_frames(REAL_SBC_FRAME * 3)
    assert len(frames) == 3 and complete
    frames, complete = air.parse_sbc_frames(REAL_SBC_FRAME * 2 + b"\x00\x01\x02")
    assert len(frames) == 2 and not complete


# ── RTP ────────────────────────────────────────────────────────────────────


def test_parse_rtp_reads_sequence_timestamp_and_ssrc():
    hdr = air.parse_rtp(REAL_RTP_HEADER + b"\x07")
    assert hdr is not None
    assert (hdr.payload_type, hdr.marker, hdr.seq, hdr.timestamp, hdr.ssrc) == (96, False, 12757, 11430272, 1)
    assert hdr.header_len == 12


def test_parse_rtp_skips_csrc_list_and_extension():
    # CC=2, X=1, marker set, PT 97; extension: profile 0xBEDE, 1 word
    data = bytes([0x92, 0xE1]) + struct.pack(">HII", 7, 99, 5) + bytes(8) + b"\xbe\xde\x00\x01" + bytes(4) + b"\x01"
    hdr = air.parse_rtp(data)
    assert hdr is not None
    assert (hdr.payload_type, hdr.marker, hdr.seq, hdr.timestamp, hdr.ssrc) == (97, True, 7, 99, 5)
    assert hdr.header_len == 12 + 8 + 8


@pytest.mark.parametrize(
    "data",
    [
        b"",
        REAL_RTP_HEADER[:11],
        bytes([0x40]) + REAL_RTP_HEADER[1:],  # version 1
        bytes([0x8F]) + REAL_RTP_HEADER[1:],  # 15 CSRCs announced, none present
        bytes([0x90]) + REAL_RTP_HEADER[1:] + b"\xbe\xde\x00\x09",  # extension longer than the packet
    ],
)
def test_parse_rtp_rejects_bad_headers(data):
    assert air.parse_rtp(data) is None


# ── ACL reassembly ─────────────────────────────────────────────────────────


def test_reassembler_joins_start_and_continuations():
    r = air.AclReassembler()
    pdu = l2cap(CID, b"0123456789")
    assert r.push(0b10, pdu[:6]) is None
    sdu = r.push(0b01, pdu[6:])
    assert sdu == air.L2capSdu(cid=CID, payload=b"0123456789")


def test_reassembler_handles_a_complete_pdu_in_one_fragment_and_non_flushable_start():
    r = air.AclReassembler()
    assert r.push(0b10, l2cap(CID, b"abc")) == air.L2capSdu(CID, b"abc")
    assert r.push(0b00, l2cap(0x42, b"xyz")) == air.L2capSdu(0x42, b"xyz")


def test_reassembler_waits_for_a_split_l2cap_header():
    r = air.AclReassembler()
    pdu = l2cap(CID, b"hello")
    assert r.push(0b10, pdu[:2]) is None
    assert r.push(0b01, pdu[2:5]) is None
    assert r.push(0b01, pdu[5:]) == air.L2capSdu(CID, b"hello")


def test_reassembler_drops_a_continuation_without_a_start():
    r = air.AclReassembler()
    pdu = l2cap(CID, b"0123456789")
    assert r.push(0b01, pdu[6:]) is None  # out of order: the continuation arrives first
    assert r.dropped_continuations == 1
    assert r.push(0b10, pdu[:6]) is None
    assert r.push(0b01, pdu[6:]) == air.L2capSdu(CID, b"0123456789")


def test_reassembler_drops_an_incomplete_sdu_when_a_new_start_arrives():
    r = air.AclReassembler()
    assert r.push(0b10, l2cap(CID, b"0123456789")[:7]) is None
    assert r.push(0b10, l2cap(CID, b"xy")) == air.L2capSdu(CID, b"xy")
    assert r.dropped_incomplete == 1


def test_reassembler_drops_an_overlong_continuation():
    r = air.AclReassembler()
    pdu = l2cap(CID, b"0123")
    assert r.push(0b10, pdu[:6]) is None
    assert r.push(0b01, pdu[6:] + b"EXTRA") is None
    assert r.dropped_malformed == 1
    assert r.push(0b01, b"zz") is None  # nothing in progress any more
    assert r.dropped_continuations == 1


def test_reassembler_refuses_sdus_above_its_bound():
    r = air.AclReassembler(max_sdu=100)
    assert r.push(0b10, struct.pack("<HH", 60000, CID) + bytes(10)) is None
    assert r.dropped_malformed == 1
    for _ in range(5):
        assert r.push(0b01, bytes(1000)) is None
    assert r.buffered == 0


def test_reassembler_survives_random_garbage():
    rng = random.Random(1234)
    r = air.AclReassembler(max_sdu=4096)
    for _ in range(5000):
        r.push(rng.randrange(4), rng.randbytes(rng.randrange(0, 64)))
        assert r.buffered <= 4096 + 4


# ── Parser: media detection ────────────────────────────────────────────────


def feed_media(parser: air.A2dpAirParser, t: float, sdu: bytes, *, index: int = 1, handle: int = HANDLE):
    out = []
    for pb, chunk in fragments(sdu):
        out += parser.feed(t, index, MONITOR_ACL_TX_PKT, acl(handle, pb, chunk))
    return out


def test_sbc_media_packet_is_reassembled_and_decoded():
    p = air.A2dpAirParser()
    assert feed_media(p, 10.0, media_sdu(12757, 11430272)) == []
    (pkt,) = p.flush()
    assert (pkt.index, pkt.handle, pkt.cid, pkt.seq, pkt.rtp_ts, pkt.ssrc) == (1, HANDLE, CID, 12757, 11430272, 1)
    assert pkt.codec == "sbc"
    assert len(pkt.frames) == 7
    assert pkt.frames[0].bitpool == 53
    assert pkt.t_send == 10.0
    assert pkt.t_complete is None


def test_non_sbc_payload_is_reported_with_no_frames():
    ldac_like = struct.pack(">BBHII", 0x80, 0x60, 5, 1000, 9) + b"\x03\xaa\x10\x20" + bytes(200)
    p = air.A2dpAirParser()
    feed_media(p, 1.0, ldac_like)
    (pkt,) = p.flush()
    assert pkt.codec == "unknown"
    assert pkt.frames == ()
    assert (pkt.seq, pkt.rtp_ts, pkt.ssrc) == (5, 1000, 9)


def test_fragmented_sbc_frame_is_reported_without_frames():
    sdu = struct.pack(">BBHII", 0x80, 0x60, 1, 0, 1) + bytes([0b1100_0010]) + REAL_SBC_FRAME[:60]
    p = air.A2dpAirParser()
    feed_media(p, 1.0, sdu)
    (pkt,) = p.flush()
    assert pkt.codec == "sbc" and pkt.frames == () and pkt.sbc_fragmented


def test_signalling_and_short_sdus_are_not_media():
    p = air.A2dpAirParser()
    # AVDTP signalling on the same dynamic CID range: label 8, single, command, Start (0x07)
    feed_media(p, 1.0, bytes([0x80, 0x07, 0x04]) + bytes(20))
    feed_media(p, 1.0, media_sdu(1, 1))  # the one real media packet
    # fixed-channel traffic (signalling CID 1) that happens to look like RTP
    p.feed(1.0, 1, MONITOR_ACL_TX_PKT, acl(HANDLE, 0b10, l2cap(0x0001, media_sdu(2, 2))))
    p.feed(1.0, 1, MONITOR_ACL_RX_PKT, acl(HANDLE, 0b10, l2cap(CID, media_sdu(3, 3))))  # inbound: not ours
    pkts = p.flush()
    assert [pk.seq for pk in pkts] == [1]


def test_parser_never_raises_on_garbage_records():
    rng = random.Random(99)
    p = air.A2dpAirParser()
    for i in range(20000):
        opcode = rng.choice([MONITOR_ACL_TX_PKT, MONITOR_EVENT_PKT, MONITOR_ACL_RX_PKT, 0, 2, 12, 0xFFFF])
        p.feed(i * 0.001, rng.randrange(3), opcode, rng.randbytes(rng.randrange(0, 80)))
    p.flush()
    assert p.stats.malformed_records > 0


def test_parser_bounds_the_number_of_links():
    p = air.A2dpAirParser(max_links=4)
    for h in range(10):
        p.feed(0.0, 0, MONITOR_ACL_TX_PKT, acl(h, 0b10, l2cap(CID, b"x")))
    assert p.stats.links_refused == 6


# ── Parser: completion crediting ───────────────────────────────────────────


def stream(parser: air.A2dpAirParser, n: int, *, pre_in_flight: int = 0, period: float = 0.020, latency: float = 0.008):
    """n media packets of 3 fragments every *period*; the controller completes each packet *latency* later.

    *pre_in_flight* fragments were queued before the capture started: their completion arrives just after
    the first packet is sent, i.e. while that packet is still in flight. Returns (emitted packets,
    true completion time per sequence number)."""
    out, truth = [], {}
    for i in range(n):
        t = 100.0 + i * period
        out += feed_media(parser, t, media_sdu(i, i * 896))
        if i == 0 and pre_in_flight:
            out += parser.feed(t + 0.001, 1, MONITOR_EVENT_PKT, nocp((HANDLE, pre_in_flight)))
        out += parser.feed(t + latency, 1, MONITOR_EVENT_PKT, nocp((HANDLE, 3)))
        truth[i] = t + latency
    return out + parser.flush(), truth


def test_completions_are_credited_fifo_to_the_last_fragment():
    pkts, truth = stream(air.A2dpAirParser(), 20)
    assert [pk.seq for pk in pkts] == list(range(20))
    credited = [pk for pk in pkts if pk.t_complete is not None]
    assert len(credited) >= 17  # a few at the start are spent proving the queue is in step
    for pk in credited:
        assert pk.t_complete == pytest.approx(truth[pk.seq])
        assert pk.t_complete > pk.t_send


@pytest.mark.parametrize("pre", [1, 2, 3, 5])
def test_capture_starting_mid_flight_never_misaligns(pre):
    pkts, truth = stream(air.A2dpAirParser(), 30, pre_in_flight=pre)
    credited = [pk for pk in pkts if pk.t_complete is not None]
    assert len(credited) >= 25
    for pk in credited:
        assert pk.t_complete == pytest.approx(truth[pk.seq]), pk.seq
    assert pkts[0].t_complete is None  # its first fragments were "completed" by pre-capture credits
    assert pkts[0].seq == 0


def test_completion_split_over_several_events_uses_the_last_fragment():
    p = air.A2dpAirParser()
    out = []
    for i in range(10):
        t = float(i)
        out += feed_media(p, t, media_sdu(i, 0))
        out += p.feed(t + 0.1, 1, MONITOR_EVENT_PKT, nocp((HANDLE, 2)))
        out += p.feed(t + 0.3, 1, MONITOR_EVENT_PKT, nocp((0x0999, 4), (HANDLE, 1)))  # other handle in the event
    out += p.flush()
    credited = [pk for pk in out if pk.t_complete is not None]
    assert credited and all(pk.t_complete == pytest.approx(pk.seq + 0.3) for pk in credited)


def test_completions_on_another_index_are_not_credited():
    p = air.A2dpAirParser()
    out = []
    for i in range(10):
        out += feed_media(p, float(i), media_sdu(i, 0))
        out += p.feed(i + 0.1, 0, MONITOR_EVENT_PKT, nocp((HANDLE, 3)))  # hci0, same handle number
    out += p.flush()
    assert len(out) == 10 and all(pk.t_complete is None for pk in out)


def test_disconnect_resets_the_link():
    p = air.A2dpAirParser()
    out = feed_media(p, 1.0, media_sdu(1, 0))
    out += p.feed(1.1, 1, MONITOR_EVENT_PKT, disconnect(HANDLE))
    assert [pk.seq for pk in out] == [1] and out[0].t_complete is None
    # a stray completion for the old handle after the disconnect is not credited to anything
    assert p.feed(1.2, 1, MONITOR_EVENT_PKT, nocp((HANDLE, 3))) == []


def test_parse_records_runs_the_parser_over_monitor_records():
    records = [
        air.MonitorRecord(1.0, 1, MONITOR_ACL_TX_PKT, acl(HANDLE, pb, c)) for pb, c in fragments(media_sdu(4, 8))
    ]
    (pkt,) = air.parse_records(records)
    assert pkt.seq == 4 and len(pkt.frames) == 7


# ── btsnoop reader ─────────────────────────────────────────────────────────

_EPOCH_DELTA_US = 0x00DCDDB30F2F8000  # btsnoop microseconds since 0000-01-01 at the unix epoch


def _btsnoop(records: list[tuple[int, int, int, bytes]], datalink: int = 2001) -> bytes:
    out = b"btsnoop\0" + struct.pack(">II", 1, datalink)
    for ts_us, index, opcode, data in records:
        out += struct.pack(">IIIIq", len(data), len(data), (index << 16) | opcode, 0, ts_us + _EPOCH_DELTA_US) + data
    return out


def test_read_btsnoop_yields_unix_times_index_and_opcode(tmp_path: Path):
    path = tmp_path / "c.btsnoop"
    path.write_bytes(_btsnoop([(1_700_000_000_250_000, 1, 4, b"abc"), (1_700_000_001_000_000, 0, 3, b"\x13")]))
    recs = list(air.read_btsnoop(path))
    assert recs == [
        air.MonitorRecord(pytest.approx(1_700_000_000.25), 1, 4, b"abc"),
        air.MonitorRecord(pytest.approx(1_700_000_001.0), 0, 3, b"\x13"),
    ]


def test_read_btsnoop_reads_gzip_and_stops_at_a_truncated_tail(tmp_path: Path):
    raw = _btsnoop([(1_000_000, 1, 4, b"abc"), (2_000_000, 1, 4, b"defgh")])
    path = tmp_path / "c.btsnoop.gz"
    path.write_bytes(gzip.compress(raw[:-2]))
    stats = air.ParseStats()
    recs = list(air.read_btsnoop(path, stats=stats))
    assert [r.payload for r in recs] == [b"abc"]
    assert stats.truncated_records == 1


def test_read_btsnoop_rejects_other_datalinks(tmp_path: Path):
    path = tmp_path / "h4.btsnoop"
    path.write_bytes(_btsnoop([], datalink=1002))
    with pytest.raises(ValueError, match="2001"):
        list(air.read_btsnoop(path))
    path.write_bytes(b"not a btsnoop file at all")
    with pytest.raises(ValueError):
        list(air.read_btsnoop(path))
