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

    return {
        "timing_metrics_available": bool(metrics),
        "backend_output_latency_ms": _milliseconds(metrics.get("output_latency_us")),
        "buffered_audio_ms": _milliseconds(metrics.get("buffered_audio_us")),
        "playback_position_us": playback_position,
        "dac_samples_recorded": dac_samples,
        "playback_sync_error_ms": _milliseconds(getattr(audio_handler, "_sync_error_filtered_us", None)),
        "clock_synchronized": clock_synchronized,
        "clock_offset_ms": _milliseconds(getattr(time_filter, "offset", None)),
        "clock_uncertainty_ms": _milliseconds(getattr(time_filter, "error", None)),
        "timing_sampled_at": datetime.now(tz=UTC).isoformat(),
    }
