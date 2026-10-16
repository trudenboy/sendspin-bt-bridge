"""The listening sounds of manual tuning (tuning by ear); the measurement keeps the click track.

The click track (``click_track``) is made for the phone's estimator: chirp pairs every 2 s whose gap
encodes the click's index. By ear its pair sounds like a flam, 2 s is sparse and a thin 2-6 kHz tick
is hard to place. These tracks are made for the ear instead:

* ``clicks``: one woodblock-like click a second, no pairs, peaking at -12 dBFS. A resonant body at
  0.95/1.65/2.5 kHz with a short band-passed noise attack: its energy sits within 0.5-5 kHz, where
  small Bluetooth speakers play it, and its sharp onset is easy to place.
* ``accent``: the same, with every 4th click 6 dB louder and pitched up by half (a fifth): offsets of
  hundreds of milliseconds are told apart from a whole-click (1 s) slip.
* ``noise``: pink noise at -18 dBFS RMS with a 2 s fade in, for the fine alignment of two speakers
  from midway between them (a centred, solid image when aligned; a phasey, hollow comb when not).

Every track is deterministic (seeded), 48 kHz 16-bit stereo with both channels identical, as long as
the click track, and titled for Music Assistant like it.
"""

from __future__ import annotations

import functools
import math
import random
import struct
import sys
from array import array
from typing import IO, Literal

from sendspin_sync import click_track as ct

Kind = Literal["clicks", "accent", "noise"]
KINDS: tuple[Kind, ...] = ("clicks", "accent", "noise")
TITLES: dict[Kind, str] = {
    "clicks": "Sendspin sync clicks (listening)",
    "accent": "Sendspin sync clicks (accent)",
    "noise": "Sendspin sync clicks (noise)",
}

SAMPLE_RATE = ct.SAMPLE_RATE
DURATION_S = ct.DURATION_S
LEAD_IN_S = 1.0
PERIOD_S = 1.0
ACCENT_EVERY = 4

# -- the click -----------------------------------------------------------------------------------
CLICK_MS = 40.0
CLICK_FADE_MS = 10.0  # the tail tapers to silence: no step at the end of the click
CLICK_PEAK_DBFS = -12.0
ACCENT_PEAK_DBFS = -6.0
ACCENT_PITCH = 1.5
# the body: (frequency Hz, decay time constant ms, amplitude) of each damped resonance
BODY_MODES = ((950.0, 7.0, 1.0), (1650.0, 5.0, 0.6), (2500.0, 3.5, 0.4))
# the attack: a short noise burst band-passed around ATTACK_HZ
ATTACK_HZ = 3000.0
ATTACK_Q = 0.9
ATTACK_TAU_MS = 0.5
ATTACK_GAIN = 0.6
CLICK_SEED = 1016

# -- the noise -----------------------------------------------------------------------------------
NOISE_RMS_DBFS = -18.0
NOISE_FADE_S = 2.0
NOISE_END_FADE_S = 0.5
NOISE_LOOP_S = 10.0  # a seamless loop, repeated: rendering 3 minutes sample by sample would take seconds
NOISE_HIGHPASS_HZ = 100.0  # below what small speakers play: no headroom spent on it
NOISE_SEED = 20261016


def _dbfs(db: float) -> float:
    return float(10 ** (db / 20))


def _bandpass(x: list[float], sample_rate: int, f0: float, q: float) -> list[float]:
    """RBJ band-pass biquad (0 dB at *f0*), direct form I."""
    w0 = 2 * math.pi * f0 / sample_rate
    alpha = math.sin(w0) / (2 * q)
    a0 = 1 + alpha
    b0, b2 = alpha / a0, -alpha / a0
    a1, a2 = -2 * math.cos(w0) / a0, (1 - alpha) / a0
    out: list[float] = []
    x1 = x2 = y1 = y2 = 0.0
    for v in x:
        y = b0 * v + b2 * x2 - a1 * y1 - a2 * y2
        x2, x1 = x1, v
        y2, y1 = y1, y
        out.append(y)
    return out


@functools.lru_cache(maxsize=8)
def _click(sample_rate: int, accented: bool) -> tuple[float, ...]:
    n = round(sample_rate * CLICK_MS / 1000)
    fade = round(sample_rate * CLICK_FADE_MS / 1000)
    pitch = ACCENT_PITCH if accented else 1.0
    rng = random.Random(CLICK_SEED)
    tau_a = ATTACK_TAU_MS / 1000
    burst = [(2 * rng.random() - 1) * math.exp(-(i / sample_rate) / tau_a) for i in range(n)]
    attack = _bandpass(burst, sample_rate, ATTACK_HZ * pitch, ATTACK_Q)
    peak_attack = max(abs(v) for v in attack) or 1.0
    out = []
    for i in range(n):
        t = i / sample_rate
        body = sum(a * math.exp(-t / (tau / 1000)) * math.sin(2 * math.pi * f * pitch * t) for f, tau, a in BODY_MODES)
        v = body + ATTACK_GAIN * attack[i] / peak_attack
        if i >= n - fade:
            v *= 0.5 + 0.5 * math.cos(math.pi * (i - (n - fade) + 1) / fade)
        out.append(v)
    peak = max(abs(v) for v in out)
    gain = _dbfs(ACCENT_PEAK_DBFS if accented else CLICK_PEAK_DBFS) / peak
    return tuple(v * gain for v in out)


def click(sample_rate: int = SAMPLE_RATE, *, accented: bool = False) -> list[float]:
    """One click (full scale = 1.0), peaking at -12 dBFS (an accented one at -6 dBFS)."""
    return list(_click(sample_rate, accented))


def click_onsets(duration_s: float = DURATION_S, *, accent: bool) -> list[tuple[float, bool]]:
    """(time, accented) of every click: one a second after a 1 s lead-in; with *accent*, every 4th."""
    out: list[tuple[float, bool]] = []
    t, k = LEAD_IN_S, 0
    while t + PERIOD_S / 2 <= duration_s:
        out.append((t, accent and k % ACCENT_EVERY == 0))
        t += PERIOD_S
        k += 1
    return out


def _int16(v: float) -> int:
    return int(max(-32767, min(32767, round(v * 32767))))


# -- the noise -----------------------------------------------------------------------------------


@functools.lru_cache(maxsize=4)
def _noise_loop(sample_rate: int) -> array[int]:
    """One seamless loop of pink noise as int16 at -18 dBFS RMS.

    White noise through Paul Kellet's pink filter and a one-pole high-pass, run twice over the same
    white noise: the second pass starts from the state the first one ended in, so the loop's end
    flows into its start."""
    n = round(NOISE_LOOP_S * sample_rate)
    rng = random.Random(NOISE_SEED)
    white = [2 * rng.random() - 1 for _ in range(n)]
    hp = math.exp(-2 * math.pi * NOISE_HIGHPASS_HZ / sample_rate)
    b0 = b1 = b2 = b3 = b4 = b5 = b6 = 0.0
    prev_in = prev_out = 0.0
    out = [0.0] * n
    for _pass in range(2):
        for i, w in enumerate(white):
            b0 = 0.99886 * b0 + w * 0.0555179
            b1 = 0.99332 * b1 + w * 0.0750759
            b2 = 0.96900 * b2 + w * 0.1538520
            b3 = 0.86650 * b3 + w * 0.3104856
            b4 = 0.55000 * b4 + w * 0.5329522
            b5 = -0.7616 * b5 - w * 0.0168980
            pink = b0 + b1 + b2 + b3 + b4 + b5 + b6 + w * 0.5362
            b6 = w * 0.115926
            prev_out = hp * (prev_out + pink - prev_in)
            prev_in = pink
            out[i] = prev_out
    rms = math.sqrt(sum(v * v for v in out) / n)
    gain = _dbfs(NOISE_RMS_DBFS) / rms
    return array("h", (_int16(v * gain) for v in out))


def _noise_gain(i: int, total: int, sample_rate: int) -> float:
    """The fade in (raised cosine) and the short fade out at the end; 1.0 in between."""
    fade_in = round(NOISE_FADE_S * sample_rate)
    fade_out = round(NOISE_END_FADE_S * sample_rate)
    g = 1.0
    if i < fade_in:
        g = 0.5 - 0.5 * math.cos(math.pi * i / fade_in)
    if i >= total - fade_out:
        g = min(g, 0.5 - 0.5 * math.cos(math.pi * (total - 1 - i) / fade_out))
    return g


def _noise_sample(loop: array[int], i: int, total: int, sample_rate: int) -> int:
    return round(loop[i % len(loop)] * _noise_gain(i, total, sample_rate))


# -- rendering -----------------------------------------------------------------------------------


def _click_starts(kind: Kind, duration_s: float, sample_rate: int) -> list[tuple[int, bool]]:
    return [(round(t * sample_rate), acc) for t, acc in click_onsets(duration_s, accent=kind == "accent")]


def render_pcm(kind: Kind, *, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE) -> bytes:
    """The track's samples (16-bit stereo), computed sample by sample (the reference for ``write_wav``)."""
    total = round(duration_s * sample_rate)
    mono = [0] * total
    if kind == "noise":
        loop = _noise_loop(sample_rate)
        mono = [_noise_sample(loop, i, total, sample_rate) for i in range(total)]
    else:
        for start, acc in _click_starts(kind, duration_s, sample_rate):
            for i, v in enumerate(_click(sample_rate, acc)):
                if start + i < total:
                    mono[start + i] = _int16(v)
    frames = bytearray(total * 4)
    for i, s in enumerate(mono):
        struct.pack_into("<hh", frames, i * 4, s, s)
    return bytes(frames)


def wav_header(kind: Kind, *, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE) -> bytes:
    return ct.wav_header(duration_s=duration_s, sample_rate=sample_rate, title=TITLES[kind])


def wav_size(kind: Kind, *, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE) -> int:
    return len(wav_header(kind, duration_s=duration_s, sample_rate=sample_rate)) + round(duration_s * sample_rate) * 4


def render_wav(kind: Kind, *, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE) -> bytes:
    return wav_header(kind, duration_s=duration_s, sample_rate=sample_rate) + render_pcm(
        kind, duration_s=duration_s, sample_rate=sample_rate
    )


def _stereo(mono: array[int]) -> bytes:
    frames = array("h", bytes(len(mono) * 4))
    frames[0::2] = mono
    frames[1::2] = mono
    if sys.byteorder == "big":
        frames.byteswap()
    return frames.tobytes()


def write_wav(
    out: IO[bytes],
    kind: Kind,
    *,
    duration_s: float = DURATION_S,
    sample_rate: int = SAMPLE_RATE,
    block_s: float = 1.0,
) -> None:
    """Write the same bytes as :func:`render_wav` block by block (memory stays at one block): clicks touch
    only their samples, and the noise copies its loop, computing samples only in the fades."""
    total = round(duration_s * sample_rate)
    block = max(1, round(block_s * sample_rate))
    out.write(wav_header(kind, duration_s=duration_s, sample_rate=sample_rate))
    if kind == "noise":
        loop = _noise_loop(sample_rate)
        fade_in = round(NOISE_FADE_S * sample_rate)
        fade_out = total - round(NOISE_END_FADE_S * sample_rate)
        for lo in range(0, total, block):
            hi = min(total, lo + block)
            if lo >= fade_in and hi <= fade_out:
                start = lo % len(loop)
                mono = loop[start : start + (hi - lo)]
                while len(mono) < hi - lo:
                    mono += loop[: hi - lo - len(mono)]
            else:
                mono = array("h", (_noise_sample(loop, i, total, sample_rate) for i in range(lo, hi)))
            out.write(_stereo(mono))
        return
    starts = _click_starts(kind, duration_s, sample_rate)
    longest = max(len(_click(sample_rate, False)), len(_click(sample_rate, True)))
    first = 0
    for lo in range(0, total, block):
        hi = min(total, lo + block)
        mono = array("h", bytes((hi - lo) * 2))
        while first < len(starts) and starts[first][0] + longest <= lo:
            first += 1
        for start, acc in starts[first:]:
            if start >= hi:
                break
            body = _click(sample_rate, acc)
            for i in range(max(0, lo - start), min(len(body), hi - start)):
                mono[start + i - lo] = _int16(body[i])
        out.write(_stereo(mono))
