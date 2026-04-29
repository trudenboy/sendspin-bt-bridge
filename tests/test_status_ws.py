"""Tests for the WebSocket status stream (v2.63.0-rc.3).

Migration target replacing ``/api/status/stream`` (SSE).  The SSE path
breaks under HA Supervisor's deflate compression on
``text/event-stream``; WebSocket frames are not transformed by ingress
proxies, eliminating the garbled-payload class of bug.

The streaming logic lives in a pure ``status_ws_iter`` generator so it
can be exercised without spinning up a real WebSocket connection — the
``flask-sock`` route handler is a thin wrapper that drives the
generator and forwards each item to ``ws.send()``.
"""

from __future__ import annotations

import threading
import time

import pytest

import state as _state
from sendspin_bridge.web.routes.api_ws import status_ws_iter


@pytest.fixture(autouse=True)
def _reset_status_version():
    """Each test starts from a known status-version baseline.

    ``notify_status_changed`` is debounced via a 100 ms timer (see
    ``services.bridge_runtime_state``), so a bare bump doesn't
    guarantee the version counter has actually advanced by the time
    the test runs — under heavy load that race can land a version
    bump mid-test and turn an expected heartbeat into a payload
    frame.  We force a flush by waiting for the version to advance
    plus a small slack for the debounce timer to finish before the
    test starts asserting on heartbeat / change semantics.
    """
    previous_version = _state.get_status_version()
    _state.notify_status_changed()
    _state.wait_for_status_change(previous_version, timeout=1.0)
    time.sleep(0.15)  # let the debounce timer fully retire
    yield


def test_status_ws_iter_emits_initial_snapshot_first():
    """The first item yielded is always the current status snapshot, so
    clients render immediately without waiting for the first change event
    (matches the SSE behaviour and is critical under HA ingress where
    polling is otherwise the only fallback)."""
    gen = status_ws_iter(max_iterations=0, change_timeout=0.01)
    initial = next(gen)
    assert isinstance(initial, dict)
    # Must NOT be a heartbeat — initial snapshot is the real payload.
    assert initial.get("event") != "heartbeat"


def test_status_ws_iter_emits_change_after_notify():
    """When ``notify_status_changed`` fires, the next iteration yields
    a fresh snapshot (not a heartbeat)."""
    gen = status_ws_iter(max_iterations=2, change_timeout=2.0)
    next(gen)  # initial snapshot

    # Trigger a change from a worker thread so the wait inside the
    # generator wakes up cleanly (mirrors the production path where
    # state mutations come from BT manager / asyncio threads).
    def _bump() -> None:
        time.sleep(0.05)
        _state.notify_status_changed()

    threading.Thread(target=_bump, daemon=True).start()
    msg = next(gen)
    assert isinstance(msg, dict)
    assert msg.get("event") != "heartbeat", msg


def test_status_ws_iter_emits_heartbeat_on_idle_timeout():
    """When no change lands within ``change_timeout``, the generator
    yields a heartbeat so proxies (and the browser) keep the connection
    open instead of timing it out as dead."""
    gen = status_ws_iter(max_iterations=2, change_timeout=0.05)
    next(gen)  # initial
    msg = next(gen)
    assert msg == {"event": "heartbeat"}


def test_status_ws_iter_stops_at_max_iterations():
    """``max_iterations`` is a test-only safety knob — the generator
    must stop iterating after that many post-snapshot loops, not loop
    forever."""
    gen = status_ws_iter(max_iterations=1, change_timeout=0.01)
    items = list(gen)
    # 1 initial snapshot + 1 heartbeat (max_iterations=1)
    assert len(items) == 2


def test_status_ws_iter_yields_session_expired_then_stops():
    """When ``max_lifetime`` elapses the generator emits a final
    sentinel and stops, mirroring the SSE 30-min cap.  Without it,
    abandoned connections accumulate indefinitely under waitress."""
    gen = status_ws_iter(max_iterations=10, change_timeout=0.01, max_lifetime=0.0)
    items = list(gen)
    # Initial snapshot + at least one expiry sentinel.
    assert items[-1] == {"event": "session_expired"}


def test_status_ws_iter_captures_version_before_initial_snapshot(monkeypatch):
    """Regression for Copilot review on PR #197: ``status_ws_iter``
    used to call ``get_status_version`` AFTER yielding the initial
    snapshot.  If a status change landed between snapshot build and
    version capture, the generator would record the *post-change*
    version and then ``wait_for_status_change`` would never trigger
    on it — clients silently saw stale state until the next change.

    Capture the version BEFORE the snapshot so any concurrent change
    pushes it past the captured baseline and the next
    ``wait_for_status_change`` returns ``changed=True`` immediately.

    Repro: monkey-patch the snapshot builder to emit a notify+flush
    DURING snapshot construction, then assert the second yield is a
    change frame (not heartbeat).
    """
    from sendspin_bridge.services.lifecycle import bridge_runtime_state as _brs
    from sendspin_bridge.web.routes.api_status import _build_status_payload as _real_build

    # Replace the snapshot builder with a spy that bumps the status
    # version synchronously (bypassing the 100 ms debounce — we go
    # straight to the underlying flush so the bump is observable
    # before the spy returns).
    def _spy_build():
        snap = _real_build()
        with _brs._status_condition:
            _brs._status_version += 1
            _brs._status_condition.notify_all()
        return snap

    monkeypatch.setattr("sendspin_bridge.web.routes.api_status._build_status_payload", _spy_build)

    gen = status_ws_iter(max_iterations=1, change_timeout=1.0)
    next(gen)  # initial snapshot — spy bumps the version mid-build
    msg = next(gen)

    # If version capture is correctly BEFORE the snapshot, the bump
    # raised the live version past the captured baseline, so the wait
    # returns ``changed=True`` and we get a fresh payload.  If
    # capture happens AFTER, the bump's new version was already
    # baked into ``last_version`` and we'd get a heartbeat instead.
    assert msg.get("event") != "heartbeat", (
        f"version captured AFTER snapshot — concurrent change was missed; got {msg!r}"
    )


# ── Live log stream (/api/logs/stream) ──────────────────────────────────


def _emit_log_line(handler, message: str) -> None:
    import logging as _logging

    record = _logging.LogRecord(
        name="test",
        level=_logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    handler.emit(record)


def test_log_stream_iter_first_frame_is_snapshot_with_history():
    """The WS handler's first frame must carry the full ring buffer so
    a freshly-opened browser tab renders historical context without a
    second polling round-trip."""
    import logging as _logging

    from sendspin_bridge.web.routes.api_ws import log_stream_iter
    from sendspin_client import _RingLogHandler

    h = _RingLogHandler(maxlen=10)
    h.setFormatter(_logging.Formatter("%(message)s"))
    _emit_log_line(h, "old1")
    _emit_log_line(h, "old2")

    gen = log_stream_iter(h, max_iterations=0, idle_timeout=0.01)
    first = next(gen)
    assert first == {"type": "snapshot", "lines": ["old1", "old2"]}


def test_log_stream_iter_emits_append_per_new_line():
    """After the snapshot, each new emit must arrive as a single
    ``{type: append, line: ...}`` frame in order."""
    import logging as _logging
    import threading as _threading
    import time as _time

    from sendspin_bridge.web.routes.api_ws import log_stream_iter
    from sendspin_client import _RingLogHandler

    h = _RingLogHandler(maxlen=10)
    h.setFormatter(_logging.Formatter("%(message)s"))

    gen = log_stream_iter(h, max_iterations=2, idle_timeout=2.0)
    snap = next(gen)
    assert snap["type"] == "snapshot"

    def _emit_after_delay() -> None:
        _time.sleep(0.05)
        _emit_log_line(h, "fresh-line")

    _threading.Thread(target=_emit_after_delay, daemon=True).start()
    msg = next(gen)
    assert msg == {"type": "append", "line": "fresh-line"}


def test_log_stream_iter_emits_heartbeat_on_idle_timeout():
    """Without a new line within ``idle_timeout``, a heartbeat keeps
    the WS connection warm against ingress / browser idle close."""
    import logging as _logging

    from sendspin_bridge.web.routes.api_ws import log_stream_iter
    from sendspin_client import _RingLogHandler

    h = _RingLogHandler(maxlen=10)
    h.setFormatter(_logging.Formatter("%(message)s"))

    gen = log_stream_iter(h, max_iterations=1, idle_timeout=0.05)
    next(gen)  # snapshot
    msg = next(gen)
    assert msg == {"type": "heartbeat"}


def test_log_stream_iter_unsubscribes_on_completion():
    """When the generator returns, the handler's subscriber list must
    not retain a stale queue — otherwise emit() leaks fan-out work
    forever for closed clients."""
    import logging as _logging

    from sendspin_bridge.web.routes.api_ws import log_stream_iter
    from sendspin_client import _RingLogHandler

    h = _RingLogHandler(maxlen=10)
    h.setFormatter(_logging.Formatter("%(message)s"))
    initial_subs = len(h._subscribers)

    list(log_stream_iter(h, max_iterations=1, idle_timeout=0.01))

    assert len(h._subscribers) == initial_subs


def test_log_stream_iter_uses_atomic_subscribe_snapshot():
    """Regression for Copilot review on PR #197: the iterator must
    subscribe BEFORE building the snapshot (using
    ``subscribe_with_snapshot``) so emits landing during snapshot
    construction are still delivered to the client.
    """
    import logging as _logging

    from sendspin_bridge.web.routes.api_ws import log_stream_iter
    from sendspin_client import _RingLogHandler

    h = _RingLogHandler(maxlen=10)
    h.setFormatter(_logging.Formatter("%(message)s"))
    _emit_log_line(h, "history-1")

    gen = log_stream_iter(h, max_iterations=1, idle_timeout=0.5)
    snap = next(gen)
    # After the snapshot frame is yielded, the queue must already be
    # subscribed — meaning the handler has at least one subscriber.
    assert snap == {"type": "snapshot", "lines": ["history-1"]}
    assert len(h._subscribers) == 1, "subscribe didn't happen atomically with snapshot"
    list(gen)  # drain to trigger unsubscribe in finally


def test_log_stream_iter_queue_is_bounded_drops_newest_when_full():
    """Per-client queue must be bounded so a stalled browser tab can't
    leak unbounded memory.  When the queue is full, the producer side
    (``_RingLogHandler.emit``) drops the *new* line by swallowing
    ``queue.Full``; the ring buffer covers the gap on the client's
    next reconnect.  This is intentionally drop-newest rather than
    drop-oldest so a flapping client can't push a steady stream of
    new messages out of view of every other connected client.
    """
    import logging as _logging

    from sendspin_bridge.web.routes.api_ws import LOG_STREAM_QUEUE_MAXSIZE, log_stream_iter
    from sendspin_client import _RingLogHandler

    h = _RingLogHandler(maxlen=4096)
    h.setFormatter(_logging.Formatter("%(message)s"))

    # Spin up the generator (subscribe happens here).
    gen = log_stream_iter(h, max_iterations=0, idle_timeout=0.01)
    next(gen)  # snapshot
    # Find the queue we just subscribed.
    assert len(h._subscribers) == 1
    q = h._subscribers[0]
    # The queue must declare a maxsize matching the public constant.
    assert q.maxsize == LOG_STREAM_QUEUE_MAXSIZE
    # Push past the cap and verify queue size never exceeds it.
    for i in range(LOG_STREAM_QUEUE_MAXSIZE + 50):
        _emit_log_line(h, f"line-{i}")
    assert q.qsize() <= LOG_STREAM_QUEUE_MAXSIZE
