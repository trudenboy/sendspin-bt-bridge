"""The audio at the point where an output delay change takes effect in place.

The output keeps playing what it already has; from its end (the junction) the
timeline continues with the shift applied. ``x`` is the audio arriving after
the change, starting with the sample that would have followed the junction.
With a shift of ``k`` frames (positive: later, negative: earlier), output frame
``t`` after the junction is

    fade_out(t) * x[t]  +  fade_in(t - max(k, 0)) * x[t - k]

the continuation of what was playing, faded out, mixed with the shifted audio,
faded in. A later play time thus inserts ``k`` frames (silence between the two
fades), an earlier one skips ``-k`` frames, and the fades keep either from
clicking. Past the region the shifted audio ``x[t - k]`` plays untouched.
"""

from __future__ import annotations

import sys
from array import array
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sendspin_bridge.services.audio.player.player import PcmFormat

FADE_MS = 5


def fade_frames(sample_rate: int) -> int:
    """Frames in each fade (5 ms)."""
    return max(1, sample_rate * FADE_MS // 1000)


def frames_needed(shift_frames: int, fade: int) -> int:
    """How much of ``x`` the region needs: the faded continuation and, when
    skipping ahead, everything skipped up to the end of the fade-in."""
    return fade + max(0, -shift_frames)


def splice_region(
    head: bytes, ahead: bytes, *, shift_frames: int, fade: int, fmt: PcmFormat, fade_out: bool = True
) -> bytes:
    """The audio that follows the junction.

    ``head`` is ``x[:fade]`` (the continuation); ``ahead`` is
    ``x[-k : -k + fade]``, the audio due at the junction when skipping ahead
    (``k`` < 0; ignored otherwise). Only those two windows are read. The
    region is ``max(k, 0) + fade`` frames long, and ``x`` from frame
    ``frames_needed(k, fade)`` on follows it directly. Without ``fade_out``
    (the output is already silent at the junction) only the shifted audio
    fades in.
    """
    channels = fmt.channels
    frame_bytes = fmt.frame_bytes()
    window = fade * frame_bytes
    if len(head) < window or (shift_frames < 0 and len(ahead) < window):
        raise ValueError("not enough audio after the junction for the splice")
    h = _decode(head[:window], fmt.bit_depth)
    limit = (1 << (fmt.bit_depth - 1)) - 1

    def clamp(value: float) -> int:
        return max(-limit - 1, min(limit, round(value)))

    def ramp(samples: list[int], *, rising: bool) -> bytes:
        out = [0] * len(samples)
        for t in range(fade):
            gain = t / fade if rising else 1.0 - t / fade
            for c in range(channels):
                i = t * channels + c
                out[i] = clamp(gain * samples[i])
        return _encode(out, fmt.bit_depth)

    if shift_frames < 0:
        a = _decode(ahead[:window], fmt.bit_depth)
        out = [0] * (fade * channels)
        for t in range(fade):
            g_in = t / fade
            g_out = 1.0 - g_in if fade_out else 0.0
            for c in range(channels):
                i = t * channels + c
                out[i] = clamp(g_out * h[i] + g_in * a[i])
        return _encode(out, fmt.bit_depth)
    if shift_frames >= fade:
        faded_out = ramp(h, rising=False) if fade_out else bytes(window)
        return faded_out + bytes((shift_frames - fade) * frame_bytes) + ramp(h, rising=True)
    # A later shift shorter than the fade: the two overlap.
    length = shift_frames + fade
    out = [0] * (length * channels)
    for t in range(length):
        g_out = 1.0 - t / fade if fade_out and t < fade else 0.0
        src = t - shift_frames
        g_in = src / fade if src >= 0 else 0.0
        for c in range(channels):
            value = g_out * h[t * channels + c] if g_out else 0.0
            if g_in > 0.0:
                value += g_in * h[src * channels + c]
            out[t * channels + c] = clamp(value)
    return _encode(out, fmt.bit_depth)


def silence_after(tail: bytes, *, frames: int, fade: int, fmt: PcmFormat) -> bytes:
    """``frames`` of silence that start by fading out what was playing.

    The audio after the junction is not there yet, so the fade-out mirrors
    ``tail`` (the last audio handed on) around the junction: the waveform
    carries on from its last sample, without a step, and decays to silence
    over ``fade`` frames.
    """
    channels = fmt.channels
    frame_bytes = fmt.frame_bytes()
    have = min(len(tail) // frame_bytes, fade, frames)
    tail_samples = _decode(tail[len(tail) - have * frame_bytes :], fmt.bit_depth) if have else []
    out = [0] * (have * channels)
    for t in range(have):
        gain = 1.0 - t / fade
        src = (have - 1 - t) * channels
        for c in range(channels):
            out[t * channels + c] = round(gain * tail_samples[src + c])
    return _encode(out, fmt.bit_depth) + bytes((frames - have) * frame_bytes)


def fade_in(payload: bytes, *, done: int, fade: int, fmt: PcmFormat) -> bytes:
    """``payload`` faded in, ``done`` frames of the fade being behind it already."""
    channels = fmt.channels
    frames = len(payload) // fmt.frame_bytes()
    ramp = min(frames, max(0, fade - done))
    if ramp == 0:
        return payload
    head_bytes = ramp * fmt.frame_bytes()
    samples = _decode(payload[:head_bytes], fmt.bit_depth)
    for t in range(ramp):
        gain = (done + t) / fade
        for c in range(channels):
            i = t * channels + c
            samples[i] = round(gain * samples[i])
    return _encode(samples, fmt.bit_depth) + payload[head_bytes:]


def _decode(data: bytes, bit_depth: int) -> list[int]:
    if bit_depth == 24:
        return [int.from_bytes(data[i : i + 3], "little", signed=True) for i in range(0, len(data) - 2, 3)]
    values = array(_typecode(bit_depth), data)
    if sys.byteorder != "little":
        values.byteswap()
    return list(values)


def _encode(samples: list[int], bit_depth: int) -> bytes:
    if bit_depth == 24:
        return b"".join(v.to_bytes(3, "little", signed=True) for v in samples)
    values = array(_typecode(bit_depth), samples)
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def _typecode(bit_depth: int) -> str:
    if bit_depth == 16:
        return "h"
    if bit_depth == 32:
        return "i"
    raise ValueError(f"unsupported PCM bit depth: {bit_depth}")
