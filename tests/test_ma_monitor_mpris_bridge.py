"""Tests for the MA → MprisPlayer reverse-state bridge.

When the MA monitor receives a new now-playing snapshot, each MprisPlayer
attached to a participating BT speaker must reflect the MA state via
PropertiesChanged so the speaker's display / LEDs update without polling.

The bridge is tested via the pure ``push_now_playing_to_mpris`` helper —
it takes a ``fresh`` snapshot dict, the active-clients list, and the
MprisRegistry, and pushes state to whichever players match.  Unit-testing
through this seam keeps the test fast (no MA WebSocket, no D-Bus) while
exercising the exact mapping logic that runs in production.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.ma_monitor import push_now_playing_to_mpris
from services.mpris_player import MprisPlayer, MprisRegistry


def _player_for(mac: str, player_id: str) -> MprisPlayer:
    return MprisPlayer(
        mac=mac,
        player_id=player_id,
        transport_callback=AsyncMock(return_value=True),
        volume_callback=AsyncMock(return_value=True),
    )


def _stub_client(player_id: str, mac: str, group_id: str | None = None):
    """Mimics SendspinClient surface used by the bridge mapping."""
    bt = SimpleNamespace(mac_address=mac)
    return SimpleNamespace(
        player_id=player_id,
        bt_manager=bt,
        status={"group_id": group_id} if group_id else {},
    )


@pytest.mark.asyncio
async def test_solo_player_state_pushed_to_matching_mpris_player():
    """``fresh`` keyed by player_id (solo) → MprisPlayer with that player_id
    receives PlaybackStatus + Metadata updates."""
    reg = MprisRegistry()
    player = _player_for("AA:BB:CC:DD:EE:FF", "uuid-solo-1")
    reg.register(player.mac, player)

    emitted: list[dict] = []
    player._emit_properties_changed = lambda c: emitted.append(dict(c))

    client = _stub_client("uuid-solo-1", "AA:BB:CC:DD:EE:FF")
    fresh = {
        "uuid-solo-1": {
            "state": "playing",
            "track": "T",
            "artist": "A",
            "album": "AL",
            "image_url": "http://x/a.jpg",
        }
    }

    await push_now_playing_to_mpris(fresh, [client], reg)

    assert any(c.get("PlaybackStatus") == "Playing" for c in emitted), emitted
    assert any("Metadata" in c for c in emitted), emitted


@pytest.mark.asyncio
async def test_syncgroup_state_pushed_to_all_grouped_clients():
    """``fresh`` keyed by syncgroup_id → every BT client whose status.group_id
    matches must receive the state update on its MprisPlayer."""
    reg = MprisRegistry()
    p1 = _player_for("AA:BB:CC:DD:EE:01", "uuid-A")
    p2 = _player_for("AA:BB:CC:DD:EE:02", "uuid-B")
    reg.register(p1.mac, p1)
    reg.register(p2.mac, p2)

    emitted: dict[str, list[dict]] = {p1.mac: [], p2.mac: []}
    p1._emit_properties_changed = lambda c, m=p1.mac: emitted[m].append(dict(c))
    p2._emit_properties_changed = lambda c, m=p2.mac: emitted[m].append(dict(c))

    c1 = _stub_client("uuid-A", "AA:BB:CC:DD:EE:01", group_id="syncgroup-XYZ")
    c2 = _stub_client("uuid-B", "AA:BB:CC:DD:EE:02", group_id="syncgroup-XYZ")
    fresh = {"syncgroup-XYZ": {"state": "paused", "track": "T", "artist": "A"}}

    await push_now_playing_to_mpris(fresh, [c1, c2], reg)

    assert any(c.get("PlaybackStatus") == "Paused" for c in emitted[p1.mac]), emitted
    assert any(c.get("PlaybackStatus") == "Paused" for c in emitted[p2.mac]), emitted


@pytest.mark.asyncio
async def test_idle_state_translates_to_stopped():
    """MA 'idle' (no current track) is the MPRIS 'Stopped' state.  Speakers
    expecting one of {Playing, Paused, Stopped} on the wire don't accept
    arbitrary strings."""
    reg = MprisRegistry()
    player = _player_for("AA:BB:CC:DD:EE:01", "uuid-1")
    player._state.status = "Playing"  # was playing — must transition to Stopped
    reg.register(player.mac, player)

    emitted: list[dict] = []
    player._emit_properties_changed = lambda c: emitted.append(dict(c))

    client = _stub_client("uuid-1", "AA:BB:CC:DD:EE:01")
    fresh = {"uuid-1": {"state": "idle", "track": "", "artist": ""}}

    await push_now_playing_to_mpris(fresh, [client], reg)

    assert any(c.get("PlaybackStatus") == "Stopped" for c in emitted), emitted


@pytest.mark.asyncio
async def test_no_op_when_player_id_has_no_mpris_player():
    """If MA pushes state for a player_id that doesn't have a connected
    BT speaker (no MprisPlayer registered), the bridge silently skips it
    rather than raising."""
    reg = MprisRegistry()  # empty
    client = _stub_client("uuid-no-bt", "AA:BB:CC:DD:EE:01")
    fresh = {"uuid-no-bt": {"state": "playing", "track": "X"}}

    # Must not raise.
    await push_now_playing_to_mpris(fresh, [client], reg)


@pytest.mark.asyncio
async def test_metadata_keys_mapped_to_xesam_namespace():
    """MPRIS Metadata uses the xesam: / mpris: namespaces.  The bridge
    must translate MA's flat keys (track/artist/album/image_url) into the
    xesam: keys speakers actually look at, otherwise the speaker display
    stays blank."""
    reg = MprisRegistry()
    player = _player_for("AA:BB:CC:DD:EE:01", "uuid-meta")
    reg.register(player.mac, player)

    emitted: list[dict] = []
    player._emit_properties_changed = lambda c: emitted.append(dict(c))

    client = _stub_client("uuid-meta", "AA:BB:CC:DD:EE:01")
    fresh = {
        "uuid-meta": {
            "state": "playing",
            "track": "Hey Jude",
            "artist": "The Beatles",
            "album": "1",
            "image_url": "http://x/cover.jpg",
        }
    }

    await push_now_playing_to_mpris(fresh, [client], reg)

    md_emit = next((c["Metadata"] for c in emitted if "Metadata" in c), None)
    assert md_emit is not None, emitted
    assert md_emit.get("xesam:title") == "Hey Jude"
    assert md_emit.get("xesam:artist") == ["The Beatles"]
    assert md_emit.get("xesam:album") == "1"
    assert md_emit.get("mpris:artUrl") == "http://x/cover.jpg"
