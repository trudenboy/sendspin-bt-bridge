"""Decode a phone's recording (m4a/AAC, wav, webm/opus, mp3, …) to the estimator's input.

The phone capture page uploads whatever the phone produced: a file from its recorder app (plain-HTTP
installs cannot use the microphone in the browser) or the browser's own MediaRecorder output. PyAV
(FFmpeg, a dependency already) decodes it in memory and resamples to mono int16 at DECODE_RATE.
"""

from __future__ import annotations

import io
import logging

import av
from av.error import FFmpegError

from sendspin_sync.ports import ControlError

logger = logging.getLogger(__name__)

#: The rate the estimator gets: the chirps are 2-6 kHz, so 16 kHz keeps them and the pure-Python
#: estimator fast on a long recording.
DECODE_RATE = 16000


def decode_to_mono(data: bytes, *, max_seconds: float, rate: int = DECODE_RATE) -> tuple[bytes, int]:
    """int16 little-endian mono PCM at *rate* and the rate; ``recording_invalid`` when *data* is not
    audio FFmpeg can read, has no audio stream, or lasts longer than *max_seconds*."""
    limit = int(max_seconds * rate)
    out = bytearray()
    try:
        with av.open(io.BytesIO(data), mode="r") as container:
            if not container.streams.audio:
                raise ControlError("recording_invalid", "the file has no audio")
            stream = container.streams.audio[0]
            resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
            for frame in container.decode(stream):
                for chunk in resampler.resample(frame):
                    out += bytes(chunk.planes[0])[: chunk.samples * 2]
                if len(out) // 2 > limit:
                    raise ControlError("recording_invalid", f"the recording is too long (over {max_seconds:g} s)")
            for chunk in resampler.resample(None):
                out += bytes(chunk.planes[0])[: chunk.samples * 2]
    except ControlError:
        raise
    except (FFmpegError, ValueError, OSError) as exc:
        logger.info("A sync recording could not be decoded: %s", exc)
        raise ControlError("recording_invalid", "the file is not a recording this bridge can read") from exc
    if len(out) // 2 > limit:
        raise ControlError("recording_invalid", f"the recording is too long (over {max_seconds:g} s)")
    if not out:
        raise ControlError("recording_invalid", "the recording is empty")
    return bytes(out), rate
