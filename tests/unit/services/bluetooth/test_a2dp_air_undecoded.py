"""The live path's undecoded SBC frames (R-19): the parser walks and validates SBC frames without
decoding their scale factors, and the tracker reads only its band's scale factors from the bytes.

Every result is compared with the decoding path (``parse_sbc_frames`` / ``frame_envelope``), which
stays the reference.
"""

from __future__ import annotations

import gzip
import random
from dataclasses import replace
from pathlib import Path

import pytest

from sendspin_bridge.services.bluetooth import a2dp_air as air
from sendspin_bridge.services.bluetooth.a2dp_air_envelope import envelope_subbands, frame_envelope, sbc_envelopes

FIX = Path(__file__).resolve().parents[3] / "fixtures" / "a2dp_air"


def random_frame(
    rng: random.Random, *, b1: int | None = None, bitpool: int | None = None, good_crc: bool = True
) -> bytes:
    """One SBC frame with random parameters (or the given header byte/bitpool) and random content."""
    b1 = rng.randrange(256) if b1 is None else b1
    mode = air.ChannelMode((b1 >> 2) & 3)
    subbands = 8 if b1 & 1 else 4
    blocks = air.SBC_BLOCKS[(b1 >> 4) & 3]
    max_bp = min(255, (16 if mode in (air.ChannelMode.MONO, air.ChannelMode.DUAL_CHANNEL) else 32) * subbands)
    bitpool = rng.randint(2, max_bp) if bitpool is None else bitpool
    length = air.sbc_frame_length(mode, subbands, blocks, bitpool)
    body = bytes(rng.randrange(256) for _ in range(length - 4))
    channels = 1 if mode is air.ChannelMode.MONO else 2
    side_bits = (subbands if mode is air.ChannelMode.JOINT_STEREO else 0) + 4 * subbands * channels
    crc = air._sbc_crc8(bytes([b1, bitpool]) + body[: (side_bits + 7) // 8], 16 + side_bits)
    if not good_crc:
        crc ^= 0x01
    return bytes([air.SBC_SYNCWORD, b1, bitpool, crc]) + body


def random_payload(rng: random.Random) -> bytes:
    kind = rng.randrange(6)
    b1 = rng.randrange(256)
    if kind == 0:  # one parameter set, as A2DP sends it
        frames = [random_frame(rng, b1=b1) for _ in range(rng.randint(1, 10))]
    elif kind == 1:  # changing bitpool
        frames = [random_frame(rng, b1=b1 | 1) for _ in range(rng.randint(1, 10))]
    elif kind == 2:  # anything goes, bad CRCs included
        frames = [random_frame(rng, good_crc=rng.random() < 0.7) for _ in range(rng.randint(1, 6))]
    elif kind == 3:  # a bad bitpool in the middle
        frames = [random_frame(rng, b1=b1) for _ in range(3)]
        frames.insert(1, bytes([air.SBC_SYNCWORD, b1, 0, 0]) + frames[0][4:])
    elif kind == 4:  # truncated
        frames = [random_frame(rng, b1=b1) for _ in range(rng.randint(1, 4))]
        frames[-1] = frames[-1][: rng.randrange(len(frames[-1]))]
    else:  # garbage between frames
        frames = [
            random_frame(rng, b1=b1),
            bytes(rng.randrange(256) for _ in range(rng.randint(1, 5))),
            random_frame(rng),
        ]
    return b"".join(frames)


@pytest.mark.parametrize("check_crc", [True, False, "first", 1, 0])  # truthy/falsy like the decoder
def test_walk_validates_exactly_like_the_decoder(check_crc):
    rng = random.Random(19)
    for _ in range(600):
        payload = random_payload(rng)
        frames, complete = air.parse_sbc_frames(payload, check_crc=check_crc)
        view, view_complete = air.walk_sbc_frames(payload, check_crc=check_crc)
        assert (len(view), view_complete) == (len(frames), complete)
        assert view.decode() == frames


@pytest.mark.parametrize("name", sorted(p.name for p in (FIX / "libsbc").glob("*.sbc.gz")))
def test_walk_matches_the_decoder_on_libsbc_streams(name):
    data = gzip.decompress((FIX / "libsbc" / name).read_bytes())
    frames, complete = air.parse_sbc_frames(data, check_crc=True)
    view, view_complete = air.walk_sbc_frames(data, check_crc=True)
    assert (len(view), view_complete) == (len(frames), complete)
    assert view.decode() == frames


def test_band_energies_from_the_bytes_equal_frame_envelope():
    rng = random.Random(7)
    for _ in range(600):
        view, _ = air.walk_sbc_frames(random_payload(rng), check_crc=False)
        if not len(view):
            continue
        first = view.layouts[0]
        bands = envelope_subbands(first.sampling_rate, first.subbands)
        assert sbc_envelopes(view, bands) == [frame_envelope(f, bands) for f in view.decode()]
        wide = tuple(range(1, 8))  # bands past a 4-sub-band frame's last are skipped, as in frame_envelope
        assert sbc_envelopes(view, wide) == [frame_envelope(f, wide) for f in view.decode()]


@pytest.mark.parametrize("fixture", ["eneby_clicks", "air_music"])
def test_undecoded_parser_emits_the_same_packets_with_undecoded_frames(fixture):
    recs = list(air.read_btsnoop(FIX / f"{fixture}.btsnoop.gz"))
    decoded_parser = air.A2dpAirParser(check_crc="first")
    undecoded_parser = air.A2dpAirParser(check_crc="first", decode_frames=False)
    decoded = air.parse_records(recs, decoded_parser)
    undecoded = air.parse_records(recs, undecoded_parser)
    assert len(decoded) == len(undecoded) > 0
    for d, u in zip(decoded, undecoded, strict=True):
        assert u.frames == ()
        if d.codec == "sbc" and not d.sbc_fragmented:
            assert u.sbc is not None and u.sbc.decode() == d.frames
        else:
            assert u.sbc is None
        assert replace(u, frames=d.frames, sbc=None) == d
    assert undecoded_parser.stats == decoded_parser.stats


def test_the_energy_memo_stays_bounded():
    from sendspin_bridge.services.bluetooth import a2dp_air_envelope as env

    rng = random.Random(4096)
    wide = tuple(range(1, 8))
    for _ in range(3000):
        view, _ = air.walk_sbc_frames(random_frame(rng, b1=0xBF), check_crc=False)  # stereo, 8 sub-bands
        assert sbc_envelopes(view, wide) == [frame_envelope(f, wide) for f in view.decode()]
        assert len(env._ENERGY) <= 4096
