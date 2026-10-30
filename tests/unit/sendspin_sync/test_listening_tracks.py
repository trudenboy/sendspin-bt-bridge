"""The listening sounds of manual tuning: one click a second (optionally every 4th accented), or pink noise."""

import cmath
import io
import itertools
import math
import struct
import wave

import pytest

from sendspin_sync import click_track as ct
from sendspin_sync import listening_tracks as lt

RATE = 48000


def _mono(pcm: bytes) -> list[int]:
    return [left for left, _right in struct.iter_unpack("<hh", pcm)]


def _db(x: float) -> float:
    return 20 * math.log10(x)


def _fft(x: list[complex]) -> list[complex]:
    n = len(x)
    if n == 1:
        return x
    even, odd = _fft(x[0::2]), _fft(x[1::2])
    tw = [cmath.exp(-2j * math.pi * k / n) * odd[k] for k in range(n // 2)]
    return [even[k] + tw[k] for k in range(n // 2)] + [even[k] - tw[k] for k in range(n // 2)]


def _power(samples: list[float], n: int = 8192) -> list[float]:
    """Power per bin (0 .. n/2) of *samples*, zero-padded or cut to *n*."""
    padded = [complex(v) for v in samples[:n]] + [0j] * max(0, n - len(samples))
    return [abs(c) ** 2 for c in _fft(padded)[: n // 2 + 1]]


def _band(power: list[float], lo: float, hi: float, n: int = 8192) -> float:
    return sum(p for k, p in enumerate(power) if lo <= k * RATE / n < hi)


def _onsets(mono: list[int], threshold: int = 300) -> list[int]:
    """First sample above *threshold* after at least half a second of quiet."""
    found: list[int] = []
    for i, v in enumerate(mono):
        if abs(v) > threshold and (not found or i - found[-1] > RATE // 2):
            found.append(i)
    return found


# -- clicks ----------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["clicks", "accent"])
def test_exactly_one_click_a_second_after_a_one_second_lead_in(kind):
    mono = _mono(lt.render_pcm(kind, duration_s=10))
    assert max(abs(v) for v in mono[:RATE]) == 0
    onsets = _onsets(mono)
    assert len(onsets) == 9
    assert all(b - a == RATE for a, b in itertools.pairwise(onsets))
    assert abs(onsets[0] - RATE) <= 2


def test_no_pairs_each_click_is_silent_well_before_the_next():
    mono = _mono(lt.render_pcm("clicks", duration_s=4))
    # the click's body has died out after 60 ms: nothing that could sound like a second hit (a flam)
    for start in (RATE, 2 * RATE, 3 * RATE):
        assert max(abs(v) for v in mono[start + round(0.06 * RATE) : start + RATE - 1]) == 0


def test_plain_clicks_peak_near_minus_12_dbfs():
    mono = _mono(lt.render_pcm("clicks", duration_s=10))
    for k in range(9):
        start = RATE * (k + 1)
        peak = max(abs(v) for v in mono[start : start + RATE // 2]) / 32767
        assert -12.5 <= _db(peak) <= -11.5


def test_every_fourth_click_is_accented_six_db_louder():
    mono = _mono(lt.render_pcm("accent", duration_s=10))
    peaks = [max(abs(v) for v in mono[RATE * (k + 1) : RATE * (k + 1) + RATE // 2]) / 32767 for k in range(9)]
    for k, peak in enumerate(peaks):
        if k % 4 == 0:
            assert -6.5 <= _db(peak) <= -5.5, k
        else:
            assert -12.5 <= _db(peak) <= -11.5, k
    assert lt.click_onsets(10.0, accent=True)[:5] == [
        (1.0, True),
        (2.0, False),
        (3.0, False),
        (4.0, False),
        (5.0, True),
    ]
    assert all(not accented for _t, accented in lt.click_onsets(10.0, accent=False))


@pytest.mark.parametrize("accented", [False, True])
def test_click_energy_sits_where_small_speakers_play_it(accented):
    power = _power(lt.click(RATE, accented=accented))
    total = sum(power)
    assert _band(power, 500, 5000) / total >= 0.85
    assert _band(power, 0, 300) / total <= 0.03  # nothing a small speaker cannot reproduce
    assert _band(power, 8000, RATE / 2 + 1) / total <= 0.03  # not a thin high tick


def test_the_accent_is_higher_pitched():
    def centroid(samples):
        power = _power(samples)
        return sum(k * RATE / 8192 * p for k, p in enumerate(power)) / sum(power)

    assert centroid(lt.click(RATE, accented=True)) > 1.3 * centroid(lt.click(RATE, accented=False))


def test_a_click_starts_at_once_and_ends_in_silence():
    body = lt.click(RATE)
    attack = max(range(len(body)), key=lambda i: abs(body[i]))
    assert attack < round(0.003 * RATE)  # the peak within 3 ms: a sharp, easy-to-place onset
    assert abs(body[-1]) < 1e-3 and abs(body[0]) < 0.2 * max(abs(v) for v in body)


# -- noise -----------------------------------------------------------------------------------


def test_noise_is_pink_at_minus_18_dbfs_rms_without_clipping():
    mono = [v / 32767 for v in _mono(lt.render_pcm("noise", duration_s=lt.NOISE_FADE_S + 4))]
    steady = mono[round((lt.NOISE_FADE_S + 0.5) * RATE) :]
    rms = math.sqrt(sum(v * v for v in steady) / len(steady))
    assert -18.5 <= _db(rms) <= -17.5
    assert _db(max(abs(v) for v in steady)) <= -3.0
    # pink: about the same energy in every octave (white noise would give +9 dB from 250-500 to 2-4 kHz)
    power = [0.0] * 4097
    for i in range(8):
        for k, p in enumerate(_power(steady[i * 8192 : (i + 1) * 8192])):
            power[k] += p
    low, high = _band(power, 250, 500), _band(power, 2000, 4000)
    assert abs(10 * math.log10(high / low)) <= 2.5


def test_noise_fades_in_smoothly():
    mono = [v / 32767 for v in _mono(lt.render_pcm("noise", duration_s=lt.NOISE_FADE_S + 1))]
    assert mono[0] == 0

    def rms(a, b):
        part = mono[round(a * RATE) : round(b * RATE)]
        return math.sqrt(sum(v * v for v in part) / len(part))

    steady = rms(lt.NOISE_FADE_S, lt.NOISE_FADE_S + 1)
    assert rms(0, 0.05) < steady / 10
    assert rms(0, 0.05) < rms(lt.NOISE_FADE_S / 2 - 0.05, lt.NOISE_FADE_S / 2 + 0.05) < steady


def test_the_noise_loop_has_no_seam():
    loop = round(lt.NOISE_LOOP_S * RATE)
    mono = _mono(lt.render_pcm("noise", duration_s=lt.NOISE_LOOP_S * 2 + 1))
    steps = [abs(mono[i + 1] - mono[i]) for i in range(loop - 2000, loop + 2000)]
    seam = abs(mono[loop] - mono[loop - 1])
    assert seam <= sorted(steps)[int(len(steps) * 0.999)]


# -- every track -----------------------------------------------------------------------------


@pytest.mark.parametrize("kind", lt.KINDS)
def test_tracks_are_deterministic_identical_on_both_channels_and_titled(kind):
    assert lt.render_pcm(kind, duration_s=3) == lt.render_pcm(kind, duration_s=3)
    assert all(left == right for left, right in struct.iter_unpack("<hh", lt.render_pcm(kind, duration_s=3)))
    wav = lt.render_wav(kind, duration_s=2)
    with wave.open(io.BytesIO(wav)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (2, 2, RATE, 2 * RATE)
    assert lt.TITLES[kind].encode() in wav and b"IART" not in wav
    assert lt.TITLES[kind].startswith("Sendspin sync clicks (") and lt.TITLES[kind] != ct.TITLE


@pytest.mark.parametrize("kind", lt.KINDS)
def test_write_wav_streams_exactly_the_rendered_track(kind):
    for duration, rate in ((7, 48000), (3, 44100)):
        out = io.BytesIO()
        lt.write_wav(out, kind, duration_s=duration, sample_rate=rate)
        assert out.getvalue() == lt.render_wav(kind, duration_s=duration, sample_rate=rate)
        assert len(out.getvalue()) == lt.wav_size(kind, duration_s=duration, sample_rate=rate)


def test_tracks_last_as_long_as_the_click_track():
    assert lt.DURATION_S == ct.DURATION_S


def test_the_measurement_track_is_unchanged():
    """The phone's estimator depends on it: the same bytes and title as before the listening sounds."""
    import hashlib

    assert ct.TITLE == "Sendspin sync clicks"
    digest = hashlib.sha256(ct.render_wav(duration_s=12)).hexdigest()
    assert digest == MEASURE_SHA256


MEASURE_SHA256 = "5f9989cfe7659092e4b84ef8235ec4c3aed99e7f06e82d36dd6acb5a506b22be"
