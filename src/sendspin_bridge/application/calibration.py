"""Latency calibration: click track, metronome, and relative-delay microphone sessions."""

from __future__ import annotations

import io
import logging
import threading
import time
import uuid
import wave
from typing import Any, Literal

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.runtime import run_on_loop
from sendspin_bridge.application.status import find_client
from sendspin_bridge.services.audio.latency_calibration import build_calibration_pcm

logger = logging.getLogger(__name__)

_SESSION_TTL_S = 600
_sessions: dict[str, dict] = {}
_lock = threading.Lock()
_ERROR_MESSAGES = {
    "silence": "No calibration sound was detected; check microphone permission and move closer",
    "weak_correlation": "The recordings did not match reliably; keep the phone still, move closer, and reduce noise",
    "insufficient_samples": "The microphone recording was too short; keep this page active and retry",
}


def tone_wav() -> bytes:
    """A deterministic 8 s click track (48 kHz stereo) for ordinary MA group playback."""
    sample_rate = 48000
    frames = build_calibration_pcm(sample_rate=sample_rate, duration_seconds=8)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(frames)
    return output.getvalue()


def play_tone(device_id: str) -> None:
    """Play the click track straight through one speaker's sink."""
    client = find_client(device_id)
    try:
        played = bool(run_on_loop(client.play_calibration_tone(), timeout=12.0, description="Calibration tone"))
    except UseCaseError:
        raise
    except Exception as exc:
        logger.exception("Calibration tone playback failed")
        raise UseCaseError(503, "playback_failed", "Calibration tone playback failed") from exc
    if not played:
        raise UseCaseError(409, "no_sink", "Bluetooth audio sink is unavailable")


def set_metronome(device_id: str, action: Literal["start", "stop"]) -> bool:
    """Start or stop the phase-aligned continuous click track; returns whether it runs."""
    client = find_client(device_id)
    if action == "start":
        started = bool(run_on_loop(client.start_calibration_metronome(), timeout=5.0, description="Metronome start"))
        if not started:
            raise UseCaseError(409, "no_sink", "Bluetooth audio sink is unavailable")
        return True
    run_on_loop(client.stop_calibration_metronome(), timeout=5.0, description="Metronome stop")
    return False


def create_session() -> dict[str, Any]:
    now = time.time()
    session_id = str(uuid.uuid4())
    with _lock:
        for key in [k for k, v in _sessions.items() if now - v["created_at"] > _SESSION_TTL_S]:
            del _sessions[key]
        _sessions[session_id] = {"created_at": now, "recordings": {}}
    return {"session_id": session_id, "expires_in_seconds": _SESSION_TTL_S}


def upload_recording(
    session_id: str, role: Literal["reference", "target"], samples: list[float], sample_rate: int
) -> dict[str, Any]:
    """Store one recording; with both present, estimate the target's delay relative to the reference."""
    max_samples = min(sample_rate * 10, 500_000)
    if sample_rate < 8000 or sample_rate > 192000 or len(samples) < 8 or len(samples) > max_samples:
        raise UseCaseError(400, "invalid_recording", "Unsupported recording size or sample rate")
    normalized = [max(-1.0, min(1.0, float(value))) for value in samples]
    logger.info(
        "Calibration recording received: session=%s role=%s samples=%d rate=%d peak=%.4f",
        session_id[:8],
        role,
        len(normalized),
        sample_rate,
        max(abs(value) for value in normalized),
    )
    with _lock:
        session = _sessions.get(session_id)
        if session is None or time.time() - session["created_at"] > _SESSION_TTL_S:
            _sessions.pop(session_id, None)
            raise UseCaseError(404, "session_expired", "Calibration session expired")
        session["recordings"][role] = (sample_rate, normalized)
        recordings = dict(session["recordings"])
    if set(recordings) != {"reference", "target"}:
        return {"status": "waiting_for_other_recording"}
    if recordings["reference"][0] != recordings["target"][0]:
        raise UseCaseError(400, "sample_rate_mismatch", "Recordings must use the same sample rate")
    from sendspin_bridge.services.audio.latency_calibration import estimate_relative_delay_ms

    estimate = estimate_relative_delay_ms(
        recordings["reference"][1], recordings["target"][1], sample_rate=recordings["reference"][0]
    )
    (logger.info if estimate.valid else logger.warning)(
        "Calibration analysis completed: session=%s valid=%s delay_ms=%s confidence=%.4f reason=%s",
        session_id[:8],
        estimate.valid,
        estimate.delay_ms,
        estimate.confidence,
        estimate.reason or "ok",
    )
    result: dict[str, Any] = {"status": "complete", "valid": estimate.valid, "estimate": estimate.to_dict()}
    if not estimate.valid:
        result["error"] = _ERROR_MESSAGES.get(
            estimate.reason, "Calibration analysis could not produce a reliable result"
        )
    return result


def delete_session(session_id: str) -> None:
    with _lock:
        _sessions.pop(session_id, None)
