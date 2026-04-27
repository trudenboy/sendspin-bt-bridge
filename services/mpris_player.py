"""Per-device MPRIS MediaPlayer2.Player export over D-Bus.

BlueZ bridges physical speaker AVRCP buttons (Play/Pause/Next/Previous,
absolute volume) to any MPRIS player exported on the system bus.  By
exporting one ``MprisPlayer`` per connected BT device we get bidirectional
hardware integration:

  Speaker button → BlueZ → MprisPlayer.<Method>() → MA queue command
  MA playback state change → MprisPlayer.set_*() → PropertiesChanged
                           → BlueZ → speaker LED / display update

State management + callback dispatch live in ``MprisPlayer``; the
``dbus_fast`` ``ServiceInterface`` wrapper is built lazily by
``_build_player_iface()`` so this module imports cleanly on dev hosts
without ``dbus_fast`` and the inner class can be exercised directly in
tests (same pattern as ``services/pairing_agent.py``).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    # Callback signatures.  The transport callback returns True on
    # success (state mutation allowed) or False (no state change).
    TransportCommand = str  # one of: "play", "pause", "stop", "next", "previous"
    TransportCallback = Callable[[str, TransportCommand], Awaitable[bool]]
    VolumeCallback = Callable[[str, int], Awaitable[bool]]
    PropertiesChangedFn = Callable[[dict[str, Any]], None]

logger = logging.getLogger(__name__)

_VALID_PLAYBACK_STATUSES = frozenset({"Playing", "Paused", "Stopped"})


@dataclass
class PlaybackState:
    """Mirror of the MPRIS Player property surface that BlueZ exposes."""

    status: str = "Stopped"  # one of _VALID_PLAYBACK_STATUSES
    volume_pct: int = 100  # 0..100, normalised from MPRIS double 0.0..1.0
    metadata: dict[str, Any] = field(default_factory=dict)
    position_us: int = 0  # microseconds — Position is read-only in MPRIS


class MprisPlayer:
    """One MPRIS Player export per connected BT device.

    Owns the playback-state mirror, the inbound-callback dispatch, and
    the Volume echo guard.  The actual D-Bus ``ServiceInterface`` object
    is built separately by :func:`_build_player_iface` so this class can
    be tested without requiring ``dbus_fast`` to be importable, and so
    the iface can swap out without touching state-management logic.

    Echo guard: when ``set_volume`` is invoked (MA → us → speaker), the
    speaker may echo the same volume back via AVRCP shortly after.  We
    arm ``_volume_echo_pending`` with the value we just sent; the next
    inbound write at exactly that level is dropped.  Mirrors the pattern
    in ``services/pa_volume_controller.py``.
    """

    def __init__(
        self,
        mac: str,
        player_id: str,
        transport_callback: TransportCallback,
        volume_callback: VolumeCallback,
        client: Any = None,
    ) -> None:
        self.mac = mac
        self.player_id = player_id
        # Optional reference to the SendspinClient this player represents.
        # The inbound dispatch resolver reads this from the registry to
        # find the right client when AVRCP source correlation succeeds.
        # Kept untyped (Any) to avoid a hard import cycle with
        # ``sendspin_client.py``.
        self.client: Any = client
        self._state = PlaybackState()
        self._transport_cb = transport_callback
        self._volume_cb = volume_callback
        # Set by _build_player_iface once the ServiceInterface is created;
        # tests override directly to capture emitted PropertiesChanged.
        self._emit_properties_changed: PropertiesChangedFn = lambda _changes: None
        self._volume_echo_pending: int | None = None

    # ── Outbound state setters (called by ma_monitor reverse hook) ──

    async def set_playback_status(self, status: str) -> None:
        """Update PlaybackStatus and emit PropertiesChanged.  No-op when unchanged."""
        if status not in _VALID_PLAYBACK_STATUSES:
            raise ValueError(f"invalid playback status: {status!r}")
        if self._state.status == status:
            return
        self._state.status = status
        self._emit_properties_changed({"PlaybackStatus": status})

    async def set_metadata(self, metadata: dict[str, Any]) -> None:
        """Update Metadata and emit PropertiesChanged.

        BlueZ forwards the metadata dict to the speaker; supported keys
        depend on speaker firmware.  Callers should pass standard MPRIS
        Metadata keys (``xesam:title``, ``xesam:artist`` (list),
        ``xesam:album``, ``mpris:artUrl``, ``mpris:length`` (microseconds)).
        """
        if self._state.metadata == metadata:
            return
        self._state.metadata = dict(metadata)
        self._emit_properties_changed({"Metadata": dict(metadata)})

    async def set_volume(self, volume_pct: int) -> None:
        """Update Volume property + arm echo guard.

        ``volume_pct`` is 0..100; MPRIS Volume property is double 0.0..1.0.
        After the update the next inbound write at this exact level is
        suppressed so AVRCP echoes from the speaker don't loop back to MA.
        """
        clamped = max(0, min(100, int(volume_pct)))
        if self._state.volume_pct == clamped:
            # Still arm echo guard — caller may be re-asserting the value.
            self._volume_echo_pending = clamped
            return
        self._state.volume_pct = clamped
        self._volume_echo_pending = clamped
        self._emit_properties_changed({"Volume": clamped / 100.0})

    # ── Inbound AVRCP method dispatch (called by D-Bus iface) ──────

    async def _on_play(self) -> None:
        logger.info("MprisPlayer[%s]: AVRCP Play", self.mac)
        await self._dispatch_transport("play", post_status="Playing")

    async def _on_pause(self) -> None:
        logger.info("MprisPlayer[%s]: AVRCP Pause", self.mac)
        await self._dispatch_transport("pause", post_status="Paused")

    async def _on_play_pause(self) -> None:
        # MPRIS PlayPause: toggle.  Mirror PlaybackStatus to decide which
        # transport command to send.  Status mutation done by the chosen
        # branch via _dispatch_transport.
        logger.info("MprisPlayer[%s]: AVRCP PlayPause (current=%s)", self.mac, self._state.status)
        if self._state.status == "Playing":
            await self._on_pause()
        else:
            await self._on_play()

    async def _on_stop(self) -> None:
        logger.info("MprisPlayer[%s]: AVRCP Stop", self.mac)
        await self._dispatch_transport("stop", post_status="Stopped")

    async def _on_next(self) -> None:
        # Track-change commands don't mutate PlaybackStatus locally —
        # MA's player_updated event will push the new status back.
        logger.info("MprisPlayer[%s]: AVRCP Next", self.mac)
        await self._dispatch_transport("next", post_status=None)

    async def _on_previous(self) -> None:
        logger.info("MprisPlayer[%s]: AVRCP Previous", self.mac)
        await self._dispatch_transport("previous", post_status=None)

    async def _on_volume_set(self, mpris_double: float) -> None:
        """MPRIS Volume property setter.  ``mpris_double`` is 0.0..1.0."""
        clamped_pct = max(0, min(100, int(round(mpris_double * 100))))
        if self._volume_echo_pending == clamped_pct:
            # Echo from our own outbound set_volume — suppress and clear guard.
            self._volume_echo_pending = None
            return
        try:
            ok = await self._volume_cb(self.player_id, clamped_pct)
        except Exception as exc:
            logger.warning("MprisPlayer[%s]: volume callback raised: %s", self.mac, exc)
            return
        if ok:
            self._state.volume_pct = clamped_pct

    async def _dispatch_transport(self, command: str, *, post_status: str | None) -> None:
        try:
            ok = await self._transport_cb(self.player_id, command)
        except Exception as exc:
            logger.warning(
                "MprisPlayer[%s]: transport callback %r raised: %s",
                self.mac,
                command,
                exc,
            )
            return
        if ok and post_status is not None:
            self._state.status = post_status
            self._emit_properties_changed({"PlaybackStatus": post_status})


def _build_player_iface(player: MprisPlayer):
    """Build the dbus_fast ServiceInterface object for *player*.

    Lazy import so ``import services.mpris_player`` stays cheap on dev
    hosts without ``dbus_fast`` and the module's pure logic
    (``MprisPlayer`` class) remains testable independently.

    All MPRIS properties are explicitly marked ``PropertyAccess.READ``
    except ``Volume`` which is ``READ_WRITE``.  Skipping the explicit
    ``access=`` argument trips a ``dbus_fast`` trap where properties
    default to writable but lack a setter, producing a runtime crash
    ("writable but does not have a setter") on the first BlueZ probe.
    """
    from dbus_fast.service import (  # type: ignore[import-untyped]
        PropertyAccess,
        ServiceInterface,
        dbus_property,
        method,
    )
    from dbus_fast.signature import Variant  # type: ignore[import-untyped]

    class _PlayerIface(ServiceInterface):
        def __init__(self) -> None:
            super().__init__("org.mpris.MediaPlayer2.Player")

        # ── Methods ────────────────────────────────────────────

        @method()
        async def Play(self):  # type: ignore[no-untyped-def]
            await player._on_play()

        @method()
        async def Pause(self):  # type: ignore[no-untyped-def]
            await player._on_pause()

        @method()
        async def PlayPause(self):  # type: ignore[no-untyped-def]
            await player._on_play_pause()

        @method()
        async def Stop(self):  # type: ignore[no-untyped-def]
            await player._on_stop()

        @method()
        async def Next(self):  # type: ignore[no-untyped-def]
            await player._on_next()

        @method()
        async def Previous(self):  # type: ignore[no-untyped-def]
            await player._on_previous()

        # ── Properties (read-only unless noted) ────────────────

        @dbus_property(access=PropertyAccess.READ)
        def PlaybackStatus(self) -> "s":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            return player._state.status

        @dbus_property(access=PropertyAccess.READ)
        def Metadata(self) -> "a{sv}":  # type: ignore[valid-type,name-defined]  # noqa: F722
            return _metadata_to_variant_dict(player._state.metadata, Variant)

        @dbus_property(access=PropertyAccess.READWRITE)
        def Volume(self) -> "d":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            return player._state.volume_pct / 100.0

        @Volume.setter  # type: ignore[no-redef]
        def Volume(self, value: "d"):  # type: ignore[valid-type,name-defined,no-untyped-def]  # noqa: F821, UP037
            # Schedule inbound write — MPRIS setter must be sync but we
            # need to run the async dispatch on the loop.
            asyncio.ensure_future(player._on_volume_set(float(value)))

        @dbus_property(access=PropertyAccess.READ)
        def Position(self) -> "x":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            return player._state.position_us

        # Capability properties — advertise full MPRIS Player surface so
        # BlueZ doesn't filter our buttons.  All hardcoded True; the
        # speaker's own AVRCP capability bits decide what actually shows
        # up on its UI.

        @dbus_property(access=PropertyAccess.READ)
        def CanGoNext(self) -> "b":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanGoPrevious(self) -> "b":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanPlay(self) -> "b":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanPause(self) -> "b":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanSeek(self) -> "b":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            return False

        @dbus_property(access=PropertyAccess.READ)
        def CanControl(self) -> "b":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            return True

        @dbus_property(access=PropertyAccess.READ)
        def Rate(self) -> "d":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            return 1.0

        @dbus_property(access=PropertyAccess.READ)
        def MinimumRate(self) -> "d":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            return 1.0

        @dbus_property(access=PropertyAccess.READ)
        def MaximumRate(self) -> "d":  # type: ignore[valid-type,name-defined]  # noqa: F821, UP037
            return 1.0

    iface = _PlayerIface()

    # Wire the player's outbound emit callback to the iface so
    # set_playback_status / set_metadata / set_volume actually push
    # PropertiesChanged on the bus.
    def _emit(changes: dict[str, Any]) -> None:
        # dbus_fast handles signature derivation for primitive properties
        # (PlaybackStatus 's', Volume 'd') from the declared @dbus_property
        # signature, but ``Metadata`` is ``a{sv}`` — values must already be
        # Variants or dbus_fast raises a type error and the speaker display
        # never refreshes.  Coerce the flat dict before forwarding.
        try:
            for key, value in changes.items():
                emit_value = value
                if key == "Metadata" and isinstance(value, dict):
                    emit_value = _metadata_to_variant_dict(value, Variant)
                iface.emit_properties_changed({key: emit_value})
        except Exception as exc:
            logger.debug("MprisPlayer[%s]: emit_properties_changed failed: %s", player.mac, exc)

    player._emit_properties_changed = _emit
    return iface


def _normalize_mac(mac: str) -> str:
    """Lowercase, strip separators — used as the registry's canonical key.

    Operators and the Claim Audio URL may pass MACs with ``:``/``-``
    separators or none at all; the registry must be tolerant.
    """
    return "".join(ch.lower() for ch in mac if ch.isalnum())


class MprisRegistry:
    """Process-level lookup table for active per-device MprisPlayer instances.

    The BluetoothManager on_connected/on_disconnected hooks register and
    unregister entries; the MA monitor reverse hook and the Claim Audio
    endpoint use the registry to find the right player for a given MAC or
    MA player_id.

    This class is pure-Python state — D-Bus export is layered on top by
    callers that hold a live ``dbus_fast`` MessageBus.  Keeping the registry
    transport-agnostic means the lookup logic can be exercised in tests
    without requiring D-Bus, and the same registry can later back a UI
    diagnostic view without re-implementing the bookkeeping.
    """

    def __init__(self) -> None:
        self._by_mac: dict[str, MprisPlayer] = {}
        # The registry is touched from BT manager threads (register /
        # unregister via the on_connected / on_disconnected hooks), Flask
        # request threads (Claim Audio endpoint), and the asyncio loop (MA
        # monitor reverse hook).  Without serialisation the iterators in
        # get_by_player_id / active_macs raise "dictionary changed size
        # during iteration" when a concurrent register / unregister lands.
        self._lock = threading.Lock()

    def register(self, mac: str, player: MprisPlayer) -> None:
        """Register *player* under *mac*; replaces any prior entry for the MAC."""
        with self._lock:
            self._by_mac[_normalize_mac(mac)] = player

    def unregister(self, mac: str) -> MprisPlayer | None:
        """Remove and return the player for *mac*; ``None`` if absent."""
        with self._lock:
            return self._by_mac.pop(_normalize_mac(mac), None)

    def get(self, mac: str) -> MprisPlayer | None:
        """Look up the active MprisPlayer for *mac*, or ``None``."""
        with self._lock:
            return self._by_mac.get(_normalize_mac(mac))

    def get_by_player_id(self, player_id: str) -> MprisPlayer | None:
        """Reverse lookup keyed on the MprisPlayer's ``player_id`` attribute.

        Used by the MA monitor reverse hook: MA pushes ``player_id`` updates
        and we need to map back to the BT-attached MprisPlayer.
        """
        with self._lock:
            snapshot = list(self._by_mac.values())
        for player in snapshot:
            if player.player_id == player_id:
                return player
        return None

    def all_players(self) -> list[MprisPlayer]:
        """Return a snapshot list of all currently-registered players.

        Used by the inbound AVRCP dispatch resolver's streaming-fallback
        branch — when the source-correlation tracker can't pin down a
        single MAC, we look across all registered players to see if
        exactly one has an actively-streaming client.
        """
        with self._lock:
            return list(self._by_mac.values())

    def active_macs(self) -> list[str]:
        """Return the canonical MACs currently holding an MprisPlayer.

        Returns the *original-case* MACs as registered, not the normalised
        form, so UI surfaces ("Claim audio" buttons) can render them in the
        format users expect to see.
        """
        with self._lock:
            return [player.mac for player in self._by_mac.values()]


# Process-wide singleton.  Exposed via get_registry() so tests can patch the
# accessor; instantiating fresh MprisRegistry per test stays cheap.
_REGISTRY = MprisRegistry()


def get_registry() -> MprisRegistry:
    """Return the process-wide MprisRegistry.

    The registry is shared across:
      - services/device_activation.py (creates/destroys players via BT hooks)
      - services/ma_monitor.py (pushes MA playback state into players)
      - routes/api_bt.py (Claim Audio endpoint looks up players by MAC)
    """
    return _REGISTRY


def resolve_avrcp_source_client(
    *,
    registry: MprisRegistry | None = None,
    tracker: Any = None,
    default_client: Any = None,
    window_s: float | None = None,
    now: float | None = None,
) -> Any:
    """Determine which SendspinClient should receive an inbound AVRCP command.

    BlueZ's AVRCP TG → MPRIS forwarder strips source-CT identity (see
    ``services/avrcp_source_tracker``).  This resolver applies two
    correlation strategies in order:

    1. **Recent MediaPlayer1 activity** — the AvrcpSourceTracker records a
       timestamp every time *any* speaker emits a
       ``org.bluez.MediaPlayer1.PropertiesChanged`` signal.  When the
       tracker reports a recent MAC and that MAC has a registered
       MprisPlayer with a non-None client, that's our answer.
    2. **Single-streaming-client fallback** — if no MAC was recent enough
       (some speaker firmwares don't emit Status updates around button
       presses, especially Next/Previous), and exactly *one* registered
       client has ``audio_streaming=True``, route there.  Single-source
       audio is the common-case shape; this handles it gracefully.

    Returns ``None`` when neither strategy yields a unique client — the
    caller should drop the command.  Mis-routing is the bug we're fixing,
    so dropping is preferable to guessing wrong.

    The ``registry``/``tracker``/``window_s`` arguments default to the
    process-wide singletons — production callers pass nothing; tests
    inject fakes.
    """
    from services.avrcp_source_tracker import (
        DEFAULT_CORRELATION_WINDOW_S,
    )
    from services.avrcp_source_tracker import (
        get_tracker as _get_tracker,
    )

    if registry is None:
        registry = get_registry()
    if tracker is None:
        tracker = _get_tracker()
    if window_s is None:
        window_s = DEFAULT_CORRELATION_WINDOW_S

    recent_mac = tracker.get_recent_active(window_s=window_s, now=now)
    if recent_mac:
        player = registry.get(recent_mac)
        if player is not None and player.client is not None:
            return player.client
        # Fall through — orphan tracker entry or player without a client.

    streaming = [
        p.client
        for p in registry.all_players()
        if p.client is not None and bool(p.client.status.get("audio_streaming"))
    ]
    if len(streaming) == 1:
        return streaming[0]

    # Strategy 3: default_client captured at registration time.  Handles dumb
    # speakers that have no AVRCP TG (no MediaPlayer1 → tracker never fires)
    # and are paused (audio_streaming=False → Strategy 2 also misses).  In the
    # multi-device case Strategy 1 should have already fired for smart devices;
    # this is the single-device / dumb-speaker safety net.
    if default_client is not None:
        return default_client

    return None


def _metadata_to_variant_dict(metadata: dict[str, Any], Variant: Any) -> dict[str, Any]:
    """Convert plain Python metadata to ``dbus_fast`` Variant dict.

    MPRIS Metadata is ``a{sv}`` — string keys to variant values.  Keys
    follow the xesam: / mpris: namespace conventions.  We coerce the
    most common types; unknown keys pass through as strings.
    """
    out: dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, str):
            out[key] = Variant("s", value)
        elif isinstance(value, bool):
            out[key] = Variant("b", value)
        elif isinstance(value, int):
            out[key] = Variant("x", value)
        elif isinstance(value, float):
            out[key] = Variant("d", value)
        elif isinstance(value, list):
            # xesam:artist / xesam:albumArtist / xesam:genre are arrays of strings.
            out[key] = Variant("as", [str(v) for v in value])
        else:
            out[key] = Variant("s", str(value))
    return out
