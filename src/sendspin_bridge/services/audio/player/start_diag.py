"""The one INFO line a stream start logs, to set beside a phone measurement."""

from __future__ import annotations

from typing import Any


def time_sync_summary(client: Any) -> dict[str, Any] | None:
    """The Sendspin time filter's state (offset and error in ms), if connected."""
    connection = getattr(client, "_admitted_connection", None) if client is not None else None
    time_filter = getattr(connection, "_time_filter", None) if connection is not None else None
    if time_filter is None:
        return None
    try:
        return {
            "synced": bool(time_filter.is_synchronized),
            "offset_ms": round(float(time_filter.offset) / 1000.0, 3),
            "error_ms": round(float(time_filter.error) / 1000.0, 3),
        }
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def format_stream_start_line(
    report: dict[str, Any],
    *,
    pa_latency: dict[str, Any] | None,
    time_sync: dict[str, Any] | None,
    output_delay_ms: float | None,
    start_cost: dict[str, Any] | None = None,
) -> str:
    pa = "n/a"
    if pa_latency is not None:
        buffer_ms = float(pa_latency["buffer_ms"])
        sink_ms = float(pa_latency["sink_ms"])
        pa = f"{buffer_ms + sink_ms:.1f} ms (buffer {buffer_ms:.1f} + sink {sink_ms:.1f})"
    sync = "n/a"
    if time_sync is not None:
        state = "synced" if time_sync.get("synced") else "not synced"
        sync = f"offset {time_sync['offset_ms']:+.3f} ms ±{time_sync['error_ms']:.3f} ms ({state})"
    delay = "n/a" if output_delay_ms is None else f"{output_delay_ms:g} ms"
    line = (
        f"Stream start timing: pipeline latency {_ms(report.get('configured_latency_ms'))} "
        f"(sink min {_ms(report.get('min_latency_ms'))}, compensated {_ms(report.get('compensated_latency_ms'))}); "
        f"PA sink-input latency {pa}; "
        f"time sync {sync}; "
        f"first chunk lead {_ms(report.get('first_lead_ms'))}; "
        f"first buffer margin {_ms(report.get('first_buffer_margin_ms'))}; "
        f"output delay {delay}"
    )
    if "overdue_dropped_ms" in report:
        # Audio already past its play time when it arrived is dropped, not played
        # late; the first sample kept should reach the sink on schedule (0 ms).
        line += (
            f"; overdue audio dropped {_ms(report.get('overdue_dropped_ms'))}"
            f"; first on-schedule sample error {_ms(report.get('first_on_schedule_error_ms'))}"
        )
    if start_cost is not None:
        held = start_cost.get("loop_block_ms")
        where = "on the loop" if held is None else f"off the loop, loop held {_ms(held)}"
        line += f"; output open {_ms(start_cost.get('output_open_ms'))} ({where})"
    return line


def _ms(value: Any) -> str:
    return "n/a" if value is None else f"{float(value):.1f} ms"
