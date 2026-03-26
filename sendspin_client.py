#!/usr/bin/env python3
"""
Sendspin Client with Bluetooth Management
Runs the sendspin CLI player with Bluetooth speaker management
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import socket
import sys
import threading
import time
from dataclasses import dataclass, field, fields
from datetime import datetime, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import concurrent.futures

import state as _state
from bluetooth_manager import BluetoothManager
from bridge_orchestrator import BridgeOrchestrator
from config import (
    CONFIG_FILE,
    CONFIG_SCHEMA_VERSION,
    _player_id_from_mac,
    config_lock,
    get_runtime_version,
    save_device_volume,
)
from services.internal_events import DeviceEventType
from services.ipc_protocol import (
    with_protocol_version,
)
from services.playback_health import PlaybackHealthMonitor
from services.status_event_builder import StatusEventBuilder
from services.subprocess_command import SubprocessCommandService
from services.subprocess_ipc import SubprocessIpcService
from services.subprocess_stderr import SubprocessStderrService
from services.subprocess_stop import SubprocessStopService

UTC = timezone.utc

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)
_MA_RECONNECT_TIMEOUT_S = 15.0

_IPC_ALLOWED_KEYS = frozenset(
    {
        "playing",
        "connected",
        "server_connected",
        "server_connected_at",
        "connected_server_url",
        "group_id",
        "group_name",
        "audio_format",
        "audio_streaming",
        "volume",
        "muted",
        "sink_muted",
        "reanchoring",
        "reanchor_count",
        "last_sync_error_ms",
        "last_reanchor_at",
        "current_track",
        "current_artist",
        "current_album",
        "current_album_artist",
        "artwork_url",
        "track_year",
        "track_number",
        "shuffle",
        "repeat_mode",
        "playback_speed",
        "supported_commands",
        "group_volume",
        "group_muted",
        "state_changed_at",
        "last_error",
        "last_error_at",
        "track_progress_ms",
        "track_duration_ms",
        "ma_reconnecting",
    }
)

_IPC_LOG_METHODS = {
    "debug": logger.debug,
    "info": logger.info,
    "warning": logger.warning,
    "error": logger.error,
    "critical": logger.critical,
}


@dataclass
class DeviceStatus:
    """Typed status container for a single Sendspin device.

    **Why both dataclass AND dict interface?**

    The subprocess (``daemon_process.py``) emits status updates as JSON dicts,
    and Flask routes historically read ``status["key"]`` everywhere.  Switching
    all callers to attribute access at once was impractical, so this class
    provides a transitional dict-compatible interface (``__getitem__``,
    ``get``, ``update``, ``copy``, ``__contains__``) on top of typed fields.

    Benefits of the hybrid approach:
    - **Type safety at definition time:** typos in field names are caught by
      IDE / mypy, unlike bare dicts.
    - **Controlled mutation:** only declared fields can be set — prevents
      unbounded growth from unexpected subprocess keys.
    - **Backward compat:** existing ``status["key"]`` / ``status.get(...)``
      callers work without modification.

    Long-term, callers should migrate to attribute access (``status.playing``).
    """

    connected: bool = False
    playing: bool = False
    bluetooth_available: bool = False
    bluetooth_connected: bool = False
    bluetooth_connected_at: str | None = None
    server_connected: bool = False
    server_connected_at: str | None = None
    current_track: str | None = None
    current_artist: str | None = None
    volume: int = 100
    muted: bool = False
    sink_muted: bool = False
    audio_format: str | None = None
    reanchor_count: int = 0
    last_sync_error_ms: float | None = None
    last_reanchor_at: float | None = None
    reanchoring: bool = False
    audio_streaming: bool = False
    state_changed_at: str | None = None
    ip_address: str = ""
    hostname: str = ""
    last_error: str | None = None
    last_error_at: str | None = None
    uptime_start: datetime = field(default_factory=lambda: datetime.now(tz=UTC))
    reconnecting: bool = False
    ma_reconnecting: bool = False
    reconnect_attempt: int = 0
    buffering: bool = False
    stopping: bool = False
    bt_management_enabled: bool = True
    bt_released_by: str | None = None
    battery_level: int | None = None
    group_name: str | None = None
    group_id: str | None = None
    connected_server_url: str | None = None
    track_progress_ms: int | None = None
    track_duration_ms: int | None = None
    playback_speed: int | None = None
    current_album: str | None = None
    current_album_artist: str | None = None
    artwork_url: str | None = None
    track_year: int | None = None
    track_number: int | None = None
    shuffle: bool | None = None
    repeat_mode: str | None = None
    supported_commands: list | None = None
    group_volume: int | None = None
    group_muted: bool | None = None
    bt_standby: bool = False
    bt_standby_since: str | None = None
    bt_waking: bool = False

    # ── Dict-compatible interface ──────────────────────────────────────────

    def __getitem__(self, key: str):
        try:
            return getattr(self, key)
        except AttributeError:
            raise KeyError(key)

    def __setitem__(self, key: str, value) -> None:
        if key in self._field_names:
            setattr(self, key, value)
        else:
            logger.debug("DeviceStatus: unknown key ignored: %s", key)

    _field_names: frozenset = field(default=frozenset(), init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # Cache field names once for fast __contains__ / __setitem__ lookups
        object.__setattr__(self, "_field_names", frozenset(f.name for f in fields(self) if f.name != "_field_names"))

    def __contains__(self, key: object) -> bool:
        return key in self._field_names if isinstance(key, str) else False

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def update(self, d: dict) -> None:
        for k, v in d.items():
            if k in self._field_names:
                setattr(self, k, v)
            else:
                logger.debug("DeviceStatus: unknown key ignored: %s", k)

    def copy(self) -> dict[str, object]:
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name != "_field_names"}


def _normalize_device_mac(mac: object) -> str:
    """Return a canonical MAC string for config/runtime comparisons."""
    return mac.strip().upper() if isinstance(mac, str) else ""


def _filter_duplicate_bluetooth_devices(devices: list[dict]) -> list[dict]:
    """Keep the first occurrence of each configured MAC and log duplicates loudly."""
    unique_devices: list[dict] = []
    first_player_by_mac: dict[str, str] = {}

    for device in devices:
        normalized = dict(device)
        mac = _normalize_device_mac(normalized.get("mac"))
        if mac:
            normalized["mac"] = mac
            if mac in first_player_by_mac:
                logger.error(
                    "Duplicate Bluetooth MAC %s for player '%s' — skipping duplicate; first occurrence belongs to '%s'",
                    mac,
                    normalized.get("player_name") or mac,
                    first_player_by_mac[mac],
                )
                continue
            first_player_by_mac[mac] = normalized.get("player_name") or mac
        unique_devices.append(normalized)

    return unique_devices


def _load_saved_device_volume(mac: str) -> int | None:
    """Read LAST_VOLUMES from config so the UI has a volume before BT reconnects."""
    try:
        with config_lock, open(CONFIG_FILE) as config_file:
            saved_config = json.load(config_file)
        saved_volume = saved_config.get("LAST_VOLUMES", {}).get(mac)
        if isinstance(saved_volume, int) and 0 <= saved_volume <= 100:
            return saved_volume
    except Exception as exc:
        logger.debug("pre-fill saved volume failed: %s", exc)
    return None


class SendspinClient:
    """Per-device orchestrator for a single Bluetooth speaker.

    Manages the full lifecycle of a Sendspin subprocess: spawning it with the
    correct ``PULSE_SINK`` environment variable, reading JSON-line status from
    its stdout, sending commands (volume, stop, reconnect) via its stdin, and
    tearing it down gracefully on disconnect or shutdown.

    Thread-safety: status mutations go through ``_update_status()`` which
    acquires ``_status_lock``.  Flask routes, the asyncio event loop, and
    D-Bus callbacks all read/write status through this single gate.
    """

    def __init__(
        self,
        player_name: str,
        server_host: str,
        server_port: int,
        bt_manager: BluetoothManager | None = None,
        listen_port: int = 8928,
        static_delay_ms: float | None = None,
        listen_host: str | None = None,
        effective_bridge: str = "",
        preferred_format: str | None = "flac:44100:16:2",
        keepalive_enabled: bool = False,
        keepalive_interval: int = 30,
        idle_disconnect_minutes: int = 0,
    ):
        self.player_name = player_name
        self.server_host = server_host
        self.server_port = server_port
        self.bt_manager = bt_manager
        self.listen_port = listen_port  # port sendspin daemon listens on
        self.listen_host = listen_host  # explicit IP for WebSocket URL display (None = auto-detect)
        self.static_delay_ms = static_delay_ms  # per-device delay override (None = use env var)
        self.preferred_format = preferred_format  # preferred audio format string (e.g. "flac:44100:16:2")
        self._effective_bridge = effective_bridge  # bridge instance label for MA device info
        self.keepalive_enabled = keepalive_enabled  # send periodic silence to keep BT speaker alive
        self.keepalive_interval = max(30, keepalive_interval)  # seconds between keepalive bursts
        self.idle_disconnect_minutes = idle_disconnect_minutes  # 0 = disabled

        # Status tracking
        self.status = DeviceStatus(
            bluetooth_available=bt_manager.check_bluetooth_available() if bt_manager else False,
            ip_address=listen_host or self.get_ip_address(),
            hostname=socket.gethostname(),
        )

        self._status_lock = threading.Lock()
        self.running = False
        # Compute player_id eagerly from BT MAC (stable UUID5)
        _mac = bt_manager.mac_address if bt_manager else None
        safe_id = "".join(c if c.isalnum() or c == "-" else "-" for c in player_name.lower()).strip("-")
        self._safe_id = safe_id
        self.player_id: str = _player_id_from_mac(_mac) if _mac else f"sendspin-{safe_id}"
        self.bt_management_enabled: bool = True
        self.bluetooth_sink_name: str | None = None  # Store Bluetooth sink name for volume sync
        self.connected_server_url: str = ""  # actual resolved ws:// URL (populated after connect)
        self._seen_ipc_protocol_warnings: set[str] = set()
        self._daemon_proc: asyncio.subprocess.Process | None = None
        self._daemon_task: asyncio.Task | None = None  # stdout reader task
        self._stderr_task: asyncio.Task | None = None  # stderr reader task
        self._monitor_task: asyncio.Task | None = None
        self._ma_reconnect_task: asyncio.Task | None = None
        self._restart_delay: float = 1.0  # exponential backoff for unexpected daemon restarts
        self._start_sendspin_lock: asyncio.Lock | None = None  # set in run(), guards concurrent starts
        self._start_sendspin_requests = 0
        self._start_sendspin_processed = 0
        self._playback_health = PlaybackHealthMonitor()
        self._ipc_service = SubprocessIpcService(
            player_name=player_name,
            protocol_warning_cache=self._seen_ipc_protocol_warnings,
            status_updater=self._update_status,
            log_methods=_IPC_LOG_METHODS,
            logger_=logger,
            allowed_keys=_IPC_ALLOWED_KEYS,
        )
        self._command_service = SubprocessCommandService(logger_=logger)
        self._stderr_service = SubprocessStderrService(
            player_name=player_name,
            update_status=self._update_status,
            logger_=logger,
        )
        self._stop_service = SubprocessStopService(logger_=logger)
        self._idle_timer_task: asyncio.Task | concurrent.futures.Future | None = None

    @property
    def _playing_since(self) -> float | None:
        return self._playback_health.playing_since

    @_playing_since.setter
    def _playing_since(self, value: float | None) -> None:
        self._playback_health.playing_since = value

    @property
    def _zombie_restart_count(self) -> int:
        return self._playback_health.restart_count

    @_zombie_restart_count.setter
    def _zombie_restart_count(self, value: int) -> None:
        self._playback_health.restart_count = value

    @property
    def _has_streamed(self) -> bool:
        return self._playback_health.has_streamed

    @_has_streamed.setter
    def _has_streamed(self, value: bool) -> None:
        self._playback_health.has_streamed = value

    def _event_device_id(self) -> str:
        """Return the stable event-history key for this device."""
        return self.player_id or f"sendspin-{self._safe_id}" or self.player_name

    def _build_status_events(
        self,
        previous: dict[str, object],
        current: dict[str, object],
        updates: dict,
    ) -> list[dict[str, object]]:
        """Translate meaningful status transitions into structured device events."""
        return StatusEventBuilder.build(previous, current, updates)

    def _update_status(self, updates: dict) -> None:
        """Thread-safe update of self.status; notifies SSE listeners."""
        recorded_events: list[dict[str, object]] = []
        with self._status_lock:
            previous = self.status.copy()
            self._playback_health.observe_status_update(
                previous_playing=bool(self.status.get("playing")),
                updates=updates,
                now=time.monotonic(),
            )
            self.status.update(updates)
            recorded_events = self._build_status_events(previous, self.status.copy(), updates)
        for event in recorded_events:
            _state.publish_device_event(
                self._event_device_id(),
                str(event["event_type"]),
                level=str(event["level"]),
                message=str(event["message"]),
                details=event["details"] if isinstance(event["details"], dict) else None,
            )
        _state.notify_status_changed()
        # Check audio_streaming transition for idle disconnect timer
        if self.idle_disconnect_minutes > 0 and "audio_streaming" in updates:
            was_streaming = previous.get("audio_streaming", False)
            now_streaming = self.status.get("audio_streaming", False)
            if was_streaming and not now_streaming:
                self._start_idle_timer()
            elif not was_streaming and now_streaming:
                self._cancel_idle_timer()

    # ── Idle disconnect timer ────────────────────────────────────────────

    def _start_idle_timer(self) -> None:
        """Start (or restart) the idle disconnect timer."""
        self._cancel_idle_timer()
        timeout = self.idle_disconnect_minutes * 60

        async def _idle_timeout() -> None:
            try:
                await asyncio.sleep(timeout)
                logger.info(
                    "[%s] Idle for %d min — entering standby",
                    self.player_name,
                    self.idle_disconnect_minutes,
                )
                await self._enter_standby()
            except asyncio.CancelledError:
                return

        loop = _state.get_main_loop()
        if loop and loop.is_running():
            self._idle_timer_task = asyncio.run_coroutine_threadsafe(_idle_timeout(), loop)
        else:
            try:
                self._idle_timer_task = asyncio.ensure_future(_idle_timeout())
            except RuntimeError:
                pass

    def _cancel_idle_timer(self) -> None:
        """Cancel any pending idle disconnect timer."""
        task = self._idle_timer_task
        if task is None:
            return
        self._idle_timer_task = None
        if hasattr(task, "cancel"):
            task.cancel()

    async def _enter_standby(self) -> None:
        """Disconnect BT and park daemon on null sink to let the speaker save power.

        Phase 2 behavior: daemon stays alive on a PA null sink so the player
        remains visible in MA.  When MA sends play, the bridge auto-reconnects BT.
        """
        if self.status.get("bt_standby"):
            return
        self._update_status(
            {
                "bt_standby": True,
                "bt_standby_since": datetime.now(tz=UTC).isoformat(),
                "bt_released_by": "idle_timeout",
            }
        )
        # Move daemon streams to null sink instead of killing daemon
        daemon_pid = self._daemon_proc.pid if self._daemon_proc else None
        if daemon_pid:
            from services.pulse import STANDBY_SINK_NAME, aensure_null_sink, amove_pid_sink_inputs

            if await aensure_null_sink():
                moved = await amove_pid_sink_inputs(daemon_pid, STANDBY_SINK_NAME)
                logger.info("[%s] Moved %d stream(s) to null sink", self.player_name, moved)
            else:
                logger.warning("[%s] Could not create null sink — falling back to daemon stop", self.player_name)
                await self.stop_sendspin()

        # Disconnect BT to save speaker battery
        if self.bt_manager:
            try:
                self.bt_manager.disconnect_device()
            except Exception as exc:
                logger.warning("[%s] BT disconnect on standby failed: %s", self.player_name, exc)
        _state.publish_device_event(
            self._event_device_id(),
            DeviceEventType.BLUETOOTH_STANDBY_ENTERED,
            message="Speaker entered standby after idle timeout",
            details={"idle_minutes": self.idle_disconnect_minutes},
        )
        logger.info("[%s] Entered standby (BT disconnected, daemon on null sink)", self.player_name)

    async def _wake_from_standby(self) -> None:
        """Begin BT reconnect while keeping daemon alive on null sink.

        Sets ``bt_waking=True`` so bt_monitor reconnects BT without killing
        the daemon.  ``bt_standby`` stays True until ``_reroute_to_bt_sink()``
        successfully moves streams to the BT sink.
        """
        if not self.status.get("bt_standby"):
            return

        self._update_status(
            {
                "bt_waking": True,
                "bt_released_by": None,
            }
        )
        if self.bt_manager:
            self.bt_manager.allow_reconnect()
        _state.publish_device_event(
            self._event_device_id(),
            DeviceEventType.BLUETOOTH_STANDBY_EXITED,
            message="Speaker waking from standby",
        )
        logger.info("[%s] Waking from standby — BT reconnect will be handled by monitor", self.player_name)

    async def _on_standby_play_detected(self) -> None:
        """Auto-wake: MA started playback while in standby — reconnect BT.

        Called from ``_read_subprocess_output()`` when ``playing=True`` arrives
        while ``bt_standby=True``.  The daemon is alive on the null sink;
        audio streams there silently until BT reconnects and streams are moved.
        """
        if not self.status.get("bt_standby") or self.status.get("bt_waking"):
            return
        logger.info("[%s] Play detected during standby — auto-waking", self.player_name)
        await self._wake_from_standby()

    async def _reroute_to_bt_sink(self) -> bool:
        """After BT reconnect, move streams from null sink to the BT sink and reanchor.

        Called from ``_start_sendspin_inner()`` when daemon is still alive after
        standby wake.  Clears ``bt_standby`` / ``bt_waking`` on success.

        Returns ``True`` if streams were rerouted, ``False`` if no streams
        existed (ALSA errors during standby may have destroyed them).
        """
        daemon_pid = self._daemon_proc.pid if self._daemon_proc else None
        if not daemon_pid or not self.bluetooth_sink_name:
            return False
        from services.pulse import amove_pid_sink_inputs

        moved = await amove_pid_sink_inputs(daemon_pid, self.bluetooth_sink_name)
        # Clear standby state regardless of streams moved
        self._update_status(
            {
                "bt_standby": False,
                "bt_standby_since": None,
                "bt_waking": False,
            }
        )
        if moved > 0:
            logger.info("[%s] Rerouted %d stream(s) to BT sink %s", self.player_name, moved, self.bluetooth_sink_name)
            await self._send_subprocess_command({"cmd": "reconnect", "delay": 1.0})
            logger.info("[%s] Sent reanchor after wake", self.player_name)
            return True

        logger.info("[%s] No streams to reroute — will restart daemon", self.player_name)
        return False

    def _cancel_ma_reconnect_task(self) -> None:
        task = self._ma_reconnect_task
        if task is not None and not task.done():
            task.cancel()
        self._ma_reconnect_task = None

    def _clear_ma_reconnecting(self) -> None:
        self._cancel_ma_reconnect_task()
        if self.get_status_value("ma_reconnecting", False):
            self._update_status({"ma_reconnecting": False})

    def _schedule_ma_reconnect_timeout(self) -> None:
        self._cancel_ma_reconnect_task()

        async def _timeout_clear() -> None:
            try:
                await asyncio.sleep(_MA_RECONNECT_TIMEOUT_S)
                if self.get_status_value("ma_reconnecting", False):
                    self._update_status({"ma_reconnecting": False})
            except asyncio.CancelledError:
                return

        self._ma_reconnect_task = asyncio.create_task(_timeout_clear())

    def _mark_ma_reconnecting(self) -> None:
        if not self.get_status_value("ma_reconnecting", False):
            self._update_status({"ma_reconnecting": True})
        self._schedule_ma_reconnect_timeout()

    # ── BluetoothManagerHost protocol implementation ──────────────────

    def update_status(self, updates: dict) -> None:
        """Public status update entry point (BluetoothManagerHost protocol)."""
        self._update_status(updates)

    def get_status_value(self, key: str, default=None):
        """Thread-safe single-value read (BluetoothManagerHost protocol)."""
        with self._status_lock:
            return self.status.get(key, default)

    def is_subprocess_running(self) -> bool:
        """Check if daemon subprocess is alive (BluetoothManagerHost protocol)."""
        return self.is_running()

    async def stop_subprocess(self) -> None:
        """Stop the daemon subprocess (BluetoothManagerHost protocol)."""
        await self.stop_sendspin()

    async def start_subprocess(self) -> None:
        """Start the daemon subprocess (BluetoothManagerHost protocol)."""
        await self.start_sendspin()

    async def send_subprocess_command(self, cmd: dict) -> None:
        """Send command to daemon stdin (BluetoothManagerHost protocol)."""
        await self._send_subprocess_command(cmd)

    def get_subprocess_pid(self) -> int | None:
        """Return daemon subprocess PID if alive (BluetoothManagerHost protocol)."""
        proc = self._daemon_proc
        if proc is not None and proc.returncode is None:
            return proc.pid
        return None

    def get_ip_address(self) -> str:
        """Get the primary IP address of this machine"""
        from config import get_local_ip

        return get_local_ip() or "unknown"

    async def _status_monitor_loop(self):
        """Periodic status monitoring loop (BT state + daemon health)."""
        logger.debug("Status monitoring loop started")
        while self.running:
            try:
                if self.bt_manager:
                    # Read cached connected flag — monitor_and_reconnect() polls
                    # is_device_connected() on its own schedule and keeps this up-to-date,
                    # so we avoid a redundant bluetoothctl subprocess here.
                    bt_connected = self.bt_manager.connected
                    if bt_connected != self.status["bluetooth_connected"]:
                        self._update_status(
                            {
                                "bluetooth_connected": bt_connected,
                                "bluetooth_connected_at": datetime.now(tz=UTC).isoformat(),
                            }
                        )

                # Check daemon subprocess health
                if self._daemon_proc is not None:
                    if self._daemon_proc.returncode is not None:
                        # Subprocess exited
                        self._update_status(
                            {
                                "server_connected": False,
                                "connected": False,
                                "group_name": None,
                                "group_id": None,
                            }
                        )
                        self._clear_ma_reconnecting()
                        self._daemon_proc = None
                        # Don't restart if BT is disconnected — monitor_and_reconnect
                        # will call start_sendspin() once BT reconnects.
                        if not self.bt_manager or self.bt_manager.connected:
                            logger.warning(
                                "Daemon subprocess died unexpectedly, restarting in %.0fs...",
                                self._restart_delay,
                            )
                            await asyncio.sleep(self._restart_delay)
                            self._restart_delay = min(self._restart_delay * 2, 30.0)
                            await self.start_sendspin()
                        else:
                            self._restart_delay = 1.0  # reset when BT drives the restart
                            logger.info("Daemon subprocess stopped; waiting for BT to reconnect")
                    else:
                        # Daemon alive — reset backoff
                        self._restart_delay = 1.0

                # Zombie playback watchdog: playing=True but no audio data for too long
                self._check_zombie_playback()

                await asyncio.sleep(5)
            except Exception as e:
                logger.error("Error updating status: %s", e)
                await asyncio.sleep(5)

    def _check_zombie_playback(self) -> None:
        """Detect zombie state (playing=True, streaming=False) and schedule restart.

        Only triggers when audio has NEVER arrived in the current play session.
        If audio was streaming before within the same ongoing play session
        (re-anchor, group resync, track change),
        PA buffers keep playing — this is normal, not a zombie.
        """
        need_restart = False
        elapsed = 0.0
        restart_count = 0
        with self._status_lock:
            need_restart, elapsed, restart_count = self._playback_health.check_zombie_playback(
                is_playing=bool(self.status.get("playing")),
                is_streaming=bool(self.status.get("audio_streaming")),
                daemon_alive=self._daemon_proc is not None and self._daemon_proc.returncode is None,
                now=time.monotonic(),
            )

        if not need_restart:
            return

        logger.warning(
            "[%s] Zombie playback detected: playing=True but no audio for %.0fs "
            "(restart %d/%d) — restarting subprocess",
            self.player_name,
            elapsed,
            restart_count,
            self._playback_health.max_zombie_restarts,
        )
        # Schedule restart on the event loop (we're called from an async context)
        asyncio.create_task(self._zombie_restart())

    async def _zombie_restart(self) -> None:
        """Restart subprocess to recover from zombie playback."""
        await self.stop_sendspin()
        await asyncio.sleep(1)
        await self.start_sendspin()

    async def start_sendspin(self) -> None:
        """Start the sendspin daemon as an isolated subprocess with PULSE_SINK routing."""
        lock = self._start_sendspin_lock
        if lock is None:
            await self._start_sendspin_inner()
            return
        self._start_sendspin_requests += 1
        if lock.locked():
            logger.debug("[%s] start_sendspin already in progress, queueing follow-up run", self.player_name)
            return
        async with lock:
            while self._start_sendspin_processed < self._start_sendspin_requests:
                self._start_sendspin_processed = self._start_sendspin_requests
                await self._start_sendspin_inner()

    async def _start_sendspin_inner(self) -> None:
        """Spawn daemon_process.py subprocess with PULSE_SINK in its environment."""
        try:
            # Configure BT audio sink if not yet done
            if self.bt_manager and self.bt_manager.connected and not self.bluetooth_sink_name:
                self.bt_manager.configure_bluetooth_audio()

            # Phase 2 standby wake: daemon is alive on null sink — reroute streams
            # to the BT sink instead of spawning a new subprocess.
            if self.is_running() and self.bluetooth_sink_name:
                logger.info(
                    "[%s] Daemon already running — rerouting to BT sink %s",
                    self.player_name,
                    self.bluetooth_sink_name,
                )
                if await self._reroute_to_bt_sink():
                    return
                # Reroute failed (ALSA errors during standby destroyed PA
                # streams) — fall through to full daemon restart.
                logger.info("[%s] Reroute found 0 streams — full daemon restart", self.player_name)

            # Stop any existing subprocess first
            await self.stop_sendspin()

            # Reset play-session tracking for new subprocess
            self._playback_health.reset_for_new_subprocess()

            client_id = self.player_id

            if self.static_delay_ms is not None:
                static_delay_ms = self.static_delay_ms
            else:
                static_delay_ms = float(os.environ.get("SENDSPIN_STATIC_DELAY_MS", "-300"))

            server_url: str | None = None
            if self.server_host and self.server_host.lower() not in ("auto", "discover", ""):
                server_url = f"ws://{self.server_host}:{self.server_port}/sendspin"
                logger.info(
                    "Starting Sendspin player '%s' connecting to %s (port %s)",
                    self.player_name,
                    server_url,
                    self.listen_port,
                )
            else:
                logger.info(
                    "Starting Sendspin player '%s' with auto-discovery (port %s)", self.player_name, self.listen_port
                )

            params = json.dumps(
                with_protocol_version(
                    {
                        "player_name": self.player_name,
                        "client_id": str(client_id),
                        "listen_port": self.listen_port,
                        "url": server_url,
                        "static_delay_ms": static_delay_ms,
                        "bluetooth_sink_name": self.bluetooth_sink_name,
                        "volume": self.status.get("volume", 100),
                        "muted": bool(self.status.get("muted", False)),
                        "settings_dir": f"/tmp/sendspin-{self._safe_id}",
                        "preferred_format": self.preferred_format,
                        "config_schema_version": CONFIG_SCHEMA_VERSION,
                    }
                )
            )

            # Build subprocess environment: inherit everything + PULSE_SINK for routing
            env = os.environ.copy()
            if self.bluetooth_sink_name:
                env["PULSE_SINK"] = self.bluetooth_sink_name
                logger.info("[%s] Subprocess PULSE_SINK=%s", self.player_name, self.bluetooth_sink_name)
            # Unique application.name so PA module-stream-restore does not confuse
            # streams across subprocesses (all share the same python3 binary name).
            env["PULSE_PROP_application.name"] = f"sendspin-{self.player_id}"

            self._daemon_proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "services.daemon_process",
                params,
                stdout=asyncio.subprocess.PIPE,
                stdin=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=os.path.dirname(os.path.abspath(__file__)),
                limit=1024 * 1024,  # 1 MB readline buffer (artwork base64 can exceed 64 KB default)
            )
            self._update_status({"playing": False})
            self._clear_ma_reconnecting()

            # Start async tasks to consume subprocess stdout and stderr
            self._daemon_task = asyncio.create_task(self._read_subprocess_output())
            self._stderr_task = asyncio.create_task(self._read_subprocess_stderr())

            def _on_reader_done(t: asyncio.Task) -> None:
                if not t.cancelled() and t.exception():
                    logger.error("[%s] stdout reader error: %s", self.player_name, t.exception())

            self._daemon_task.add_done_callback(_on_reader_done)
            self._stderr_task.add_done_callback(_on_reader_done)
            logger.info("Sendspin daemon subprocess started (PID %s) for '%s'", self._daemon_proc.pid, self.player_name)

        except Exception as e:
            logger.error("Failed to start Sendspin daemon subprocess: %s", e)
            self._update_status({"last_error": str(e), "server_connected": False})

    async def _read_subprocess_output(self) -> None:
        """Read JSON lines from daemon subprocess stdout and merge into self.status."""
        if self._daemon_proc is None or self._daemon_proc.stdout is None:
            return
        async for line in self._daemon_proc.stdout:
            msg = self._ipc_service.parse_line(line)
            if msg is None:
                continue
            if msg.get("type") == "status":
                # handle_message applies updates atomically via _update_status;
                # use the returned updates dict to detect volume changes without
                # separate lock acquisitions that could race.
                updates = self._ipc_service.handle_message(msg)
                if updates:
                    if updates.get("server_connected") is True:
                        self._clear_ma_reconnecting()
                    # Auto-wake: MA started playback while daemon is on null sink
                    if updates.get("playing") is True and self.status.get("bt_standby"):
                        asyncio.ensure_future(self._on_standby_play_detected())
                    new_volume = updates.get("volume")
                    _mac = self.bt_manager.mac_address if self.bt_manager else None
                    if isinstance(new_volume, int) and _mac:
                        save_device_volume(_mac, new_volume)
            else:
                self._ipc_service.handle_message(msg)

    async def _read_subprocess_stderr(self) -> None:
        """Forward daemon subprocess stderr lines with severity matching their content."""
        if self._daemon_proc is None or self._daemon_proc.stderr is None:
            return
        await self._stderr_service.read_stream(self._daemon_proc.stderr)

    def _handle_subprocess_stderr_line(self, line: str) -> None:
        """Compatibility proxy for stderr classification tests and legacy call sites."""
        self._stderr_service.handle_line(line)

    async def _send_subprocess_command(self, cmd: dict) -> None:
        """Write a JSON command to the daemon subprocess stdin."""
        await self._command_service.send(self._daemon_proc, cmd)

    async def send_reconnect(self) -> None:
        """Trigger the sendspin subprocess to reconnect to MA server.

        This causes the subprocess to send a fresh client_hello with the
        current bridge version and hostname, updating stale device_info in MA.
        Only call when the player is not actively playing.

        A 3-second delay is inserted after disconnect so that MA has time to
        process ClientRemovedEvent and unregister the old player before the
        new client_hello arrives (workaround for MA using register() instead
        of register_or_update() — see music-assistant/support#5049).
        """
        proc = self._daemon_proc
        if proc is None or proc.returncode is not None or not self.status.get("server_connected"):
            return
        self._mark_ma_reconnecting()
        await self._send_subprocess_command({"cmd": "reconnect", "delay": 3.0})

    async def send_transport_command(self, action: str, value: object = None) -> bool:
        """Send a native Sendspin transport command to the daemon subprocess.

        Returns True if the command was dispatched, False otherwise.
        """
        if self._daemon_proc is None or self._daemon_proc.returncode is not None:
            return False
        cmd: dict = {"cmd": "transport", "action": action}
        if value is not None:
            cmd["value"] = value
        await self._send_subprocess_command(cmd)
        return True

    async def _keepalive_loop(self) -> None:
        """Periodically send a short silence burst to the BT sink to prevent speaker auto-disconnect."""
        try:
            # Stagger startup across devices to avoid simultaneous paplay bursts
            await asyncio.sleep(random.uniform(0, self.keepalive_interval))
            while self.running:
                await asyncio.sleep(self.keepalive_interval)
                if (
                    self.bt_manager
                    and self.bt_manager.connected
                    and self.bluetooth_sink_name
                    and not self.status.get("audio_streaming")
                    and not self.status.get("bt_standby")
                ):
                    await self._send_keepalive_burst()
        except asyncio.CancelledError:
            return

    async def _send_keepalive_burst(self) -> None:
        """Write 500 ms of PCM silence to the BT PulseAudio sink via paplay."""
        # 500 ms x 44100 Hz x 2 channels x 2 bytes/sample = 88200 bytes
        silence = b"\x00" * (int(44100 * 0.5) * 2 * 2)
        try:
            proc = await asyncio.create_subprocess_exec(
                "paplay",
                f"--device={self.bluetooth_sink_name}",
                "--raw",
                "--format=s16le",
                "--rate=44100",
                "--channels=2",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if proc.stdin:
                proc.stdin.write(silence)
                await proc.stdin.drain()
                proc.stdin.close()
            await asyncio.wait_for(proc.wait(), timeout=5.0)
            logger.debug("[%s] Keepalive burst sent to %s", self.player_name, self.bluetooth_sink_name)
        except Exception as exc:
            logger.debug("[%s] Keepalive burst failed: %s", self.player_name, exc)

    async def stop_sendspin(self) -> None:
        """Stop the daemon subprocess gracefully."""
        cleared_tasks = await self._stop_service.stop_process(
            self._daemon_proc,
            send_stop=self._send_subprocess_command,
            player_name=self.player_name,
            reader_tasks={
                "_daemon_task": self._daemon_task,
                "_stderr_task": self._stderr_task,
            },
        )
        if cleared_tasks:
            self._daemon_task = cleared_tasks["_daemon_task"]
            self._stderr_task = cleared_tasks["_stderr_task"]
        else:
            self._daemon_task = None
            self._stderr_task = None
        self._daemon_proc = None
        self._clear_ma_reconnecting()

        self._update_status(
            {
                "server_connected": False,
                "connected": False,
                "playing": False,
                "audio_streaming": False,
                "current_track": None,
                "current_artist": None,
                "audio_format": None,
                "reanchoring": False,
                "group_name": None,
                "group_id": None,
            }
        )

    def is_running(self) -> bool:
        """Return True if the daemon subprocess is alive."""
        return self._daemon_proc is not None and self._daemon_proc.returncode is None

    def snapshot(self) -> dict:
        """Return all client attributes for status reporting under a single lock.

        Captures mutable state atomically so that ``build_device_snapshot``
        does not suffer TOCTOU races from reading attributes across multiple
        lock acquisitions.
        """
        bt_mgr = self.bt_manager
        with self._status_lock:
            return {
                "status": self.status.copy(),
                "bluetooth_sink_name": self.bluetooth_sink_name,
                "bt_management_enabled": self.bt_management_enabled,
                "connected_server_url": self.connected_server_url,
                "is_running": self._daemon_proc is not None and self._daemon_proc.returncode is None,
                "player_name": self.player_name,
                "player_id": self.player_id,
                "listen_port": self.listen_port,
                "server_host": self.server_host,
                "server_port": self.server_port,
                "static_delay_ms": self.static_delay_ms,
                "bt_manager": bt_mgr,
                "bluetooth_mac": bt_mgr.mac_address if bt_mgr else None,
                "effective_adapter_mac": getattr(bt_mgr, "effective_adapter_mac", None) if bt_mgr else None,
                "adapter": getattr(bt_mgr, "adapter", None) if bt_mgr else None,
                "adapter_hci_name": getattr(bt_mgr, "adapter_hci_name", "") if bt_mgr else "",
                "battery_level": getattr(bt_mgr, "battery_level", None) if bt_mgr else None,
                "paired": getattr(bt_mgr, "paired", None) if bt_mgr else None,
                "max_reconnect_fails": int(getattr(bt_mgr, "max_reconnect_fails", 0) or 0) if bt_mgr else 0,
            }

    async def run(self) -> None:
        """Main run loop — connects BT, starts subprocess, monitors health."""
        self.running = True
        self._start_sendspin_lock = asyncio.Lock()
        self._start_sendspin_requests = 0
        self._start_sendspin_processed = 0

        # Start Sendspin player: immediately if no BT device, deferred if BT configured
        if not self.bt_management_enabled:
            logger.info("[%s] BT management disabled — skipping sendspin startup", self.player_name)
        elif not self.bt_manager:
            # No BT device configured — start on default audio immediately
            await self.start_sendspin()
        else:
            # BT device configured — defer daemon start until BT actually connects
            logger.info("[%s] Waiting for BT connection before starting player", self.player_name)

        # Start background tasks
        tasks = [asyncio.create_task(self._status_monitor_loop())]
        if self.keepalive_enabled:
            tasks.append(asyncio.create_task(self._keepalive_loop()))

        # Handle Bluetooth connection in background if configured
        logger.info("Bluetooth manager present: %s", self.bt_manager is not None)
        if self.bt_manager:
            logger.info("Starting Bluetooth connection task...")

            async def connect_bluetooth_async():
                """Connect Bluetooth in background without blocking"""
                logger.info("Bluetooth async task started, waiting 2 seconds...")
                await asyncio.sleep(2)  # Let sendspin start first
                if not self.bt_management_enabled:
                    return
                logger.info("Connecting Bluetooth speaker...")
                try:
                    # Run in thread pool to avoid blocking
                    loop = asyncio.get_running_loop()
                    await loop.run_in_executor(None, self.bt_manager.connect_device)
                    bt_now = self.bt_manager.is_device_connected()
                    if bt_now != self.status["bluetooth_connected"]:
                        self._update_status(
                            {
                                "bluetooth_connected": bt_now,
                                "bluetooth_connected_at": datetime.now(tz=UTC).isoformat(),
                            }
                        )
                    # Restart daemon with correct BT audio device now that sink is known.
                    # At start_sendspin() time bluetooth_sink_name was None (BT not yet
                    # connected), so the daemon was bound to the default audio device.
                    # Re-starting here ensures each player routes audio to its own BT sink.
                    # NOTE: bluetooth_sink_name is set by _on_sink_found() which runs
                    # synchronously inside connect_device() → configure_bluetooth_audio(),
                    # so it is guaranteed to be set before run_in_executor returns.
                    if bt_now and self.bluetooth_sink_name:
                        logger.info(
                            "[%s] BT connected with sink %s — starting player",
                            self.player_name,
                            self.bluetooth_sink_name,
                        )
                        await self.start_sendspin()
                except Exception as e:
                    logger.error("Error connecting Bluetooth: %s", e)

            tasks.append(asyncio.create_task(connect_bluetooth_async()))
            mon_task = asyncio.create_task(self.bt_manager.monitor_and_reconnect())

            def _on_monitor_done(t):
                if not t.cancelled() and t.exception():
                    logger.error("[%s] monitor_and_reconnect task DIED: %s", self.player_name, t.exception())

            mon_task.add_done_callback(_on_monitor_done)
            tasks.append(mon_task)

        try:
            # Keep running
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Client shutting down...")
        finally:
            # Cleanup
            for task in tasks:
                task.cancel()
            await self.stop_sendspin()

    async def stop(self) -> None:
        """Stop the client and its subprocess."""
        self.running = False

    def set_bt_management_enabled(self, enabled: bool) -> None:
        """Release (enabled=False) or reclaim (enabled=True) the BT adapter."""
        self.bt_management_enabled = enabled
        self._update_status(
            {
                "bt_management_enabled": enabled,
                "bt_released_by": None if enabled else "user",
            }
        )
        if self.bt_manager:
            if enabled:
                self.bt_manager.allow_reconnect()
            else:
                self.bt_manager.cancel_reconnect()
        if not enabled:
            # Stop daemon via asyncio event loop (subprocess objects are not thread-safe)
            if self.is_running() and self._daemon_proc:
                logger.info("[%s] BT released — stopping sendspin daemon", self.player_name)
                loop = _state.get_main_loop()
                if loop and loop.is_running():
                    fut = asyncio.run_coroutine_threadsafe(self.stop_sendspin(), loop)
                    try:
                        fut.result(timeout=5.0)
                    except (TimeoutError, asyncio.CancelledError, RuntimeError) as exc:
                        logger.debug("[%s] stop_sendspin timed out on BT release: %s", self.player_name, exc)
                else:
                    # Fallback: direct os.kill is safe from any thread
                    try:
                        self._daemon_proc.kill()
                    except Exception as exc:
                        logger.debug("daemon proc kill on BT release failed: %s", exc)
            # Disconnect BT device (synchronous subprocess call, safe from any thread)
            if self.bt_manager:
                try:
                    self.bt_manager.disconnect_device()
                except Exception as e:
                    logger.warning("[%s] Disconnect on release failed: %s", self.player_name, e)
            logger.info("[%s] BT adapter released to host", self.player_name)
        else:
            logger.info("[%s] BT adapter reclaimed — monitor will reconnect", self.player_name)


async def main():
    """Main entry point"""
    orchestrator = BridgeOrchestrator()
    bootstrap = await orchestrator.initialize_runtime()

    try:
        from services.bluetooth import persist_device_enabled as _persist_enabled
    except ImportError:
        _persist_enabled = None

    await orchestrator.run_bridge_lifecycle(
        bootstrap,
        version=get_runtime_version(),
        client_factory=SendspinClient,
        bt_manager_factory=BluetoothManager,
        filter_devices_fn=_filter_duplicate_bluetooth_devices,
        load_saved_volume_fn=_load_saved_device_volume,
        persist_enabled_fn=_persist_enabled,
    )


if __name__ == "__main__":
    asyncio.run(main())
