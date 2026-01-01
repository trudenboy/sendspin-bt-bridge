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

# Global client instance holder (class-based singleton)
class ClientHolder:
    """Thread-safe holder for the client instance"""
    _instance = None
    
    @classmethod
    def set_client(cls, client):
        cls._instance = client
        
    @classmethod
    def get_client(cls):
        return cls._instance


class BluetoothManager:
    """Manages Bluetooth speaker connections using bluetoothctl"""
    
    def __init__(self, mac_address: str, client=None):
        self.mac_address = mac_address
        self.client = client
        self.connected = False
        self.last_check = 0
        self.check_interval = 10  # Check every 10 seconds
        
    def _run_bluetoothctl(self, commands: list) -> tuple[bool, str]:
        """Run bluetoothctl commands"""
        try:
            # Create a command string with all commands
            cmd_string = '\n'.join(commands)
            # Use bash -c with echo pipe for more reliable output
            bash_cmd = f"echo '{cmd_string}' | bluetoothctl"
            result = subprocess.run(
                ['bash', '-c', bash_cmd],
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
            result = subprocess.run(
                ['bluetoothctl', 'show'],
                capture_output=True,
                text=True,
                timeout=5
            )
            # Check for controller more flexibly
            if result.returncode == 0:
                output_lower = result.stdout.lower()
                return 'controller' in output_lower and 'no default controller' not in output_lower
            return False
        except Exception as e:
            logger.error(f"Bluetooth not available: {e}")
            return False
    
    def is_device_paired(self) -> bool:
        """Check if device is paired"""
        success, output = self._run_bluetoothctl(['info', self.mac_address])
        return success and 'Paired: yes' in output
    
    def is_device_connected(self) -> bool:
        """Check if device is currently connected"""
        success, output = self._run_bluetoothctl(['info', self.mac_address])
        self.connected = success and 'Connected: yes' in output
        return self.connected
    
    def pair_device(self) -> bool:
        """Pair with the Bluetooth device"""
        logger.info(f"Pairing with {self.mac_address}...")
        success, output = self._run_bluetoothctl([
            'power on',
            'agent on',
            'default-agent',
            f'pair {self.mac_address}'
        ])
        if success:
            logger.info("Pairing successful")
        return success
    
    def trust_device(self) -> bool:
        """Trust the Bluetooth device"""
        success, _ = self._run_bluetoothctl([f'trust {self.mac_address}'])
        return success
    

    def configure_bluetooth_audio(self) -> bool:
        """Configure host's PipeWire/PulseAudio to use the Bluetooth device as audio output"""
        try:
            import time
            
            # Wait for PipeWire/PulseAudio to see the device
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
            
            # Try to find and set the Bluetooth sink as default
            # PipeWire typically names them: bluez_output.XX_XX_XX_XX_XX_XX.1
            sink_names = [
                f"bluez_output.{pa_mac}.1",  # PipeWire format
                f"bluez_output.{pa_mac}.a2dp-sink",
                f"bluez_sink.{pa_mac}.a2dp_sink",  # Legacy format
                f"bluez_sink.{pa_mac}",
            ]
            
            success = False
            configured_sink = None
            for sink_name in sink_names:
                result = subprocess.run(
                    ['pactl', 'set-default-sink', sink_name],
                    capture_output=True,
                    text=True,
                    timeout=5
                )
                if result.returncode == 0:
                    logger.info(f"✓ Set default audio sink to: {sink_name}")
                    configured_sink = sink_name
                    success = True
                    break
                else:
                    logger.debug(f"Sink {sink_name} not found, trying next...")
            
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
                    
                    # Restore last volume if saved
                    restored = False
                    try:
                        config_path = '/config/config.json'
                        if os.path.exists(config_path):
                            with open(config_path, 'r') as f:
                                config = json.load(f)
                            last_volume = config.get('LAST_VOLUME')
                            if last_volume and isinstance(last_volume, int) and 0 <= last_volume <= 100:
                                result = subprocess.run(
                                    ['pactl', 'set-sink-volume', configured_sink, f'{last_volume}%'],
                                    capture_output=True,
                                    text=True,
                                    timeout=2
                                )
                                if result.returncode == 0:
                                    logger.info(f"✓ Restored volume to {last_volume}%")
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
        
        # Ensure paired and trusted
        if not self.is_device_paired():
            logger.info("Device not paired, attempting to pair...")
            if not self.pair_device():
                return False
            self.trust_device()
        
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
        while True:
            try:
                current_time = time.time()
                if current_time - self.last_check >= self.check_interval:
                    self.last_check = current_time
                    
                    if not self.is_device_connected():
                        logger.warning("Bluetooth speaker disconnected, attempting reconnect...")
                        self.connect_device()
                    
                await asyncio.sleep(5)
            except Exception as e:
                logger.error(f"Error in Bluetooth monitor: {e}")
                await asyncio.sleep(10)


class SendspinClient:
    """Wrapper for sendspin CLI with status tracking"""
    
    def __init__(self, player_name: str, server_host: str, server_port: int, 
                 bt_manager: Optional[BluetoothManager] = None):
        self.player_name = player_name
        self.server_host = server_host
        self.server_port = server_port
        self.bt_manager = bt_manager
        
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
            'ip_address': self.get_ip_address(),
            'hostname': socket.gethostname(),
            'last_error': None,
            'uptime_start': datetime.now()
        }
        
        self.process = None
        self.running = False
        self.bluetooth_sink_name = None  # Store Bluetooth sink name for volume sync
        self.volume_restore_done = False  # Flag to prevent saving initial volume
    
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
                s.connect(("8.8.8.8", 80))
                ip = s.getsockname()[0]
                s.close()
                return ip
            except Exception:
                return "unknown"
    
    async def update_status(self):
        """Update client status"""
        while self.running:
            try:
                if self.bt_manager:
                    bt_connected = self.bt_manager.is_device_connected()
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
                        logger.warning("Sendspin process died, restarting...")
                        await self.start_sendspin_process()
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
            # Build command
            cmd = [
                'sendspin',
                '--headless',
                '--name', self.player_name,
            ]
            
            # Add server URL only if explicitly configured
            if self.server_host and self.server_host.lower() not in ['auto', 'discover', '']:
                server_url = f"ws://{self.server_host}:{self.server_port}/sendspin"
                logger.info(f"Starting Sendspin player connecting to {server_url}")
                cmd.extend(['--url', server_url])
            else:
                logger.info("Starting Sendspin player with auto-discovery (mDNS)")
            
            # Start the sendspin process
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            
            logger.info(f"Sendspin player started (PID: {self.process.pid})")
            logger.info(f"Sendspin command: {' '.join(cmd)}")
            self.status['server_connected'] = True
            self.status['connected'] = True
            
            # Monitor output in background
            asyncio.create_task(self.monitor_output())
            
        except Exception as e:
            logger.error(f"Failed to start Sendspin player: {e}")
            self.status['last_error'] = str(e)
            self.status['server_connected'] = False
    
    async def monitor_output(self):
        """Monitor sendspin process output and sync volume changes"""
        if not self.process:
            return
        
        try:
            while self.running and self.process.poll() is None:
                line = self.process.stdout.readline()
                if line:
                    line_str = line.strip()
                    logger.info(f"Sendspin: {line_str}")
                    
                    # Update playing state from output
                    if line_str.startswith("State:") or line_str.startswith("Playback state:"):
                        state = line_str.split(":")[-1].strip().lower()
                        self.status['playing'] = (state == 'playing')
                    
                    # Track current track and artist
                    if line_str.startswith("Now playing:"):
                        track_name = line_str.split("Now playing:")[-1].strip()
                        # Store just the track name, artist will be combined later
                        self.status['_track_name'] = track_name
                    
                    if line_str.startswith("Artist:"):
                        artist_name = line_str.split("Artist:")[-1].strip()
                        self.status['current_artist'] = artist_name
                        # Combine artist and track if we have both
                        if hasattr(self.status, '__getitem__') and self.status.get('_track_name'):
                            self.status['current_track'] = f"{artist_name} - {self.status['_track_name']}"
                    
                    # Track server connection
                    if "Connected to" in line_str and "ws://" in line_str:
                        if not self.status['server_connected_at']:
                            self.status['server_connected_at'] = datetime.now().isoformat()
                        self.status['server_connected'] = True
                    
                    # Sync volume changes to Bluetooth speaker
                    if line_str.startswith("Volume:") and self.bluetooth_sink_name:
                        try:
                            # Extract volume percentage (e.g., "Volume: 75%" -> "75")
                            volume_part = line_str.split("Volume:")[-1].strip().rstrip('%')
                            if volume_part and volume_part.isdigit():
                                volume_percent = int(volume_part)
                                self.status['volume'] = volume_percent
                                
                                # Save volume to config for persistence (only after initial restore)
                                if self.volume_restore_done:
                                    try:
                                        config_path = '/config/config.json'
                                        if os.path.exists(config_path):
                                            with open(config_path, 'r') as f:
                                                config = json.load(f)
                                            config['LAST_VOLUME'] = volume_percent
                                            with open(config_path, 'w') as f:
                                                json.dump(config, f, indent=2)
                                    except Exception as e:
                                        logger.debug(f"Could not save volume to config: {e}")
                                
                                # Update Bluetooth speaker volume to match
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
                await asyncio.sleep(0.1)
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
            tasks.append(asyncio.create_task(self.bt_manager.monitor_and_reconnect()))
        
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


# Global client instance for web UI access
_client_instance: Optional[SendspinClient] = None


def get_client_instance() -> Optional[SendspinClient]:
    """Get the global client instance"""
    return _client_instance


async def main():
    """Main entry point"""
    global _client_instance
    
    # Load configuration from file (web UI editable)
    config = load_config()
    player_name = config.get('SENDSPIN_NAME', f'Docker-{socket.gethostname()}')
    server_host = config.get('SENDSPIN_SERVER', 'auto')
    bt_mac = config.get('BLUETOOTH_MAC', '')
    
    # Set timezone
    tz = os.getenv('TZ', 'Australia/Melbourne')
    os.environ['TZ'] = tz
    time.tzset()
    
    logger.info(f"Starting Sendspin Client: {player_name}")
    if server_host and server_host.lower() not in ['auto', 'discover', '']:
        logger.info(f"Server: {server_host}:{server_port}")
    else:
        logger.info("Server: Auto-discovery enabled (mDNS)")
    logger.info(f"Timezone: {tz}")
    
    # Log Bluetooth MAC if provided (manager created after client)
    if bt_mac:
        logger.info(f"Bluetooth MAC: {bt_mac}")
    
    # Create client first (without BT manager)
    client = SendspinClient(player_name, server_host, 9000, None)
    
    # Now create Bluetooth manager with client reference and assign to client
    if bt_mac:
        bt_manager = BluetoothManager(bt_mac, client)
        if not bt_manager.check_bluetooth_available():
            logger.warning("Bluetooth not available on this system!")
        # Assign the manager to the client using the correct attribute name
        client.bt_manager = bt_manager
        # Update status
        client.status['bluetooth_available'] = bt_manager.check_bluetooth_available()
    
    # Set the client using our class holder
    ClientHolder.set_client(client)
    logger.info("Client instance registered")
    
    # Start web interface in background thread AFTER client is created
    import threading
    def run_web_server():
        from web_interface import set_client, main as web_main
        set_client(client)  # Pass the client reference to web interface
        web_main()
    
    web_thread = threading.Thread(target=run_web_server, daemon=True, name="WebServer")
    web_thread.start()
    logger.info("Web interface starting in background...")
    
    # Handle shutdown signals
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("Received shutdown signal")
        asyncio.create_task(client.stop())
    
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, signal_handler)
    
    # Run client
    await client.run()



def load_config():
    """Load configuration from file"""
    from pathlib import Path
    import json
    
    config_dir = Path(os.getenv('CONFIG_DIR', '/config'))
    config_file = config_dir / 'config.json'
    
    default_config = {
        'SENDSPIN_NAME': f'Sendspin-{socket.gethostname()}',
        'SENDSPIN_SERVER': 'auto',
        'BLUETOOTH_MAC': '',
        'TZ': 'Australia/Melbourne',
    }
    
    if config_file.exists():
        try:
            with open(config_file) as f:
                saved_config = json.load(f)
                # Update with saved config
                for key, value in saved_config.items():
                    if key in default_config or key in ['SENDSPIN_NAME', 'SENDSPIN_SERVER', 'BLUETOOTH_MAC', 'TZ']:
                        default_config[key] = value
                logger.info(f"Loaded config from {config_file}")
        except Exception as e:
            logger.warning(f"Error loading config: {e}, using defaults")
    else:
        logger.info(f"Config file not found at {config_file}, using defaults")
    
    return default_config


if __name__ == '__main__':
    asyncio.run(main())
