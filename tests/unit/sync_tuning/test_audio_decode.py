"""Phone uploads (m4a/aac, wav, …) decoded with PyAV to the estimator's mono PCM. Fixtures are made here."""

import math
import struct

import pytest

from sendspin_bridge.sync_tuning.audio_decode import DECODE_RATE, decode_to_mono
from sendspin_sync.ports import ControlError
from tests.support.audio_files import encode_audio


def _encode(fmt: str, codec: str, *, seconds: float = 2.0, rate: int = 44100, layout: str = "mono") -> bytes:
    n = int(seconds * rate)
    tone = [0.3 * math.sin(2 * math.pi * 1000 * i / rate) for i in range(n)]
    return encode_audio(tone, fmt=fmt, codec=codec, rate=rate, layout=layout)


def test_an_m4a_aac_recording_decodes_to_mono_pcm_at_the_estimators_rate():
    data = _encode("mp4", "aac", seconds=2.0, rate=44100)
    pcm, rate = decode_to_mono(data, max_seconds=30)
    assert rate == DECODE_RATE == 16000
    n = len(pcm) // 2
    assert 1.9 * rate <= n <= 2.2 * rate
    samples = struct.unpack(f"<{n}h", pcm)
    assert max(abs(v) for v in samples[rate // 2 : rate]) > 3000  # the tone survives


def test_a_stereo_wav_decodes_too():
    data = _encode("wav", "pcm_s16le", seconds=1.0, rate=48000, layout="stereo")
    pcm, rate = decode_to_mono(data, max_seconds=30)
    assert abs(len(pcm) // 2 - rate) < rate * 0.05


def test_garbage_is_refused_as_an_invalid_recording():
    with pytest.raises(ControlError) as exc:
        decode_to_mono(b"definitely not audio" * 100, max_seconds=30)
    assert exc.value.code == "recording_invalid"


def test_a_recording_longer_than_allowed_is_refused():
    data = _encode("wav", "pcm_s16le", seconds=3.0, rate=8000)
    with pytest.raises(ControlError) as exc:
        decode_to_mono(data, max_seconds=2)
    assert exc.value.code == "recording_invalid" and "long" in exc.value.detail


# -- uploads never make the bridge fetch or read anything else (security review) --------------------


@pytest.fixture
def listener():
    """A local TCP port that records whether anything connected to it."""
    import socket
    import threading

    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    srv.settimeout(0.2)
    hits: list[int] = []
    stop = threading.Event()

    def accept():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            hits.append(1)
            conn.close()

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    yield srv.getsockname()[1], hits
    stop.set()
    t.join(1)
    srv.close()


@pytest.mark.parametrize(
    "payload",
    [
        "#EXTM3U\n#EXT-X-TARGETDURATION:10\n#EXTINF:10,\nhttp://127.0.0.1:{port}/x.ts\n#EXT-X-ENDLIST\n",
        "#EXTM3U\n#EXTINF:10,\nfile:///etc/passwd\n#EXT-X-ENDLIST\n",
        "ffconcat version 1.0\nfile 'http://127.0.0.1:{port}/x.wav'\n",
        "ffconcat version 1.0\nfile '/etc/passwd'\n",
        "v=0\no=- 0 0 IN IP4 127.0.0.1\ns=x\nc=IN IP4 127.0.0.1\nt=0 0\nm=audio {port} RTP/AVP 0\n",
        "http://127.0.0.1:{port}/x.wav",
    ],
)
def test_a_playlist_or_reference_is_refused_without_any_network_or_file_access(payload, listener, monkeypatch):
    import av

    from sendspin_bridge.sync_tuning import audio_decode

    port, hits = listener
    opened = []
    monkeypatch.setattr(audio_decode.av, "open", lambda *a, **kw: opened.append(a) or av.open(*a, **kw))
    with pytest.raises(ControlError) as exc:
        decode_to_mono(payload.format(port=port).encode() * 3, max_seconds=30)
    assert exc.value.code == "recording_invalid"
    assert opened == []  # refused by its first bytes, before FFmpeg sees it
    assert hits == []


def test_only_known_audio_containers_reach_the_decoder_and_never_with_network_protocols(monkeypatch):
    import av

    from sendspin_bridge.sync_tuning import audio_decode

    seen = []
    real = av.open

    def spy(file, *a, **kw):
        seen.append((type(file).__name__, kw.get("format"), kw.get("options")))
        return real(file, *a, **kw)

    m4a, wav = _encode("mp4", "aac", seconds=1.0), _encode("wav", "pcm_s16le", seconds=1.0, rate=16000)
    monkeypatch.setattr(audio_decode.av, "open", spy)  # after the fixtures are made (same av module)
    decode_to_mono(m4a, max_seconds=30)
    decode_to_mono(wav, max_seconds=30)
    assert [s[:2] for s in seen] == [("BytesIO", "mov,mp4,m4a,3gp,3g2,mj2"), ("BytesIO", "wav")]
    for _kind, _fmt, options in seen:
        assert options["protocol_whitelist"] == "none"
        assert "hls" not in options["format_whitelist"] and "concat" not in options["format_whitelist"]
