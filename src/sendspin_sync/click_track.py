"""The click track: chirp pairs every 2 s whose gap encodes the click index in a 10 s cycle."""

from __future__ import annotations

import io
import math
import struct
import wave

SAMPLE_RATE = 48000
DURATION_S = 180
LEAD_IN_S = 1.0
PERIOD_S = 2.0
CYCLE_CLICKS = 5
CYCLE_S = PERIOD_S * CYCLE_CLICKS
CHIRP_MS = 8
CHIRP_F0 = 2000.0
CHIRP_F1 = 6000.0
GAP_BASE_MS = 40
GAP_STEP_MS = 20
PEAK = 0.25


def gap_ms(k: int) -> int:
    return GAP_BASE_MS + GAP_STEP_MS * (k % CYCLE_CLICKS)


def chirp(sample_rate: int) -> list[float]:
    n = max(2, round(sample_rate * CHIRP_MS / 1000))
    dur = n / sample_rate
    rate = (CHIRP_F1 - CHIRP_F0) / dur
    out = []
    for i in range(n):
        t = i / sample_rate
        window = 0.5 - 0.5 * math.cos(2 * math.pi * i / (n - 1))
        out.append(window * math.sin(2 * math.pi * (CHIRP_F0 * t + 0.5 * rate * t * t)))
    return out


def click_times(duration_s: float = DURATION_S) -> list[tuple[float, int]]:
    times: list[tuple[float, int]] = []
    t, k = LEAD_IN_S, 0
    while t + PERIOD_S / 2 <= duration_s:
        times.append((t, k))
        t += PERIOD_S
        k = (k + 1) % CYCLE_CLICKS
    return times


def render_pcm(*, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE) -> bytes:
    total = round(duration_s * sample_rate)
    mono = [0.0] * total
    template = chirp(sample_rate)
    for t, k in click_times(duration_s):
        for start in (round(t * sample_rate), round((t + gap_ms(k) / 1000) * sample_rate)):
            for i, v in enumerate(template):
                if start + i < total:
                    mono[start + i] = v
    frames = bytearray(total * 4)
    for i, v in enumerate(mono):
        s = int(max(-32767, min(32767, round(v * PEAK * 32767))))
        struct.pack_into("<hh", frames, i * 4, s, s)
    return bytes(frames)


def render_wav(*, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(render_pcm(duration_s=duration_s, sample_rate=sample_rate))
    return out.getvalue()
