"""Decode a phone's recording (m4a/AAC, wav, webm/opus, mp3, …) to the estimator's input.

The phone capture page uploads whatever the phone produced: a file from its recorder app (plain-HTTP
installs cannot use the microphone in the browser) or the browser's own MediaRecorder output. PyAV
(FFmpeg, a dependency already) decodes it in memory and resamples to mono int16 at DECODE_RATE.

The upload comes from a page anyone holding the link can use, so FFmpeg never gets to choose what to
read (security review): the container is recognised from its first bytes and only plain audio
containers are decoded, with that demuxer forced (``format=``), FFmpeg's ``format_whitelist`` set to
the same audio demuxers and ``protocol_whitelist`` to none. Playlists and reference formats (HLS,
ffconcat, SDP, image sequences, URLs) are refused before FFmpeg sees a byte, and decoding always
reads from memory: nothing is fetched from the network or read from disk.
"""

from __future__ import annotations

import io
import logging

import av
from av.error import FFmpegError

from sendspin_sync.ports import ControlError

logger = logging.getLogger(__name__)

#: Recognised containers: their magic bytes -> the FFmpeg demuxer forced for them.
_MOV = "mov,mp4,m4a,3gp,3g2,mj2"
_ALLOWED_DEMUXERS = (_MOV, "wav", "ogg", "matroska,webm", "mp3", "aac", "flac", "amr")
#: Codecs decoded inside those containers (a small surface: what phones and browsers record).
_ALLOWED_CODECS = {"aac", "opus", "vorbis", "mp3", "flac", "alac", "amr_wb"}
_SUPPORTED = "m4a/AAC, 3GP, WAV, WebM/Opus, OGG, MP3, FLAC or AMR-WB"
_FORMAT_WHITELIST = ",".join(_ALLOWED_DEMUXERS)


def sniff_format(data: bytes) -> str | None:
    """The FFmpeg demuxer for *data* when its first bytes are a plain audio container, else None."""
    head = data[:16]
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return _MOV
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "wav"
    if head[:4] == b"OggS":
        return "ogg"
    if head[:4] == b"\x1aE\xdf\xa3":
        return "matroska,webm"
    if head[:4] == b"fLaC":
        return "flac"
    if head[:5] == b"#!AMR":  # AMR-NB "#!AMR\n", AMR-WB "#!AMR-WB\n"
        return "amr"
    if head[:3] == b"ID3":
        return "mp3"
    if len(head) >= 2 and head[0] == 0xFF:
        if head[1] & 0xF6 == 0xF0:  # ADTS sync word, layer 0: AAC
            return "aac"
        if head[1] & 0xE0 == 0xE0 and head[1] & 0x06:  # MPEG audio frame, layer I-III
            return "mp3"
    return None


#: The rate the estimator gets: the chirps are 2-6 kHz, so 16 kHz keeps them and the pure-Python
#: estimator fast on a long recording.
DECODE_RATE = 16000


def decode_to_mono(data: bytes, *, max_seconds: float, rate: int = DECODE_RATE) -> tuple[bytes, int]:
    """int16 little-endian mono PCM at *rate* and the rate; ``recording_invalid`` when *data* is not
    audio FFmpeg can read, has no audio stream, or lasts longer than *max_seconds*."""
    limit = int(max_seconds * rate)
    fmt = sniff_format(data)
    if fmt is None:
        raise ControlError("recording_invalid", f"this file type cannot be read here; record as {_SUPPORTED}")
    out = bytearray()
    options = {"protocol_whitelist": "none", "format_whitelist": _FORMAT_WHITELIST}
    try:
        with av.open(io.BytesIO(data), mode="r", format=fmt, options=options) as container:
            if not container.streams.audio:
                raise ControlError("recording_invalid", "the file has no audio")
            stream = container.streams.audio[0]
            codec = stream.codec_context.name
            if codec == "amr_nb":
                raise ControlError(
                    "recording_invalid",
                    "AMR-NB (phone-call quality) cuts off the clicks; set the recorder to a higher quality (m4a/AAC)",
                )
            if codec not in _ALLOWED_CODECS and not codec.startswith("pcm_"):
                raise ControlError(
                    "recording_invalid", f"audio coded as {codec} cannot be read here; record as {_SUPPORTED}"
                )
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
        raise ControlError(
            "recording_invalid", f"the file could not be read as {fmt.split(',')[0]} audio; record as {_SUPPORTED}"
        ) from exc
    if len(out) // 2 > limit:
        raise ControlError("recording_invalid", f"the recording is too long (over {max_seconds:g} s)")
    if not out:
        raise ControlError("recording_invalid", "the recording is empty")
    return bytes(out), rate
