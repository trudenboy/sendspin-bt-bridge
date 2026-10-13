"""PulseAudio sink state monitor for idle disconnect.

Subscribes to PA sink events via ``pulsectl_asyncio`` and tracks
``running ↔ idle/suspended`` transitions for registered Bluetooth sinks.
Runs in the parent process on the main asyncio event loop — one subscription
covers all devices.

When pulsectl is unavailable (import-time failure) the monitor degrades
gracefully: ``start()`` becomes a no-op and ``available`` stays ``False``.

At runtime, if the PA socket cannot be reached (pipewire-pulse without
compat socket, UID mismatch, missing mount, ...) the monitor logs one
diagnostic WARNING on the first failure, a second WARNING after
``_INITIAL_FAILURE_THRESHOLD`` consecutive failures, then self-disables
by returning from ``_monitor_loop`` — so ``available`` becomes ``False``
and callers fall back to daemon-flag idle detection. A manual ``start()``
(typically via bridge restart) is required to retry.

If at least one successful connect occurred, transient disconnects use
exponential backoff (5 → 10 → 20 → 40 → 60s cap) and demote repeat
WARNINGs to DEBUG to avoid log flood.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sendspin_bridge.services.audio.pulse import _PULSECTL_AVAILABLE
from sendspin_bridge.services.audio.sink_inventory import (
    SinkInventoryUnavailable,
    SinkMatch,
    list_local_sinks,
    match_sink,
)
from sendspin_bridge.services.audio.sink_names import address_from_sink_name, is_bluez_sink_name

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from sendspin_bridge.services.audio.sink_inventory import LocalSink

if _PULSECTL_AVAILABLE:
    import pulsectl_asyncio  # type: ignore[import-untyped]


logger = logging.getLogger(__name__)

__all__ = ["SinkMonitor", "extract_mac_from_sink"]

# PA sink state constants (from <pulse/def.h>).
_PA_SINK_RUNNING = 0
_PA_SINK_IDLE = 1
_PA_SINK_SUSPENDED = 2

# Reconnect tuning. Public for tests.
_INITIAL_FAILURE_THRESHOLD = 3
_BACKOFF_BASE = 5.0
_BACKOFF_MAX = 60.0
_BACKOFF_FACTOR = 2.0


def _describe_pa_failure(exc: BaseException) -> tuple[str, str]:
    """Classify a PA connection failure and produce an actionable hint.

    Returns ``(label, hint)`` where *label* is a short machine-friendly tag
    and *hint* describes how to fix the most likely root cause.
    """
    pulse_server = os.environ.get("PULSE_SERVER") or "<unset>"
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR") or "<unset>"
    uid = os.getuid() if hasattr(os, "getuid") else -1

    if isinstance(exc, FileNotFoundError):
        return (
            "socket-missing",
            f"PulseAudio socket not found (PULSE_SERVER={pulse_server}). "
            "Check docker-compose volume mounts for /run/user/<uid>/pulse/native "
            "or the pipewire-pulse socket path.",
        )
    if isinstance(exc, PermissionError):
        return (
            "permission-denied",
            f"Cannot access PulseAudio socket (container UID={uid}, "
            f"PULSE_SERVER={pulse_server}). Set AUDIO_UID in docker-compose.yml "
            "to match the host audio user, or fix socket permissions.",
        )
    if isinstance(exc, ConnectionRefusedError):
        return (
            "server-not-listening",
            f"PulseAudio server refused connection (PULSE_SERVER={pulse_server}, "
            f"XDG_RUNTIME_DIR={xdg_runtime}). Verify 'pactl info' works from inside "
            "the container and that the audio backend is running.",
        )
    if isinstance(exc, OSError) and exc.errno in (errno.ECONNRESET, errno.EPIPE):
        return (
            "protocol-error",
            f"PulseAudio protocol error (errno={exc.errno}). Likely libpulse / "
            "pipewire-pulse version mismatch — try upgrading the host audio stack.",
        )
    return (
        "unknown",
        f"PulseAudio connection failed ({type(exc).__name__}: {exc}). "
        f"PULSE_SERVER={pulse_server}, XDG_RUNTIME_DIR={xdg_runtime}, UID={uid}. "
        "Check pactl connectivity from inside the container.",
    )


async def _strict_inventory() -> list[LocalSink]:
    return await list_local_sinks(strict=True)


@dataclass
class _Presence:
    """One local output a player follows: how to find it, and whom to tell."""

    match: dict[str, Any]
    stored_name: str | None
    on_present: Callable[[str], None]
    on_absent: Callable[[], None]
    on_active: Callable[[], None] | None = None
    on_idle: Callable[[], None] | None = None
    owner: object | None = None
    sink: str | None = None  # the sink it plays to right now; None while unplugged


def extract_mac_from_sink(sink_name: str) -> str | None:
    """Extract a colon-delimited MAC address from a bluez sink name.

    Reads through the shared grammar, which is also what writes these names
    in the connect path — the two used to be described separately and had
    drifted apart on the raw-colon form WirePlumber publishes.

    Returns ``None`` for non-bluez or malformed names.
    """
    address = address_from_sink_name(sink_name)
    return address.colons if address else None


class SinkMonitor:
    """Watches PA sink state transitions and dispatches per-device callbacks.

    Usage::

        monitor = SinkMonitor()
        monitor.register(mac, sink_name, on_active_cb, on_idle_cb)
        await monitor.start()
        ...
        await monitor.stop()
    """

    def __init__(self, *, inventory: Callable[[], Awaitable[list[LocalSink]]] | None = None) -> None:
        # Keys are a Bluetooth MAC or, for a local output, the player id.
        self._callbacks: dict[str, tuple[Callable[[], None], Callable[[], None]]] = {}
        self._sink_names: dict[str, str] = {}  # MAC → sink_name
        self._sink_name_to_mac: dict[str, str] = {}  # sink_name → MAC (reverse index)
        self._sink_states: dict[str, str] = {}  # sink_name → "running"|"idle"|"suspended"
        self._sink_index_to_name: dict[int, str] = {}  # PA index → sink_name
        self._task: asyncio.Task[None] | None = None
        self._consecutive_connect_failures: int = 0
        self._ever_connected: bool = False
        self._transient_warning_logged: bool = False
        # Local (non-Bluetooth) outputs followed across unplug/replug, by player id.
        self._inventory = inventory or _strict_inventory
        self._presence: dict[str, _Presence] = {}
        self._presence_lock: asyncio.Lock | None = None
        self._poll_task: asyncio.Task[None] | None = None

    # ── Public API ────────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """True when the monitor loop is actively running."""
        return self._task is not None and not self._task.done()

    def register(
        self,
        mac: str,
        sink_name: str,
        on_active: Callable[[], None],
        on_idle: Callable[[], None],
    ) -> None:
        """Register callbacks for a device's sink state transitions.

        *on_active* is called when the sink enters ``running``.
        *on_idle* is called when the sink leaves ``running`` (→ idle/suspended/removed).

        If sink state was already observed before registration, the
        appropriate callback fires immediately so no transitions are lost.
        """
        # Clean up old sink name mapping if MAC was already registered
        old_sink = self._sink_names.get(mac)
        if old_sink and old_sink != sink_name:
            self._sink_states.pop(old_sink, None)
            self._sink_name_to_mac.pop(old_sink, None)
        self._callbacks[mac] = (on_active, on_idle)
        self._sink_names[mac] = sink_name
        self._sink_name_to_mac[sink_name] = mac

        # Immediately dispatch based on already-observed sink state so
        # events that arrived before registration are not lost.
        known_state = self._sink_states.get(sink_name)
        if known_state == "running":
            logger.debug("SinkMonitor: %s [%s] register → already running, firing on_active", sink_name, mac)
            on_active()
        elif known_state in ("idle", "suspended"):
            logger.debug("SinkMonitor: %s [%s] register → already %s, firing on_idle", sink_name, mac, known_state)
            on_idle()

    def unregister(self, mac: str) -> None:
        """Remove all state tracking for *mac*."""
        self._callbacks.pop(mac, None)
        sink = self._sink_names.pop(mac, None)
        if sink:
            self._sink_states.pop(sink, None)
            self._sink_name_to_mac.pop(sink, None)

    async def start(self) -> None:
        """Start the PA event subscription loop as a background task."""
        if not _PULSECTL_AVAILABLE:
            logger.warning("SinkMonitor: pulsectl unavailable — sink state monitoring disabled")
            return
        if self._task is not None:
            return
        # Reset initial-failure bookkeeping so a retry after self-disable
        # gets a fresh window instead of tripping the threshold immediately.
        self._consecutive_connect_failures = 0
        self._transient_warning_logged = False
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        """Cancel the monitor task (and the presence poll) and clean up."""
        for attr in ("_poll_task", "_task"):
            task = getattr(self, attr)
            if task is None:
                continue
            setattr(self, attr, None)
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    # ── Local outputs: presence across unplug / replug ────────────────

    def register_presence(
        self,
        key: str,
        match: dict[str, Any],
        stored_name: str | None,
        on_present: Callable[[str], None],
        on_absent: Callable[[], None],
        *,
        current_sink: str | None = None,
        on_active: Callable[[], None] | None = None,
        on_idle: Callable[[], None] | None = None,
        owner: object | None = None,
    ) -> None:
        """Follow a local output for player *key*.

        *on_absent* fires when the sink it plays to (*current_sink*) goes away;
        *on_present* fires with the sink name when ``match_sink`` finds it again
        (possibly under a new name).  While a sink is held, its running/idle
        transitions go to *on_active* / *on_idle* like a Bluetooth sink's.
        *owner* lets a retiring client unregister without removing its successor.
        """
        self.unregister(key)
        self._presence[key] = _Presence(
            match=dict(match or {}),
            stored_name=stored_name,
            on_present=on_present,
            on_absent=on_absent,
            on_active=on_active,
            on_idle=on_idle,
            owner=owner,
        )
        if current_sink:
            self._hold(key, current_sink)

    def unregister_presence(self, key: str, *, owner: object | None = None) -> None:
        """Stop following *key* — only if *owner* is the one that registered it."""
        entry = self._presence.get(key)
        if entry is None or (owner is not None and entry.owner is not owner):
            return
        del self._presence[key]
        self.unregister(key)

    def has_presence(self, key: str) -> bool:
        return key in self._presence

    def presence_sink(self, key: str) -> str | None:
        """The sink player *key* plays to now, ``None`` while it is unplugged."""
        entry = self._presence.get(key)
        return entry.sink if entry else None

    def set_presence_stored_name(self, key: str, stored_name: str | None) -> None:
        """The config now names another sink (after a rematch)."""
        entry = self._presence.get(key)
        if entry is not None:
            entry.stored_name = stored_name

    async def rematch(self, key: str) -> SinkMatch:
        """Match *key*'s output against the current inventory and remember the result.

        Fires no callback — the caller acts on the result.  Raises
        :class:`SinkInventoryUnavailable` when the sinks cannot be listed.
        """
        entry = self._presence.get(key)
        sinks = await self._inventory()
        if entry is None:
            return SinkMatch(None, None, "sink_missing")
        claimed = {p.sink for k, p in self._presence.items() if k != key and p.sink}
        found = match_sink(entry.match, entry.stored_name, sinks, claimed=claimed)
        if found.status == "ok" and found.sink is not None:
            self._seed_state(found.sink)
            self._hold(key, found.sink.name)
        else:
            self._release(key)
        return found

    async def refresh_presence(self) -> None:
        """Re-read the inventory: report outputs that went away and those that came back.

        Used on a new sink, after a (re)connect to PulseAudio and by the
        polling fallback.  An unreadable inventory changes nothing.
        """
        if not self._presence:
            return
        if self._presence_lock is None:
            self._presence_lock = asyncio.Lock()
        async with self._presence_lock:
            try:
                sinks = await self._inventory()
            except SinkInventoryUnavailable as exc:
                logger.debug("SinkMonitor: sink inventory unavailable: %s", exc)
                return
            present = {s.name for s in sinks}
            for key, entry in list(self._presence.items()):
                if entry.sink and entry.sink not in present:
                    self._lose(key, entry)
            claimed = {p.sink for p in self._presence.values() if p.sink}
            for key, entry in list(self._presence.items()):
                if entry.sink:
                    # Still here: keep its running/idle state current.
                    current = next((s for s in sinks if s.name == entry.sink), None)
                    if current is not None:
                        self._observe_state(current.name, current.state)
                    continue
                found = match_sink(entry.match, entry.stored_name, sinks, claimed=claimed)
                if found.status != "ok" or found.sink is None:
                    continue
                claimed.add(found.sink.name)
                self._seed_state(found.sink)
                self._hold(key, found.sink.name)
                logger.info("SinkMonitor: output for %s present as %s (%s)", key, found.sink.name, found.method)
                _call(entry.on_present, found.sink.name)

    def start_presence_polling(self, interval: float) -> None:
        """Poll the inventory every *interval* seconds while sink events are unavailable."""
        if self._poll_task is not None and not self._poll_task.done():
            return
        self._poll_task = asyncio.create_task(self._presence_poll_loop(max(0.001, float(interval))))

    async def _presence_poll_loop(self, interval: float) -> None:
        while True:
            await asyncio.sleep(interval)
            # A mock sink file (dev only) raises no PulseAudio events: always poll it.
            if (self.available and not os.environ.get("SENDSPIN_MOCK_SINKS")) or not self._presence:
                continue
            try:
                await self.refresh_presence()
            except Exception:  # pragma: no cover - a poll must never end the loop
                logger.exception("SinkMonitor: presence poll failed")

    def _hold(self, key: str, sink_name: str) -> None:
        entry = self._presence[key]
        if entry.sink == sink_name and key in self._callbacks:
            return
        entry.sink = sink_name
        if entry.on_active is not None and entry.on_idle is not None:
            self.register(key, sink_name, entry.on_active, entry.on_idle)

    def _release(self, key: str) -> None:
        entry = self._presence.get(key)
        if entry is not None:
            entry.sink = None
        self.unregister(key)

    def _lose(self, key: str, entry: _Presence) -> None:
        logger.info("SinkMonitor: output %s for %s is gone", entry.sink, key)
        self._release(key)
        _call(entry.on_absent)

    def _seed_state(self, sink: LocalSink) -> None:
        """Learn a sink's state from the inventory, before its callbacks are registered."""
        if sink.state in ("running", "idle", "suspended") and sink.name not in self._sink_name_to_mac:
            self._sink_states[sink.name] = sink.state

    def _observe_state(self, sink_name: str, state: str) -> None:
        """A state read for a held sink (poll / scan) — dispatched like a change event."""
        if state not in ("running", "idle", "suspended"):
            return
        old_state = self._sink_states.get(sink_name)
        if state == old_state:
            return
        self._sink_states[sink_name] = state
        self._dispatch_transition(sink_name, old_state, state)

    def _dispatch_transition(self, sink_name: str, old_state: str | None, new_state: str) -> None:
        key = self._sink_name_to_mac.get(sink_name)
        if key is None:
            return
        on_active, on_idle = self._callbacks.get(key, (None, None))
        if on_active is None or on_idle is None:
            return
        if new_state == "running" and old_state != "running":
            logger.debug("SinkMonitor: %s [%s] → running", sink_name, key)
            on_active()
        elif new_state in ("idle", "suspended") and old_state in ("running", None):
            logger.debug("SinkMonitor: %s [%s] → %s", sink_name, key, new_state)
            on_idle()

    # ── Event loop ────────────────────────────────────────────────────

    async def _monitor_loop(self) -> None:
        """Persistent subscribe loop with diagnose-then-disable on boot failures."""
        current_backoff = _BACKOFF_BASE
        while True:
            try:
                async with pulsectl_asyncio.PulseAsync("sendspin-sink-monitor") as pulse:
                    logger.info("SinkMonitor: connected to PulseAudio — subscribing to sink events")
                    # Reset failure state on a successful connect.
                    self._ever_connected = True
                    self._consecutive_connect_failures = 0
                    self._transient_warning_logged = False
                    current_backoff = _BACKOFF_BASE
                    # Scan existing sinks to populate _sink_states before
                    # subscribing — avoids stale cache after PA reconnect and
                    # ensures register() dispatches correctly on first call.
                    await self._scan_all_sinks(pulse)
                    # Outputs unplugged or replugged while we were not listening.
                    await self.refresh_presence()
                    async for event in pulse.subscribe_events("sink"):
                        await self._dispatch_event(pulse, str(event.t), event.index)
            except asyncio.CancelledError:
                return
            except Exception as exc:
                self._sink_index_to_name.clear()
                label, hint = _describe_pa_failure(exc)

                if not self._ever_connected:
                    # Never connected — diagnose first, then self-disable after
                    # threshold failures so callers fall back via `available`.
                    self._consecutive_connect_failures += 1
                    if self._consecutive_connect_failures == 1:
                        logger.warning(
                            "SinkMonitor: cannot connect to PulseAudio [%s] — %s",
                            label,
                            hint,
                        )
                    else:
                        logger.debug(
                            "SinkMonitor: connect attempt %d failed [%s]: %s",
                            self._consecutive_connect_failures,
                            label,
                            exc,
                        )
                    if self._consecutive_connect_failures >= _INITIAL_FAILURE_THRESHOLD:
                        logger.warning(
                            "SinkMonitor disabled after %d connect failures "
                            "(root cause: %s). Daemon-flag idle detection will "
                            "be used as fallback. Fix hint: %s",
                            self._consecutive_connect_failures,
                            label,
                            hint,
                        )
                        # Clear the handle so a caller can re-invoke start()
                        # after fixing the underlying PA setup without having
                        # to reconstruct the SinkMonitor instance.
                        self._task = None
                        return
                    await asyncio.sleep(_BACKOFF_BASE)
                else:
                    # Previously-healthy connection dropped — reconnect with
                    # exponential backoff and demote repeat logs to DEBUG.
                    if not self._transient_warning_logged:
                        logger.warning(
                            "SinkMonitor: PA connection lost [%s] — %s. Reconnecting with exponential backoff.",
                            label,
                            hint,
                        )
                        self._transient_warning_logged = True
                    else:
                        logger.debug(
                            "SinkMonitor: reconnect attempt failed [%s]: %s (next delay %.0fs)",
                            label,
                            exc,
                            current_backoff,
                        )
                    await asyncio.sleep(current_backoff)
                    current_backoff = min(current_backoff * _BACKOFF_FACTOR, _BACKOFF_MAX)

    # ── Event handlers ────────────────────────────────────────────────

    async def _dispatch_event(self, pulse: Any, ev_type: str, sink_index: int) -> None:
        """Route one PulseAudio sink event."""
        if ev_type == "change":
            await self._handle_sink_change(pulse, sink_index)
        elif ev_type == "new":
            await self._handle_sink_change(pulse, sink_index)
            name = self._sink_index_to_name.get(sink_index)
            # Only a local output can bring back a followed one; Bluetooth
            # connects (and the bridge's own null sinks) never need the inventory.
            if (
                name
                and not is_bluez_sink_name(name)
                and not name.startswith("sendspin_")
                and any(p.sink is None for p in self._presence.values())
            ):
                await self.refresh_presence()
        elif ev_type == "remove":
            self._handle_sink_remove(sink_index)

    async def _handle_sink_change(self, pulse: Any, sink_index: int) -> None:
        """Query the sink, store state for all bluez sinks, and fire callbacks."""
        try:
            sink_info = await pulse.sink_info(sink_index)  # type: ignore[union-attr]
        except Exception:
            return

        sink_name: str = sink_info.name
        self._sink_index_to_name[sink_index] = sink_name

        # Track Bluetooth sinks, and the local outputs players are registered on.
        if not is_bluez_sink_name(sink_name) and sink_name not in self._sink_name_to_mac:
            return

        new_state = self._classify_state(sink_info.state)
        old_state = self._sink_states.get(sink_name)

        if new_state == old_state:
            return

        # Always store state — even before registration — so register()
        # can immediately dispatch based on known state.
        self._sink_states[sink_name] = new_state

        # O(1) reverse lookup for registered MAC
        mac = self._sink_name_to_mac.get(sink_name)
        if mac is None:
            return

        on_active, on_idle = self._callbacks.get(mac, (None, None))
        if on_active is None:
            return

        if new_state == "running" and old_state != "running":
            logger.debug("SinkMonitor: %s [%s] → running", sink_name, mac)
            on_active()
        elif new_state in ("idle", "suspended") and old_state in ("running", None):
            logger.debug("SinkMonitor: %s [%s] → %s", sink_name, mac, new_state)
            on_idle()  # type: ignore[misc]

    def _handle_sink_remove(self, sink_index: int) -> None:
        """Handle sink removal — a followed output is lost; otherwise idle if it was running."""
        sink_name = self._sink_index_to_name.pop(sink_index, None)
        if sink_name is None:
            return

        for key, entry in list(self._presence.items()):
            if entry.sink == sink_name:
                self._sink_states.pop(sink_name, None)
                self._lose(key, entry)
                return

        old_state = self._sink_states.pop(sink_name, None)
        mac = self._sink_name_to_mac.get(sink_name)
        if mac is None or old_state != "running":
            return

        _on_active, on_idle = self._callbacks.get(mac, (None, None))
        if on_idle is not None:
            logger.debug("SinkMonitor: %s [%s] removed while running — treating as idle", sink_name, mac)
            on_idle()

    # ── Helpers ───────────────────────────────────────────────────────

    async def _scan_all_sinks(self, pulse: Any) -> None:
        """One-shot scan of all PA sinks to refresh ``_sink_states``.

        Called on initial connect and after reconnect so cached state is
        up-to-date before the subscribe loop starts processing events.
        Fires callbacks for registered sinks whose state changed during
        the disconnect window.
        """
        try:
            sinks = await pulse.sink_list()
        except Exception as exc:
            logger.debug("SinkMonitor: initial sink scan failed: %s", exc)
            return

        for sink_info in sinks:
            sink_name: str = sink_info.name
            self._sink_index_to_name[sink_info.index] = sink_name

            if not is_bluez_sink_name(sink_name) and sink_name not in self._sink_name_to_mac:
                continue

            new_state = self._classify_state(sink_info.state)
            old_state = self._sink_states.get(sink_name)

            if new_state == old_state:
                continue

            self._sink_states[sink_name] = new_state

            mac = self._sink_name_to_mac.get(sink_name)
            if mac is None:
                continue

            on_active, on_idle = self._callbacks.get(mac, (None, None))
            if on_active is None:
                continue

            if new_state == "running" and old_state != "running":
                logger.debug("SinkMonitor: scan %s [%s] → running", sink_name, mac)
                on_active()
            elif new_state in ("idle", "suspended") and old_state in ("running", None):
                logger.debug("SinkMonitor: scan %s [%s] → %s", sink_name, mac, new_state)
                on_idle()  # type: ignore[misc]

        logger.debug("SinkMonitor: initial scan complete — %d bluez sink(s) tracked", len(self._sink_states))

    @staticmethod
    def _classify_state(state: Any) -> str:
        """Map PA sink state to a string label.

        ``pulsectl`` returns ``EnumValue`` wrappers that support
        ``== 'suspended'`` but **not** ``int(state)`` or ``== 2``.
        We try string equality first (works for ``EnumValue``), then
        fall back to integer lookup for raw ``int`` values.
        """
        # pulsectl EnumValue: supports __eq__ with string labels
        if state == "running":
            return "running"
        if state == "idle":
            return "idle"
        if state == "suspended":
            return "suspended"
        # Raw int fallback (older pulsectl or direct PA C API)
        if isinstance(state, int):
            return {
                _PA_SINK_RUNNING: "running",
                _PA_SINK_IDLE: "idle",
                _PA_SINK_SUSPENDED: "suspended",
            }.get(state, "unknown")
        return "unknown"


def _call(callback: Callable[..., None], *args: Any) -> None:
    """Run a player's callback; one failing player must not stop the monitor."""
    try:
        callback(*args)
    except Exception:
        logger.exception("SinkMonitor: presence callback failed")
