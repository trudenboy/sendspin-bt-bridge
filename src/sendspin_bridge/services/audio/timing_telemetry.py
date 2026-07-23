"""Compatibility adapter for Sendspin timing telemetry."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc


def _milliseconds(value: Any) -> float | None:
    try:
        return round(float(value) / 1000.0, 3)
    except (TypeError, ValueError, OverflowError):
        return None


def _safe_attr(obj: object | None, name: str) -> object | None:
    """Read an optional metric property without letting telemetry affect playback."""
    if obj is None:
        return None
    try:
        return getattr(obj, name, None)
    except Exception:
        return None


def collect_timing_snapshot(audio_handler: object, client: object) -> dict[str, object]:
    """Collect public metrics and guarded compatibility fields.

    Private attributes are isolated here and may disappear on library updates;
    callers receive ``None`` rather than a failed telemetry task.
    """
    getter = getattr(audio_handler, "get_timing_metrics", None)
    try:
        metrics = getter() if callable(getter) else {}
    except Exception:
        metrics = {}
    if not isinstance(metrics, dict):
        metrics = {}

    sync_check = getattr(client, "is_time_synchronized", None)
    try:
        clock_synchronized = bool(sync_check()) if callable(sync_check) else False
    except Exception:
        clock_synchronized = False
    time_filter = getattr(client, "_time_filter", None)

    playback_position = metrics.get("playback_position_us")
    dac_samples = metrics.get("dac_samples_recorded", 0)
    try:
        playback_position = int(playback_position) if playback_position is not None else None
    except (TypeError, ValueError, OverflowError):
        playback_position = None
    try:
        dac_samples = int(dac_samples)
    except (TypeError, ValueError, OverflowError):
        dac_samples = 0

    # The aiosendspin time filter exposes ``error`` as ``round(sqrt(covariance))``.
    # Before the first samples arrive its covariance is infinite, so reading the
    # property raises OverflowError.  Do not touch either clock metric until the
    # filter reports synchronization, and keep guarded reads for future library
    # implementations whose metric properties may raise for other transient states.
    clock_offset = _safe_attr(time_filter, "offset") if clock_synchronized else None
    clock_uncertainty = _safe_attr(time_filter, "error") if clock_synchronized else None

    return {
        "timing_metrics_available": bool(metrics),
        "backend_output_latency_ms": _milliseconds(metrics.get("output_latency_us")),
        "buffered_audio_ms": _milliseconds(metrics.get("buffered_audio_us")),
        "playback_position_us": playback_position,
        "dac_samples_recorded": dac_samples,
        "playback_sync_error_ms": _milliseconds(getattr(audio_handler, "_sync_error_filtered_us", None)),
        "clock_synchronized": clock_synchronized,
        "clock_offset_ms": _milliseconds(clock_offset),
        "clock_uncertainty_ms": _milliseconds(clock_uncertainty),
        "timing_sampled_at": datetime.now(tz=UTC).isoformat(),
    }
