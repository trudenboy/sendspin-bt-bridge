#!/usr/bin/env python3
"""
Sendspin Client with Bluetooth Management
Runs the sendspin CLI player with Bluetooth speaker management
"""

import asyncio
import json
import logging
import os
import re
import signal
import socket
import subprocess
import threading
import time
from datetime import datetime
from typing import Optional

import netifaces

from config import (
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
    read_mpris_metadata_for as _read_mpris_metadata_for,
)
from bluetooth_manager import BluetoothManager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

CLIENT_VERSION = "1.4.0"

# Per-player audio format cache — keyed by player_name.
# Updated when a full "Audio format: flac 48000Hz/24-bit/2ch" line is received;
# read by the same player to fill in format details when only
# "Stream started with codec X" was received.
_last_full_audio_format: dict = {}  # player_name → format string

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
        }

        self.process = None
        self.running = False
        self.bt_management_enabled: bool = True
        self.bluetooth_sink_name = None  # Store Bluetooth sink name for volume sync
        self.connected_server_url: str = ''  # actual resolved ws:// URL (populated after connect)
        self.volume_restore_done = False  # Flag to prevent saving initial volume
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
            # Try to get IP from default gateway interface
            gws = netifaces.gateways()
            default_interface = gws['default'][netifaces.AF_INET][1]
            addrs = netifaces.ifaddresses(default_interface)
            return addrs[netifaces.AF_INET][0]['addr']
        except Exception:
            # Fallback method
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                try:
                    s.connect(("8.8.8.8", 80))
                    return s.getsockname()[0]
                finally:
                    s.close()
            except Exception:
                return "unknown"
    
    async def update_status(self):
        """Update client status"""
        logger.debug("Status monitoring loop started")
        loop = asyncio.get_event_loop()
        while self.running:
            try:
                if self.bt_manager:
                    # Run blocking BT check in thread pool — never block the event loop
                    bt_connected = await loop.run_in_executor(None, self.bt_manager.is_device_connected)
                    logger.debug(f"Bluetooth status check: connected={bt_connected}")
                    if bt_connected != self.status['bluetooth_connected']:
                        self.status['bluetooth_connected'] = bt_connected
                        self.status['bluetooth_connected_at'] = datetime.now().isoformat()
                
                # Check if process is still running
                if self.process:
                    if self.process.poll() is not None:
                        if self.status['server_connected']:
                            self.status['server_connected_at'] = datetime.now().isoformat()
                        self.status['server_connected'] = False
                        self.status['connected'] = False
                        # Don't restart sendspin if BT is disconnected — monitor_and_reconnect
                        # will start it again once BT reconnects (prevents PortAudio error flood).
                        if not self.bt_manager or self.bt_manager.connected:
                            logger.warning("Sendspin process died, restarting...")
                            await self.start_sendspin_process()
                        else:
                            logger.info("Sendspin process stopped; waiting for BT to reconnect before restarting")
                    else:
                        # Process is running, mark as connected
                        if not self.status['server_connected']:
                            self.status['server_connected_at'] = datetime.now().isoformat()
                        self.status['server_connected'] = True
                        self.status['connected'] = True

                        # Fallback: detect actual server IP via /proc/{pid}/fd + /proc/net/tcp[6]
                        if self.status['server_connected'] and not self.connected_server_url and self.process:
                            try:
                                self.connected_server_url = self._detect_server_url_from_proc()
                            except Exception:
                                pass

                        # Poll MPRIS for track metadata and authoritative playback state
                        if self.process:
                            artist, track, playback_status = await loop.run_in_executor(
                                None, _read_mpris_metadata_for, self.process.pid
                            )
                            if playback_status is not None:
                                # MPRIS is authoritative — overrides log-based detection
                                is_playing = (playback_status == 'Playing')
                                if is_playing != self.status.get('playing'):
                                    self.status['playing'] = is_playing
                                    self.status['state_changed_at'] = datetime.now().isoformat()
                            if artist is not None or track is not None:
                                self.status['current_artist'] = artist
                                self.status['current_track'] = track
                            # Don't clear track/artist on pause — keep last known values for display
                        else:
                            self.status['current_artist'] = None
                            self.status['current_track'] = None

                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Error updating status: {e}")
                await asyncio.sleep(10)
    
    def _detect_server_url_from_proc(self) -> str:
        """Detect server URL from /proc/net/tcp by finding the inbound connection from MA.

        MA connects TO sendspin's listen_port; the remote IP on that connection is MA's host.
        We combine it with the configured server_port to build the ws:// URL.
        """
        import os as _os
        pid = self.process.pid
        # Collect socket inodes owned by this process
        socket_inodes: set = set()
        try:
            fd_dir = f'/proc/{pid}/fd'
            for fd in _os.listdir(fd_dir):
                try:
                    target = _os.readlink(f'{fd_dir}/{fd}')
                    if target.startswith('socket:['):
                        socket_inodes.add(target[8:-1])
                except OSError:
                    pass
        except OSError:
            return ''
        if not socket_inodes:
            return ''

        listen_port_hex = format(self.listen_port, '04X') if self.listen_port else None

        def _decode_ipv4(ip_hex: str) -> str:
            b = bytes.fromhex(ip_hex)
            return f'{b[3]}.{b[2]}.{b[1]}.{b[0]}'

        for fname in ('/proc/net/tcp6', '/proc/net/tcp'):
            try:
                with open(fname) as _f:
                    lines = _f.readlines()[1:]
            except OSError:
                continue
            for line in lines:
                cols = line.split()
                if len(cols) < 10 or cols[3] != '01':  # state 01 = ESTABLISHED
                    continue
                if cols[9] not in socket_inodes:
                    continue
                local_parts = cols[1].split(':')
                remote_parts = cols[2].split(':')
                if len(local_parts) < 2 or len(remote_parts) < 2:
                    continue
                local_port_hex = local_parts[-1]
                # We want the inbound connection to our listen_port (MA → sendspin)
                if listen_port_hex and local_port_hex.upper() != listen_port_hex:
                    continue
                # Decode the remote (MA) IP address
                ip_hex = remote_parts[0]
                try:
                    if len(ip_hex) == 32:  # tcp6 — check for IPv4-mapped ::ffff:x.x.x.x
                        words = [ip_hex[i:i+8] for i in range(0, 32, 8)]
                        if words[0] == '00000000' and words[1] == '00000000' and words[2] == 'FFFF0000':
                            ip = _decode_ipv4(words[3])
                        else:
                            continue  # pure IPv6 — skip
                    elif len(ip_hex) == 8:  # tcp — little-endian IPv4
                        ip = _decode_ipv4(ip_hex)
                    else:
                        continue
                    server_port = self.server_port or 9000
                    return f'ws://{ip}:{server_port}/sendspin'
                except Exception:
                    continue
        return ''

    async def start_sendspin_process(self):
        """Start the sendspin CLI player"""
        try:
            # If BT is connected but sink hasn't been configured yet (e.g. process restart
            # triggered by _read_until_eof before monitor_and_reconnect runs configure),
            # configure the audio sink now so bluetooth_sink_name is available.
            if self.bt_manager and self.bt_manager.connected and not self.bluetooth_sink_name:
                self.bt_manager.configure_bluetooth_audio()

            # Kill any existing process first to free the port
            if self.process and self.process.poll() is None:
                logger.info(f"Stopping existing sendspin process (PID {self.process.pid}) before restart")
                try:
                    self.process.terminate()
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, lambda: self.process.wait(timeout=3))
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass

            # Build command — use 'daemon' subcommand with unique port per instance
            safe_id = ''.join(c if c.isalnum() or c == '-' else '-' for c in self.player_name.lower()).strip('-')
            _mac = self.bt_manager.mac_address if self.bt_manager else None
            client_id = _player_id_from_mac(_mac) if _mac else f"sendspin-{safe_id}"
            # static_delay_ms compensates for BT A2DP + PA buffer latency (~500ms total)
            # Negative value = schedule audio earlier to account for output latency
            # Per-device value takes priority over the env var global default
            if self.static_delay_ms is not None:
                static_delay_ms = self.static_delay_ms
            else:
                static_delay_ms = float(os.environ.get('SENDSPIN_STATIC_DELAY_MS', '-500'))
            cmd = [
                'sendspin', 'daemon',
                '--name', self.player_name,
                '--id', client_id,
                '--port', str(self.listen_port),
                '--static-delay-ms', str(static_delay_ms),
                '--hardware-volume', 'false',
            ]

            # Add server URL only if explicitly configured
            if self.server_host and self.server_host.lower() not in ['auto', 'discover', '']:
                server_url = f"ws://{self.server_host}:{self.server_port}/sendspin"
                logger.info(f"Starting Sendspin player '{self.player_name}' connecting to {server_url} (port {self.listen_port})")
                cmd.extend(['--url', server_url])
            else:
                logger.info(f"Starting Sendspin player '{self.player_name}' with auto-discovery (port {self.listen_port})")

            # Isolate per-instance config via HOME — avoids shared ~/.config/sendspin/
            instance_home = f"/tmp/sendspin-{safe_id}"
            os.makedirs(instance_home, exist_ok=True)
            env = os.environ.copy()
            env['HOME'] = instance_home

            if self.bt_manager:
                pa_mac = self.bt_manager.mac_address.replace(':', '_')
                # Use the sink name probed by configure_bluetooth_audio if available;
                # fall back to legacy PulseAudio format for PULSE_SINK env var only.
                if self.bluetooth_sink_name:
                    pulse_sink = self.bluetooth_sink_name
                    env['PULSE_SINK'] = pulse_sink
                    cmd.extend(['--audio-device', pulse_sink])
                    logger.info(f"Routing audio to sink: {pulse_sink}")
                else:
                    # Sink not confirmed yet — set PULSE_SINK only, omit --audio-device
                    pulse_sink = f"bluez_sink.{pa_mac}.a2dp_sink"
                    env['PULSE_SINK'] = pulse_sink
                    logger.info(f"Routing audio to sink (PULSE_SINK only): {pulse_sink}")

            # Start the sendspin process with elevated scheduling priority
            def _set_nice():
                try:
                    os.nice(-5)
                except OSError:
                    pass  # requires root or CAP_SYS_NICE

            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                preexec_fn=_set_nice,
            )

            logger.info(f"Sendspin player started (PID: {self.process.pid})")
            logger.info(f"Sendspin command: {' '.join(cmd)}")
            self.status['playing'] = False  # reset; monitor_output() will set True on Stream STARTED
            
            # Monitor output in background — cancel any previous task first
            if self._monitor_task and not self._monitor_task.done():
                self._monitor_task.cancel()
            self._monitor_task = asyncio.create_task(self.monitor_output())
            def _on_monitor_output_done(t):
                if not t.cancelled() and t.exception():
                    logger.error(
                        f"[{self.player_name}] monitor_output ended with error: {t.exception()}"
                    )
            self._monitor_task.add_done_callback(_on_monitor_output_done)
            
        except Exception as e:
            logger.error(f"Failed to start Sendspin player: {e}")
            self.status['last_error'] = str(e)
            self.status['server_connected'] = False
    
    async def monitor_output(self):
        """Monitor sendspin process output and sync volume changes"""
        if not self.process:
            return

        # Capture process reference so this task stays bound to THIS process instance
        # even if self.process is reassigned when a new process is started.
        process = self.process
        loop = asyncio.get_event_loop()

        def _read_until_eof():
            """Run in a SINGLE thread pool slot.
            Reading one line at a time with per-line run_in_executor calls saturates
            the thread pool when sendspin emits errors at high rate (100+ lines/sec),
            starving is_device_connected() calls and blocking BT disconnect detection.
            Running the entire loop in ONE executor call avoids that problem.
            Python's logging and dict writes are thread-safe via the GIL.
            Sentinel is '' (empty str) because process is opened with text=True."""
            for raw_line in iter(process.stdout.readline, ''):
                if not self.running:
                    break
                line_str = raw_line.strip()
                logger.info(f"Sendspin: {line_str}")

                # Update playing state — sendspin uses Python logging format:
                # "INFO:sendspin.audio:Stream STARTED: N chunks, ..."
                # "INFO:sendspin.audio:Stream STOPPED" (if/when emitted)
                # "INFO:aiosendspin.client.client:Stream started with codec flac"
                if 'Stream STARTED' in line_str or 'Stream started with codec' in line_str:
                    self.status['playing'] = True
                    self.status['state_changed_at'] = datetime.now().isoformat()
                    # Extract codec from "Stream started with codec flac"
                    if 'Stream started with codec' in line_str:
                        try:
                            codec = line_str.split('Stream started with codec')[-1].strip()
                            if codec:
                                # Use cached full format if codec matches, else just codec
                                cached = _last_full_audio_format.get(self.player_name, '')
                                if cached and cached.startswith(codec):
                                    self.status['audio_format'] = cached
                                else:
                                    self.status['audio_format'] = codec
                        except Exception:
                            pass
                elif 'Stream STOPPED' in line_str or 'MPRIS interface stopped' in line_str:
                    self.status['playing'] = False
                    self.status['state_changed_at'] = datetime.now().isoformat()

                # Parse audio format: "Audio format: flac 48000Hz/24-bit/2ch"
                if 'Audio format:' in line_str:
                    try:
                        fmt = line_str.split('Audio format:')[-1].strip()
                        if fmt:
                            _last_full_audio_format[self.player_name] = fmt
                        self.status['audio_format'] = fmt
                    except Exception:
                        pass

                # Parse sync events: "Sync error 503.6 ms too large; re-anchoring"
                # or "Audio underflow detected; requesting re-anchor"
                if 're-anchoring' in line_str or 're-anchor' in line_str:
                    try:
                        m = re.search(r'Sync error ([\d.]+)\s*ms', line_str)
                        if m:
                            self.status['last_sync_error_ms'] = float(m.group(1))
                        self.status['reanchor_count'] += 1
                        self.status['reanchoring'] = True
                    except Exception:
                        pass
                elif 'Stream STARTED' in line_str:
                    self.status['reanchoring'] = False

                # Track server connection — actual sendspin output:
                # "INFO:sendspin.daemon.daemon:Server connected"
                # "INFO:aiosendspin.client.client:Handshake with server complete"
                if 'Server connected' in line_str or 'Handshake with server complete' in line_str:
                    if not self.status['server_connected_at']:
                        self.status['server_connected_at'] = datetime.now().isoformat()
                    self.status['server_connected'] = True

                # Try to capture actual server URL from sendspin output
                if not self.connected_server_url:
                    m = re.search(r'ws://[^\s/]+:\d+/\S*', line_str)
                    if m:
                        self.connected_server_url = m.group(0)

                # Sync volume changes to Bluetooth speaker
                # Handles "Volume: XX%" and "Server set player volume: XX%"
                if ('Volume:' in line_str or 'player volume:' in line_str.lower()) and self.bluetooth_sink_name:
                    try:
                        # Split on last colon to get the value regardless of prefix
                        volume_part = line_str.rsplit(':', 1)[-1].strip().rstrip('%')
                        if volume_part and volume_part.isdigit():
                            volume_percent = int(volume_part)
                            self.status['volume'] = volume_percent

                            if self.volume_restore_done:
                                try:
                                    _save_device_volume(
                                        getattr(self.bt_manager, 'mac_address', None),
                                        volume_percent
                                    )
                                except Exception as e:
                                    logger.debug(f"Could not save volume to config: {e}")

                            result = subprocess.run(
                                ['pactl', 'set-sink-volume', self.bluetooth_sink_name, f'{volume_percent}%'],
                                capture_output=True,
                                text=True,
                                timeout=2
                            )
                            if result.returncode == 0:
                                logger.info(f"✓ Synced Bluetooth speaker volume to {volume_percent}%")
                    except Exception as e:
                        logger.debug(f"Could not sync volume: {e}")

        try:
            await loop.run_in_executor(None, _read_until_eof)
        except asyncio.CancelledError:
            pass  # task was cancelled (e.g. process restarted) — exit cleanly
        except Exception as e:
            logger.error(f"Error monitoring output: {e}")
    
    async def run(self):
        """Main run loop"""
        self.running = True

        # Start Sendspin player first (don't block on Bluetooth)
        if self.bt_management_enabled:
            await self.start_sendspin_process()
        else:
            logger.info(f"[{self.player_name}] BT management disabled — skipping sendspin startup")

        # Start background tasks
        tasks = [
            asyncio.create_task(self.update_status())
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
                    self.status['bluetooth_connected'] = self.bt_manager.is_device_connected()
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
            
            if self.process:
                try:
                    self.process.terminate()
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, lambda: self.process.wait(timeout=5))
                except Exception as e:
                    logger.error(f"Error terminating process: {e}")
                    try:
                        self.process.kill()
                    except Exception:
                        pass
    
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
            # Stop sendspin subprocess
            if self.process and self.process.poll() is None:
                logger.info(f"[{self.player_name}] BT released — stopping sendspin")
                try:
                    self.process.terminate()
                except Exception:
                    pass
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
                                       prefer_sbc=prefer_sbc)
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
        from web_interface import set_clients, main as web_main
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
            if c.process:
                try:
                    c.process.terminate()
                except Exception:
                    pass

    def signal_handler():
        loop.create_task(_graceful_shutdown())

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    # Run all clients in parallel
    await asyncio.gather(*[c.run() for c in clients])




if __name__ == '__main__':
    asyncio.run(main())
