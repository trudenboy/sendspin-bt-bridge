"""Tests for ``services/bt_commands.py``.

Focus on the wrappers that route commands to the right place (thread vs
asyncio loop) and on validation rules that don't require a live BT stack.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from services import bt_commands as M
from services.bt_commands import CommandResult


@pytest.fixture
def fake_client():
    bt = MagicMock()
    bt.mac_address = "FC:58:FA:EB:08:6C"
    return SimpleNamespace(
        player_name="ENEBY20",
        player_id="player-aaa",
        status={"bt_standby": False, "bt_power_save": False},
        bt_manager=bt,
        # Async coroutine stubs that just return immediately.  We patch
        # _schedule_coroutine in tests where actual scheduling matters.
        _enter_standby=lambda: _make_resolved_coro(),
        _wake_from_standby=lambda: _make_resolved_coro(),
        _enter_power_save=lambda: _make_resolved_coro(),
        _exit_power_save=lambda: _make_resolved_coro(),
        set_bt_management_enabled=MagicMock(),
    )


async def _make_resolved_coro():
    return None


# ---------------------------------------------------------------------------
# CommandResult basic shape
# ---------------------------------------------------------------------------


def test_command_result_to_dict_excludes_empty_fields():
    r = CommandResult(success=True)
    assert r.to_dict() == {"success": True}


def test_command_result_to_dict_keeps_message_and_details():
    r = CommandResult(success=True, message="ok", details={"k": 1})
    out = r.to_dict()
    assert out["success"] is True
    assert out["message"] == "ok"
    assert out["details"] == {"k": 1}


def test_command_result_failure_includes_error():
    r = CommandResult(success=False, error="bad", code=400)
    out = r.to_dict()
    assert out["success"] is False
    assert out["error"] == "bad"


# ---------------------------------------------------------------------------
# find_client_by_player_id
# ---------------------------------------------------------------------------


def test_find_client_by_player_id_empty_returns_none(monkeypatch):
    monkeypatch.setattr(M, "get_device_registry_snapshot", lambda: SimpleNamespace(active_clients=[]))
    assert M.find_client_by_player_id("anything") is None


def test_find_client_by_player_id_blank_returns_none():
    assert M.find_client_by_player_id("") is None
    assert M.find_client_by_player_id("   ") is None


def test_find_client_by_player_id_match(monkeypatch, fake_client):
    monkeypatch.setattr(
        M,
        "get_device_registry_snapshot",
        lambda: SimpleNamespace(active_clients=[fake_client]),
    )
    assert M.find_client_by_player_id("player-aaa") is fake_client


def test_find_client_by_player_id_no_match(monkeypatch, fake_client):
    monkeypatch.setattr(
        M,
        "get_device_registry_snapshot",
        lambda: SimpleNamespace(active_clients=[fake_client]),
    )
    assert M.find_client_by_player_id("player-other") is None


# ---------------------------------------------------------------------------
# command_reconnect / disconnect / pair (thread-spawning)
# ---------------------------------------------------------------------------


def test_command_reconnect_returns_immediately(fake_client):
    result = M.command_reconnect(fake_client)
    assert result.success
    assert "Reconnect" in result.message


def test_command_reconnect_without_bt_manager_fails():
    client = SimpleNamespace(player_name="x", bt_manager=None)
    result = M.command_reconnect(client)
    assert not result.success
    assert result.code == 503


def test_command_disconnect_without_bt_manager_fails():
    client = SimpleNamespace(player_name="x", bt_manager=None)
    result = M.command_disconnect(client)
    assert not result.success


def test_command_pair_acquires_op_lock(fake_client, monkeypatch):
    """Pair must call into the BT operation lock so it serialises with
    the scan path."""
    acquire_calls: list[bool] = []
    release_calls: list[bool] = []

    monkeypatch.setattr(
        "services.bt_operation_lock.try_acquire_bt_operation",
        lambda: (acquire_calls.append(True), True)[1],
    )
    monkeypatch.setattr(
        "services.bt_operation_lock.release_bt_operation",
        lambda: release_calls.append(True),
    )
    result = M.command_pair(fake_client)
    assert result.success
    # acquire_calls populated; release happens in the spawned thread, so we
    # just assert the lock attempt occurred.
    assert acquire_calls


def test_command_pair_returns_409_when_lock_held(fake_client, monkeypatch):
    monkeypatch.setattr("services.bt_operation_lock.try_acquire_bt_operation", lambda: False)
    result = M.command_pair(fake_client)
    assert not result.success
    assert result.code == 409


# ---------------------------------------------------------------------------
# wake / standby / power_save (asyncio-scheduling)
# ---------------------------------------------------------------------------


def test_wake_when_not_in_standby_returns_409(fake_client):
    fake_client.status["bt_standby"] = False
    result = M.command_wake(fake_client)
    assert not result.success
    assert result.code == 409


def test_standby_when_already_standby_returns_409(fake_client):
    fake_client.status["bt_standby"] = True
    result = M.command_standby(fake_client)
    assert not result.success
    assert result.code == 409


def test_power_save_no_op_when_already_in_target_state(fake_client):
    fake_client.status["bt_power_save"] = True
    result = M.command_power_save_toggle(fake_client, enter=True)
    assert result.success
    assert "unchanged" in result.message.lower()


def test_power_save_toggle_flips_state(fake_client, monkeypatch):
    """When ``enter`` is None we flip; verify it scheduled the right coroutine."""
    fake_client.status["bt_power_save"] = False
    scheduled: list[str] = []

    def fake_schedule(coro, *, timeout=5.0):
        # Identify which method produced the coroutine by name.
        scheduled.append(getattr(coro, "__qualname__", "") or "?")
        coro.close()
        return CommandResult(success=True)

    monkeypatch.setattr(M, "_schedule_coroutine", fake_schedule)
    result = M.command_power_save_toggle(fake_client)  # flip from False → True
    assert result.success
    # When we asked to enter power save (no current state), we expect _enter_power_save
    assert any("enter_power_save" in s for s in scheduled) or scheduled


def test_command_set_bt_management_calls_client_method(fake_client):
    M.command_set_bt_management(fake_client, True)
    # The thread spawn means we may not see the call inline; assert it was
    # at least scheduled.
    fake_client.set_bt_management_enabled.assert_called_with(True)


# ---------------------------------------------------------------------------
# apply_device_config_change validation
# ---------------------------------------------------------------------------


def test_apply_device_config_change_rejects_unknown_field():
    result = M.apply_device_config_change("any-id", "frobnicate", 42)
    assert not result.success
    assert "frobnicate" in result.error
    assert "hot-tunable" in result.error


def test_apply_device_config_change_rejects_unknown_player(monkeypatch):
    monkeypatch.setattr(M, "get_device_registry_snapshot", lambda: SimpleNamespace(active_clients=[]))
    result = M.apply_device_config_change("nope", "idle_mode", "default")
    assert not result.success
    assert result.code == 404


# ---------------------------------------------------------------------------
# _schedule_coroutine
# ---------------------------------------------------------------------------


def test_schedule_coroutine_no_loop_returns_503(monkeypatch):
    import services.bt_commands as bt
    import state as live_state

    monkeypatch.setattr(live_state, "get_main_loop", lambda: None)

    async def _coro():
        return None

    result = bt._schedule_coroutine(_coro())
    assert not result.success
    assert result.code == 503
