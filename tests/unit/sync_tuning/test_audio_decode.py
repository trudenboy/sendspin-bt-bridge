"""Phone uploads (m4a/aac, wav, …) decoded with PyAV to the estimator's mono PCM. Fixtures are made here."""

import math
import struct

import pytest

from sendspin_bridge.sync_tuning.audio_decode import DECODE_RATE, decode_to_mono
from sendspin_sync.ports import ControlError
from tests.support.audio_files import encode_audio


def _encode(fmt: str, codec: str, *, seconds: float = 2.0, rate: int = 44100, layout: str = "mono") -> bytes:
    n = int(seconds * rate)
    tone = [0.3 * math.sin(2 * math.pi * 1000 * i / rate) for i in range(n)]
    return encode_audio(tone, fmt=fmt, codec=codec, rate=rate, layout=layout)


def test_an_m4a_aac_recording_decodes_to_mono_pcm_at_the_estimators_rate():
    data = _encode("mp4", "aac", seconds=2.0, rate=44100)
    pcm, rate = decode_to_mono(data, max_seconds=30)
    assert rate == DECODE_RATE == 16000
    n = len(pcm) // 2
    assert 1.9 * rate <= n <= 2.2 * rate
    samples = struct.unpack(f"<{n}h", pcm)
    assert max(abs(v) for v in samples[rate // 2 : rate]) > 3000  # the tone survives


def test_a_stereo_wav_decodes_too():
    data = _encode("wav", "pcm_s16le", seconds=1.0, rate=48000, layout="stereo")
    pcm, rate = decode_to_mono(data, max_seconds=30)
    assert abs(len(pcm) // 2 - rate) < rate * 0.05


def test_garbage_is_refused_as_an_invalid_recording():
    with pytest.raises(ControlError) as exc:
        decode_to_mono(b"definitely not audio" * 100, max_seconds=30)
    assert exc.value.code == "recording_invalid"


def test_a_recording_longer_than_allowed_is_refused():
    data = _encode("wav", "pcm_s16le", seconds=3.0, rate=8000)
    with pytest.raises(ControlError) as exc:
        decode_to_mono(data, max_seconds=2)
    assert exc.value.code == "recording_invalid" and "long" in exc.value.detail
