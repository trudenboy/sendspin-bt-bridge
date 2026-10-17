from __future__ import annotations

from sendspin_bridge.services.audio.player.start_diag import format_stream_start_line, time_sync_summary

REPORT = {
    "compensated_latency_ms": 200.0,
    "configured_latency_ms": 200.0,
    "min_latency_ms": 110.0,
    "first_lead_ms": 1250.0,
    "first_buffer_margin_ms": 1249.6,
}


def test_start_line_names_every_term_with_its_value():
    line = format_stream_start_line(
        REPORT,
        pa_latency={"buffer_ms": 87.0, "sink_ms": 25.3},
        time_sync={"synced": True, "offset_ms": 1234.5678, "error_ms": 0.12},
        output_delay_ms=150.0,
    )

    assert line == (
        "Stream start timing: pipeline latency 200.0 ms (sink min 110.0 ms, compensated 200.0 ms); "
        "PA sink-input latency 112.3 ms (buffer 87.0 + sink 25.3); "
        "time sync offset +1234.568 ms ±0.120 ms (synced); "
        "first chunk lead 1250.0 ms; first buffer margin 1249.6 ms; output delay 150 ms"
    )


def test_start_line_marks_what_is_unknown():
    line = format_stream_start_line(
        {**REPORT, "min_latency_ms": None, "first_buffer_margin_ms": None},
        pa_latency=None,
        time_sync=None,
        output_delay_ms=None,
    )

    assert "sink min n/a" in line
    assert "PA sink-input latency n/a" in line
    assert "time sync n/a" in line
    assert "first buffer margin n/a" in line
    assert "output delay n/a" in line


def test_time_sync_summary_reads_the_admitted_connection_filter():
    from types import SimpleNamespace

    flt = SimpleNamespace(is_synchronized=True, offset=-2_500.0, error=180)
    client = SimpleNamespace(_admitted_connection=SimpleNamespace(_time_filter=flt))

    assert time_sync_summary(client) == {"synced": True, "offset_ms": -2.5, "error_ms": 0.18}


def test_time_sync_summary_without_a_connection_is_none():
    from types import SimpleNamespace

    assert time_sync_summary(None) is None
    assert time_sync_summary(SimpleNamespace(_admitted_connection=None)) is None


def test_start_line_tells_the_overdue_audio_and_the_start_cost():
    line = format_stream_start_line(
        {**REPORT, "first_lead_ms": -747.0, "overdue_dropped_ms": 747.25, "first_on_schedule_error_ms": 0.0},
        pa_latency=None,
        time_sync=None,
        output_delay_ms=247.0,
        start_cost={"output_open_ms": 962.4, "decoder_ms": 1.2},
    )

    assert line.endswith(
        "first chunk lead -747.0 ms; first buffer margin 1249.6 ms; output delay 247 ms; "
        "overdue audio dropped 747.2 ms; first on-schedule sample error 0.0 ms; "
        "output open 962.4 ms (decoder 1.2 ms)"
    )


def test_start_line_marks_an_unknown_first_sample_error():
    line = format_stream_start_line(
        {**REPORT, "overdue_dropped_ms": 0.0, "first_on_schedule_error_ms": None},
        pa_latency=None,
        time_sync=None,
        output_delay_ms=None,
    )

    assert line.endswith("overdue audio dropped 0.0 ms; first on-schedule sample error n/a")
