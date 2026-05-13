"""Stderr-tail ring buffer added in the issue #291 follow-up."""

from __future__ import annotations

from datetime import datetime, timezone

from sendspin_bridge.services.ipc.subprocess_stderr import SubprocessStderrService


def _make_service() -> SubprocessStderrService:
    return SubprocessStderrService(
        player_name="Test",
        update_status=lambda _u: None,
        now_factory=lambda: datetime(2026, 5, 13, tzinfo=timezone.utc),
    )


def test_tail_starts_empty():
    svc = _make_service()
    assert svc.tail() == []


def test_tail_collects_lines_in_order():
    svc = _make_service()
    svc.handle_line("first")
    svc.handle_line("second")
    svc.handle_line("third")
    assert svc.tail() == ["first", "second", "third"]


def test_tail_respects_maxlen():
    """maxlen=20 — older entries are evicted in FIFO order."""
    svc = _make_service()
    for i in range(30):
        svc.handle_line(f"line {i}")
    tail = svc.tail()
    assert len(tail) == 20
    assert tail[0] == "line 10"  # first 10 evicted
    assert tail[-1] == "line 29"


def test_tail_skips_blank_lines():
    """Blank lines aren't meaningful diagnostics — don't take up tail slots."""
    svc = _make_service()
    svc.handle_line("real")
    svc.handle_line("")
    svc.handle_line("   ")
    svc.handle_line("another")
    assert svc.tail() == ["real", "another"]


def test_tail_returns_copy_not_live_deque():
    """tail() returns a snapshot — caller shouldn't be able to corrupt internal state."""
    svc = _make_service()
    svc.handle_line("one")
    snapshot = svc.tail()
    snapshot.append("forgery")
    assert svc.tail() == ["one"]
