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


def test_the_bed_keeps_a_slow_speaker_awake_and_the_chirps_stand_above_it():
    """A continuous -30 dBFS bed under the chirps (Lenco LS-500 never un-mutes for a chirp alone).

    The chirps stay ~24 dB above the bed, which is what the phone's estimator needs.
    """
    pcm = ct.render_pcm(duration_s=4, sample_rate=48000)
    mono = _mono(pcm)
    lead_in = mono[:48000]
    assert max(abs(v) for v in lead_in) > 0  # the bed, not silence
    assert max(abs(v) for v in lead_in) < 2000  # ... and well below a chirp
    first = mono[48000 : 48000 + 384]  # 8 ms chirp
    assert 15000 <= max(abs(v) for v in first) <= 17500  # 0.5 x 32767 ~ 16384
    second_start = 48000 + round(0.040 * 48000)  # k=0 -> 40 ms gap
    second = mono[second_start : second_start + 384]
    assert max(abs(v) for v in second) >= 15000
    gap = mono[48000 + 384 : second_start]
    assert max(abs(v) for v in gap) < 4000  # only the bed between the pair
    # k=1 click at 3 s has a 60 ms gap
    third_pair = 3 * 48000 + round(0.060 * 48000)
    assert max(abs(v) for v in mono[third_pair : third_pair + 384]) >= 15000


def test_channels_are_identical_and_wav_header_is_right():
    wav = ct.render_wav(duration_s=2)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (2, 2, 48000, 96000)
    assert all(left == right for left, right in struct.iter_unpack("<hh", ct.render_pcm(duration_s=2)))


def test_render_is_deterministic():
    assert ct.render_pcm(duration_s=3) == ct.render_pcm(duration_s=3)


def test_write_wav_streams_exactly_the_rendered_track():
    for duration, rate in ((7, 48000), (3, 44100)):
        out = io.BytesIO()
        ct.write_wav(out, duration_s=duration, sample_rate=rate)
        assert out.getvalue() == ct.render_wav(duration_s=duration, sample_rate=rate)


def test_write_wav_size_is_known_up_front():
    out = io.BytesIO()
    ct.write_wav(out, duration_s=4)
    assert len(out.getvalue()) == ct.wav_size(duration_s=4)


def test_the_track_carries_a_readable_title_for_music_assistant():
    """MA names a URL item after its title tag (and keeps an untagged one a sound effect without artist/album)."""
    wav = ct.render_wav(duration_s=2)
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    info = wav.index(b"LIST")
    assert wav[info + 8 : info + 12] == b"INFO" and wav.index(b"INAM") > info
    assert ct.TITLE.encode() in wav and b"IART" not in wav
    assert info < wav.index(b"data")  # before the samples, where a streaming reader finds it
    with wave.open(io.BytesIO(wav)) as w:
        assert w.getnframes() == 96000
    assert int.from_bytes(wav[4:8], "little") == len(wav) - 8
