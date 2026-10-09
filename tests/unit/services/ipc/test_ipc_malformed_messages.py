"""Malformed daemon IPC messages are ignored, not applied."""

from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION
from sendspin_bridge.services.ipc.subprocess_ipc import SubprocessIpcService

# ---------------------------------------------------------------------------
# Malformed JSON in IPC messages
# ---------------------------------------------------------------------------


def test_handle_message_ignores_status_without_allowed_keys():
    """handle_message returns empty dict for a status envelope with no allowed fields."""
    service = SubprocessIpcService(
        player_name="Test",
        protocol_warning_cache=set(),
        status_updater=lambda _: None,
        allowed_keys=frozenset(),
    )
    result = service.handle_message({"type": "status", "protocol_version": IPC_PROTOCOL_VERSION, "unknown_field": True})
    assert result == {}


def test_handle_message_ignores_dict_without_type():
    """A dict with no recognized type/cmd returns None."""
    updates: list[dict] = []
    service = SubprocessIpcService(
        player_name="Test",
        protocol_warning_cache=set(),
        status_updater=updates.append,
        allowed_keys=frozenset({"playing"}),
    )
    result = service.handle_message({"random_key": "value"})
    assert result is None
    assert updates == []


def test_parse_line_returns_none_for_malformed_json():
    """parse_line must silently return None for invalid JSON."""
    service = SubprocessIpcService(
        player_name="Test",
        protocol_warning_cache=set(),
        status_updater=lambda _: None,
    )
    assert service.parse_line(b"this is not json\n") is None
    assert service.parse_line(b"{truncated\n") is None
    assert service.parse_line(b"") is None


def test_parse_line_returns_none_for_json_array():
    """JSON arrays should be rejected (only objects are valid)."""
    service = SubprocessIpcService(
        player_name="Test",
        protocol_warning_cache=set(),
        status_updater=lambda _: None,
    )
    assert service.parse_line(b"[1, 2, 3]\n") is None


def test_handle_message_with_empty_status_envelope():
    """A status envelope with no allowed keys returns empty updates."""
    service = SubprocessIpcService(
        player_name="Test",
        protocol_warning_cache=set(),
        status_updater=lambda _: None,
        allowed_keys=frozenset({"playing"}),
    )
    result = service.handle_message({"type": "status", "protocol_version": IPC_PROTOCOL_VERSION})
    assert result == {}
