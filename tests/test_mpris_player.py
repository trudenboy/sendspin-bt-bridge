"""Tests for services/mpris_player.py — per-device MPRIS MediaPlayer2.Player export.

The MPRIS player is exported on the system D-Bus so BlueZ routes physical
speaker AVRCP buttons (play/pause/next/prev/volume) into our process and
forwards our PlaybackStatus / Metadata updates back to the speaker's display
(Bose, Sony WH-1000XM, etc.).

Tests focus on the pure state-management + callback-dispatch logic.  The
``dbus_fast`` ServiceInterface wrapper is exercised directly (the @method
decorator strips return values on direct calls — same pattern as
``tests/test_pairing_agent.py``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from services.mpris_player import MprisPlayer, _build_player_iface


def _make_player(transport_cb=None, volume_cb=None):
    return MprisPlayer(
        mac="AA:BB:CC:DD:EE:FF",
        player_id="aabbccddeeff",
        transport_callback=transport_cb or AsyncMock(return_value=True),
        volume_callback=volume_cb or AsyncMock(return_value=True),
    )


# ── State setters ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_playback_status_updates_state_and_emits_props_changed():
    player = _make_player()
    emitted: list[dict] = []
    player._emit_properties_changed = lambda changes: emitted.append(dict(changes))

    await player.set_playback_status("Playing")

    assert player._state.status == "Playing"
    assert emitted == [{"PlaybackStatus": "Playing"}]


@pytest.mark.asyncio
async def test_set_playback_status_noop_when_unchanged():
    player = _make_player()
    player._state.status = "Playing"
    emitted: list[dict] = []
    player._emit_properties_changed = lambda changes: emitted.append(dict(changes))

    await player.set_playback_status("Playing")

    assert emitted == []  # already in this state — no spurious PropertiesChanged


@pytest.mark.asyncio
async def test_set_playback_status_rejects_invalid_value():
    player = _make_player()
    with pytest.raises(ValueError):
        await player.set_playback_status("Bouncing")


@pytest.mark.asyncio
async def test_set_metadata_updates_state_and_emits_props_changed():
    player = _make_player()
    emitted: list[dict] = []
    player._emit_properties_changed = lambda changes: emitted.append(dict(changes))

    md = {"xesam:title": "Track 1", "xesam:artist": ["Artist"], "mpris:artUrl": "http://x/a.jpg"}
    await player.set_metadata(md)

    assert player._state.metadata == md
    assert emitted and "Metadata" in emitted[0]


@pytest.mark.asyncio
async def test_set_volume_updates_state_arms_echo_guard_and_emits_props_changed():
    player = _make_player()
    emitted: list[dict] = []
    player._emit_properties_changed = lambda changes: emitted.append(dict(changes))

    await player.set_volume(60)

    assert player._state.volume_pct == 60
    assert player._volume_echo_pending == 60  # next inbound write at this level is suppressed
    assert emitted and emitted[0].get("Volume") == pytest.approx(0.60)


@pytest.mark.asyncio
async def test_set_volume_clamps_out_of_range():
    player = _make_player()
    await player.set_volume(150)
    assert player._state.volume_pct == 100
    await player.set_volume(-5)
    assert player._state.volume_pct == 0


# ── Inbound AVRCP method dispatch ──────────────────────────────────────


@pytest.mark.asyncio
async def test_play_method_invokes_transport_callback_and_updates_status():
    transport_cb = AsyncMock(return_value=True)
    player = _make_player(transport_cb=transport_cb)

    await player._on_play()

    transport_cb.assert_awaited_once_with("aabbccddeeff", "play")
    assert player._state.status == "Playing"


@pytest.mark.asyncio
async def test_pause_method_invokes_transport_callback_and_updates_status():
    transport_cb = AsyncMock(return_value=True)
    player = _make_player(transport_cb=transport_cb)

    await player._on_pause()

    transport_cb.assert_awaited_once_with("aabbccddeeff", "pause")
    assert player._state.status == "Paused"


@pytest.mark.asyncio
async def test_next_and_previous_route_to_transport_without_state_change():
    transport_cb = AsyncMock(return_value=True)
    player = _make_player(transport_cb=transport_cb)
    player._state.status = "Playing"

    await player._on_next()
    await player._on_previous()

    assert [c.args for c in transport_cb.await_args_list] == [
        ("aabbccddeeff", "next"),
        ("aabbccddeeff", "previous"),
    ]
    assert player._state.status == "Playing"  # transport handles track change, status untouched


@pytest.mark.asyncio
async def test_transport_callback_failure_does_not_mutate_state():
    transport_cb = AsyncMock(return_value=False)  # MA refused
    player = _make_player(transport_cb=transport_cb)
    player._state.status = "Stopped"

    await player._on_play()

    assert player._state.status == "Stopped"  # rollback / no transition on failure


@pytest.mark.asyncio
async def test_transport_callback_exception_is_swallowed_with_log():
    transport_cb = AsyncMock(side_effect=RuntimeError("MA gone"))
    player = _make_player(transport_cb=transport_cb)
    player._state.status = "Stopped"

    # Must not raise — speaker buttons should never crash the bridge.
    await player._on_play()

    assert player._state.status == "Stopped"


# ── Volume write echo suppression ──────────────────────────────────────


@pytest.mark.asyncio
async def test_inbound_volume_write_routes_to_volume_callback():
    volume_cb = AsyncMock(return_value=True)
    player = _make_player(volume_cb=volume_cb)

    # MPRIS Volume property is double 0.0..1.0
    await player._on_volume_set(0.42)

    volume_cb.assert_awaited_once_with("aabbccddeeff", 42)
    assert player._state.volume_pct == 42


@pytest.mark.asyncio
async def test_inbound_volume_write_echo_is_suppressed():
    # We just set volume to 75 → PropertiesChanged → BlueZ → speaker → BlueZ
    # echoes back via AVRCP Volume → MPRIS volume_set.  Must NOT round-trip
    # to MA (would loop).
    volume_cb = AsyncMock(return_value=True)
    player = _make_player(volume_cb=volume_cb)
    player._volume_echo_pending = 75
    player._state.volume_pct = 75

    await player._on_volume_set(0.75)

    volume_cb.assert_not_awaited()
    # echo guard consumed:
    assert player._volume_echo_pending is None


@pytest.mark.asyncio
async def test_inbound_volume_write_clamps_out_of_range():
    volume_cb = AsyncMock(return_value=True)
    player = _make_player(volume_cb=volume_cb)

    await player._on_volume_set(2.5)  # > 1.0
    assert player._state.volume_pct == 100
    await player._on_volume_set(-0.5)  # < 0.0
    assert player._state.volume_pct == 0


# ── dbus_fast ServiceInterface wiring (smoke) ──────────────────────────


def test_build_player_iface_exposes_mpris_methods_and_properties():
    """The dbus_fast ServiceInterface must expose the MPRIS contract.

    BlueZ relies on these symbol names being present on
    ``org.mpris.MediaPlayer2.Player`` — verify directly that the iface
    object exports them.  (The @method decorator strips return values on
    direct invocation, same trap as in test_pairing_agent.py.)
    """
    player = _make_player()
    iface = _build_player_iface(player)

    # Required MPRIS Player methods
    for name in ("Play", "Pause", "PlayPause", "Stop", "Next", "Previous"):
        assert hasattr(iface, name), f"missing MPRIS method {name}"

    # Required MPRIS Player properties (read-only except Volume)
    for name in ("PlaybackStatus", "Metadata", "Volume", "Position", "CanGoNext", "CanPause"):
        assert hasattr(iface, name), f"missing MPRIS property {name}"


def test_build_player_iface_wires_emit_callback_to_iface():
    """After ``_build_player_iface``, the player's ``_emit_properties_changed``
    must be wired to the iface's ``emit_properties_changed`` (else outbound
    state setters silently no-op on the bus).

    We don't assert on the iface's emit-changed call directly because dbus_fast
    requires a connected bus to actually fan out PropertiesChanged signals;
    the test only verifies the wiring callable was replaced with something
    other than the no-op default.
    """
    player = _make_player()
    default_emit = player._emit_properties_changed

    _build_player_iface(player)

    assert player._emit_properties_changed is not default_emit


def test_build_player_iface_playback_status_returns_current_state():
    player = _make_player()
    player._state.status = "Paused"
    iface = _build_player_iface(player)

    # Direct property access on the iface (the @dbus_property decorator
    # exposes the property as a Python attribute that delegates to the
    # underlying state on read).
    assert iface.PlaybackStatus == "Paused"
