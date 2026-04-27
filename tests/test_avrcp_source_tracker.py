"""Tests for services/avrcp_source_tracker.py.

Tracks per-MAC last-activity timestamps from BlueZ MediaPlayer1
PropertiesChanged signals so the inbound AVRCP MPRIS dispatch can
correlate an anonymous Play/Pause/Next call back to the source speaker.

See module docstring of services.avrcp_source_tracker for the BlueZ
limitation this works around.
"""

from __future__ import annotations

import asyncio

import pytest


def test_note_activity_then_get_recent_active_returns_mac():
    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()
    tracker.note_activity("AA:BB:CC:DD:EE:FF", now=100.0)

    assert tracker.get_recent_active(window_s=2.0, now=100.5) == "AA:BB:CC:DD:EE:FF"


def test_get_recent_active_returns_none_when_nothing_recorded():
    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()
    assert tracker.get_recent_active(window_s=2.0, now=100.0) is None


def test_get_recent_active_drops_entries_outside_window():
    """Window expiration is the whole point — without it the most-recent
    speaker would be sticky forever and a button press from another speaker
    would still mis-route."""
    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()
    tracker.note_activity("AA:BB:CC:DD:EE:FF", now=100.0)

    # 5 seconds later, well outside default 2s window:
    assert tracker.get_recent_active(window_s=2.0, now=105.0) is None


def test_get_recent_active_returns_most_recent_when_multiple():
    """Two speakers both fired Status updates; the more-recent one wins.

    This is the normal case when multiple speakers are streaming
    simultaneously and a user taps a button on one of them — its
    MediaPlayer1.Status update will be the freshest signal.
    """
    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()
    tracker.note_activity("AA:BB:CC:DD:EE:01", now=100.0)
    tracker.note_activity("AA:BB:CC:DD:EE:02", now=100.5)

    assert tracker.get_recent_active(window_s=2.0, now=101.0) == "AA:BB:CC:DD:EE:02"


def test_note_activity_normalizes_mac_to_uppercase():
    """BlueZ object paths use uppercase MAC underscored; manager-side
    code may pass either form.  Normalising at the boundary keeps the
    lookup table single-keyed and avoids silent misses on case
    mismatch (which would silently degrade routing, not crash)."""
    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()
    tracker.note_activity("aa:bb:cc:dd:ee:ff", now=100.0)

    assert tracker.get_recent_active(window_s=2.0, now=100.5) == "AA:BB:CC:DD:EE:FF"


def test_note_activity_empty_mac_is_silent_noop():
    """Defensive: never store an empty key — would alias to other empty
    activity reports and confuse correlation."""
    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()
    tracker.note_activity("", now=100.0)

    assert tracker.get_recent_active(window_s=2.0, now=100.5) is None


def test_note_activity_overwrites_previous_for_same_mac():
    """Repeated activity from the same speaker bumps the timestamp so
    the entry stays fresh."""
    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()
    tracker.note_activity("AA:BB:CC:DD:EE:FF", now=100.0)
    tracker.note_activity("AA:BB:CC:DD:EE:FF", now=103.0)

    # 103.5 is outside window from 100.0 but inside window from 103.0
    assert tracker.get_recent_active(window_s=2.0, now=103.5) == "AA:BB:CC:DD:EE:FF"


def test_clear_drops_entry_for_mac():
    """On disconnect we want to forget this speaker's activity so a
    stale recent-activity record doesn't mis-route a fresh command
    after the device is gone."""
    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()
    tracker.note_activity("AA:BB:CC:DD:EE:FF", now=100.0)
    tracker.clear("AA:BB:CC:DD:EE:FF")

    assert tracker.get_recent_active(window_s=2.0, now=100.5) is None


def test_clear_unknown_mac_is_silent_noop():
    """Disconnect hook may fire twice (BT manager + reconfig) — clear()
    must tolerate the second call cleanly."""
    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()
    tracker.clear("AA:BB:CC:DD:EE:FF")  # must not raise


def test_get_tracker_returns_process_singleton():
    """Module-level singleton matches the MprisRegistry pattern — the
    BluetoothManager subscription threads, the inbound MPRIS dispatch
    on the asyncio loop, and the disconnect hook all need to see the
    same tracker instance."""
    from services.avrcp_source_tracker import get_tracker

    assert get_tracker() is get_tracker()


@pytest.mark.asyncio
async def test_wait_for_next_activity_returns_when_note_activity_called():
    """The MPRIS callback uses this to *wait* for the kernel HCI monitor
    (or the D-Bus PropertiesChanged subscription) to record a fresh
    source MAC, instead of guessing a fixed sleep.  Returns True when
    activity arrived during the wait.
    """
    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()

    async def _delayed_note():
        await asyncio.sleep(0.005)
        tracker.note_activity("AA:BB:CC:DD:EE:FF")

    asyncio.create_task(_delayed_note())
    arrived = await tracker.wait_for_next_activity(timeout=1.0)

    assert arrived is True
    assert tracker.get_recent_active() == "AA:BB:CC:DD:EE:FF"


@pytest.mark.asyncio
async def test_wait_for_next_activity_returns_false_on_timeout():
    """Safety cap — if HCI monitor is unavailable (graceful degradation
    on hosts without CAP_NET_RAW) and no D-Bus subscription is active
    either, the callback can't hang forever.  Returns False after the
    cap so the resolver falls back to default_client.
    """
    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()

    arrived = await tracker.wait_for_next_activity(timeout=0.05)

    assert arrived is False


@pytest.mark.asyncio
async def test_wait_for_next_activity_only_wakes_on_NEW_activity():
    """Stale data already in the tracker (from a press 1s earlier) must
    NOT cause an immediate return — the waiter is for FRESH activity
    correlated with the current dispatch, not whatever happens to be
    cached.  Only a *new* note_activity call fires the waiter.
    """
    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()
    tracker.note_activity("AA:BB:CC:DD:EE:01")  # pre-existing stale entry

    arrived = await tracker.wait_for_next_activity(timeout=0.05)

    assert arrived is False  # no NEW activity during the wait → timeout


@pytest.mark.asyncio
async def test_wait_for_next_activity_supports_multiple_concurrent_waiters():
    """Two concurrent inbound MPRIS dispatches (transport + volume
    racing, or two rapid presses) must both wake on a single
    note_activity call — neither should starve the other.
    """
    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()

    async def _waiter() -> bool:
        return await tracker.wait_for_next_activity(timeout=1.0)

    t1 = asyncio.create_task(_waiter())
    t2 = asyncio.create_task(_waiter())
    await asyncio.sleep(0.005)  # let both register
    tracker.note_activity("AA:BB:CC:DD:EE:FF")

    assert await t1 is True
    assert await t2 is True


@pytest.mark.asyncio
async def test_wait_for_next_activity_cross_thread_signal():
    """note_activity may run on the asyncio loop (HCI monitor) OR on a
    D-Bus signal thread (PropertiesChanged delivery).  The waiter must
    wake regardless of which thread fires the signal — covered by
    ``loop.call_soon_threadsafe`` in the implementation.
    """
    import threading
    import time as _time

    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()

    def _signal_from_other_thread():
        _time.sleep(0.005)
        tracker.note_activity("AA:BB:CC:DD:EE:FF")

    threading.Thread(target=_signal_from_other_thread, daemon=True).start()
    arrived = await tracker.wait_for_next_activity(timeout=1.0)

    assert arrived is True


def test_concurrent_note_and_query_does_not_raise():
    """Tracker is touched from BT-manager subscription threads and the
    asyncio loop's MPRIS dispatch — needs the same internal lock as
    MprisRegistry to avoid 'dictionary changed size during iteration'.
    """
    import threading

    from services.avrcp_source_tracker import AvrcpSourceTracker

    tracker = AvrcpSourceTracker()
    errors: list[BaseException] = []
    stop = threading.Event()

    def _writer() -> None:
        try:
            i = 0
            while not stop.is_set():
                tracker.note_activity(f"AA:BB:CC:DD:00:{i % 256:02X}")
                i += 1
        except BaseException as exc:
            errors.append(exc)

    def _reader() -> None:
        try:
            for _ in range(10000):
                tracker.get_recent_active()
        except BaseException as exc:
            errors.append(exc)

    writers = [threading.Thread(target=_writer) for _ in range(4)]
    readers = [threading.Thread(target=_reader) for _ in range(4)]
    for t in writers + readers:
        t.start()
    for t in readers:
        t.join(timeout=10.0)
    stop.set()
    for t in writers:
        t.join(timeout=10.0)

    assert errors == [], f"concurrent tracker access raised: {errors[:3]!r}"
