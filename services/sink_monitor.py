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
import re
from typing import TYPE_CHECKING, Any

from services.pulse import _PULSECTL_AVAILABLE

if TYPE_CHECKING:
    from collections.abc import Callable

if _PULSECTL_AVAILABLE:
    import pulsectl_asyncio  # type: ignore[import-untyped]


logger = logging.getLogger(__name__)

__all__ = ["SinkMonitor", "extract_mac_from_sink"]

# PA sink state constants (from <pulse/def.h>).
_PA_SINK_RUNNING = 0
_PA_SINK_IDLE = 1
_PA_SINK_SUSPENDED = 2

# Regex: extract the 17-char underscore-delimited MAC from a bluez sink name.
_BLUEZ_MAC_RE = re.compile(r"^bluez_(?:sink|output)\.([0-9A-Fa-f]{2}(?:_[0-9A-Fa-f]{2}){5})")

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


def extract_mac_from_sink(sink_name: str) -> str | None:
    """Extract a colon-delimited MAC address from a bluez sink name.

    Supported patterns::

        bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink
        bluez_sink.FC_58_FA_EB_08_6C
        bluez_output.FC_58_FA_EB_08_6C.a2dp-sink
        bluez_output.FC_58_FA_EB_08_6C.1

    Returns ``None`` for non-bluez or malformed names.
    """
    m = _BLUEZ_MAC_RE.match(sink_name)
    if not m:
        return None
    return m.group(1).replace("_", ":").upper()


class SinkMonitor:
    """Watches PA sink state transitions and dispatches per-device callbacks.

    Usage::

        monitor = SinkMonitor()
        monitor.register(mac, sink_name, on_active_cb, on_idle_cb)
        await monitor.start()
        ...
        await monitor.stop()
    """

    def __init__(self) -> None:
        self._callbacks: dict[str, tuple[Callable[[], None], Callable[[], None]]] = {}
        self._sink_names: dict[str, str] = {}  # MAC → sink_name
        self._sink_name_to_mac: dict[str, str] = {}  # sink_name → MAC (reverse index)
        self._sink_states: dict[str, str] = {}  # sink_name → "running"|"idle"|"suspended"
        self._sink_index_to_name: dict[int, str] = {}  # PA index → sink_name
        self._task: asyncio.Task[None] | None = None
        self._consecutive_connect_failures: int = 0
        self._ever_connected: bool = False
        self._transient_warning_logged: bool = False

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
        self._task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        """Cancel the monitor task and clean up."""
        task = self._task
        if task is None:
            return
        self._task = None
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

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
                    async for event in pulse.subscribe_events("sink"):
                        ev_type = str(event.t)
                        if ev_type == "change" or ev_type == "new":
                            await self._handle_sink_change(pulse, event.index)
                        elif ev_type == "remove":
                            self._handle_sink_remove(event.index)
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

    async def _handle_sink_change(self, pulse: Any, sink_index: int) -> None:
        """Query the sink, store state for all bluez sinks, and fire callbacks."""
        try:
            sink_info = await pulse.sink_info(sink_index)  # type: ignore[union-attr]
        except Exception:
            return

        sink_name: str = sink_info.name
        self._sink_index_to_name[sink_index] = sink_name

        # Only track bluez sinks (Bluetooth audio)
        if not _BLUEZ_MAC_RE.match(sink_name):
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
        """Handle sink removal — treat as idle if it was running."""
        sink_name = self._sink_index_to_name.pop(sink_index, None)
        if sink_name is None:
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

            if not _BLUEZ_MAC_RE.match(sink_name):
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
