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
import time
from datetime import datetime
from typing import Optional

import netifaces

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

CLIENT_VERSION = "1.2.0"

# Per-player audio format cache — keyed by player_name.
# Updated when a full "Audio format: flac 48000Hz/24-bit/2ch" line is received;
# read by the same player to fill in format details when only
# "Stream started with codec X" was received.
_last_full_audio_format: dict = {}  # player_name → format string

_CONFIG_PATH = '/config/config.json'


def _save_device_volume(mac: Optional[str], volume: int) -> None:
    """Persist per-device volume to config.json under LAST_VOLUMES[mac]."""
    if not mac or not os.path.exists(_CONFIG_PATH):
        return
    try:
        with open(_CONFIG_PATH, 'r') as f:
            cfg = json.load(f)
        cfg.setdefault('LAST_VOLUMES', {})[mac] = volume
        with open(_CONFIG_PATH, 'w') as f:
            json.dump(cfg, f, indent=2)
    except Exception as e:
        logger.debug(f"Could not save volume for {mac}: {e}")


class BluetoothManager:
    """Manages Bluetooth speaker connections using bluetoothctl"""
    
    def __init__(self, mac_address: str, adapter: str = "", device_name: str = "", client=None):
        self.mac_address = mac_address
        self.adapter = adapter        # "hci0", "hci1", etc. — empty = use default
        self.device_name = device_name or mac_address
        self.client = client
        self.connected = False
        self.last_check = 0
        self.check_interval = 10  # Check every 10 seconds

        # Resolve adapter name to MAC for reliable 'select' in bridged D-Bus setups.
        # In LXC containers, 'select hci0' fails ("Controller hci0 not available");
        # selecting by MAC address works because D-Bus objects use MACs, not hciN names.
        self._adapter_select = self._resolve_adapter_select(adapter) if adapter else ''

    def _resolve_adapter_select(self, adapter: str) -> str:
        """Resolve hciN to adapter MAC address for bluetoothctl 'select'.
        Falls back to the original name if resolution fails."""
        if not adapter or not adapter.startswith('hci'):
            return adapter  # Already a MAC or empty string
        try:
            idx = int(adapter[3:])  # N from hciN
        except ValueError:
            return adapter
        try:
            result = subprocess.run(
                ['bash', '-c', 'bluetoothctl list 2>/dev/null'],
                capture_output=True, text=True, timeout=5
            )
            # Parse "Controller <MAC> description [default]" lines
            macs = []
            for line in result.stdout.splitlines():
                if 'Controller' in line:
                    for part in line.split():
                        if len(part) == 17 and part.count(':') == 5:
                            macs.append(part.upper())
                            break
            if idx < len(macs):
                logger.info(f"Resolved adapter {adapter} → {macs[idx]}")
                return macs[idx]
        except Exception as e:
            logger.debug(f"Adapter MAC resolution failed: {e}")
        return adapter  # Fall back to hciN name

    def _run_bluetoothctl(self, commands: list) -> tuple[bool, str]:
        """Run bluetoothctl commands, prepending 'select <adapter_mac>' if configured.
        Uses stdin pipe directly — no shell, no injection risk."""
        try:
            all_commands = []
            if self._adapter_select:
                all_commands.append(f'select {self._adapter_select}')
            all_commands.extend(commands)
            cmd_string = '\n'.join(all_commands) + '\n'
            result = subprocess.run(
                ['bluetoothctl'],
                input=cmd_string,
                capture_output=True,
                text=True,
                timeout=10
            )
            return result.returncode == 0, result.stdout
        except Exception as e:
            logger.error(f"Bluetoothctl error: {e}")
            return False, str(e)
    
    def check_bluetooth_available(self) -> bool:
        """Check if Bluetooth is available on the system"""
        try:
            if self.adapter:
                # Check specific adapter via _run_bluetoothctl (includes select)
                success, output = self._run_bluetoothctl(['show'])
                return success and 'Controller' in output
            # Default: check for any controller
            result = subprocess.run(
                ['bluetoothctl', 'show'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.returncode == 0:
                output_lower = result.stdout.lower()
                return 'controller' in output_lower and 'no default controller' not in output_lower
            return False
        except Exception as e:
            logger.error(f"Bluetooth not available: {e}")
            return False
    
    def is_device_paired(self) -> bool:
        """Check if device is paired"""
        # 'info MAC' as a single command (not split into two list elements).
        # select is prepended automatically via _adapter_select (resolved to adapter MAC).
        success, output = self._run_bluetoothctl([f'info {self.mac_address}'])
        return success and 'Paired: yes' in output

    def is_device_connected(self) -> bool:
        """Check if device is currently connected"""
        try:
            # Use resolved adapter MAC for 'select' so that 'info MAC' queries
            # the right adapter-specific device DB (BlueZ devices are adapter-scoped).
            success, output = self._run_bluetoothctl([f'info {self.mac_address}'])
            is_connected = success and 'Connected: yes' in output

            # Log status changes
            if is_connected != self.connected:
                if is_connected:
                    logger.info(f"✓ BT device {self.device_name} ({self.mac_address}) connected")
                else:
                    logger.warning(f"✗ BT device {self.device_name} ({self.mac_address}) disconnected")

            self.connected = is_connected
            return self.connected
        except Exception as e:
            logger.debug(f"Error checking Bluetooth connection: {e}")
            self.connected = False
            return False
    
    def pair_device(self) -> bool:
        """Pair with the Bluetooth device.

        Uses a single long-running bluetoothctl session with stdin kept open:
        1. Scan for 12s so BlueZ caches the device (required for 'pair' to work)
        2. Pair + trust while device is still in cache / pairing mode
        The device MUST be in pairing/discoverable mode when this runs.
        """
        logger.info(f"Pairing with {self.mac_address}...")
        adapter_prefix = f'select {self._adapter_select}\n' if self._adapter_select else ''
        mac = self.mac_address
        # Keep stdin open: scan 12s to discover device → pair → trust → scan off
        bash_cmd = (
            f'( printf "{adapter_prefix}power on\\nagent on\\ndefault-agent\\nscan on\\n";'
            f' sleep 12;'
            f' printf "pair {mac}\\ntrust {mac}\\nscan off\\n";'
            f' sleep 10'
            f' ) | timeout 28 bluetoothctl 2>&1'
        )
        try:
            result = subprocess.run(
                ['bash', '-c', bash_cmd],
                capture_output=True, text=True, timeout=32
            )
            out = result.stdout
            logger.info(f"Pair output (last 600 chars): {out[-600:]}")
            ok = ('Pairing successful' in out or 'Already paired' in out
                  or 'Paired: yes' in out)
            if ok:
                logger.info("Pairing successful")
            else:
                logger.warning(f"Pairing may have failed. Output: {out[-200:]}")
            return ok
        except Exception as e:
            logger.error(f"Pair error: {e}")
            return False
    
    def trust_device(self) -> bool:
        """Trust the Bluetooth device"""
        success, _ = self._run_bluetoothctl([f'trust {self.mac_address}'])
        return success
    

    def configure_bluetooth_audio(self) -> bool:
        """Configure host's PipeWire/PulseAudio to use the Bluetooth device as audio output"""
        try:
            # Wait for PipeWire/PulseAudio to register the device.
            # A2DP profile takes a few seconds to appear after BT connects.
            time.sleep(3)
            
            # Format the MAC address for PipeWire/PulseAudio (replace : with _)
            pa_mac = self.mac_address.replace(':', '_')
            
            # List available sinks first
            result = subprocess.run(
                ['pactl', 'list', 'short', 'sinks'],
                capture_output=True,
                text=True,
                timeout=5
            )
            logger.info(f"Available audio sinks:\n{result.stdout}")
            
            # Find the Bluetooth sink (do NOT change system default — PULSE_SINK handles per-process routing)
            sink_names = [
                f"bluez_output.{pa_mac}.1",  # PipeWire format
                f"bluez_output.{pa_mac}.a2dp-sink",
                f"bluez_sink.{pa_mac}.a2dp_sink",  # Legacy PulseAudio format
                f"bluez_sink.{pa_mac}",
            ]

            success = False
            configured_sink = None
            # Retry up to 3 times — A2DP sink may take a few extra seconds to appear
            for attempt in range(3):
                for sink_name in sink_names:
                    result = subprocess.run(
                        ['pactl', 'get-sink-volume', sink_name],
                        capture_output=True,
                        text=True,
                        timeout=5
                    )
                    if result.returncode == 0:
                        logger.info(f"✓ Found audio sink: {sink_name}")
                        configured_sink = sink_name
                        success = True
                        break
                    else:
                        logger.debug(f"Sink {sink_name} not found, trying next...")
                if success:
                    break
                if attempt < 2:
                    logger.info(f"Sink not yet available, retrying in 3s... (attempt {attempt + 1}/3)")
                    time.sleep(3)
            
            if success and configured_sink:
                # Set volume to 100% for maximum output
                result = subprocess.run(
                    ['pactl', 'set-sink-volume', configured_sink, '100%'],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    logger.info(f"✓ Set Bluetooth speaker volume to 100%")
                else:
                    logger.warning("Could not set Bluetooth speaker volume")
                    
                # Store the sink name in client for volume sync
                if self.client:
                    self.client.bluetooth_sink_name = configured_sink
                    logger.info(f"Stored Bluetooth sink for volume sync: {configured_sink}")
                    
                    # Restore last volume for this device (keyed by MAC)
                    restored = False
                    try:
                        config_path = '/config/config.json'
                        if os.path.exists(config_path):
                            with open(config_path, 'r') as f:
                                cfg = json.load(f)
                            # Per-device dict (preferred); fall back to legacy single value
                            volumes = cfg.get('LAST_VOLUMES', {})
                            last_volume = volumes.get(self.mac_address)
                            if last_volume is None:
                                last_volume = cfg.get('LAST_VOLUME')
                            if last_volume is not None and isinstance(last_volume, int) and 0 <= last_volume <= 100:
                                result = subprocess.run(
                                    ['pactl', 'set-sink-volume', configured_sink, f'{last_volume}%'],
                                    capture_output=True,
                                    text=True,
                                    timeout=2
                                )
                                if result.returncode == 0:
                                    logger.info(f"✓ Restored volume to {last_volume}% for {self.mac_address}")
                                    self.client.status['volume'] = last_volume
                                    restored = True
                    except Exception as e:
                        logger.debug(f"Could not restore volume: {e}")

                    # Always set flag to allow saving future changes
                    self.client.volume_restore_done = True
                    if not restored:
                        logger.info("No saved volume to restore, will use current volume")
            elif not success:
                logger.warning(f"Could not find Bluetooth sink for {self.mac_address}")
                logger.warning("Audio may play from default device instead of Bluetooth")
            
            return success
            
        except Exception as e:
            logger.error(f"Error configuring Bluetooth audio: {e}")
            return False

    def connect_device(self) -> bool:
        """Connect to the Bluetooth device"""
        # First check if already connected
        if self.is_device_connected():
            logger.info("Device already connected")
            self.connected = True
            # Ensure audio is configured
            self.configure_bluetooth_audio()
            return True
        
        logger.info(f"Connecting to {self.mac_address}...")
        
        # Ensure paired and trusted (pair_device also runs trust)
        if not self.is_device_paired():
            logger.info("Device not paired, attempting to pair...")
            if not self.pair_device():
                return False
        
        # Power on bluetooth
        self._run_bluetoothctl(['power on'])
        time.sleep(1)
        
        # Try to connect
        success, output = self._run_bluetoothctl([f'connect {self.mac_address}'])
        
        # Wait for connection to establish
        for i in range(5):
            time.sleep(1)
            if self.is_device_connected():
                logger.info("Successfully connected to Bluetooth speaker")
                self.connected = True
                # Configure audio routing
                self.configure_bluetooth_audio()
                return True
        
        logger.warning(f"Failed to connect after 5 attempts")
        return False
    
    def disconnect_device(self) -> bool:
        """Disconnect from the Bluetooth device"""
        success, _ = self._run_bluetoothctl([f'disconnect {self.mac_address}'])
        if success:
            self.connected = False
        return success
    
    async def monitor_and_reconnect(self):
        """Continuously monitor connection and reconnect if needed"""
        logger.info(f"[{self.device_name}] monitor_and_reconnect task started")
        loop = asyncio.get_event_loop()
        iteration = 0
        reconnect_attempt = 0
        while True:
            iteration += 1
            try:
                current_time = time.time()
                if current_time - self.last_check >= self.check_interval:
                    self.last_check = current_time
                    logger.info(f"[{self.device_name}] BT check #{iteration}")

                    # Run blocking BT check in thread pool — never block the event loop
                    connected = await loop.run_in_executor(None, self.is_device_connected)
                    logger.info(f"[{self.device_name}] BT connected={connected}")
                    if not connected:
                        reconnect_attempt += 1
                        if self.client:
                            self.client.status['reconnecting'] = True
                            self.client.status['reconnect_attempt'] = reconnect_attempt

                        # Kill sendspin daemon immediately — if the BT sink is gone,
                        # sendspin floods PortAudioErrors on every audio chunk, which
                        # starves its own event loop and causes WebSocket PONG timeouts.
                        if self.client and self.client.process and self.client.process.poll() is None:
                            logger.info(f"BT disconnected for {self.device_name}, stopping sendspin daemon...")
                            try:
                                self.client.process.terminate()
                            except Exception:
                                pass

                        logger.warning(f"Bluetooth device {self.device_name} disconnected, attempting reconnect... (attempt {reconnect_attempt})")
                        success = await loop.run_in_executor(None, self.connect_device)
                        if success and self.client:
                            reconnect_attempt = 0
                            self.client.status['reconnecting'] = False
                            self.client.status['reconnect_attempt'] = 0
                            # BT reconnected — start fresh sendspin to register with MA
                            logger.info(f"BT reconnected for {self.device_name}, starting sendspin...")
                            await self.client.start_sendspin_process()
                    else:
                        # Device is connected — clear any reconnect state
                        if self.client and self.client.status.get('reconnecting'):
                            self.client.status['reconnecting'] = False
                            self.client.status['reconnect_attempt'] = 0
                        reconnect_attempt = 0

                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error in Bluetooth monitor: {e}")
                await asyncio.sleep(10)


class SendspinClient:
    """Wrapper for sendspin CLI with status tracking"""
    
    def __init__(self, player_name: str, server_host: str, server_port: int,
                 bt_manager: Optional[BluetoothManager] = None,
                 listen_port: int = 8928,
                 static_delay_ms: Optional[float] = None,
                 listen_host: Optional[str] = None):
        self.player_name = player_name
        self.server_host = server_host
        self.server_port = server_port
        self.bt_manager = bt_manager
        self.listen_port = listen_port  # port sendspin daemon listens on
        self.listen_host = listen_host  # explicit IP for WebSocket URL display (None = auto-detect)
        self.static_delay_ms = static_delay_ms  # per-device delay override (None = use env var)

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
            'ip_address': listen_host or self.get_ip_address(),
            'hostname': socket.gethostname(),
            'last_error': None,
            'uptime_start': datetime.now(),
            'reconnecting': False,
            'reconnect_attempt': 0,
        }
        
        self.process = None
        self.running = False
        self.bluetooth_sink_name = None  # Store Bluetooth sink name for volume sync
        self.volume_restore_done = False  # Flag to prevent saving initial volume
        self._monitor_task: Optional[asyncio.Task] = None
    
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
                        
                await asyncio.sleep(10)
            except Exception as e:
                logger.error(f"Error updating status: {e}")
                await asyncio.sleep(10)
    
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
                    self.process.wait(timeout=3)
                except Exception:
                    try:
                        self.process.kill()
                    except Exception:
                        pass

            # Build command — use 'daemon' subcommand with unique port + settings-dir per instance
            safe_id = ''.join(c if c.isalnum() or c == '-' else '-' for c in self.player_name.lower()).strip('-')
            client_id = f"sendspin-{safe_id}"
            settings_dir = f"/tmp/sendspin-{safe_id}"
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
                '--settings-dir', settings_dir,
                '--static-delay-ms', str(static_delay_ms),
            ]

            # Add server URL only if explicitly configured
            if self.server_host and self.server_host.lower() not in ['auto', 'discover', '']:
                server_url = f"ws://{self.server_host}:{self.server_port}/sendspin"
                logger.info(f"Starting Sendspin player '{self.player_name}' connecting to {server_url} (port {self.listen_port})")
                cmd.extend(['--url', server_url])
            else:
                logger.info(f"Starting Sendspin player '{self.player_name}' with auto-discovery (port {self.listen_port})")
            
            # Set PULSE_SINK so this process routes audio to its specific BT sink
            env = os.environ.copy()
            if self.bt_manager:
                pa_mac = self.bt_manager.mac_address.replace(':', '_')
                pulse_sink = f"bluez_sink.{pa_mac}.a2dp_sink"
                env['PULSE_SINK'] = pulse_sink
                logger.info(f"Routing audio to sink: {pulse_sink}")

            # Override device info reported to Music Assistant
            env['SENDSPIN_BRIDGE_MANUFACTURER'] = 'Sendspin'
            env['SENDSPIN_BRIDGE_PRODUCT_NAME'] = 'Bluetooth Bridge'
            env['SENDSPIN_BRIDGE_VERSION'] = CLIENT_VERSION

            # Start the sendspin process
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
            )

            logger.info(f"Sendspin player started (PID: {self.process.pid})")
            logger.info(f"Sendspin command: {' '.join(cmd)}")
            self.status['server_connected'] = True
            self.status['connected'] = True
            self.status['playing'] = False  # reset; monitor_output() will set True on Stream STARTED
            
            # Monitor output in background — cancel any previous task first
            if self._monitor_task and not self._monitor_task.done():
                self._monitor_task.cancel()
            self._monitor_task = asyncio.create_task(self.monitor_output())
            self._monitor_task.add_done_callback(
                lambda t: logger.error(
                    f"[{self.player_name}] monitor_output ended with error: {t.exception()}"
                ) if not t.cancelled() and t.exception() else None
            )
            
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
        await self.start_sendspin_process()
        
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
            mon_task.add_done_callback(
                lambda t: logger.error(f"[{self.player_name}] monitor_and_reconnect task DIED: {t.exception()}")
                if not t.cancelled() and t.exception() else None
            )
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
                    self.process.wait(timeout=5)
                except Exception as e:
                    logger.error(f"Error terminating process: {e}")
                    try:
                        self.process.kill()
                    except Exception:
                        pass
    
    async def stop(self):
        """Stop the client"""
        self.running = False


async def main():
    """Main entry point"""

    config = load_config()
    server_host = config.get('SENDSPIN_SERVER', 'auto')
    server_port = int(config.get('SENDSPIN_PORT', 9000))

    # Set timezone
    tz = os.getenv('TZ', config.get('TZ', 'UTC'))
    os.environ['TZ'] = tz
    time.tzset()
    logger.info(f"Timezone: {tz}")

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

    base_listen_port = 8928
    clients = []
    for i, device in enumerate(bt_devices):
        mac = device.get('mac', '')
        adapter = device.get('adapter', '')
        player_name = device.get('player_name') or 'Sendspin Player'
        # 'listen_port' is the preferred key; 'port' kept for backward compat
        listen_port = int(device.get('listen_port') or device.get('port') or base_listen_port + i)
        listen_host = device.get('listen_host')
        static_delay_ms = device.get('static_delay_ms')
        if static_delay_ms is not None:
            static_delay_ms = float(static_delay_ms)

        client = SendspinClient(player_name, server_host, server_port, None,
                                listen_port=listen_port, static_delay_ms=static_delay_ms,
                                listen_host=listen_host)
        if mac:
            bt_mgr = BluetoothManager(mac, adapter=adapter, device_name=player_name, client=client)
            if not bt_mgr.check_bluetooth_available():
                logger.warning(f"BT adapter '{adapter or 'default'}' not available for {player_name}")
            client.bt_manager = bt_mgr
            client.status['bluetooth_available'] = bt_mgr.check_bluetooth_available()
        clients.append(client)
        logger.info(f"  Player: '{player_name}', BT: {mac or 'none'}, Adapter: {adapter or 'default'}")

    logger.info("Client instance(s) registered")

    # Start web interface in background thread
    import threading
    def run_web_server():
        from web_interface import set_clients, main as web_main
        set_clients(clients)
        web_main()

    web_thread = threading.Thread(target=run_web_server, daemon=True, name="WebServer")
    web_thread.start()
    logger.info("Web interface starting in background...")

    # Handle shutdown signals
    loop = asyncio.get_event_loop()

    def signal_handler():
        logger.info("Received shutdown signal")
        for c in clients:
            c.running = False
            if c.process:
                try:
                    c.process.terminate()
                except Exception:
                    pass

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)

    # Run all clients in parallel
    await asyncio.gather(*[c.run() for c in clients])



def load_config():
    """Load configuration from file"""
    from pathlib import Path
    import json
    
    config_dir = Path(os.getenv('CONFIG_DIR', '/config'))
    config_file = config_dir / 'config.json'
    
    default_config = {
        'SENDSPIN_SERVER': 'auto',
        'SENDSPIN_PORT': 9000,
        'BLUETOOTH_MAC': '',
        'BLUETOOTH_DEVICES': [],
        'TZ': 'Australia/Melbourne',
    }

    allowed_keys = {'SENDSPIN_SERVER', 'SENDSPIN_PORT', 'BLUETOOTH_MAC',
                    'BLUETOOTH_DEVICES', 'TZ', 'LAST_VOLUME'}

    if config_file.exists():
        try:
            with open(config_file) as f:
                saved_config = json.load(f)
                # Update with saved config
                for key, value in saved_config.items():
                    if key in allowed_keys:
                        default_config[key] = value
                logger.info(f"Loaded config from {config_file}")
        except Exception as e:
            logger.warning(f"Error loading config: {e}, using defaults")
    else:
        logger.info(f"Config file not found at {config_file}, using defaults")
    
    return default_config


if __name__ == '__main__':
    asyncio.run(main())
