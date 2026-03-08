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
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass, field, fields
from datetime import datetime

import state as _state
from bluetooth_manager import BluetoothManager
from config import (
    CONFIG_FILE,
    _player_id_from_mac,
    ensure_bridge_name,
    load_config,
    save_device_volume,
)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


@dataclass
class DeviceStatus:
    """Typed status container for a single Sendspin device.

    Supports dict-style access (``status["key"]``, ``status.get("key")``,
    ``status.update({...})``, ``status.copy()``) so existing callers require
    no changes.  Only declared fields can be set — this prevents unbounded
    dict growth from unexpected subprocess keys.
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
    uptime_start: datetime = field(default_factory=datetime.now)
    reconnecting: bool = False
    reconnect_attempt: int = 0
    bt_management_enabled: bool = True
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
        if hasattr(self, key):
            setattr(self, key, value)
        else:
            logger.debug("DeviceStatus: unknown key ignored: %s", key)

    def __contains__(self, key: object) -> bool:
        return hasattr(self, key) if isinstance(key, str) else False

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def update(self, d: dict) -> None:
        for k, v in d.items():
            if hasattr(self, k):
                setattr(self, k, v)
            else:
                logger.debug("DeviceStatus: unknown key ignored: %s", k)

    def copy(self) -> dict:
        return {f.name: getattr(self, f.name) for f in fields(self)}


class SendspinClient:
    """Wrapper for sendspin CLI with status tracking"""

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
        self.player_id: str = ""  # set in _start_sendspin_inner; used for solo MA queue lookup
        self.bt_management_enabled: bool = True
        self.bluetooth_sink_name = None  # Store Bluetooth sink name for volume sync
        self.connected_server_url: str = ""  # actual resolved ws:// URL (populated after connect)
        self._daemon_proc: asyncio.subprocess.Process | None = None
        self._daemon_task: asyncio.Task | None = None  # stdout reader task
        self._stderr_task: asyncio.Task | None = None  # stderr reader task
        self._monitor_task: asyncio.Task | None = None
        self._restart_delay: float = 1.0  # exponential backoff for unexpected daemon restarts
        self._start_sendspin_lock: asyncio.Lock | None = None  # set in run(), guards concurrent starts
        self._playing_since: float | None = None  # monotonic time when playing became True
        self._zombie_restart_count: int = 0  # consecutive zombie restarts (reset on real stream)

    def _update_status(self, updates: dict) -> None:
        """Thread-safe update of self.status; notifies SSE listeners."""
        with self._status_lock:
            # Track zombie-playback timing: record when playing goes True
            if "playing" in updates:
                if updates["playing"] and not self.status.get("playing"):
                    self._playing_since = time.monotonic()
                elif not updates["playing"]:
                    self._playing_since = None
                    self._zombie_restart_count = 0
            # Reset zombie tracking when real audio arrives
            if updates.get("audio_streaming"):
                self._zombie_restart_count = 0
            self.status.update(updates)
        _state.notify_status_changed()

    def get_ip_address(self) -> str:
        """Get the primary IP address of this machine"""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
        except Exception:
            return "unknown"

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
                                "bluetooth_connected_at": datetime.now().isoformat(),
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
        """Detect zombie state (playing=True, streaming=False) and schedule restart."""
        with self._status_lock:
            is_playing = self.status.get("playing")
            is_streaming = self.status.get("audio_streaming")
            is_alive = self._daemon_proc is not None and (
                self._daemon_proc.returncode is None if self._daemon_proc else False
            )

        if not is_playing or is_streaming or not is_alive:
            return
        if self._playing_since is None:
            return
        if self._zombie_restart_count >= self._MAX_ZOMBIE_RESTARTS:
            return  # already gave up

        elapsed = time.monotonic() - self._playing_since
        if elapsed < self._ZOMBIE_TIMEOUT_S:
            return

        self._zombie_restart_count += 1
        self._playing_since = None  # reset so we don't fire again immediately
        logger.warning(
            "[%s] Zombie playback detected: playing=True but no audio for %.0fs "
            "(restart %d/%d) — restarting subprocess",
            self.player_name,
            elapsed,
            self._zombie_restart_count,
            self._MAX_ZOMBIE_RESTARTS,
        )
        # Schedule restart on the event loop (we're called from an async context)
        asyncio.ensure_future(self._zombie_restart())

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

            safe_id = "".join(c if c.isalnum() or c == "-" else "-" for c in self.player_name.lower()).strip("-")
            _mac = self.bt_manager.mac_address if self.bt_manager else None
            client_id = _player_id_from_mac(_mac) if _mac else f"sendspin-{safe_id}"
            self.player_id = str(client_id)  # persist for MA solo-queue lookup

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
                {
                    "player_name": self.player_name,
                    "client_id": str(client_id),
                    "listen_port": self.listen_port,
                    "url": server_url,
                    "static_delay_ms": static_delay_ms,
                    "bluetooth_sink_name": self.bluetooth_sink_name,
                    "volume": self.status.get("volume", 100),
                    "muted": bool(self.status.get("muted", False)),
                    "settings_dir": f"/tmp/sendspin-{safe_id}",
                    "preferred_format": self.preferred_format,
                }
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
        async for line in self._daemon_proc.stdout:
            try:
                msg = json.loads(line.decode().strip())
            except (json.JSONDecodeError, ValueError):
                continue
            if msg.get("type") == "status":
                # Track volume changes for persistence
                with self._status_lock:
                    prev_volume = self.status.get("volume")
                updates = {k: v for k, v in msg.items() if k in _ALLOWED_KEYS}
                if updates:
                    self._update_status(updates)
                new_volume = self.status.get("volume")
                _mac = self.bt_manager.mac_address if self.bt_manager else None
                if new_volume is not None and isinstance(new_volume, int) and new_volume != prev_volume and _mac:
                    save_device_volume(_mac, new_volume)
            elif msg.get("type") == "log":
                log_fn = _LOG_METHODS.get(msg.get("level", "info"), logger.info)
                log_fn("[%s/proc] %s", self.player_name, msg.get("msg", ""))

    async def _read_subprocess_stderr(self) -> None:
        """Forward daemon subprocess stderr lines to logger as warnings."""
        if self._daemon_proc is None or self._daemon_proc.stderr is None:
            return
        while self._daemon_proc and self._daemon_proc.stderr:
            line = await self._daemon_proc.stderr.readline()
            if not line:
                break
            logger.warning("[%s] daemon stderr: %s", self.player_name, line.decode().rstrip())

    async def _send_subprocess_command(self, cmd: dict) -> None:
        """Write a JSON command to the daemon subprocess stdin."""
        if self._daemon_proc and self._daemon_proc.stdin and self._daemon_proc.returncode is None:
            try:
                self._daemon_proc.stdin.write((json.dumps(cmd) + "\n").encode())
                await self._daemon_proc.stdin.drain()
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

        with self._status_lock:
            self.status["server_connected"] = False
            self.status["connected"] = False
            self.status["playing"] = False
            self.status["audio_streaming"] = False
            self.status["current_track"] = None
            self.status["current_artist"] = None
            self.status["audio_format"] = None
            self.status["reanchoring"] = False
            self.status["group_name"] = None
            self.status["group_id"] = None

    def is_running(self) -> bool:
        """Return True if the daemon subprocess is alive."""
        return self._daemon_proc is not None and self._daemon_proc.returncode is None

    async def run(self):
        """Main run loop"""
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
                                "bluetooth_connected_at": datetime.now().isoformat(),
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

    async def stop(self):
        """Stop the client"""
        self.running = False

    def set_bt_management_enabled(self, enabled: bool) -> None:
        """Release (enabled=False) or reclaim (enabled=True) the BT adapter."""
        self.bt_management_enabled = enabled
        self._update_status({"bt_management_enabled": enabled})
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

    config = load_config()
    server_host = config.get("SENDSPIN_SERVER", "auto")
    server_port = int(config.get("SENDSPIN_PORT") or 9000)

    # Bridge name identification (auto-populated with hostname on first run)
    effective_bridge = ensure_bridge_name(config)
    # Set timezone
    tz = os.getenv("TZ", config.get("TZ", "UTC"))
    os.environ["TZ"] = tz
    time.tzset()
    logger.info("Timezone: %s", tz)

    # PulseAudio latency — larger buffer reduces underflows on slow hardware
    pulse_latency_msec = int(config.get("PULSE_LATENCY_MSEC") or 200)
    os.environ["PULSE_LATENCY_MSEC"] = str(pulse_latency_msec)
    logger.info("PULSE_LATENCY_MSEC: %s ms", pulse_latency_msec)

    # Log level — apply to root logger and inherit in subprocesses via env var
    log_level = config.get("LOG_LEVEL", "INFO").upper()
    if log_level not in ("INFO", "DEBUG"):
        log_level = "INFO"
    logging.getLogger().setLevel(getattr(logging, log_level))
    os.environ["LOG_LEVEL"] = log_level
    logger.info("Log level: %s", log_level)

    prefer_sbc = bool(config.get("PREFER_SBC_CODEC", False))
    if prefer_sbc:
        logger.info("PREFER_SBC_CODEC: enabled — will request SBC codec after BT connect")

    bt_check_interval = int(config.get("BT_CHECK_INTERVAL") or 10)
    bt_max_reconnect_fails = int(config.get("BT_MAX_RECONNECT_FAILS") or 0)
    bt_churn_threshold = int(config.get("BT_CHURN_THRESHOLD") or 0)
    bt_churn_window = float(config.get("BT_CHURN_WINDOW") or 300)
    if bt_churn_threshold > 0:
        logger.info("BT churn isolation: enabled (threshold=%d in %.0fs)", bt_churn_threshold, bt_churn_window)

    # Normalise device list — fall back to legacy BLUETOOTH_MAC
    bt_devices = config.get("BLUETOOTH_DEVICES", [])
    if not bt_devices:
        mac = config.get("BLUETOOTH_MAC", "")
        bt_devices = [{"mac": mac, "adapter": "", "player_name": "Sendspin Player"}]

    logger.info("Starting %s player instance(s)", len(bt_devices))
    if server_host and server_host.lower() not in ["auto", "discover", ""]:
        logger.info("Server: %s:%s", server_host, server_port)
    else:
        logger.info("Server: Auto-discovery enabled (mDNS)")

    _default_player_name = os.getenv("SENDSPIN_NAME") or f"Sendspin-{socket.gethostname()}"

    base_listen_port = 8928
    clients = []
    for i, device in enumerate(bt_devices):
        mac = device.get("mac", "")
        adapter = device.get("adapter", "")
        player_name = device.get("player_name") or _default_player_name
        if effective_bridge:
            player_name = f"{player_name} @ {effective_bridge}"
        # 'listen_port' is the preferred key; 'port' kept for backward compat
        listen_port = int(device.get("listen_port") or device.get("port") or base_listen_port + i)
        listen_host = device.get("listen_host")
        static_delay_ms = device.get("static_delay_ms")
        if static_delay_ms is not None:
            static_delay_ms = float(static_delay_ms)
        preferred_format = device.get("preferred_format", "flac:44100:16:2")
        keepalive_interval = int(device.get("keepalive_interval") or 0)
        # keepalive_silence (bool) is the legacy key; interval > 0 is the new canonical form
        keepalive_enabled = keepalive_interval > 0 or bool(device.get("keepalive_silence", False))
        keepalive_interval = max(30, keepalive_interval) if keepalive_enabled else 30

        client = SendspinClient(
            player_name,
            server_host,
            server_port,
            None,
            listen_port=listen_port,
            static_delay_ms=static_delay_ms,
            listen_host=listen_host,
            effective_bridge=effective_bridge,
            preferred_format=preferred_format or None,
            keepalive_enabled=keepalive_enabled,
            keepalive_interval=keepalive_interval,
        )
        if mac:

            def _on_sink_found(sink_name: str, restored_volume=None, _c=client) -> None:
                _c.bluetooth_sink_name = sink_name
                logger.info("Stored Bluetooth sink for volume sync: %s", sink_name)
                if restored_volume is not None:
                    _c._update_status({"volume": restored_volume})

            bt_mgr = BluetoothManager(
                mac,
                adapter=adapter,
                device_name=player_name,
                client=client,
                prefer_sbc=prefer_sbc,
                check_interval=bt_check_interval,
                max_reconnect_fails=bt_max_reconnect_fails,
                on_sink_found=_on_sink_found,
                churn_threshold=bt_churn_threshold,
                churn_window=bt_churn_window,
            )
            bt_available = bt_mgr.check_bluetooth_available()
            if not bt_available:
                logger.warning("BT adapter '%s' not available for %s", adapter or "default", player_name)
            client.bt_manager = bt_mgr
            client._update_status({"bluetooth_available": bt_available})
            bt_enabled = device.get("enabled", True)
            if not bt_enabled:
                client.bt_management_enabled = False
                client._update_status({"bt_management_enabled": False})
                bt_mgr.management_enabled = False
                logger.info("  Player '%s': BT management disabled at startup", player_name)
            # Pre-fill volume from saved LAST_VOLUMES so UI shows correct value before BT connects
            try:
                with open(CONFIG_FILE) as _f:
                    _saved = json.load(_f)
                _saved_vol = _saved.get("LAST_VOLUMES", {}).get(mac)
                if _saved_vol is not None and isinstance(_saved_vol, int) and 0 <= _saved_vol <= 100:
                    client._update_status({"volume": _saved_vol})
            except Exception as exc:
                logger.debug("pre-fill saved volume failed: %s", exc)
        clients.append(client)
        logger.info("  Player: '%s', BT: %s, Adapter: %s", player_name, mac or "none", adapter or "default")

    logger.info("Client instance(s) registered")

    # Sync enabled state to options.json so HA addon config page reflects current state
    try:
        from services.bluetooth import persist_device_enabled as _persist_enabled

        for _c in clients:
            _persist_enabled(_c.player_name, _c.bt_management_enabled)
    except Exception as _e:
        logger.debug("Could not sync enabled state to options.json: %s", _e)

    # Warn about listen_port collisions (all containers share host network)
    used_ports: set = set()
    for _c in clients:
        if _c.listen_port in used_ports:
            logger.warning(
                "[%s] listen_port %s already used by another client — sendspin daemon will fail to bind. Set unique 'listen_port' per device.",
                _c.player_name,
                _c.listen_port,
            )
        elif _c.listen_port == 8928 and len(clients) > 1:
            logger.warning(
                "[%s] Using default listen_port 8928 with multiple devices — set explicit ports.", _c.player_name
            )
        used_ports.add(_c.listen_port)

    # Size the thread pool to support concurrent BT reconnects across all devices.
    # Default asyncio executor has too few threads when many devices reconnect simultaneously.
    from concurrent.futures import ThreadPoolExecutor

    _pool_size = min(64, max(8, len(clients) * 2 + 4))
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=_pool_size))
    logger.debug("ThreadPoolExecutor: max_workers=%s", _pool_size)

    # Start web interface in background thread
    def run_web_server():
        from state import set_clients
        from web_interface import main as web_main

        set_clients(clients)
        web_main()

    web_thread = threading.Thread(target=run_web_server, daemon=True, name="WebServer")
    web_thread.start()
    logger.info("Web interface starting in background...")

    # Handle shutdown signals
    loop = asyncio.get_running_loop()

    async def _graceful_shutdown():
        logger.info("Received shutdown signal — pausing players before exit...")
        active = [c for c in clients if c.is_running()]
        if active:
            await asyncio.gather(*[c._send_subprocess_command({"cmd": "pause"}) for c in active])
            logger.info("Sent pause to %s player(s) — waiting 500 ms...", len(active))
            await asyncio.sleep(0.5)
        for c in clients:
            c.running = False
            await c.stop_sendspin()

    def signal_handler():
        loop.create_task(_graceful_shutdown())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    # Expose the running loop so Flask/WSGI threads can schedule coroutines
    _state.set_main_loop(asyncio.get_running_loop())

    # Discover MA syncgroups via MA API.
    # In HA addon mode (SUPERVISOR_TOKEN present): auto-detect URL and try supervisor token.
    # Otherwise: use explicit MA_API_URL + MA_API_TOKEN from config.
    ma_api_url = config.get("MA_API_URL", "").strip()
    ma_api_token = config.get("MA_API_TOKEN", "").strip()

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
    if supervisor_token:
        # HA addon mode: auto-detect MA URL if not explicitly configured.
        # Both addons share host networking → localhost:8095 is always reachable.
        if not ma_api_url:
            if server_host and server_host.lower() not in ("auto", "discover", ""):
                ma_api_url = f"http://{server_host}:8095"
            else:
                ma_api_url = "http://localhost:8095"
            logger.info("MA API URL auto-detected (addon mode): %s", ma_api_url)
        # SUPERVISOR_TOKEN is a HA token, NOT an MA token. MA uses its own JWT auth.
        # Do NOT use SUPERVISOR_TOKEN for MA auth. Warn user if no explicit MA token.
        if not ma_api_token:
            logger.warning(
                "MA API: running in HA addon mode but no 'ma_api_token' configured. "
                "Create a long-lived token in MA → Settings → API Tokens and set ma_api_token in bridge config."
            )

    if ma_api_url and ma_api_token:
        _state.set_ma_api_credentials(ma_api_url, ma_api_token)
        try:
            from services.ma_client import discover_ma_groups

            player_names = [c.player_name for c in clients]
            name_map, all_groups = await discover_ma_groups(ma_api_url, ma_api_token, player_names)
            _state.set_ma_groups(name_map, all_groups)
            if name_map:
                _state.set_ma_connected(True)
        except Exception as _ma_exc:
            logger.warning("MA API group discovery error: %s", _ma_exc)

    # Start MA monitor if credentials configured
    ma_monitor_task = None
    if ma_api_url and ma_api_token:
        from services.ma_monitor import start_monitor

        monitor = start_monitor(ma_api_url, ma_api_token)
        ma_monitor_task = asyncio.create_task(monitor.run())

    # Run all clients in parallel
    client_tasks = [asyncio.create_task(c.run()) for c in clients]
    tasks = client_tasks + ([ma_monitor_task] if ma_monitor_task else [])
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
