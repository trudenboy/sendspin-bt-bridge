import io
import struct
import wave

from sendspin_sync import click_track as ct


def _mono(pcm: bytes) -> list[int]:
    return [left for left, _right in struct.iter_unpack("<hh", pcm)]


def test_click_schedule_follows_period_and_cycle():
    times = ct.click_times(21.0)
    assert times[:6] == [(1.0, 0), (3.0, 1), (5.0, 2), (7.0, 3), (9.0, 4), (11.0, 0)]
    assert ct.gap_ms(4) == 120 and ct.CYCLE_S == 10.0


def test_track_is_silent_for_one_second_then_pairs_of_chirps_at_minus_12_dbfs():
    pcm = ct.render_pcm(duration_s=4, sample_rate=48000)
    mono = _mono(pcm)
    assert max(abs(v) for v in mono[:48000]) == 0
    first = mono[48000 : 48000 + 384]  # 8 ms chirp
    assert 7500 <= max(abs(v) for v in first) <= 8300  # 0.25 x 32767 ~ 8192
    second_start = 48000 + round(0.040 * 48000)  # k=0 -> 40 ms gap
    second = mono[second_start : second_start + 384]
    assert max(abs(v) for v in second) >= 7500
    gap = mono[48000 + 384 : second_start]
    assert max(abs(v) for v in gap) == 0
    # k=1 click at 3 s has a 60 ms gap
    third_pair = 3 * 48000 + round(0.060 * 48000)
    assert max(abs(v) for v in mono[third_pair : third_pair + 384]) >= 7500


def test_channels_are_identical_and_wav_header_is_right():
    wav = ct.render_wav(duration_s=2)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (2, 2, 48000, 96000)
    assert all(left == right for left, right in struct.iter_unpack("<hh", ct.render_pcm(duration_s=2)))


def test_render_is_deterministic():
    assert ct.render_pcm(duration_s=3) == ct.render_pcm(duration_s=3)
