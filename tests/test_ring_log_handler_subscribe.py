"""Tests for the ``_RingLogHandler`` subscribe / unsubscribe API.

The live log stream WebSocket endpoint (``/api/logs/stream``)
subscribes to the same ring buffer that ``GET /api/logs?lines=…`` reads
from, so emit-time fan-out replaces the existing 2 s polling cadence
with push notification.  Multiple WS clients can subscribe in
parallel; unsubscribing must drop them silently when their tab closes.
"""

from __future__ import annotations

import logging
import queue

import pytest

from sendspin_client import _RingLogHandler


@pytest.fixture
def handler() -> _RingLogHandler:
    h = _RingLogHandler(maxlen=10)
    h.setFormatter(logging.Formatter("%(message)s"))
    return h


def _emit(handler: _RingLogHandler, message: str) -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )
    handler.emit(record)


def test_subscribe_receives_subsequent_emits(handler: _RingLogHandler):
    """A subscribed queue must receive every new log line emitted after
    subscription, in order."""
    q: queue.Queue[str] = queue.Queue()
    handler.subscribe(q)

    _emit(handler, "alpha")
    _emit(handler, "beta")

    assert q.get_nowait() == "alpha"
    assert q.get_nowait() == "beta"
    assert q.empty()


def test_subscribe_does_not_replay_history(handler: _RingLogHandler):
    """Subscription only sees lines emitted *after* it was placed.
    Historical lines belong in the snapshot path (``snapshot()``) so
    the WS handler can deliver them once on connect, not on every
    fan-out."""
    _emit(handler, "before-subscribe")
    q: queue.Queue[str] = queue.Queue()
    handler.subscribe(q)

    _emit(handler, "after-subscribe")

    items = []
    while not q.empty():
        items.append(q.get_nowait())
    assert items == ["after-subscribe"]


def test_unsubscribe_stops_delivery(handler: _RingLogHandler):
    q: queue.Queue[str] = queue.Queue()
    handler.subscribe(q)
    _emit(handler, "first")
    handler.unsubscribe(q)
    _emit(handler, "second")

    assert q.get_nowait() == "first"
    assert q.empty()


def test_unsubscribe_unknown_queue_is_silent_noop(handler: _RingLogHandler):
    """Closing a never-subscribed queue (e.g. registration race) must
    not raise — keep the WS handler's cleanup path simple."""
    handler.unsubscribe(queue.Queue())  # must not raise


def test_multiple_subscribers_each_receive_emit(handler: _RingLogHandler):
    """Two browser tabs both watching the log panel must each get a
    copy of every line — fan-out, not round-robin."""
    q1: queue.Queue[str] = queue.Queue()
    q2: queue.Queue[str] = queue.Queue()
    handler.subscribe(q1)
    handler.subscribe(q2)

    _emit(handler, "line")

    assert q1.get_nowait() == "line"
    assert q2.get_nowait() == "line"


def test_snapshot_returns_current_ring_contents(handler: _RingLogHandler):
    """``snapshot()`` is what the WS handler sends as the initial
    ``{type: snapshot, lines: [...]}`` frame so the panel renders
    historical context immediately on connect."""
    _emit(handler, "old1")
    _emit(handler, "old2")

    snap = handler.snapshot()
    assert snap == ["old1", "old2"]
    # Must be a list copy — caller mutating the snapshot can't affect
    # the live ring (the WS handler may post-process before sending).
    snap.clear()
    assert handler.snapshot() == ["old1", "old2"]


def test_emit_failure_in_one_subscriber_does_not_block_others(handler: _RingLogHandler):
    """If one subscriber's queue full / closed raises, the rest of the
    fan-out must still complete — one stuck WS client must not stall
    the global log stream."""

    class _BadQueue:
        def put_nowait(self, _item):
            raise RuntimeError("intentional")

    good_q: queue.Queue[str] = queue.Queue()
    handler.subscribe(_BadQueue())
    handler.subscribe(good_q)

    _emit(handler, "still-delivered")

    assert good_q.get_nowait() == "still-delivered"
