"""
BluetoothManager — manages Bluetooth speaker connections for sendspin-bt-bridge.

Handles pairing, connecting, disconnecting, audio sink configuration, and
automatic reconnection via bluetoothctl subprocess with stdin pipe.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import time
import threading
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from config import _CONFIG_PATH, _config_lock, _save_device_volume

if TYPE_CHECKING:
    from sendspin_client import SendspinClient

logger = logging.getLogger(__name__)

def _force_sbc_codec(pa_mac: str) -> None:
    """Attempt to force SBC codec on the BlueZ card for this device.

    SBC is the simplest mandatory A2DP codec — least CPU for the PA encoder.
    Silently ignores failures (older PA, device already on SBC, or PA 14 that
    lacks the send-message routing).
    """
    card_prefix = f"bluez_card.{pa_mac}"
    try:
        cards = subprocess.check_output(
            ['pactl', 'list', 'short', 'cards'], text=True, timeout=5
        )
        for line in cards.splitlines():
            if card_prefix in line:
                card_name = line.split()[1]
                result = subprocess.run(
                    ['pactl', 'send-message',
                     f'/card/{card_name}/bluez5/set_codec',
                     'a2dp_sink', 'SBC'],
                    timeout=5, check=False, capture_output=True, text=True
                )
                if result.returncode == 0:
                    logger.info(f"✓ Forced SBC codec on {card_name}")
                else:
                    logger.debug(f"SBC force failed for {card_name}: {result.stderr.strip()}")
                return
    except Exception as e:
        logger.debug(f"SBC codec force skipped: {e}")


class BluetoothManager:
    """Manages Bluetooth speaker connections using bluetoothctl"""

    def __init__(self, mac_address: str, adapter: str = "", device_name: str = "", client=None,
                 prefer_sbc: bool = False, check_interval: int = 10, max_reconnect_fails: int = 0):
        self.mac_address = mac_address
        self.adapter = adapter        # "hci0", "hci1", etc. — empty = use default
        self.device_name = device_name or mac_address
        self.client = client
        self.prefer_sbc = prefer_sbc
        self.connected = False
        self.last_check = 0
        self.check_interval = check_interval
        self.max_reconnect_fails = max_reconnect_fails

        # Resolve adapter name to MAC for reliable 'select' in bridged D-Bus setups.
        # In LXC containers, 'select hci0' fails ("Controller hci0 not available");
        # selecting by MAC address works because D-Bus objects use MACs, not hciN names.
        self._adapter_select = self._resolve_adapter_select(adapter) if adapter else ''
        self.management_enabled: bool = True  # False = released; monitor loop skips reconnect

        # Resolve effective adapter MAC for display (handles empty/default adapter case)
        if self._adapter_select:
            self.effective_adapter_mac = self._adapter_select
        else:
            self.effective_adapter_mac = self._detect_default_adapter_mac()

        self.adapter_hci_name = self._resolve_adapter_hci_name()

    def _detect_default_adapter_mac(self) -> str:
        """Return the MAC of the default Bluetooth controller, or empty string."""
        try:
            out = subprocess.check_output(
                ['bluetoothctl', 'show'], stderr=subprocess.DEVNULL, timeout=5, text=True
            )
            m = re.search(r'Controller\s+([0-9A-Fa-f:]{17})', out)
            return m.group(1) if m else ''
        except Exception:
            return ''

    def _resolve_adapter_hci_name(self) -> str:
        """Return hciN name for the effective adapter MAC (e.g. 'hci0'), or empty string."""
        if self.adapter.startswith('hci'):
            return self.adapter  # Already have it from config
        effective = (self.effective_adapter_mac or '').upper()
        if not effective:
            return ''
        try:
            result = subprocess.run(
                ['bluetoothctl', 'list'], capture_output=True, text=True, timeout=5
            )
            idx = 0
            for line in result.stdout.splitlines():
                if 'Controller' not in line:
                    continue
                for part in line.split():
                    if len(part) == 17 and part.count(':') == 5:
                        if part.upper() == effective:
                            return f'hci{idx}'
                        idx += 1
                        break
        except Exception:
            pass
        return ''

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
        Uses stdin pipe directly — no shell, no injection risk.
        """
        import re
        mac = self.mac_address
        if not re.fullmatch(r'([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}', mac):
            logger.error(f"Invalid MAC address format: {mac}")
            return False

        logger.info(f"Pairing with {mac}...")

        initial_cmds = []
        if self._adapter_select:
            initial_cmds.append(f'select {self._adapter_select}')
        initial_cmds.extend(['power on', 'agent on', 'default-agent', 'scan on'])

        pair_cmds = [f'pair {mac}', f'trust {mac}', 'scan off']

        proc = None
        try:
            proc = subprocess.Popen(
                ['bluetoothctl'],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            # Send initial setup and start scanning
            proc.stdin.write('\n'.join(initial_cmds) + '\n')
            proc.stdin.flush()
            # Wait for scan to discover device
            time.sleep(12)
            # Send pair/trust commands
            proc.stdin.write('\n'.join(pair_cmds) + '\n')
            proc.stdin.flush()
            # Wait for pairing to complete
            time.sleep(10)

            out, _ = proc.communicate(timeout=5)
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
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
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
                # Try to force SBC codec (lowest CPU A2DP codec) if requested
                if self.prefer_sbc:
                    _force_sbc_codec(pa_mac)

                # Store the sink name in client for volume sync
                if self.client:
                    self.client.bluetooth_sink_name = configured_sink
                    logger.info(f"Stored Bluetooth sink for volume sync: {configured_sink}")
                    
                    # Restore last volume for this device (keyed by MAC)
                    restored = False
                    try:
                        config_path = _CONFIG_PATH
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
                if not self.management_enabled:
                    await asyncio.sleep(5)
                    continue

                current_time = time.time()
                if current_time - self.last_check >= self.check_interval:
                    self.last_check = current_time
                    logger.info(f"[{self.device_name}] BT check #{iteration}")

                    # Run blocking BT check in thread pool — never block the event loop
                    connected = await loop.run_in_executor(None, self.is_device_connected)
                    logger.info(f"[{self.device_name}] BT connected={connected}")

                    # Keep client status in sync so update_status() can read the cached flag
                    if self.client:
                        if connected != self.client.status.get('bluetooth_connected'):
                            self.client.status['bluetooth_connected'] = connected
                            self.client.status['bluetooth_connected_at'] = datetime.now().isoformat()

                    if not connected:
                        reconnect_attempt += 1
                        if self.client:
                            self.client.status['reconnecting'] = True
                            self.client.status['reconnect_attempt'] = reconnect_attempt

                        # Auto-disable BT management after too many consecutive failures
                        if self.max_reconnect_fails > 0 and reconnect_attempt >= self.max_reconnect_fails:
                            logger.warning(
                                f"[{self.device_name}] {reconnect_attempt} consecutive failed reconnects "
                                f"(threshold={self.max_reconnect_fails}) — auto-disabling BT management"
                            )
                            self.management_enabled = False
                            if self.client:
                                self.client.bt_management_enabled = False
                                self.client.status['bt_management_enabled'] = False
                                self.client.status['reconnecting'] = False
                            try:
                                from services.bluetooth import persist_device_enabled
                                persist_device_enabled(self.device_name, False)
                            except Exception as _e:
                                logger.debug(f"persist_device_enabled failed: {_e}")
                            reconnect_attempt = 0
                            continue

                        # Kill sendspin daemon immediately — if the BT sink is gone,
                        # sendspin floods PortAudioErrors on every audio chunk, which
                        # starves its own event loop and causes WebSocket PONG timeouts.
                        if self.client and self.client.is_running():
                            logger.info(f"BT disconnected for {self.device_name}, stopping sendspin daemon...")
                            await self.client.stop_sendspin()

                        logger.warning(f"Bluetooth device {self.device_name} disconnected, attempting reconnect... (attempt {reconnect_attempt})")
                        success = await loop.run_in_executor(None, self.connect_device)
                        if success and self.client:
                            reconnect_attempt = 0
                            self.client.status['reconnecting'] = False
                            self.client.status['reconnect_attempt'] = 0
                            # BT reconnected — start fresh sendspin to register with MA
                            logger.info(f"BT reconnected for {self.device_name}, starting sendspin...")
                            await self.client.start_sendspin()
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


