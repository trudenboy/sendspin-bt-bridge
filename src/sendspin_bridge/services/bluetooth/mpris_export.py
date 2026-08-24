"""The D-Bus side of a device's MPRIS player, as one owned resource.

Exporting an MPRIS player means three things that must be undone in order: a
system-bus connection, an exported object path, and a ``Media1.RegisterPlayer``
pointer BlueZ keeps until it is told otherwise.  Connect and disconnect used
to schedule those steps as two independent fire-and-forget coroutines, with
the state kept as attributes on the player object.  Nothing ordered them, so
on a reconnect bounce the unexport read a bus attribute the in-flight export
had not written yet, decided there was nothing to undo, and returned — and
the export then completed, leaving one leaked socket and one registration for
a player already dropped from the registry.  A speaker that reconnect-loops
repeats that until the process restarts.

Here the three steps belong to one object with one lock, so an unexport
either waits for the export it overlaps or finds nothing to undo, and a
generation counter makes a late export abandon a run that has been
superseded.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

__all__ = ["MPRIS_PATH_PREFIX", "MprisExport", "mpris_dbus_path"]

MPRIS_PATH_PREFIX = "/org/sendspin/players/"


def mpris_dbus_path(mac: str) -> str:
    """The object path this bridge exports a device's player at."""
    return MPRIS_PATH_PREFIX + mac.upper().replace(":", "_")


def _default_bus_factory():
    from dbus_fast import BusType
    from dbus_fast.aio import MessageBus

    return MessageBus(bus_type=BusType.SYSTEM)


def _default_player_props() -> dict:
    from dbus_fast.signature import Variant

    return {
        "PlaybackStatus": Variant("s", "Stopped"),
        "LoopStatus": Variant("s", "None"),
        "Rate": Variant("d", 1.0),
        "Shuffle": Variant("b", False),
        "Volume": Variant("d", 1.0),
        "Position": Variant("x", 0),
        "MinimumRate": Variant("d", 1.0),
        "MaximumRate": Variant("d", 1.0),
        "CanGoNext": Variant("b", True),
        "CanGoPrevious": Variant("b", True),
        "CanPlay": Variant("b", True),
        "CanPause": Variant("b", True),
        "CanSeek": Variant("b", False),
        "CanControl": Variant("b", True),
        "Metadata": Variant(
            "a{sv}",
            {
                "xesam:title": Variant("s", "Sendspin Bridge"),
                "xesam:artist": Variant("as", [""]),
                "mpris:length": Variant("x", 0),
            },
        ),
    }


class MprisExport:
    """One device's MPRIS player on the system bus.

    ``ensure_exported`` and ``ensure_unexported`` are idempotent and
    serialised against each other.  Both answer without raising: an export
    that fails is a degraded but working device (the in-process registry
    still serves the web UI and the MA bridge), never a broken connect.
    """

    def __init__(
        self,
        *,
        mac: str,
        player: Any,
        adapter_path_provider: Callable[[], str | None],
        bus_factory: Callable[[], Any] | None = None,
        iface_builder: Callable[[Any], Any] | None = None,
        props_factory: Callable[[], dict] | None = None,
    ):
        self.mac = mac
        self.path = mpris_dbus_path(mac)
        self._player = player
        self._adapter_path_provider = adapter_path_provider
        self._bus_factory = bus_factory or _default_bus_factory
        self._iface_builder = iface_builder
        self._props_factory = props_factory or _default_player_props
        self._lock = asyncio.Lock()
        self._generation = 0
        self._bus: Any | None = None
        self._adapter_path: str | None = None
        self._registered = False

    @property
    def exported(self) -> bool:
        return self._bus is not None

    @property
    def registered(self) -> bool:
        """True once BlueZ holds a player pointer for this path."""
        return self._registered

    async def ensure_exported(self) -> bool:
        """Export the player and register it with BlueZ. True when exported."""
        async with self._lock:
            if self._bus is not None:
                return True
            generation = self._generation
            try:
                iface = self._build_iface()
                bus = await self._bus_factory().connect()
            except Exception as exc:
                logger.warning("MPRIS D-Bus export for %s failed: %s", self.mac, exc)
                return False

            if generation != self._generation:
                # An unexport landed while we were connecting: this run has
                # been superseded, so undo it instead of publishing it.
                await self._close_bus(bus)
                return False

            try:
                bus.export(self.path, iface)
            except Exception as exc:
                logger.warning("MPRIS D-Bus export for %s failed: %s", self.mac, exc)
                await self._close_bus(bus)
                return False

            self._bus = bus
            self._adapter_path = self._resolve_adapter_path()
            if self._adapter_path is None:
                logger.warning(
                    "MPRIS player for %s exported at %s but adapter unknown — AVRCP forwarding inactive",
                    self.mac,
                    self.path,
                )
                return True

            self._registered = await self._register(bus, self._adapter_path)
            return True

    async def ensure_unexported(self) -> None:
        """Unregister from BlueZ, unexport the path and drop the connection."""
        self._generation += 1
        async with self._lock:
            bus, self._bus = self._bus, None
            adapter_path, self._adapter_path = self._adapter_path, None
            registered, self._registered = self._registered, False
            if bus is None:
                return
            if registered and adapter_path is not None:
                # BlueZ must drop the AVRCP forwarding registration before the
                # path goes away, or the next forwarded command races an
                # object that is already gone.
                await self._unregister(bus, adapter_path)
            try:
                bus.unexport(self.path)
            except Exception as exc:
                logger.debug("MPRIS unexport for %s failed: %s", self.mac, exc)
            await self._close_bus(bus)

    # -- internals -----------------------------------------------------

    def _build_iface(self) -> Any:
        if self._iface_builder is not None:
            return self._iface_builder(self._player)
        from sendspin_bridge.services.audio.mpris_player import _build_player_iface

        return _build_player_iface(self._player)

    def _resolve_adapter_path(self) -> str | None:
        try:
            return self._adapter_path_provider()
        except Exception as exc:
            logger.debug("MPRIS adapter path lookup failed for %s: %s", self.mac, exc)
            return None

    async def _media_interface(self, bus, adapter_path: str):
        introspection = await bus.introspect("org.bluez", adapter_path)
        proxy = bus.get_proxy_object("org.bluez", adapter_path, introspection)
        return proxy.get_interface("org.bluez.Media1")

    async def _register(self, bus, adapter_path: str) -> bool:
        try:
            media = await self._media_interface(bus, adapter_path)
        except Exception as exc:
            logger.warning("MPRIS register: org.bluez.Media1 not on %s for %s: %s", adapter_path, self.mac, exc)
            return False
        try:
            await media.call_register_player(self.path, self._props_factory())
        except Exception as exc:
            logger.warning(
                "MPRIS register: Media1.RegisterPlayer(%s) on %s failed for %s: %s",
                self.path,
                adapter_path,
                self.mac,
                exc,
            )
            return False
        logger.info(
            "MPRIS player registered with BlueZ Media1 for %s at %s on %s",
            self.mac,
            self.path,
            adapter_path,
        )
        return True

    async def _unregister(self, bus, adapter_path: str) -> None:
        try:
            media = await self._media_interface(bus, adapter_path)
            await media.call_unregister_player(self.path)
        except Exception as exc:
            logger.debug("MPRIS Media1.UnregisterPlayer(%s) failed: %s", self.path, exc)

    async def _close_bus(self, bus) -> None:
        """Drop a connection. The path is unexported by the caller that owns it."""
        try:
            result = bus.disconnect()
            if asyncio.iscoroutine(result):
                await result
        except Exception as exc:
            logger.debug("MPRIS bus disconnect for %s failed: %s", self.mac, exc)
