#!/usr/bin/env python3
"""
Sendspin Client with Bluetooth Management
Runs the sendspin CLI player with Bluetooth speaker management
"""

import asyncio
import json
import logging
import os
import signal
import socket
import threading
import time
from datetime import datetime
from typing import Optional

from config import (
    VERSION as CLIENT_VERSION,
    _CONFIG_PATH,
    _config_lock,
    _player_id_from_mac,
    _save_device_volume,
    load_config,
)
from mpris import (
    _DBUS_MPRIS_AVAILABLE,
    _GLib,
    MprisIdentityService,
    pause_all_via_mpris as _pause_all_via_mpris,
)
from bluetooth_manager import BluetoothManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class SendspinClient:
    """Wrapper for sendspin CLI with status tracking"""
    
    def __init__(self, player_name: str, server_host: str, server_port: int,
                 bt_manager: Optional[BluetoothManager] = None,
                 listen_port: int = 8928,
                 static_delay_ms: Optional[float] = None,
                 listen_host: Optional[str] = None,
                 effective_bridge: str = ''):
        self.player_name = player_name
        self.server_host = server_host
        self.server_port = server_port
        self.bt_manager = bt_manager
        self.listen_port = listen_port  # port sendspin daemon listens on
        self.listen_host = listen_host  # explicit IP for WebSocket URL display (None = auto-detect)
        self.static_delay_ms = static_delay_ms  # per-device delay override (None = use env var)
        self._effective_bridge = effective_bridge  # bridge instance label for MA device info

        # Status tracking
        self.status = {
            'connected': False,
            'playing': False,
            'bluetooth_available': bt_manager.check_bluetooth_available() if bt_manager else False,
            'bluetooth_connected': False,
            'bluetooth_connected_at': None,
            'server_connected': False,
            'server_connected_at': None,
            'current_track': None,
            'current_artist': None,
            'volume': 100,
            'muted': False,
            'audio_format': None,
            'reanchor_count': 0,
            'last_sync_error_ms': None,
            'reanchoring': False,
            'state_changed_at': None,
            'ip_address': listen_host or self.get_ip_address(),
            'hostname': socket.gethostname(),
            'last_error': None,
            'uptime_start': datetime.now(),
            'reconnecting': False,
            'reconnect_attempt': 0,
            'bt_management_enabled': True,
            'group_name': None,
            'group_id': None,
        }

        self.process = None  # kept for API compatibility (routes/api.py uses it for process check)
        self.running = False
        self.bt_management_enabled: bool = True
        self.bluetooth_sink_name = None  # Store Bluetooth sink name for volume sync
        self.connected_server_url: str = ''  # actual resolved ws:// URL (populated after connect)
        self._daemon_task: Optional[asyncio.Task] = None
        self._bridge_daemon = None  # BridgeDaemon instance (in-process sendspin)
        self._monitor_task: Optional[asyncio.Task] = None
        self._status_lock = threading.Lock()  # protects concurrent reads/writes of self.status

    def update_status(self, **kwargs) -> None:
        """Thread-safe update of one or more status fields."""
        with self._status_lock:
            self.status.update(kwargs)

    def get_status(self) -> dict:
        """Return a shallow copy of status dict for safe cross-thread reads."""
        with self._status_lock:
            return dict(self.status)
    
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
                    if bt_connected != self.status['bluetooth_connected']:
                        self.status['bluetooth_connected'] = bt_connected
                        self.status['bluetooth_connected_at'] = datetime.now().isoformat()

                # Check in-process daemon health
                if self._daemon_task:
                    if self._daemon_task.done():
                        self.status['server_connected'] = False
                        self.status['connected'] = False
                        self.status['group_name'] = None
                        self.status['group_id'] = None
                        # Don't restart if BT is disconnected — monitor_and_reconnect
                        # will call start_sendspin() once BT reconnects.
                        if not self.bt_manager or self.bt_manager.connected:
                            logger.warning("Daemon task died unexpectedly, restarting...")
                            await self.start_sendspin()
                        else:
                            logger.info("Daemon task stopped; waiting for BT to reconnect")
                    else:
                        # Daemon is running — update connected state from daemon client
                        daemon = self._bridge_daemon
                        if daemon and daemon._client is not None:
                            is_connected = getattr(daemon._client, 'connected', False)
                            if is_connected and not self.status.get('server_connected'):
                                self.status['server_connected'] = True
                                self.status['connected'] = True
                                if not self.status.get('server_connected_at'):
                                    self.status['server_connected_at'] = datetime.now().isoformat()

                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Error updating status: {e}")
                await asyncio.sleep(10)
    
    def _detect_server_url_from_proc(self) -> str:
        """Kept for backwards compatibility — in-process daemon doesn't use this."""
        return ''

    async def start_sendspin(self) -> None:
        """Start the sendspin daemon in-process via BridgeDaemon."""
        try:
            from services.bridge_daemon import BridgeDaemon, resolve_audio_device_for_sink
            from sendspin.daemon.daemon import DaemonArgs
            from sendspin.settings import get_client_settings
        except ImportError as e:
            logger.error(f"sendspin package not available (running outside container?): {e}")
            self.status['last_error'] = str(e)
            return

        try:
            # Configure BT audio sink if not yet done
            if self.bt_manager and self.bt_manager.connected and not self.bluetooth_sink_name:
                self.bt_manager.configure_bluetooth_audio()

            # Stop any existing daemon task first
            await self.stop_sendspin()

            safe_id = ''.join(
                c if c.isalnum() or c == '-' else '-' for c in self.player_name.lower()
            ).strip('-')
            _mac = self.bt_manager.mac_address if self.bt_manager else None
            client_id = _player_id_from_mac(_mac) if _mac else f"sendspin-{safe_id}"

            if self.static_delay_ms is not None:
                static_delay_ms = self.static_delay_ms
            else:
                static_delay_ms = float(os.environ.get('SENDSPIN_STATIC_DELAY_MS', '-500'))

            server_url: Optional[str] = None
            if self.server_host and self.server_host.lower() not in ('auto', 'discover', ''):
                server_url = f"ws://{self.server_host}:{self.server_port}/sendspin"
                logger.info(f"Starting Sendspin player '{self.player_name}' connecting to {server_url} (port {self.listen_port})")
            else:
                logger.info(f"Starting Sendspin player '{self.player_name}' with auto-discovery (port {self.listen_port})")

            # Resolve audio device matching the BT sink
            audio_device = await resolve_audio_device_for_sink(self.bluetooth_sink_name)
            if audio_device:
                logger.info(f"[{self.player_name}] Audio device resolved: {audio_device.name!r} (index {audio_device.index}) for sink {self.bluetooth_sink_name!r}")
            else:
                logger.error("No audio output device found — cannot start daemon")
                self.status['last_error'] = 'No audio output device found'
                return

            # Per-instance isolated settings to avoid ~/.config/sendspin/ conflicts
            settings = await get_client_settings('daemon', config_dir=f'/tmp/sendspin-{safe_id}')
            # Seed volume from saved value so playback starts at correct level
            saved_vol = self.status.get('volume', 100)
            if isinstance(saved_vol, int) and 0 <= saved_vol <= 100:
                settings.player_volume = saved_vol

            args = DaemonArgs(
                audio_device=audio_device,
                client_id=client_id,
                client_name=self.player_name,
                settings=settings,
                url=server_url,
                static_delay_ms=static_delay_ms,
                listen_port=self.listen_port,
                use_mpris=True,
                use_hardware_volume=False,  # we manage volume via pactl against the BT sink
            )

            def _on_volume_save(vol: int) -> None:
                try:
                    _save_device_volume(getattr(self.bt_manager, 'mac_address', None), vol)
                except Exception as exc:
                    logger.debug(f"Could not save volume: {exc}")

            self._bridge_daemon = BridgeDaemon(
                args=args,
                status=self.status,
                bluetooth_sink_name=self.bluetooth_sink_name,
                on_volume_save=_on_volume_save,
            )
            self.status['playing'] = False

            self._daemon_task = asyncio.create_task(self._bridge_daemon.run())

            def _on_daemon_done(t: asyncio.Task) -> None:
                if not t.cancelled() and t.exception():
                    logger.error(f"[{self.player_name}] BridgeDaemon ended with error: {t.exception()}")

            self._daemon_task.add_done_callback(_on_daemon_done)
            logger.info(f"Sendspin daemon started in-process for '{self.player_name}'")

        except Exception as e:
            logger.error(f"Failed to start Sendspin daemon: {e}")
            self.status['last_error'] = str(e)
            self.status['server_connected'] = False

    async def stop_sendspin(self) -> None:
        """Stop the in-process sendspin daemon task."""
        if self._daemon_task and not self._daemon_task.done():
            self._daemon_task.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._daemon_task), timeout=3.0)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        self._daemon_task = None
        self._bridge_daemon = None
        self.status['server_connected'] = False
        self.status['connected'] = False
        self.status['group_name'] = None
        self.status['group_id'] = None

    def is_running(self) -> bool:
        """Return True if the in-process daemon task is alive."""
        return self._daemon_task is not None and not self._daemon_task.done()

    async def run(self):
        """Main run loop"""
        self.running = True

        # Start Sendspin player first (don't block on Bluetooth)
        if self.bt_management_enabled:
            await self.start_sendspin()
        else:
            logger.info(f"[{self.player_name}] BT management disabled — skipping sendspin startup")

        # Start background tasks
        tasks = [
            asyncio.create_task(self._status_monitor_loop())
        ]

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
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, self.bt_manager.connect_device)
                    bt_now = self.bt_manager.is_device_connected()
                    if bt_now != self.status['bluetooth_connected']:
                        self.status['bluetooth_connected'] = bt_now
                        self.status['bluetooth_connected_at'] = datetime.now().isoformat()
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
        self.status['bt_management_enabled'] = enabled
        if self.bt_manager:
            self.bt_manager.management_enabled = enabled
        if not enabled:
            # Cancel the in-process daemon task (task.cancel() is thread-safe)
            if self.is_running():
                logger.info(f"[{self.player_name}] BT released — stopping sendspin daemon")
                if self._daemon_task:
                    self._daemon_task.cancel()
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
    server_host = config.get('SENDSPIN_SERVER', 'auto')
    server_port = int(config.get('SENDSPIN_PORT', 9000))

    # Bridge name identification
    raw_bridge = config.get('BRIDGE_NAME', '') or os.getenv('BRIDGE_NAME', '')
    if raw_bridge.lower() in ('auto', 'hostname'):
        effective_bridge = socket.gethostname()
    else:
        effective_bridge = raw_bridge  # '' = disabled
    # Set timezone
    tz = os.getenv('TZ', config.get('TZ', 'UTC'))
    os.environ['TZ'] = tz
    time.tzset()
    logger.info(f"Timezone: {tz}")

    # PulseAudio latency — larger buffer reduces underflows on slow hardware
    pulse_latency_msec = int(config.get('PULSE_LATENCY_MSEC', 200))
    os.environ['PULSE_LATENCY_MSEC'] = str(pulse_latency_msec)
    logger.info(f"PULSE_LATENCY_MSEC: {pulse_latency_msec} ms")

    prefer_sbc = bool(config.get('PREFER_SBC_CODEC', False))
    if prefer_sbc:
        logger.info("PREFER_SBC_CODEC: enabled — will request SBC codec after BT connect")

    bt_check_interval = int(config.get('BT_CHECK_INTERVAL', 10))
    bt_max_reconnect_fails = int(config.get('BT_MAX_RECONNECT_FAILS', 0))

    # Normalise device list — fall back to legacy BLUETOOTH_MAC
    bt_devices = config.get('BLUETOOTH_DEVICES', [])
    if not bt_devices:
        mac = config.get('BLUETOOTH_MAC', '')
        bt_devices = [{'mac': mac, 'adapter': '', 'player_name': 'Sendspin Player'}]

    logger.info(f"Starting {len(bt_devices)} player instance(s)")
    if server_host and server_host.lower() not in ['auto', 'discover', '']:
        logger.info(f"Server: {server_host}:{server_port}")
    else:
        logger.info("Server: Auto-discovery enabled (mDNS)")

    _default_player_name = (
        os.getenv('SENDSPIN_NAME')
        or f"Sendspin-{socket.gethostname()}"
    )

    base_listen_port = 8928
    clients = []
    for i, device in enumerate(bt_devices):
        mac = device.get('mac', '')
        adapter = device.get('adapter', '')
        player_name = device.get('player_name') or _default_player_name
        if effective_bridge:
            player_name = f"{player_name} @ {effective_bridge}"
        # 'listen_port' is the preferred key; 'port' kept for backward compat
        listen_port = int(device.get('listen_port') or device.get('port') or base_listen_port + i)
        listen_host = device.get('listen_host')
        static_delay_ms = device.get('static_delay_ms')
        if static_delay_ms is not None:
            static_delay_ms = float(static_delay_ms)

        client = SendspinClient(player_name, server_host, server_port, None,
                                listen_port=listen_port, static_delay_ms=static_delay_ms,
                                listen_host=listen_host, effective_bridge=effective_bridge)
        if mac:
            bt_mgr = BluetoothManager(mac, adapter=adapter, device_name=player_name, client=client,
                                       prefer_sbc=prefer_sbc,
                                       check_interval=bt_check_interval,
                                       max_reconnect_fails=bt_max_reconnect_fails)
            if not bt_mgr.check_bluetooth_available():
                logger.warning(f"BT adapter '{adapter or 'default'}' not available for {player_name}")
            client.bt_manager = bt_mgr
            client.status['bluetooth_available'] = bt_mgr.check_bluetooth_available()
            bt_enabled = device.get('enabled', True)
            if not bt_enabled:
                client.bt_management_enabled = False
                client.status['bt_management_enabled'] = False
                bt_mgr.management_enabled = False
                logger.info(f"  Player '{player_name}': BT management disabled at startup")
            # Pre-fill volume from saved LAST_VOLUMES so UI shows correct value before BT connects
            try:
                with open(_CONFIG_PATH) as _f:
                    _saved = json.load(_f)
                _saved_vol = _saved.get('LAST_VOLUMES', {}).get(mac)
                if _saved_vol is not None and isinstance(_saved_vol, int) and 0 <= _saved_vol <= 100:
                    client.status['volume'] = _saved_vol
            except Exception:
                pass
        clients.append(client)
        logger.info(f"  Player: '{player_name}', BT: {mac or 'none'}, Adapter: {adapter or 'default'}")

    logger.info("Client instance(s) registered")

    # Register MPRIS Identity services on the session bus (one per player)
    if _DBUS_MPRIS_AVAILABLE:
        try:
            dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
            for _i, _c in enumerate(clients):
                MprisIdentityService(_c.player_name, _i)
            threading.Thread(target=_GLib.MainLoop().run, daemon=True, name='mpris-glib').start()
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
                f"[{_c.player_name}] Using default listen_port 8928 with multiple devices — "
                f"set explicit ports."
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
    loop = asyncio.get_event_loop()

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

    # Run all clients in parallel
    await asyncio.gather(*[c.run() for c in clients])




if __name__ == '__main__':
    asyncio.run(main())
