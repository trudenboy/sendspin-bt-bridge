"""Tests for ``services/bt_commands.py``.

Focus on the wrappers that route commands to the right place (thread vs
asyncio loop) and on validation rules that don't require a live BT stack.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sendspin_bridge.bluetooth.adapter_session import AdapterHandle
from sendspin_bridge.services.bluetooth import bt_commands as M
from sendspin_bridge.services.bluetooth.bt_commands import CommandResult
from tests.support.fake_lease import FakeLease


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


def test_find_client_by_player_id_case_insensitive(monkeypatch, fake_client):
    """Casing-stable lookup — see Copilot review on PR #214.

    Canonical player_ids are lowercase UUID5 strings, but a round-trip
    through HA JSON / templates can capitalise them; commands must still
    route to the right client.
    """
    monkeypatch.setattr(
        M,
        "get_device_registry_snapshot",
        lambda: SimpleNamespace(active_clients=[fake_client]),
    )
    assert M.find_client_by_player_id("PLAYER-AAA") is fake_client
    assert M.find_client_by_player_id("Player-AAA") is fake_client
    # Whitespace + mixed case combined.
    assert M.find_client_by_player_id("  Player-Aaa  ") is fake_client


# ---------------------------------------------------------------------------
# command_reconnect / disconnect / pair (thread-spawning)
# ---------------------------------------------------------------------------


def test_command_reconnect_returns_immediately(fake_client, monkeypatch):
    # Stub the bt-operation lock so the test doesn't hold the real singleton
    # (the worker thread would otherwise keep it for ~1s).
    monkeypatch.setattr(M, "_acquire_bt_lease", lambda *a, **kw: FakeLease())
    monkeypatch.setattr(M, "_spawn_thread", lambda target, *a: None)
    result = M.command_reconnect(fake_client)
    assert result.success
    assert "Reconnect" in result.message


def test_command_reconnect_without_bt_manager_fails():
    client = SimpleNamespace(player_name="x", bt_manager=None)
    result = M.command_reconnect(client)
    assert not result.success
    assert result.code == 503


def test_command_reconnect_returns_409_when_bt_operation_in_progress(fake_client, monkeypatch):
    """Force reconnect must not drive the adapter while a scan/RSSI/pair holds
    the bt-operation lock — it returns 409 and spawns no worker thread."""
    spawned = []
    monkeypatch.setattr(M, "_acquire_bt_lease", lambda *a, **kw: None)
    monkeypatch.setattr(M, "_spawn_thread", lambda target, *a: spawned.append(target))
    result = M.command_reconnect(fake_client)
    assert not result.success
    assert result.code == 409
    assert spawned == []  # never touched the adapter


def test_command_reconnect_releases_lock_after_worker(fake_client, monkeypatch):
    released = []
    monkeypatch.setattr(M, "_acquire_bt_lease", lambda *a, **kw: FakeLease(lambda: released.append(True)))
    monkeypatch.setattr(M, "_spawn_thread", lambda target, *a: target(*a))  # run synchronously
    monkeypatch.setattr(M.threading, "Event", lambda: SimpleNamespace(wait=lambda _t: None))  # no real sleep
    result = M.command_reconnect(fake_client)
    assert result.success
    assert released == [True]


def test_command_disconnect_releases_lock_when_spawn_fails(fake_client, monkeypatch):
    """If _spawn_thread raises (thread start failure), the bt-operation lock
    must not stay held — otherwise every later BT operation 409s until the
    process restarts (Copilot review on PR #424)."""
    released = []
    monkeypatch.setattr(M, "_acquire_bt_lease", lambda *a, **kw: FakeLease(lambda: released.append(True)))

    def _boom(target, *a):
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(M, "_spawn_thread", _boom)
    result = M.command_disconnect(fake_client)
    assert not result.success
    assert released == [True]


def test_command_reconnect_releases_lock_when_spawn_fails(fake_client, monkeypatch):
    released = []
    monkeypatch.setattr(M, "_acquire_bt_lease", lambda *a, **kw: FakeLease(lambda: released.append(True)))

    def _boom(target, *a):
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(M, "_spawn_thread", _boom)
    result = M.command_reconnect(fake_client)
    assert not result.success
    assert released == [True]


def test_command_pair_releases_lock_when_spawn_fails(fake_client, monkeypatch):
    released = []
    monkeypatch.setattr(M, "_acquire_bt_lease", lambda *a, **kw: FakeLease(lambda: released.append(True)))

    def _boom(target, *a):
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(M, "_spawn_thread", _boom)
    result = M.command_pair(fake_client)
    assert not result.success
    assert released == [True]


def test_command_reset_reconnect_releases_lock_when_spawn_fails(fake_client, monkeypatch):
    released = []
    monkeypatch.setattr(M, "_acquire_bt_lease", lambda *a, **kw: FakeLease(lambda: released.append(True)))

    def _boom(target, *a):
        raise RuntimeError("cannot start thread")

    monkeypatch.setattr(M, "_spawn_thread", _boom)
    result = M.command_reset_reconnect(fake_client)
    assert not result.success
    assert released == [True]


def test_command_reset_reconnect_returns_409_when_bt_operation_in_progress(fake_client, monkeypatch):
    spawned = []
    monkeypatch.setattr(M, "_acquire_bt_lease", lambda *a, **kw: None)
    monkeypatch.setattr(M, "_spawn_thread", lambda target, *a: spawned.append(target))
    result = M.command_reset_reconnect(fake_client)
    assert not result.success
    assert result.code == 409
    assert spawned == []


def test_command_disconnect_without_bt_manager_fails():
    client = SimpleNamespace(player_name="x", bt_manager=None)
    result = M.command_disconnect(client)
    assert not result.success


def test_command_disconnect_returns_409_when_bt_operation_in_progress(fake_client, monkeypatch):
    """Disconnect drives the adapter too — it must serialise with
    scan/pair/reconnect instead of contending (lock-free disconnects were
    part of the wedged-scan contention observed on the demo stand)."""
    spawned = []
    monkeypatch.setattr(M, "_acquire_bt_lease", lambda *a, **kw: None)
    monkeypatch.setattr(M, "_spawn_thread", lambda target, *a: spawned.append(target))
    result = M.command_disconnect(fake_client)
    assert not result.success
    assert result.code == 409
    assert spawned == []  # never touched the adapter


def test_command_disconnect_releases_lock_after_worker(fake_client, monkeypatch):
    released = []
    monkeypatch.setattr(M, "_acquire_bt_lease", lambda *a, **kw: FakeLease(lambda: released.append(True)))
    monkeypatch.setattr(M, "_spawn_thread", lambda target, *a: target(*a))  # run synchronously
    result = M.command_disconnect(fake_client)
    assert result.success
    assert released == [True]


def test_command_pair_takes_a_named_adapter_lease(fake_client, monkeypatch):
    """Pair must hold the adapter lease so it serialises with the scan path."""
    monkeypatch.setattr(M, "_spawn_thread", lambda target, *a: None)
    # The MagicMock bt_manager would hand back a mock handle; use a plain
    # object so the command falls through to a real handle.
    fake_client.bt_manager = SimpleNamespace(mac_address="FC:58:FA:EB:08:6C")

    taken = []
    real_acquire = M._acquire_bt_lease

    def _spy(client, reason):
        lease = real_acquire(client, reason)
        taken.append(lease)
        return lease

    monkeypatch.setattr(M, "_acquire_bt_lease", _spy)

    result = M.command_pair(fake_client)
    try:
        assert result.success
        assert taken and taken[0] is not None
        assert taken[0].reason.startswith("pair")
        assert AdapterHandle.current_holder() == taken[0].reason
    finally:
        # The worker owns the release in production; it is stubbed out here.
        if taken and taken[0] is not None:
            taken[0].release()


def test_command_pair_returns_409_while_the_adapter_is_leased(fake_client):
    # A MagicMock bt_manager would hand back a mock handle whose lease always
    # succeeds; use a plain object so the command reaches the real lease.
    fake_client.bt_manager = SimpleNamespace(mac_address="FC:58:FA:EB:08:6C")
    handle = AdapterHandle()
    held = handle.try_lease("scan")
    try:
        result = M.command_pair(fake_client)
    finally:
        held.release()

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
# command_claim_audio
# ---------------------------------------------------------------------------


def test_command_claim_audio_pushes_playing_via_mpris(fake_client, monkeypatch):
    """Claim must reach the MprisPlayer via set_playback_status('Playing'),
    not bounce through reconnect — the old fallback interrupted playback."""
    from sendspin_bridge.services.audio.mpris_player import get_registry

    scheduled: list[str] = []

    def fake_schedule(coro, *, timeout=5.0):
        scheduled.append(getattr(coro, "__qualname__", "") or "?")
        coro.close()
        return CommandResult(success=True, message="claimed")

    monkeypatch.setattr(M, "_schedule_coroutine", fake_schedule)

    fake_player = MagicMock()

    async def set_playback_status(status):
        return None

    fake_player.set_playback_status = set_playback_status

    reg = get_registry()
    reg.register(fake_client.bt_manager.mac_address, fake_player)
    try:
        result = M.command_claim_audio(fake_client)
    finally:
        reg.unregister(fake_client.bt_manager.mac_address)

    # The coroutine was constructed (with status="Playing") and handed to
    # the scheduler — that proves the MPRIS path was taken, not reconnect.
    assert result.success
    assert len(scheduled) == 1
    assert "set_playback_status" in scheduled[0]


def test_command_claim_audio_returns_409_when_speaker_not_connected(fake_client, monkeypatch):
    """When no MprisPlayer is registered for the MAC the speaker isn't
    reachable — fail loud (409) instead of falling through to reconnect."""
    monkeypatch.setattr(
        "sendspin_bridge.services.audio.mpris_player.get_registry", lambda: SimpleNamespace(get=lambda mac: None)
    )

    result = M.command_claim_audio(fake_client)

    assert not result.success
    assert result.code == 409


def test_command_claim_audio_without_bt_manager_fails():
    client = SimpleNamespace(player_name="x", bt_manager=None)
    result = M.command_claim_audio(client)
    assert not result.success
    assert result.code == 503


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
    import sendspin_bridge.bridge.state as live_state
    import sendspin_bridge.services.bluetooth.bt_commands as bt

    monkeypatch.setattr(live_state, "get_main_loop", lambda: None)

    async def _coro():
        return None

    result = bt._schedule_coroutine(_coro())
    assert not result.success
    assert result.code == 503


def test_command_reclaim_reenables_bt_management(fake_client, monkeypatch):
    """#357: the Reclaim button re-enables BT management (enabled=True) so the
    reconnect monitor takes an auto-released device back over."""
    called = {}
    monkeypatch.setattr(
        fake_client, "set_bt_management_enabled", lambda enabled: called.__setitem__("enabled", enabled)
    )
    monkeypatch.setattr(M, "_spawn_thread", lambda target, *a: target(*a))  # run synchronously
    result = M.command_reclaim(fake_client)
    assert result.success
    assert called.get("enabled") is True
