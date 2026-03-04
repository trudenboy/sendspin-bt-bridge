#!/usr/bin/env python3
"""
Sendspin Client with Bluetooth Management
Runs the sendspin CLI player with Bluetooth speaker management
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import signal
import socket
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

import state as _state
from bluetooth_manager import BluetoothManager
from config import (
    CONFIG_FILE as _CONFIG_PATH,
)
from config import (
    _player_id_from_mac,
    _save_device_volume,
    load_config,
)
from mpris import (
    _DBUS_MPRIS_AVAILABLE,
    MprisIdentityService,
    _GLib,
)
from mpris import (
    pause_all_via_mpris as _pause_all_via_mpris,
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
        # Silently ignore unknown keys (mirrors old dict behaviour for safety)

    def __contains__(self, key: object) -> bool:
        return hasattr(self, key) if isinstance(key, str) else False

    def get(self, key: str, default=None):
        return getattr(self, key, default)

    def update(self, d: dict) -> None:
        for k, v in d.items():
            if hasattr(self, k):
                setattr(self, k, v)

    def copy(self) -> dict:
        return dataclasses.asdict(self)


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

        # Status tracking
        self.status = DeviceStatus(
            bluetooth_available=bt_manager.check_bluetooth_available() if bt_manager else False,
            ip_address=listen_host or self.get_ip_address(),
            hostname=socket.gethostname(),
        )

        self._status_lock = threading.Lock()
        self.running = False
        self.bt_management_enabled: bool = True
        self.bluetooth_sink_name = None  # Store Bluetooth sink name for volume sync
        self.connected_server_url: str = ""  # actual resolved ws:// URL (populated after connect)
        self._daemon_proc: asyncio.subprocess.Process | None = None
        self._daemon_task: asyncio.Task | None = None  # stdout reader task
        self._stderr_task: asyncio.Task | None = None  # stderr reader task
        self._bridge_daemon = None  # kept for API compatibility, always None in subprocess mode
        self._monitor_task: asyncio.Task | None = None
        self._restart_delay: float = 1.0  # exponential backoff for unexpected daemon restarts
        self._start_sendspin_lock: asyncio.Lock | None = None  # set in run(), guards concurrent starts

    def _update_status(self, updates: dict) -> None:
        """Thread-safe update of self.status; notifies SSE listeners."""
        with self._status_lock:
            self.status.update(updates)
        _state.notify_status_changed()

    def get_ip_address(self) -> str:
        """Get the primary IP address of this machine"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                s.connect(("8.8.8.8", 80))
                return s.getsockname()[0]
            finally:
                s.close()
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

                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"Error updating status: {e}")
                await asyncio.sleep(2)

    async def start_sendspin(self) -> None:
        """Start the sendspin daemon as an isolated subprocess with PULSE_SINK routing."""
        lock = self._start_sendspin_lock
        if lock is None:
            await self._start_sendspin_inner()
            return
        if lock.locked():
            logger.debug(f"[{self.player_name}] start_sendspin already in progress, skipping duplicate")
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

            if self.static_delay_ms is not None:
                static_delay_ms = self.static_delay_ms
            else:
                static_delay_ms = float(os.environ.get("SENDSPIN_STATIC_DELAY_MS", "-500"))

            server_url: str | None = None
            if self.server_host and self.server_host.lower() not in ("auto", "discover", ""):
                server_url = f"ws://{self.server_host}:{self.server_port}/sendspin"
                logger.info(
                    f"Starting Sendspin player '{self.player_name}' connecting to {server_url} (port {self.listen_port})"
                )
            else:
                logger.info(
                    f"Starting Sendspin player '{self.player_name}' with auto-discovery (port {self.listen_port})"
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
                    "settings_dir": f"/tmp/sendspin-{safe_id}",
                    "preferred_format": self.preferred_format,
                }
            )

            # Build subprocess environment: inherit everything + PULSE_SINK for routing
            env = os.environ.copy()
            if self.bluetooth_sink_name:
                env["PULSE_SINK"] = self.bluetooth_sink_name
                logger.info(f"[{self.player_name}] Subprocess PULSE_SINK={self.bluetooth_sink_name}")

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
                    logger.error(f"[{self.player_name}] stdout reader error: {t.exception()}")

            self._daemon_task.add_done_callback(_on_reader_done)
            logger.info(f"Sendspin daemon subprocess started (PID {self._daemon_proc.pid}) for '{self.player_name}'")

        except Exception as e:
            logger.error(f"Failed to start Sendspin daemon subprocess: {e}")
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
                    _save_device_volume(_mac, new_volume)
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
                logger.warning(f"[{self.player_name}] Daemon subprocess did not exit, killing")
                self._daemon_proc.kill()
                await self._daemon_proc.wait()
            except Exception as exc:
                logger.debug("stop_sendspin: %s", exc)
        self._daemon_proc = None
        self._bridge_daemon = None

        with self._status_lock:
            self.status["server_connected"] = False
            self.status["connected"] = False
            self.status["group_name"] = None
            self.status["group_id"] = None

    def is_running(self) -> bool:
        """Return True if the daemon subprocess is alive."""
        return self._daemon_proc is not None and self._daemon_proc.returncode is None

    async def run(self):
        """Main run loop"""
        self.running = True
        self._start_sendspin_lock = asyncio.Lock()

        # Start Sendspin player first (don't block on Bluetooth)
        if self.bt_management_enabled:
            await self.start_sendspin()
        else:
            logger.info(f"[{self.player_name}] BT management disabled — skipping sendspin startup")

        # Start background tasks
        tasks = [asyncio.create_task(self._status_monitor_loop())]

        # Handle Bluetooth connection in background if configured
        logger.info(f"Bluetooth manager present: {self.bt_manager is not None}")
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
                    if bt_now and self.bluetooth_sink_name:
                        logger.info(
                            f"[{self.player_name}] BT connected with sink {self.bluetooth_sink_name} "
                            "— restarting daemon on correct audio device"
                        )
                        await self.start_sendspin()
                except Exception as e:
                    logger.error(f"Error connecting Bluetooth: {e}")

            tasks.append(asyncio.create_task(connect_bluetooth_async()))
            mon_task = asyncio.create_task(self.bt_manager.monitor_and_reconnect())

            def _on_monitor_done(t):
                if not t.cancelled() and t.exception():
                    logger.error(f"[{self.player_name}] monitor_and_reconnect task DIED: {t.exception()}")

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
            # Terminate the daemon subprocess (safe from any thread via kill)
            if self.is_running() and self._daemon_proc:
                logger.info(f"[{self.player_name}] BT released — stopping sendspin daemon")
                self._daemon_proc.kill()
            # Disconnect BT device (synchronous subprocess call, safe from any thread)
            if self.bt_manager:
                try:
                    self.bt_manager.disconnect_device()
                except Exception as e:
                    logger.warning(f"[{self.player_name}] Disconnect on release failed: {e}")
            logger.info(f"[{self.player_name}] BT adapter released to host")
        else:
            logger.info(f"[{self.player_name}] BT adapter reclaimed — monitor will reconnect")


async def main():
    """Main entry point"""

    config = load_config()
    server_host = config.get("SENDSPIN_SERVER", "auto")
    server_port = int(config.get("SENDSPIN_PORT", 9000))

    # Bridge name identification
    raw_bridge = config.get("BRIDGE_NAME", "") or os.getenv("BRIDGE_NAME", "")
    if raw_bridge.lower() in ("auto", "hostname"):
        effective_bridge = socket.gethostname()
    elif bool(config.get("BRIDGE_NAME_SUFFIX", False)) and not raw_bridge:
        # No explicit bridge name but suffix enabled → use hostname
        effective_bridge = socket.gethostname()
    else:
        effective_bridge = raw_bridge  # '' = disabled
    # Set timezone
    tz = os.getenv("TZ", config.get("TZ", "UTC"))
    os.environ["TZ"] = tz
    time.tzset()
    logger.info(f"Timezone: {tz}")

    # PulseAudio latency — larger buffer reduces underflows on slow hardware
    pulse_latency_msec = int(config.get("PULSE_LATENCY_MSEC", 200))
    os.environ["PULSE_LATENCY_MSEC"] = str(pulse_latency_msec)
    logger.info(f"PULSE_LATENCY_MSEC: {pulse_latency_msec} ms")

    prefer_sbc = bool(config.get("PREFER_SBC_CODEC", False))
    if prefer_sbc:
        logger.info("PREFER_SBC_CODEC: enabled — will request SBC codec after BT connect")

    bt_check_interval = int(config.get("BT_CHECK_INTERVAL", 10))
    bt_max_reconnect_fails = int(config.get("BT_MAX_RECONNECT_FAILS", 0))

    # Normalise device list — fall back to legacy BLUETOOTH_MAC
    bt_devices = config.get("BLUETOOTH_DEVICES", [])
    if not bt_devices:
        mac = config.get("BLUETOOTH_MAC", "")
        bt_devices = [{"mac": mac, "adapter": "", "player_name": "Sendspin Player"}]

    logger.info(f"Starting {len(bt_devices)} player instance(s)")
    if server_host and server_host.lower() not in ["auto", "discover", ""]:
        logger.info(f"Server: {server_host}:{server_port}")
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
        )
        if mac:
            bt_mgr = BluetoothManager(
                mac,
                adapter=adapter,
                device_name=player_name,
                client=client,
                prefer_sbc=prefer_sbc,
                check_interval=bt_check_interval,
                max_reconnect_fails=bt_max_reconnect_fails,
            )
            if not bt_mgr.check_bluetooth_available():
                logger.warning(f"BT adapter '{adapter or 'default'}' not available for {player_name}")
            client.bt_manager = bt_mgr
            client._update_status({"bluetooth_available": bt_mgr.check_bluetooth_available()})
            bt_enabled = device.get("enabled", True)
            if not bt_enabled:
                client.bt_management_enabled = False
                client._update_status({"bt_management_enabled": False})
                bt_mgr.management_enabled = False
                logger.info(f"  Player '{player_name}': BT management disabled at startup")
            # Pre-fill volume from saved LAST_VOLUMES so UI shows correct value before BT connects
            try:
                with open(_CONFIG_PATH) as _f:
                    _saved = json.load(_f)
                _saved_vol = _saved.get("LAST_VOLUMES", {}).get(mac)
                if _saved_vol is not None and isinstance(_saved_vol, int) and 0 <= _saved_vol <= 100:
                    client._update_status({"volume": _saved_vol})
            except Exception:
                pass
        clients.append(client)
        logger.info(f"  Player: '{player_name}', BT: {mac or 'none'}, Adapter: {adapter or 'default'}")

    logger.info("Client instance(s) registered")

    # Sync enabled state to options.json so HA addon config page reflects current state
    try:
        from services.bluetooth import persist_device_enabled as _persist_enabled

        for _c in clients:
            _persist_enabled(_c.player_name, _c.bt_management_enabled)
    except Exception as _e:
        logger.debug(f"Could not sync enabled state to options.json: {_e}")

    # Register MPRIS Identity services on the session bus (one per player)
    if _DBUS_MPRIS_AVAILABLE and _GLib is not None:
        try:
            import dbus.mainloop.glib as _dbus_ml

            _dbus_ml.DBusGMainLoop(set_as_default=True)
            for _i, _c in enumerate(clients):
                MprisIdentityService(_c.player_name, _i)
            threading.Thread(target=_GLib.MainLoop().run, daemon=True, name="mpris-glib").start()
            logger.info("MPRIS Identity service(s) registered on session bus")
        except Exception as _e:
            logger.warning(f"MPRIS Identity service unavailable: {_e}")

    # Warn about listen_port collisions (all containers share host network)
    used_ports: set = set()
    for _c in clients:
        if _c.listen_port in used_ports:
            logger.warning(
                f"[{_c.player_name}] listen_port {_c.listen_port} already used by another "
                f"client — sendspin daemon will fail to bind. Set unique 'listen_port' per device."
            )
        elif _c.listen_port == 8928 and len(clients) > 1:
            logger.warning(
                f"[{_c.player_name}] Using default listen_port 8928 with multiple devices — set explicit ports."
            )
        used_ports.add(_c.listen_port)

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
        paused = await loop.run_in_executor(None, _pause_all_via_mpris)
        if paused:
            logger.info(f"Paused {paused} player(s) in MA — waiting 500 ms...")
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

    # Run all clients in parallel
    await asyncio.gather(*[c.run() for c in clients])


if __name__ == "__main__":
    asyncio.run(main())
