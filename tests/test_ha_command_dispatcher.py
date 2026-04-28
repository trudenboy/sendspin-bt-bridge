"""Validation + dispatch tests for ``services/ha_command_dispatcher.py``.

Uses a single fake client + monkey-patched bt_commands so tests focus on
the dispatcher's *routing* logic — not on BT semantics, which are covered
elsewhere.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services import ha_command_dispatcher as M
from services.bt_commands import CommandResult

# ---------------------------------------------------------------------------
# Test scaffolding
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_client():
    """Minimal stand-in for ``SendspinClient`` — the dispatcher only reads
    ``status`` and ``bt_manager.mac_address`` for the codepaths under test."""
    return SimpleNamespace(
        player_name="ENEBY20",
        player_id="player-aaa",
        status={"bt_standby": False, "bt_power_save": False},
        bt_manager=SimpleNamespace(mac_address="FC:58:FA:EB:08:6C"),
    )


@pytest.fixture
def dispatcher_with_calls(monkeypatch, fake_client):
    """Capture every bt_commands call without actually touching BT/config."""
    calls: list[tuple[str, tuple, dict]] = []

    def make_recorder(name):
        def _record(*args, **kwargs):
            calls.append((name, args, kwargs))
            return CommandResult(success=True, message=f"{name} ok")

        return _record

    # Patch every helper the dispatcher might call.
    for name in [
        "command_reconnect",
        "command_disconnect",
        "command_wake",
        "command_standby",
        "command_power_save_toggle",
        "command_set_bt_management",
        "command_claim_audio",
        "command_reset_reconnect",
        "apply_device_config_change",
        "apply_device_enabled",
    ]:
        monkeypatch.setattr(M.bt_commands, name, make_recorder(name))

    # Force the dispatcher to find our fake client.
    monkeypatch.setattr(
        M.bt_commands, "find_client_by_player_id", lambda pid: fake_client if pid == "player-aaa" else None
    )

    return M.HaCommandDispatcher(), calls


# ---------------------------------------------------------------------------
# Catalog coverage
# ---------------------------------------------------------------------------


def test_dispatcher_exposes_every_command_in_catalog():
    """Every spec with a ``command`` field must be reachable through the
    dispatcher.  Catches drift where someone adds a spec but forgets to
    add a handler — silent feature gap."""
    from services.ha_entity_model import (
        BRIDGE_ENTITIES,
        DEVICE_ENTITIES,
    )

    d = M.HaCommandDispatcher()
    device_cmds_in_catalog = {s.command for s in DEVICE_ENTITIES if s.command}
    bridge_cmds_in_catalog = {s.command for s in BRIDGE_ENTITIES if s.command}
    assert device_cmds_in_catalog == set(d.known_device_commands())
    assert bridge_cmds_in_catalog == set(d.known_bridge_commands())


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def test_unknown_command_returns_404(dispatcher_with_calls):
    d, _ = dispatcher_with_calls
    result = d.dispatch_device("player-aaa", "do_a_barrel_roll")
    assert not result.success
    assert result.code == 404


def test_unknown_player_returns_404(dispatcher_with_calls):
    d, _ = dispatcher_with_calls
    result = d.dispatch_device("nope", "reconnect")
    assert not result.success
    assert result.code == 404


def test_missing_player_id_rejected(dispatcher_with_calls):
    d, _ = dispatcher_with_calls
    result = d.dispatch_device("", "reconnect")
    assert not result.success


def test_missing_command_rejected(dispatcher_with_calls):
    d, _ = dispatcher_with_calls
    result = d.dispatch_device("player-aaa", "")
    assert not result.success


# ---------------------------------------------------------------------------
# Button routing
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "command,expected_helper",
    [
        ("reconnect", "command_reconnect"),
        ("disconnect", "command_disconnect"),
        ("wake", "command_wake"),
        ("standby", "command_standby"),
        ("claim_audio", "command_claim_audio"),
        ("reset_reconnect", "command_reset_reconnect"),
    ],
)
def test_button_command_routes_to_helper(dispatcher_with_calls, command, expected_helper):
    d, calls = dispatcher_with_calls
    result = d.dispatch_device("player-aaa", command)
    assert result.success, result.error
    assert any(name == expected_helper for name, *_ in calls), f"{expected_helper} not called"


def test_pair_command_not_exposed_via_dispatcher(dispatcher_with_calls):
    """Pairing intentionally NOT routable from HA — see PR #216 discussion."""
    d, _ = dispatcher_with_calls
    result = d.dispatch_device("player-aaa", "pair")
    assert not result.success
    assert result.code == 404


def test_scan_command_not_exposed_via_bridge_dispatcher():
    """Bridge-level scan intentionally NOT routable from HA — pure no-op
    without the bridge UI's pair-flow modal."""
    d = M.HaCommandDispatcher()
    result = d.dispatch_bridge("scan")
    assert not result.success
    assert result.code == 404


def test_power_save_toggle_routes_to_helper(dispatcher_with_calls):
    d, calls = dispatcher_with_calls
    result = d.dispatch_device("player-aaa", "power_save_toggle")
    assert result.success
    assert any(name == "command_power_save_toggle" for name, *_ in calls)


# ---------------------------------------------------------------------------
# Switch coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ON", True),
        ("OFF", False),
        ("on", True),
        ("off", False),
        ("true", True),
        ("false", False),
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("1", True),
        ("0", False),
        ("yes", True),
        ("no", False),
    ],
)
def test_set_enabled_coerces_payloads(dispatcher_with_calls, raw, expected):
    d, calls = dispatcher_with_calls
    d.dispatch_device("player-aaa", "set_enabled", raw)
    _name, args, _ = next(c for c in calls if c[0] == "apply_device_enabled")
    assert args[1] is expected, f"{raw!r} → {args[1]} expected {expected}"


def test_set_bt_management_coerces_payloads(dispatcher_with_calls):
    d, calls = dispatcher_with_calls
    d.dispatch_device("player-aaa", "set_bt_management", "ON")
    _name, args, _ = next(c for c in calls if c[0] == "command_set_bt_management")
    assert args[1] is True


# ---------------------------------------------------------------------------
# Select validation
# ---------------------------------------------------------------------------


def test_set_idle_mode_accepts_valid_option(dispatcher_with_calls):
    d, calls = dispatcher_with_calls
    result = d.dispatch_device("player-aaa", "set_idle_mode", "power_save")
    assert result.success
    _name, args, _ = next(c for c in calls if c[0] == "apply_device_config_change")
    assert args[0] == "player-aaa"
    assert args[1] == "idle_mode"
    assert args[2] == "power_save"


def test_set_idle_mode_rejects_unknown_option(dispatcher_with_calls):
    d, _ = dispatcher_with_calls
    result = d.dispatch_device("player-aaa", "set_idle_mode", "warp_speed")
    assert not result.success
    assert "warp_speed" in result.error


def test_set_keep_alive_method_routes(dispatcher_with_calls):
    d, calls = dispatcher_with_calls
    result = d.dispatch_device("player-aaa", "set_keep_alive_method", "silence")
    assert result.success
    _name, args, _ = next(c for c in calls if c[0] == "apply_device_config_change")
    assert args[1] == "keep_alive_method"
    assert args[2] == "silence"


# ---------------------------------------------------------------------------
# Number validation
# ---------------------------------------------------------------------------


def test_set_static_delay_ms_clamps_above_max(dispatcher_with_calls):
    d, _ = dispatcher_with_calls
    result = d.dispatch_device("player-aaa", "set_static_delay_ms", 99999)
    assert not result.success
    assert "above max" in result.error


def test_set_static_delay_ms_rejects_below_min(dispatcher_with_calls):
    d, _ = dispatcher_with_calls
    result = d.dispatch_device("player-aaa", "set_static_delay_ms", -5)
    assert not result.success
    assert "below min" in result.error


def test_set_static_delay_ms_rejects_non_numeric(dispatcher_with_calls):
    d, _ = dispatcher_with_calls
    result = d.dispatch_device("player-aaa", "set_static_delay_ms", "fast")
    assert not result.success


def test_set_static_delay_ms_persists_int_when_step_integral(dispatcher_with_calls):
    d, calls = dispatcher_with_calls
    result = d.dispatch_device("player-aaa", "set_static_delay_ms", 250.0)
    assert result.success
    _name, args, _ = next(c for c in calls if c[0] == "apply_device_config_change")
    assert args[1] == "static_delay_ms"
    assert args[2] == 250
    assert isinstance(args[2], int)


def test_set_power_save_delay_minutes_routes(dispatcher_with_calls):
    d, calls = dispatcher_with_calls
    result = d.dispatch_device("player-aaa", "set_power_save_delay_minutes", 5)
    assert result.success
    _name, args, _ = next(c for c in calls if c[0] == "apply_device_config_change")
    assert args[1] == "power_save_delay_minutes"
    assert args[2] == 5


# ---------------------------------------------------------------------------
# Bridge commands
# ---------------------------------------------------------------------------


def test_bridge_unknown_command_returns_404():
    d = M.HaCommandDispatcher()
    result = d.dispatch_bridge("nuke")
    assert not result.success
    assert result.code == 404


# ---------------------------------------------------------------------------
# Default singleton
# ---------------------------------------------------------------------------


def test_default_dispatcher_is_singleton():
    d1 = M.get_default_dispatcher()
    d2 = M.get_default_dispatcher()
    assert d1 is d2
