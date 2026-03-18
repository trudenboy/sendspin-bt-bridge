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

import state as _state
from bluetooth_manager import BluetoothManager
from bridge_orchestrator import BridgeOrchestrator
from config import (
    CONFIG_FILE,
    CONFIG_SCHEMA_VERSION,
    VERSION,
    _player_id_from_mac,
    config_lock,
    save_device_volume,
)
from services.ipc_protocol import (
    IPC_PROTOCOL_VERSION,
    IPC_PROTOCOL_VERSION_KEY,
    parse_protocol_version,
    with_protocol_version,
)
from services.log_analysis import classify_subprocess_stderr_level

UTC = timezone.utc

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


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
    reconnect_attempt: int = 0
    bt_management_enabled: bool = True
    bt_released_by: str | None = None
    battery_level: int | None = None
    group_name: str | None = None
    group_id: str | None = None
    connected_server_url: str | None = None
    track_progress_ms: int | None = None
    track_duration_ms: int | None = None

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
        self._restart_delay: float = 1.0  # exponential backoff for unexpected daemon restarts
        self._start_sendspin_lock: asyncio.Lock | None = None  # set in run(), guards concurrent starts
        self._playing_since: float | None = None  # monotonic time when playing became True
        self._zombie_restart_count: int = 0  # consecutive zombie restarts (reset on real stream)
        self._has_streamed: bool = False  # True after audio_streaming=True in the current play session

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
        events: list[dict[str, object]] = []

        def _add(
            event_type: str,
            *,
            level: str = "info",
            message: str,
            details: dict[str, object] | None = None,
        ) -> None:
            events.append(
                {
                    "event_type": event_type,
                    "level": level,
                    "message": message,
                    "details": dict(details or {}),
                }
            )

        if "bluetooth_connected" in updates and bool(previous.get("bluetooth_connected")) != bool(
            current.get("bluetooth_connected")
        ):
            _add(
                "bluetooth-connected" if current.get("bluetooth_connected") else "bluetooth-disconnected",
                level="info" if current.get("bluetooth_connected") else "warning",
                message="Bluetooth speaker connected"
                if current.get("bluetooth_connected")
                else "Bluetooth speaker disconnected",
            )

        if "server_connected" in updates and bool(previous.get("server_connected")) != bool(
            current.get("server_connected")
        ):
            _add(
                "daemon-connected" if current.get("server_connected") else "daemon-disconnected",
                level="info" if current.get("server_connected") else "warning",
                message="Sendspin daemon connected"
                if current.get("server_connected")
                else "Sendspin daemon disconnected",
            )

        if "playing" in updates and bool(previous.get("playing")) != bool(current.get("playing")):
            _add(
                "playback-started" if current.get("playing") else "playback-stopped",
                message="Playback started" if current.get("playing") else "Playback stopped",
                details={
                    "current_track": current.get("current_track"),
                    "current_artist": current.get("current_artist"),
                },
            )

        if updates.get("audio_streaming") and not previous.get("audio_streaming"):
            _add("audio-streaming", message="Audio stream detected")

        if current.get("reconnecting") and not previous.get("reconnecting"):
            _add(
                "reconnecting",
                level="warning",
                message="Reconnect in progress",
                details={"reconnect_attempt": current.get("reconnect_attempt")},
            )

        if current.get("reanchoring") and not previous.get("reanchoring"):
            _add(
                "reanchoring",
                level="warning",
                message="Audio sync re-anchor in progress",
                details={"reanchor_count": current.get("reanchor_count")},
            )

        current_error = str(current.get("last_error") or "").strip()
        previous_error = str(previous.get("last_error") or "").strip()
        if current_error and current_error != previous_error:
            _add(
                "runtime-error",
                level="error",
                message=current_error,
                details={"last_error_at": current.get("last_error_at")},
            )

        return events

    def _update_status(self, updates: dict) -> None:
        """Thread-safe update of self.status; notifies SSE listeners."""
        recorded_events: list[dict[str, object]] = []
        with self._status_lock:
            previous = self.status.copy()
            # Track zombie-playback timing: record when playing goes True
            if "playing" in updates:
                if updates["playing"] and not self.status.get("playing"):
                    self._playing_since = time.monotonic()
                    self._has_streamed = False
                elif not updates["playing"]:
                    self._playing_since = None
                    self._has_streamed = False
                    self._zombie_restart_count = 0
            # Mark that real audio has arrived in the current play session
            if updates.get("audio_streaming"):
                self._has_streamed = True
                self._zombie_restart_count = 0
            self.status.update(updates)
            recorded_events = self._build_status_events(previous, self.status.copy(), updates)
        for event in recorded_events:
            _state.record_device_event(
                self._event_device_id(),
                str(event["event_type"]),
                level=str(event["level"]),
                message=str(event["message"]),
                details=event["details"] if isinstance(event["details"], dict) else None,
            )
        _state.notify_status_changed()

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

    _ZOMBIE_TIMEOUT_S = 15  # seconds of playing=True without audio_streaming before restart
    _MAX_ZOMBIE_RESTARTS = 3  # stop retrying after this many consecutive zombie restarts

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
            is_playing = self.status.get("playing")
            is_streaming = self.status.get("audio_streaming")
            is_alive = self._daemon_proc is not None and (
                self._daemon_proc.returncode is None if self._daemon_proc else False
            )
            playing_since = self._playing_since
            zombie_count = self._zombie_restart_count
            has_streamed = self._has_streamed

            # Skip if audio has already streamed in this play session —
            # brief gaps (re-anchor, track change) are normal.
            if has_streamed:
                return

            if is_playing and not is_streaming and is_alive and playing_since is not None:
                if zombie_count < self._MAX_ZOMBIE_RESTARTS:
                    elapsed = time.monotonic() - playing_since
                    if elapsed >= self._ZOMBIE_TIMEOUT_S:
                        self._zombie_restart_count += 1
                        self._playing_since = None
                        restart_count = self._zombie_restart_count
                        need_restart = True

        if not need_restart:
            return

        logger.warning(
            "[%s] Zombie playback detected: playing=True but no audio for %.0fs "
            "(restart %d/%d) — restarting subprocess",
            self.player_name,
            elapsed,
            restart_count,
            self._MAX_ZOMBIE_RESTARTS,
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
        if lock.locked():
            logger.debug("[%s] start_sendspin already in progress, skipping duplicate", self.player_name)
            return
        async with lock:
            await self._start_sendspin_inner()

    async def _start_sendspin_inner(self) -> None:
        """Spawn daemon_process.py subprocess with PULSE_SINK in its environment."""
        try:
            # Configure BT audio sink if not yet done
            if self.bt_manager and self.bt_manager.connected and not self.bluetooth_sink_name:
                self.bt_manager.configure_bluetooth_audio()

            # Stop any existing subprocess first
            await self.stop_sendspin()

            # Reset play-session tracking for new subprocess
            self._has_streamed = False

            client_id = self.player_id

            if self.static_delay_ms is not None:
                static_delay_ms = self.static_delay_ms
            else:
                static_delay_ms = float(os.environ.get("SENDSPIN_STATIC_DELAY_MS", "-500"))

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
            )
            with self._status_lock:
                self.status["playing"] = False

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
        _LOG_METHODS = {
            "debug": logger.debug,
            "info": logger.info,
            "warning": logger.warning,
            "error": logger.error,
            "critical": logger.critical,
        }
        # Whitelist of keys accepted from subprocess — prevents unbounded status dict growth
        _ALLOWED_KEYS = frozenset(
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
                "state_changed_at",
                "last_error",
                "last_error_at",
                "track_progress_ms",
                "track_duration_ms",
            }
        )

        def _warn_incompatible_protocol(value: object) -> None:
            if value is None:
                return
            parsed = parse_protocol_version(value)
            if parsed == IPC_PROTOCOL_VERSION:
                return
            cache_key = str(value)
            if cache_key in self._seen_ipc_protocol_warnings:
                return
            self._seen_ipc_protocol_warnings.add(cache_key)
            logger.warning(
                "[%s] Received daemon IPC message with protocol_version=%r; attempting compatible parse",
                self.player_name,
                value,
            )

        async for line in self._daemon_proc.stdout:
            try:
                msg = json.loads(line.decode().strip())
            except (json.JSONDecodeError, ValueError):
                continue
            _warn_incompatible_protocol(msg.get(IPC_PROTOCOL_VERSION_KEY))
            if msg.get("type") == "status":
                updates = {k: v for k, v in msg.items() if k in _ALLOWED_KEYS}
                # Track volume changes for persistence (read both values atomically)
                volume_changed = False
                new_volume = None
                if updates:
                    with self._status_lock:
                        prev_volume = self.status.get("volume")
                    self._update_status(updates)
                    with self._status_lock:
                        new_volume = self.status.get("volume")
                    volume_changed = (
                        new_volume is not None and isinstance(new_volume, int) and new_volume != prev_volume
                    )
                _mac = self.bt_manager.mac_address if self.bt_manager else None
                if volume_changed and _mac and isinstance(new_volume, int):
                    save_device_volume(_mac, new_volume)
            elif msg.get("type") == "log":
                log_fn = _LOG_METHODS.get(msg.get("level", "info"), logger.info)
                log_fn("[%s/proc] %s", self.player_name, msg.get("msg", ""))

    async def _read_subprocess_stderr(self) -> None:
        """Forward daemon subprocess stderr lines with severity matching their content."""
        if self._daemon_proc is None or self._daemon_proc.stderr is None:
            return
        while self._daemon_proc and self._daemon_proc.stderr:
            line = await self._daemon_proc.stderr.readline()
            if not line:
                break
            self._handle_subprocess_stderr_line(line.decode(errors="replace").rstrip())

    def _handle_subprocess_stderr_line(self, line: str) -> None:
        """Classify a daemon stderr line and mirror crash-like output into status."""
        text = line.rstrip()
        if not text:
            return
        level = classify_subprocess_stderr_level(text)
        if level in ("error", "critical"):
            self._update_status(
                {
                    "last_error": text[:500],
                    "last_error_at": datetime.now(tz=UTC).isoformat(),
                }
            )
        log_fn = {
            "warning": logger.warning,
            "error": logger.error,
            "critical": logger.critical,
        }.get(level, logger.warning)
        log_fn("[%s] daemon stderr: %s", self.player_name, text)

    async def _send_subprocess_command(self, cmd: dict) -> None:
        """Write a JSON command to the daemon subprocess stdin."""
        proc = self._daemon_proc
        stdin = proc.stdin if proc else None
        if proc and stdin and proc.returncode is None:
            try:
                stdin.write((json.dumps(with_protocol_version(cmd)) + "\n").encode())
                await stdin.drain()
            except Exception as exc:
                logger.debug("Could not send subprocess command: %s", exc)

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
        await self._send_subprocess_command({"cmd": "reconnect", "delay": 3.0})

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
        # Cancel stdout/stderr reader tasks
        for _task_attr in ("_daemon_task", "_stderr_task"):
            _t = getattr(self, _task_attr, None)
            if _t and not _t.done():
                _t.cancel()
                try:
                    await asyncio.wait_for(_t, timeout=2.0)
                except (TimeoutError, asyncio.CancelledError):
                    pass
            setattr(self, _task_attr, None)

        # Terminate subprocess
        if self._daemon_proc and self._daemon_proc.returncode is None:
            try:
                await self._send_subprocess_command({"cmd": "stop"})
                await asyncio.wait_for(self._daemon_proc.wait(), timeout=3.0)
            except TimeoutError:
                logger.warning("[%s] Daemon subprocess did not exit, killing", self.player_name)
                self._daemon_proc.kill()
                await self._daemon_proc.wait()
            except Exception as exc:
                logger.debug("stop_sendspin: %s", exc)
        self._daemon_proc = None

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

    async def run(self) -> None:
        """Main run loop — connects BT, starts subprocess, monitors health."""
        self.running = True
        self._start_sendspin_lock = asyncio.Lock()

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
            self.bt_manager.management_enabled = enabled
        if not enabled:
            # Stop daemon via asyncio event loop (subprocess objects are not thread-safe)
            if self.is_running() and self._daemon_proc:
                logger.info("[%s] BT released — stopping sendspin daemon", self.player_name)
                loop = _state.get_main_loop()
                if loop and loop.is_running():
                    fut = asyncio.run_coroutine_threadsafe(self.stop_sendspin(), loop)
                    try:
                        fut.result(timeout=5.0)
                    except Exception:
                        logger.debug("[%s] stop_sendspin timed out on BT release", self.player_name)
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
    except Exception:
        _persist_enabled = None

    await orchestrator.run_bridge_lifecycle(
        bootstrap,
        version=VERSION,
        client_factory=SendspinClient,
        bt_manager_factory=BluetoothManager,
        filter_devices_fn=_filter_duplicate_bluetooth_devices,
        load_saved_volume_fn=_load_saved_device_volume,
        persist_enabled_fn=_persist_enabled,
    )


if __name__ == "__main__":
    asyncio.run(main())
