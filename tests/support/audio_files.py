"""In-memory audio files made with PyAV (no committed recordings, no numpy)."""

from __future__ import annotations

import io
import struct

import av


def _frame(samples: list[float], *, codec: str, rate: int, layout: str) -> av.AudioFrame:
    """A frame from floats in -1..1 (no numpy: the planes are filled with packed bytes)."""
    channels = 1 if layout == "mono" else 2
    fmt = "fltp" if codec == "aac" else "s16"
    frame = av.AudioFrame(format=fmt, layout=layout, samples=len(samples))
    frame.sample_rate = rate
    if fmt == "fltp":
        data = struct.pack(f"<{len(samples)}f", *samples)
        for c in range(channels):
            frame.planes[c].update(data + bytes(frame.planes[c].buffer_size - len(data)))
    else:
        ints = [int(v * 32767) for v in samples for _ in range(channels)]
        data = struct.pack(f"<{len(ints)}h", *ints)
        frame.planes[0].update(data + bytes(frame.planes[0].buffer_size - len(data)))
    return frame


def encode_audio(samples: list[float], *, fmt: str, codec: str, rate: int, layout: str = "mono") -> bytes:
    """Encode *samples* (floats at *rate*) into an in-memory file, e.g. an m4a with AAC."""
    buf = io.BytesIO()
    with av.open(buf, "w", format=fmt) as out:
        stream = out.add_stream(codec, rate=rate, layout=layout)
        step = stream.codec_context.frame_size or 1024
        for i in range(0, len(samples), step):
            chunk = samples[i : i + step]
            if codec == "aac" and len(chunk) < step:
                chunk = chunk + [0.0] * (step - len(chunk))
            frame = _frame(chunk, codec=codec, rate=rate, layout=layout)
            frame.pts = i
            for packet in stream.encode(frame):
                out.mux(packet)
        for packet in stream.encode(None):
            out.mux(packet)
    return buf.getvalue()


def pcm_to_m4a(pcm: bytes, rate: int) -> bytes:
    """int16 mono PCM as an m4a (AAC) file, as a phone's recorder app would save it."""
    n = len(pcm) // 2
    samples = [v / 32768 for v in struct.unpack(f"<{n}h", pcm)]
    return encode_audio(samples, fmt="mp4", codec="aac", rate=rate)
