"""IPC protocol integration tests — roundtrip envelope build/parse correctness."""

from sendspin_client import _IPC_ALLOWED_KEYS
from services.ipc_protocol import (
    IPC_PROTOCOL_VERSION,
    IPC_PROTOCOL_VERSION_KEY,
    build_command_envelope,
    build_error_envelope,
    build_log_envelope,
    build_status_envelope,
    parse_command_envelope,
    parse_error_envelope,
    parse_log_envelope,
    parse_status_envelope,
)

# ---------------------------------------------------------------------------
# Status envelope roundtrip
# ---------------------------------------------------------------------------


def test_status_roundtrip_with_allowed_keys():
    """build → parse roundtrip preserves all allowed fields."""
    original = {"playing": True, "volume": 75, "muted": False, "connected": True}
    envelope = build_status_envelope(original)

    assert envelope["type"] == "status"
    assert envelope[IPC_PROTOCOL_VERSION_KEY] == IPC_PROTOCOL_VERSION

    parsed = parse_status_envelope(envelope, allowed_keys=_IPC_ALLOWED_KEYS)
    assert parsed is not None
    assert parsed.protocol_version == IPC_PROTOCOL_VERSION
    for key, value in original.items():
        assert parsed.updates[key] == value


def test_status_roundtrip_filters_unknown_keys():
    """Keys not in the allowed set are silently dropped."""
    envelope = build_status_envelope({"playing": True, "injected_field": "evil"})
    parsed = parse_status_envelope(envelope, allowed_keys=_IPC_ALLOWED_KEYS)

    assert parsed is not None
    assert "playing" in parsed.updates
    assert "injected_field" not in parsed.updates


def test_status_roundtrip_with_allowed_keys_none():
    """When allowed_keys=None, ALL status fields pass through (M28 fix)."""
    envelope = build_status_envelope({"playing": True, "volume": 50})
    parsed = parse_status_envelope(envelope, allowed_keys=None)

    assert parsed is not None
    assert parsed.updates == {"playing": True, "volume": 50}
    # Raw payload should still contain all fields
    assert parsed.raw["playing"] is True
    assert parsed.raw["volume"] == 50


def test_status_roundtrip_with_explicit_empty_frozenset():
    """Empty frozenset yields no updates — same behavior as None."""
    envelope = build_status_envelope({"playing": True})
    parsed = parse_status_envelope(envelope, allowed_keys=frozenset())

    assert parsed is not None
    assert parsed.updates == {}


def test_status_roundtrip_with_full_ipc_allowed_keys():
    """All _IPC_ALLOWED_KEYS survive the roundtrip."""
    status = {key: f"value_{i}" for i, key in enumerate(_IPC_ALLOWED_KEYS)}
    envelope = build_status_envelope(status)
    parsed = parse_status_envelope(envelope, allowed_keys=_IPC_ALLOWED_KEYS)

    assert parsed is not None
    assert set(parsed.updates.keys()) == _IPC_ALLOWED_KEYS


# ---------------------------------------------------------------------------
# Oversized / pathological messages (M25 defense)
# ---------------------------------------------------------------------------


def test_oversized_status_message_parses_without_crash():
    """A very large status payload should parse without error."""
    big_status = {key: "x" * 10_000 for key in ("playing", "volume", "muted")}
    envelope = build_status_envelope(big_status)
    parsed = parse_status_envelope(envelope, allowed_keys=_IPC_ALLOWED_KEYS)

    assert parsed is not None
    assert parsed.updates["playing"] == "x" * 10_000


def test_deeply_nested_status_does_not_crash():
    """Nested dicts in status values should not cause crashes."""
    nested = {"a": {"b": {"c": {"d": "deep"}}}}
    envelope = build_status_envelope({"current_track": nested})
    parsed = parse_status_envelope(envelope, allowed_keys=_IPC_ALLOWED_KEYS)

    assert parsed is not None
    assert parsed.updates["current_track"] == nested


# ---------------------------------------------------------------------------
# Non-status envelope types
# ---------------------------------------------------------------------------


def test_parse_status_returns_none_for_log_envelope():
    """Log envelopes must not be mistaken for status envelopes."""
    log = build_log_envelope(level="error", msg="crash")
    assert parse_status_envelope(log, allowed_keys=_IPC_ALLOWED_KEYS) is None


def test_parse_status_returns_none_for_non_dict():
    """Non-dict payloads return None gracefully."""
    assert parse_status_envelope("not a dict") is None
    assert parse_status_envelope(42) is None
    assert parse_status_envelope(None) is None


# ---------------------------------------------------------------------------
# Command envelope roundtrip
# ---------------------------------------------------------------------------


def test_command_roundtrip():
    """build_command_envelope → parse_command_envelope roundtrip."""
    envelope = build_command_envelope("set_volume", value=75)
    parsed = parse_command_envelope(envelope)

    assert parsed is not None
    assert parsed.cmd == "set_volume"
    assert parsed.payload["value"] == 75
    assert parsed.protocol_version == IPC_PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# Error envelope roundtrip
# ---------------------------------------------------------------------------


def test_error_roundtrip():
    """build_error_envelope → parse_error_envelope roundtrip."""
    envelope = build_error_envelope("sink_missing", "No audio output", details={"at": "2026-01-01T00:00:00Z"})
    parsed = parse_error_envelope(envelope)

    assert parsed is not None
    assert parsed.error_code == "sink_missing"
    assert parsed.message == "No audio output"
    assert parsed.details["at"] == "2026-01-01T00:00:00Z"
    assert parsed.protocol_version == IPC_PROTOCOL_VERSION


# ---------------------------------------------------------------------------
# Log envelope roundtrip
# ---------------------------------------------------------------------------


def test_log_roundtrip():
    """build_log_envelope → parse_log_envelope roundtrip."""
    envelope = build_log_envelope(level="warning", name="daemon", msg="reconnecting")
    parsed = parse_log_envelope(envelope)

    assert parsed is not None
    assert parsed.level == "warning"
    assert parsed.name == "daemon"
    assert parsed.msg == "reconnecting"
