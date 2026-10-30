"""The click track: chirp pairs every 2 s whose gap encodes the click index in a 10 s cycle."""

from __future__ import annotations

import math
import struct
from typing import IO

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
PEAK = 0.5
# A continuous pink-noise bed at -30 dBFS under the chirps. Speakers with a slow un-mute ramp
# (Lenco LS-500: an 8 ms chirp after silence is never heard, a continuous tone is) stay awake on
# the bed, and the chirps sit ~24 dB above it, so the phone's estimator still sees them cleanly.
BED_PEAK = 0.0316
BED_SEED = 2024
# The name Music Assistant shows for the track (its LIST/INFO "INAM" tag). A title alone keeps the
# item a sound effect (MA treats a URL with artist/album tags as music), and the queue reads
# "Sendspin sync clicks" instead of the URL's file hash.
TITLE = "Sendspin sync clicks"


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
    mono = [_bed(i) for i in range(total)]
    template = [_sample(v) for v in chirp(sample_rate)]
    for t, k in click_times(duration_s):
        for start in (round(t * sample_rate), round((t + gap_ms(k) / 1000) * sample_rate)):
            for i, v in enumerate(template):
                if start + i < total:
                    mono[start + i] = max(-32767, min(32767, mono[start + i] + v))
    frames = bytearray(total * 4)
    for i, v in enumerate(mono):
        struct.pack_into("<hh", frames, i * 4, v, v)
    return bytes(frames)


def _sample(v: float) -> int:
    return int(max(-32767, min(32767, round(v * PEAK * 32767))))


def _noise(i: int) -> float:
    """Deterministic white noise in [-1, 1) from the sample index alone."""
    x = (i * 1103515245 + 12345) & 0x7FFFFFFF
    x ^= x >> 13
    x = (x * 2654435761) & 0x7FFFFFFF
    return (x & 0xFFFF) / 32768.0 - 1.0


def _bed(i: int) -> int:
    """One sample of the continuous bed, computed from near indices alone.

    A 4-tap moving average keeps the bed low-frequency (a soft hiss, not white noise in the
    chirps' 2-6 kHz band), and being index-driven it stays block-independent: the streaming writer
    and ``render_pcm`` produce the same bytes.
    """
    smoothed = (_noise(i) + _noise(i - 1) + _noise(i - 2) + _noise(i - 3)) / 4.0
    return round(smoothed * BED_PEAK * 32767)


def _info_chunk(title: str = TITLE) -> bytes:
    """A RIFF LIST/INFO chunk naming the track (NUL-terminated, padded to an even size)."""
    name = title.encode("ascii") + b"\0"
    if len(name) % 2:
        name += b"\0"
    inam = b"INAM" + struct.pack("<I", len(name)) + name
    body = b"INFO" + inam
    return b"LIST" + struct.pack("<I", len(body)) + body


def wav_header(*, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE, title: str = TITLE) -> bytes:
    """RIFF header, fmt (16-bit stereo PCM), the title, then the data chunk header."""
    data_size = round(duration_s * sample_rate) * 4
    fmt = struct.pack("<HHIIHH", 1, 2, sample_rate, sample_rate * 4, 4, 16)
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + _info_chunk(title) + b"data" + struct.pack("<I", data_size)
    return b"RIFF" + struct.pack("<I", 4 + len(chunks) + data_size) + b"WAVE" + chunks


def wav_size(*, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE) -> int:
    """Bytes of the WAV file: the header (with the title) and 16-bit stereo frames."""
    return len(wav_header(duration_s=duration_s, sample_rate=sample_rate)) + round(duration_s * sample_rate) * 4


def write_wav(
    out: IO[bytes], *, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE, block_s: float = 1.0
) -> None:
    """Write the same bytes as :func:`render_wav` block by block, touching only the chirps.

    Memory stays at one block, and the silence costs nothing to compute, so the full track
    takes a fraction of a second instead of seconds of per-sample Python.
    """
    total = round(duration_s * sample_rate)
    template = [_sample(v) for v in chirp(sample_rate)]
    starts = sorted(
        start
        for t, k in click_times(duration_s)
        for start in (round(t * sample_rate), round((t + gap_ms(k) / 1000) * sample_rate))
    )
    block = max(1, round(block_s * sample_rate))
    out.write(wav_header(duration_s=duration_s, sample_rate=sample_rate))
    first = 0  # first chirp that may still reach into the current block
    for lo in range(0, total, block):
        hi = min(total, lo + block)
        frames = bytearray((hi - lo) * 4)
        for j in range(hi - lo):
            v = _bed(lo + j)
            struct.pack_into("<hh", frames, j * 4, v, v)
        while first < len(starts) and starts[first] + len(template) <= lo:
            first += 1
        for start in starts[first:]:
            if start >= hi:
                break
            for i in range(max(0, lo - start), min(len(template), hi - start)):
                offset = (start + i - lo) * 4
                left, _right = struct.unpack_from("<hh", frames, offset)
                value = max(-32767, min(32767, left + template[i]))
                struct.pack_into("<hh", frames, offset, value, value)
        out.write(frames)


def render_wav(*, duration_s: float = DURATION_S, sample_rate: int = SAMPLE_RATE) -> bytes:
    return wav_header(duration_s=duration_s, sample_rate=sample_rate) + render_pcm(
        duration_s=duration_s, sample_rate=sample_rate
    )
