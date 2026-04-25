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


# ── Concurrency / lifecycle hardening (PR #197 review fixes) ───────────


def test_snapshot_does_not_raise_under_concurrent_emit(handler: _RingLogHandler):
    """Regression for Copilot review on PR #197: ``snapshot()``
    iterated ``self.records`` while ``emit()`` mutated the same deque
    from arbitrary logging threads, raising
    ``RuntimeError: deque mutated during iteration`` under load.

    Stress: 4 emit threads × 5000 lines each, racing 4 snapshot
    threads × 1000 reads each.  Without the records lock this fails
    deterministically; with it, no exception escapes either side.
    """
    import threading
    import time

    errors: list[BaseException] = []
    stop = threading.Event()

    def _emitter(name: str) -> None:
        try:
            for i in range(5000):
                if stop.is_set():
                    return
                _emit(handler, f"{name}-{i}")
        except BaseException as exc:
            errors.append(exc)

    def _reader() -> None:
        try:
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline:
                handler.snapshot()
        except BaseException as exc:
            errors.append(exc)

    emitters = [threading.Thread(target=_emitter, args=(f"e{i}",)) for i in range(4)]
    readers = [threading.Thread(target=_reader) for _ in range(4)]
    for t in emitters + readers:
        t.start()
    for t in readers:
        t.join(timeout=10.0)
    stop.set()
    for t in emitters:
        t.join(timeout=10.0)

    assert errors == [], f"concurrent snapshot/emit raised: {errors[:3]!r}"


def test_subscribe_with_snapshot_returns_atomic_pair(handler: _RingLogHandler):
    """``subscribe_with_snapshot()`` must take the ring snapshot AND
    register the queue under the same lock so log lines emitted between
    those two ops can't slip through both gaps.

    Without atomicity: a line landing between snapshot and subscribe
    would be missing from snapshot AND missed by the subscriber, so the
    client would silently never see it.  Verified via the call-shape
    contract: helper returns (queue, snapshot_lines) tuple, and the
    queue is registered (visible in the subscriber list).
    """
    _emit(handler, "history-1")
    _emit(handler, "history-2")

    q, snap = handler.subscribe_with_snapshot()

    assert isinstance(q, queue.Queue)
    assert snap == ["history-1", "history-2"]
    # Queue is now subscribed → next emit lands in it.
    _emit(handler, "after-subscribe")
    assert q.get_nowait() == "after-subscribe"


def test_subscribe_with_snapshot_no_line_lost_under_concurrent_emit(handler: _RingLogHandler):
    """The atomicity guarantee under stress: after subscribe_with_snapshot,
    every line in the universe must be either in the snapshot or in the
    queue (and never neither).  Run a writer in parallel and check
    coverage of all emitted ids.
    """
    import threading
    import time

    seen_in_snapshot: list[str] = []
    seen_in_queue: list[str] = []
    stop_writer = threading.Event()

    def _writer() -> None:
        i = 0
        while not stop_writer.is_set():
            _emit(handler, f"x-{i}")
            i += 1
            time.sleep(0.0001)

    writer = threading.Thread(target=_writer)
    writer.start()
    try:
        # Let the writer get going so there's contention at subscribe time.
        time.sleep(0.05)
        q, snap = handler.subscribe_with_snapshot()
        seen_in_snapshot.extend(snap)
        # Drain a window of new emits.
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            try:
                seen_in_queue.append(q.get(timeout=0.05))
            except queue.Empty:
                continue
    finally:
        stop_writer.set()
        writer.join(timeout=5.0)

    # Coverage check: there must be no gap between the last snapshot id
    # and the first queue id (modulo deque-trim).  Concretely, every id
    # immediately following the snapshot's last item must be in the
    # queue, with no skipped numbers.
    if seen_in_snapshot and seen_in_queue:
        last_snap_n = int(seen_in_snapshot[-1].split("-")[1])
        first_q_n = int(seen_in_queue[0].split("-")[1])
        # Gap of exactly 1 (next id) means the subscribe was atomic.
        # A gap > 1 means lines slipped between snapshot and subscribe.
        assert first_q_n == last_snap_n + 1, (
            f"line(s) lost between snapshot and subscribe: snapshot ended at {last_snap_n}, queue began at {first_q_n}"
        )
