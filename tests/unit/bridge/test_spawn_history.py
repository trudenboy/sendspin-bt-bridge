"""Spawn-history ring buffer + repeating-interval detection (issue #291 follow-up)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sendspin_bridge.bridge.client import SendspinClient, SpawnRecord

UTC = timezone.utc


# ---------------------------------------------------------------------------
# SpawnRecord dataclass
# ---------------------------------------------------------------------------


def test_spawn_record_defaults():
    rec = SpawnRecord(pid=123, spawn_at=datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC))
    assert rec.exit_at is None
    assert rec.exit_code is None
    assert rec.signal is None
    assert rec.lifetime_s is None
    assert rec.stderr_tail == []
    assert rec.unexpected is True


# ---------------------------------------------------------------------------
# _detect_repeating_lifetime
# ---------------------------------------------------------------------------


def _client_with_history(*lifetimes_unexpected: tuple[float, bool]) -> SendspinClient:
    """Build a client with synthetic spawn history.  Each tuple is (lifetime_s, unexpected)."""
    client = SendspinClient("Test", "auto", 8927)
    base = datetime(2026, 5, 13, 10, 0, 0, tzinfo=UTC)
    for i, (lifetime, unexpected) in enumerate(lifetimes_unexpected):
        rec = SpawnRecord(
            pid=1000 + i,
            spawn_at=base + timedelta(seconds=20 * i),
            exit_at=base + timedelta(seconds=20 * i + lifetime),
            exit_code=0,
            lifetime_s=lifetime,
            unexpected=unexpected,
        )
        client._spawn_history.append(rec)
    return client


def test_detect_repeating_lifetime_returns_none_when_fewer_than_three_deaths():
    client = _client_with_history((10.0, True), (10.0, True))
    assert client._detect_repeating_lifetime() is None


def test_detect_repeating_lifetime_returns_mean_for_three_within_tolerance():
    client = _client_with_history((10.0, True), (10.1, True), (9.95, True))
    result = client._detect_repeating_lifetime()
    assert result is not None
    assert abs(result - 10.017) < 0.01


def test_detect_repeating_lifetime_returns_none_for_varied_intervals():
    client = _client_with_history((10.0, True), (5.0, True), (10.0, True))
    assert client._detect_repeating_lifetime() is None


def test_detect_repeating_lifetime_ignores_explicit_stops():
    """Explicit (expected) stops should not pollute the pattern signal."""
    client = _client_with_history(
        (1.0, False),  # explicit stop — ignored
        (10.0, True),
        (10.0, True),
        (10.0, True),
    )
    result = client._detect_repeating_lifetime()
    assert result is not None
    assert abs(result - 10.0) < 0.01


def test_detect_repeating_lifetime_uses_last_three_unexpected_only():
    """Older entries shouldn't drown out a fresh non-pattern."""
    client = _client_with_history(
        (10.0, True),
        (10.0, True),
        (10.0, True),
        (3.0, True),
    )
    # Last 3 unexpected = [10.0, 10.0, 3.0] → not within tolerance
    assert client._detect_repeating_lifetime() is None


# ---------------------------------------------------------------------------
# recent_spawn_records accessor
# ---------------------------------------------------------------------------


def test_recent_spawn_records_returns_serializable_dicts():
    client = _client_with_history((10.0, True), (10.0, True), (10.0, True))
    records = client.recent_spawn_records(n=5)
    assert isinstance(records, list)
    assert len(records) == 3
    # Each record must be JSON-serializable (no datetime objects, no enums).
    import json

    json.dumps(records)


def test_recent_spawn_records_preserves_order_oldest_to_newest():
    client = _client_with_history((10.0, True), (10.0, True), (10.0, True))
    records = client.recent_spawn_records(n=5)
    assert [r["pid"] for r in records] == [1000, 1001, 1002]


def test_recent_spawn_records_limits_to_n():
    client = _client_with_history(*[(10.0, True)] * 8)
    records = client.recent_spawn_records(n=3)
    assert len(records) == 3
    # Should be the 3 most recent (oldest first within the window).
    assert [r["pid"] for r in records] == [1005, 1006, 1007]


# ---------------------------------------------------------------------------
# DeviceStatus carries the recurring-lifetime field
# ---------------------------------------------------------------------------


def test_device_status_has_daemon_recurring_lifetime_field():
    from sendspin_bridge.bridge.client import DeviceStatus

    status = DeviceStatus()
    assert status.daemon_recurring_lifetime_s is None
    status.daemon_recurring_lifetime_s = 10.0
    assert status.daemon_recurring_lifetime_s == 10.0
