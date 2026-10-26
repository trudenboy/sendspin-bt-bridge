"""The formats a bridged speaker declares to the server in ``client/hello`` (#543).

Music Assistant only lets a player use a format the client declared, and the
server treats the first declared format as the preferred one.  Speakers keep
CD-class 16-bit output by default; 24-bit and 96 kHz are declared after it so a
player configured for them in Music Assistant can actually get them.
"""

from __future__ import annotations

from aiosendspin.models.types import AudioCodec

from sendspin_bridge.services.ipc.bridge_daemon import _declared_formats


def _shape(fmt):
    return (fmt.codec, fmt.sample_rate, fmt.bit_depth, fmt.channels)


def test_preferred_format_stays_16_bit_48k_flac():
    assert _shape(_declared_formats()[0]) == (AudioCodec.FLAC, 48000, 16, 2)


def test_all_16_bit_formats_come_before_hi_res():
    depths = [fmt.bit_depth for fmt in _declared_formats()]
    assert depths == sorted(depths)


def test_hi_res_formats_are_declared():
    shapes = {_shape(fmt) for fmt in _declared_formats()}
    for codec in (AudioCodec.FLAC, AudioCodec.PCM):
        assert (codec, 48000, 24, 2) in shapes
        assert (codec, 96000, 24, 2) in shapes


def test_16_bit_fallbacks_are_kept():
    shapes = {_shape(fmt) for fmt in _declared_formats()}
    for codec in (AudioCodec.FLAC, AudioCodec.PCM):
        for rate in (48000, 44100):
            assert (codec, rate, 16, 2) in shapes
