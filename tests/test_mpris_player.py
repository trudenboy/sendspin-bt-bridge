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

from types import SimpleNamespace
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


# ── MprisRegistry — process-level lookup table for active players ──────


def test_registry_register_then_get_returns_same_player():
    """register(mac, player) stores the instance; get(mac) returns it.

    Used by the BluetoothManager on_connected hook (services/device_activation.py)
    and by the MA monitor reverse hook to find the right MprisPlayer for a
    player_id arriving from MA.
    """
    from services.mpris_player import MprisRegistry

    reg = MprisRegistry()
    player = _make_player()
    reg.register("AA:BB:CC:DD:EE:FF", player)

    assert reg.get("AA:BB:CC:DD:EE:FF") is player


def test_registry_get_normalizes_mac_case_and_separators():
    """Lookup must be MAC-case-insensitive and tolerate ``-`` vs ``:`` so the
    Claim Audio endpoint works regardless of how the operator typed the URL."""
    from services.mpris_player import MprisRegistry

    reg = MprisRegistry()
    player = _make_player()
    reg.register("aa:bb:cc:dd:ee:ff", player)

    assert reg.get("AA-BB-CC-DD-EE-FF") is player
    assert reg.get("aabbccddeeff") is player


def test_registry_unregister_drops_player():
    """unregister(mac) removes the player; subsequent get() returns None."""
    from services.mpris_player import MprisRegistry

    reg = MprisRegistry()
    player = _make_player()
    reg.register("AA:BB:CC:DD:EE:FF", player)
    reg.unregister("AA:BB:CC:DD:EE:FF")

    assert reg.get("AA:BB:CC:DD:EE:FF") is None


def test_registry_unregister_unknown_mac_is_silent_noop():
    """unregister() must tolerate an already-removed MAC (race with disconnect
    transition firing twice or operator double-clicking Release)."""
    from services.mpris_player import MprisRegistry

    reg = MprisRegistry()
    reg.unregister("AA:BB:CC:DD:EE:FF")  # must not raise


def test_registry_get_by_player_id_lookup():
    """The MA monitor reverse hook starts with a player_id (MA-side identifier)
    and needs to find the corresponding MprisPlayer.  Reverse lookup is keyed
    on the player_id stored on the MprisPlayer instance."""
    from services.mpris_player import MprisRegistry

    reg = MprisRegistry()
    player = _make_player()  # player_id="aabbccddeeff"
    reg.register("AA:BB:CC:DD:EE:FF", player)

    assert reg.get_by_player_id("aabbccddeeff") is player
    assert reg.get_by_player_id("nonexistent") is None


def test_registry_register_replaces_existing_player_for_same_mac():
    """If a previous MprisPlayer is still registered (e.g. transition fired
    twice without intervening unregister), register() must replace cleanly —
    no AssertionError, no leak.  The previous player is silently dropped."""
    from services.mpris_player import MprisRegistry

    reg = MprisRegistry()
    first = _make_player()
    second = _make_player()
    reg.register("AA:BB:CC:DD:EE:FF", first)
    reg.register("AA:BB:CC:DD:EE:FF", second)

    assert reg.get("AA:BB:CC:DD:EE:FF") is second


@pytest.mark.asyncio
async def test_build_player_iface_emit_wraps_metadata_values_in_variant():
    """``Metadata`` is an ``a{sv}`` MPRIS property; dbus_fast expects each
    value to be a ``Variant``, not a raw Python primitive.  When
    ``set_metadata`` triggers PropertiesChanged, the wired emit closure
    must convert the flat dict via ``_metadata_to_variant_dict`` before
    handing it to dbus_fast — otherwise the bus library raises a type
    error and the speaker display never updates.

    Regression test for Copilot review on PR #195: previous _emit()
    forwarded the raw dict and only mentioned coercion in a comment.
    """
    from dbus_fast.signature import Variant  # type: ignore[import-untyped]

    player = _make_player()
    iface = _build_player_iface(player)

    captured: list[dict] = []
    iface.emit_properties_changed = lambda changes: captured.append(dict(changes))

    await player.set_metadata({"xesam:title": "T", "xesam:artist": ["A"], "mpris:length": 1234})

    md_emit = next(
        (c["Metadata"] for c in captured if "Metadata" in c),
        None,
    )
    assert md_emit is not None, captured
    # Each value must be a Variant — not a raw string / int / list.
    for key, value in md_emit.items():
        assert isinstance(value, Variant), f"Metadata[{key}] is {type(value).__name__}, want Variant"


def test_registry_active_macs_lists_currently_registered():
    """The Claim Audio UI button needs to know which MACs have an active
    MprisPlayer (so it can hide the button for offline devices)."""
    from services.mpris_player import MprisRegistry

    reg = MprisRegistry()
    p1 = MprisPlayer("AA:BB:CC:DD:EE:01", "1", AsyncMock(), AsyncMock())
    p2 = MprisPlayer("AA:BB:CC:DD:EE:02", "2", AsyncMock(), AsyncMock())
    reg.register(p1.mac, p1)
    reg.register(p2.mac, p2)

    assert sorted(reg.active_macs()) == ["AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"]


# ── resolve_avrcp_source_client — inbound dispatch correlation ─────────


def test_resolver_returns_client_for_recently_active_mac():
    """Happy path: tracker has a recent MAC → registry has that player →
    that player has a client → resolver returns it.

    This is the case that fixes the VM 105 mis-route: speaker WH presses
    Next, BlueZ forwards anonymously to whichever MprisPlayer it picked
    as addressed (could be ENEBY's), the player's transport_callback
    consults the resolver, the resolver sees WH's MAC was the most
    recent MediaPlayer1.PropertiesChanged source, and returns WH's client.
    """
    from services.avrcp_source_tracker import AvrcpSourceTracker
    from services.mpris_player import MprisPlayer, MprisRegistry, resolve_avrcp_source_client

    wh_client = object()  # opaque sentinel — resolver doesn't introspect it
    eneby_client = object()
    wh = MprisPlayer("80:99:E7:C2:0B:D3", "wh-id", AsyncMock(), AsyncMock(), client=wh_client)
    eneby = MprisPlayer("6C:5C:3D:35:17:99", "eneby-id", AsyncMock(), AsyncMock(), client=eneby_client)
    registry = MprisRegistry()
    registry.register(wh.mac, wh)
    registry.register(eneby.mac, eneby)

    tracker = AvrcpSourceTracker()
    tracker.note_activity("80:99:E7:C2:0B:D3", now=100.0)

    resolved = resolve_avrcp_source_client(registry=registry, tracker=tracker, default_client=eneby_client, now=100.5)

    assert resolved is wh_client


def test_resolver_falls_back_to_single_streaming_client_when_no_recent_source():
    """No MediaPlayer1 activity in the window — but exactly one client is
    actively streaming.  Route to it (single-source heuristic; matches
    the scyto fallback for the case where speaker firmware doesn't emit
    Status updates around button presses)."""
    from services.avrcp_source_tracker import AvrcpSourceTracker
    from services.mpris_player import MprisPlayer, MprisRegistry, resolve_avrcp_source_client

    streaming_client = SimpleNamespace(status={"audio_streaming": True})
    quiet_client = SimpleNamespace(status={"audio_streaming": False})
    p1 = MprisPlayer("AA:BB:CC:DD:EE:01", "1", AsyncMock(), AsyncMock(), client=streaming_client)
    p2 = MprisPlayer("AA:BB:CC:DD:EE:02", "2", AsyncMock(), AsyncMock(), client=quiet_client)
    registry = MprisRegistry()
    registry.register(p1.mac, p1)
    registry.register(p2.mac, p2)

    tracker = AvrcpSourceTracker()  # no activity recorded

    resolved = resolve_avrcp_source_client(registry=registry, tracker=tracker, default_client=None, now=100.0)

    assert resolved is streaming_client


def test_resolver_returns_none_when_no_recent_and_multiple_streaming():
    """Two streaming clients + no source identifiable → drop the command.

    Mis-routing is the bug we're fixing.  Better to drop than route to
    the wrong device (the operator will press the button again or see
    no effect — clearer signal than the wrong speaker doing the wrong
    thing)."""
    from services.avrcp_source_tracker import AvrcpSourceTracker
    from services.mpris_player import MprisPlayer, MprisRegistry, resolve_avrcp_source_client

    c1 = SimpleNamespace(status={"audio_streaming": True})
    c2 = SimpleNamespace(status={"audio_streaming": True})
    p1 = MprisPlayer("AA:BB:CC:DD:EE:01", "1", AsyncMock(), AsyncMock(), client=c1)
    p2 = MprisPlayer("AA:BB:CC:DD:EE:02", "2", AsyncMock(), AsyncMock(), client=c2)
    registry = MprisRegistry()
    registry.register(p1.mac, p1)
    registry.register(p2.mac, p2)

    tracker = AvrcpSourceTracker()  # nothing recent

    resolved = resolve_avrcp_source_client(registry=registry, tracker=tracker, default_client=None, now=100.0)

    assert resolved is None


def test_resolver_returns_none_when_no_streaming_and_no_recent_source():
    """No activity AND no streaming clients → nothing to do.  Drop."""
    from services.avrcp_source_tracker import AvrcpSourceTracker
    from services.mpris_player import MprisPlayer, MprisRegistry, resolve_avrcp_source_client

    quiet = SimpleNamespace(status={"audio_streaming": False})
    p1 = MprisPlayer("AA:BB:CC:DD:EE:01", "1", AsyncMock(), AsyncMock(), client=quiet)
    registry = MprisRegistry()
    registry.register(p1.mac, p1)

    tracker = AvrcpSourceTracker()

    resolved = resolve_avrcp_source_client(registry=registry, tracker=tracker, default_client=None, now=100.0)

    assert resolved is None


def test_resolver_skips_recent_mac_with_no_registered_player():
    """Stale tracker entry for a MAC that's been disconnected (registry
    entry already gone, but disconnect hook didn't clear() yet) — the
    resolver must fall through to the streaming-fallback rather than
    return None on the first miss.
    """
    from services.avrcp_source_tracker import AvrcpSourceTracker
    from services.mpris_player import MprisPlayer, MprisRegistry, resolve_avrcp_source_client

    streaming = SimpleNamespace(status={"audio_streaming": True})
    p1 = MprisPlayer("AA:BB:CC:DD:EE:01", "1", AsyncMock(), AsyncMock(), client=streaming)
    registry = MprisRegistry()
    registry.register(p1.mac, p1)

    tracker = AvrcpSourceTracker()
    tracker.note_activity("AA:BB:CC:DD:EE:99", now=100.0)  # not in registry

    resolved = resolve_avrcp_source_client(registry=registry, tracker=tracker, default_client=None, now=100.5)

    # Falls through to streaming-fallback → returns the only streaming client.
    assert resolved is streaming


def test_resolver_skips_recent_mac_with_player_but_no_client():
    """A registered player whose client field is None (not yet set, or
    cleared during teardown) — must not return None just because the
    correlation hit landed on it; fall through to the fallback."""
    from services.avrcp_source_tracker import AvrcpSourceTracker
    from services.mpris_player import MprisPlayer, MprisRegistry, resolve_avrcp_source_client

    orphan = MprisPlayer("AA:BB:CC:DD:EE:01", "1", AsyncMock(), AsyncMock(), client=None)
    streaming = SimpleNamespace(status={"audio_streaming": True})
    p2 = MprisPlayer("AA:BB:CC:DD:EE:02", "2", AsyncMock(), AsyncMock(), client=streaming)
    registry = MprisRegistry()
    registry.register(orphan.mac, orphan)
    registry.register(p2.mac, p2)

    tracker = AvrcpSourceTracker()
    tracker.note_activity("AA:BB:CC:DD:EE:01", now=100.0)  # hits orphan

    resolved = resolve_avrcp_source_client(registry=registry, tracker=tracker, default_client=None, now=100.5)

    assert resolved is streaming


def test_resolver_uses_module_singletons_when_called_without_args():
    """The transport_callback closure in production calls
    ``resolve_avrcp_source_client()`` with no args — it should fall back
    to the process-wide registry + tracker singletons.

    This wires the production codepath to the singletons populated by
    ``device_activation`` (registry on connect, tracker via
    MediaPlayer1 subscription).
    """
    from services import avrcp_source_tracker as tracker_mod
    from services import mpris_player as player_mod
    from services.mpris_player import MprisPlayer, resolve_avrcp_source_client

    # Patch BOTH module-level singletons so the call uses our test
    # instances instead of the real process-wide ones (which other tests
    # may have polluted).
    fresh_registry = player_mod.MprisRegistry()
    fresh_tracker = tracker_mod.AvrcpSourceTracker()

    streaming = SimpleNamespace(status={"audio_streaming": True})
    p = MprisPlayer("AA:BB:CC:DD:EE:01", "1", AsyncMock(), AsyncMock(), client=streaming)
    fresh_registry.register(p.mac, p)
    fresh_tracker.note_activity("AA:BB:CC:DD:EE:01", now=100.0)

    import unittest.mock as _mock

    with (
        _mock.patch.object(player_mod, "_REGISTRY", fresh_registry),
        _mock.patch.object(tracker_mod, "_TRACKER", fresh_tracker),
    ):
        resolved = resolve_avrcp_source_client(now=100.5)

    assert resolved is streaming


def test_registry_concurrent_register_and_iterate_does_not_raise():
    """Regression test for Copilot review on PR #195: ``MprisRegistry`` is
    accessed from BT manager threads (register/unregister), Flask request
    threads (Claim Audio endpoint), and the main asyncio loop (MA monitor
    reverse hook).  Without synchronisation, iterating ``_by_mac.values()``
    from one thread while another mutates it raises
    ``RuntimeError: dictionary changed size during iteration``.

    This test stresses the read+write fan-out and asserts no such error
    surfaces.  It does NOT assert on intermediate values (those depend on
    interleaving) — it only proves the operations are safe to interleave.
    """
    import threading

    from services.mpris_player import MprisRegistry

    reg = MprisRegistry()
    seed = MprisPlayer("AA:BB:CC:DD:EE:00", "seed", AsyncMock(), AsyncMock())
    reg.register(seed.mac, seed)

    errors: list[BaseException] = []
    stop = threading.Event()

    # Pre-populate so iterators have material to walk.  The bigger the
    # snapshot, the more bytecode the iterator burns inside ``values()`` —
    # widening the window during which a writer can mutate the underlying
    # dict mid-iteration.
    for i in range(200):
        p = MprisPlayer(f"AA:BB:CC:DD:EE:{i % 256:02X}", f"seed-{i}", AsyncMock(), AsyncMock())
        reg.register(f"AA:BB:CC:DD:EE:{i:04X}", p)

    def _writer() -> None:
        try:
            i = 0
            while not stop.is_set():
                mac = f"AA:BB:CC:DD:E1:{i % 256:02X}"
                reg.register(mac, MprisPlayer(mac, f"w-{i}", AsyncMock(), AsyncMock()))
                reg.unregister(mac)
                i += 1
        except BaseException as exc:
            errors.append(exc)

    def _reader() -> None:
        try:
            for _ in range(20000):
                reg.get_by_player_id("seed")
                reg.active_macs()
        except BaseException as exc:
            errors.append(exc)

    writers = [threading.Thread(target=_writer) for _ in range(4)]
    readers = [threading.Thread(target=_reader) for _ in range(4)]
    for t in writers + readers:
        t.start()
    for t in readers:
        t.join(timeout=15.0)
    stop.set()
    for t in writers:
        t.join(timeout=15.0)

    assert errors == [], f"concurrent registry access raised: {errors[:3]!r}"
