"""
BluetoothManager — manages Bluetooth speaker connections for sendspin-bt-bridge.

Handles pairing, connecting, disconnecting, audio sink configuration, and
automatic reconnection. Uses D-Bus (dbus-fast) for instant disconnect detection
via PropertiesChanged signals; falls back to bluetoothctl polling if unavailable.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import threading
import time
from datetime import datetime

from config import CONFIG_FILE as _CONFIG_FILE
from config import config_lock as config_lock
from services.pulse import get_sink_volume, list_sinks, set_sink_volume

logger = logging.getLogger(__name__)


def _force_sbc_codec(pa_mac: str) -> None:
    """Attempt to force SBC codec on the BlueZ card for this device.

    SBC is the simplest mandatory A2DP codec — least CPU for the PA encoder.
    Silently ignores failures (older PA, device already on SBC, or PA 14 that
    lacks the send-message routing).
    """
    card_prefix = f"bluez_card.{pa_mac}"
    try:
        cards = subprocess.check_output(["pactl", "list", "short", "cards"], text=True, timeout=5)
        for line in cards.splitlines():
            if card_prefix in line:
                card_name = line.split()[1]
                result = subprocess.run(
                    [
                        "pactl",
                        "send-message",
                        f"/card/{card_name}/bluez5/set_codec",
                        "a2dp_sink",
                        "SBC",
                    ],
                    timeout=5,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    logger.info("✓ Forced SBC codec on %s", card_name)
                else:
                    logger.debug("SBC force failed for %s: %s", card_name, result.stderr.strip())
                return
    except Exception as e:
        logger.debug("SBC codec force skipped: %s", e)


def _dbus_get_device_property(device_path: str, property_name: str, adapter_hci: str = "hci0"):
    """Read a single BlueZ Device1 property synchronously via dbus-python.

    Falls back to None on any error (D-Bus unavailable, device not registered, etc.).
    This is ~10× faster than spawning a bluetoothctl subprocess.
    """
    try:
        import dbus as _dbus

        bus = _dbus.SystemBus()
        device = bus.get_object("org.bluez", device_path)
        props = _dbus.Interface(device, "org.freedesktop.DBus.Properties")
        return props.Get("org.bluez.Device1", property_name)
    except Exception:
        return None


def _dbus_call_device_method(device_path: str, method_name: str) -> bool:
    """Call a BlueZ Device1 method synchronously via dbus-python.

    Returns True on success, False on error.
    """
    try:
        import dbus as _dbus

        bus = _dbus.SystemBus()
        device = bus.get_object("org.bluez", device_path)
        iface = _dbus.Interface(device, "org.bluez.Device1")
        getattr(iface, method_name)()
        return True
    except Exception as e:
        logger.debug("D-Bus %s failed: %s", method_name, e)
        return False


class BluetoothManager:
    """Manages Bluetooth speaker connections using bluetoothctl and D-Bus"""

    def __init__(
        self,
        mac_address: str,
        adapter: str = "",
        device_name: str = "",
        client=None,
        prefer_sbc: bool = False,
        check_interval: int = 10,
        max_reconnect_fails: int = 0,
        on_sink_found=None,
    ):
        self.mac_address = mac_address
        self.adapter = adapter  # "hci0", "hci1", etc. — empty = use default
        self.device_name = device_name or mac_address
        self.client = client
        self.on_sink_found = on_sink_found  # Callable[[str, int | None], None] | None
        self.prefer_sbc = prefer_sbc
        self.connected = False
        self.last_check = 0
        self.check_interval = check_interval
        self.max_reconnect_fails = max_reconnect_fails

        # Resolve adapter name to MAC for reliable 'select' in bridged D-Bus setups.
        # In LXC containers, 'select hci0' fails ("Controller hci0 not available");
        # selecting by MAC address works because D-Bus objects use MACs, not hciN names.
        self._adapter_select = self._resolve_adapter_select(adapter) if adapter else ""
        self.management_enabled: bool = True  # False = released; monitor loop skips reconnect
        self._connect_lock = threading.Lock()  # prevents concurrent connect_device() calls

        # Resolve effective adapter MAC for display (handles empty/default adapter case)
        if self._adapter_select:
            self.effective_adapter_mac = self._adapter_select
        else:
            self.effective_adapter_mac = self._detect_default_adapter_mac()

        self.adapter_hci_name = self._resolve_adapter_hci_name()
        # D-Bus device path: /org/bluez/<adapter>/dev_XX_XX_XX_XX_XX_XX
        _mac_dbus = self.mac_address.upper().replace(":", "_")
        _hci = self.adapter_hci_name or "hci0"
        self._dbus_device_path: str = f"/org/bluez/{_hci}/dev_{_mac_dbus}"

    def _detect_default_adapter_mac(self) -> str:
        """Return the MAC of the default Bluetooth controller, or empty string."""
        try:
            out = subprocess.check_output(
                ["bluetoothctl", "show"],
                stderr=subprocess.DEVNULL,
                timeout=5,
                text=True,
            )
            m = re.search(r"Controller\s+([0-9A-Fa-f:]{17})", out)
            return m.group(1) if m else ""
        except Exception:
            return ""

    def _resolve_adapter_hci_name(self) -> str:
        """Return hciN name for the effective adapter MAC (e.g. 'hci0'), or empty string."""
        if self.adapter.startswith("hci"):
            return self.adapter  # Already have it from config
        effective = (self.effective_adapter_mac or "").upper()
        if not effective:
            return ""
        # Prefer sysfs lookup — it maps MACs to hciN names without relying on
        # the ordering of 'bluetoothctl list' output which may not match hciN indices.
        import pathlib

        mac_norm = effective.replace(":", "").lower()
        bt_sysfs = pathlib.Path("/sys/class/bluetooth")
        try:
            for hci in sorted(bt_sysfs.iterdir()):
                addr_file = hci / "address"
                if addr_file.exists():
                    addr = addr_file.read_text().strip().replace(":", "").lower()
                    if addr == mac_norm:
                        return hci.name
        except Exception:
            pass
        # Fallback: count adapter positions in bluetoothctl output (fragile, but last resort)
        try:
            result = subprocess.run(["bluetoothctl", "list"], capture_output=True, text=True, timeout=5)
            idx = 0
            for line in result.stdout.splitlines():
                if "Controller" not in line:
                    continue
                for part in line.split():
                    if len(part) == 17 and part.count(":") == 5:
                        if part.upper() == effective:
                            return f"hci{idx}"
                        idx += 1
                        break
        except Exception:
            pass
        return ""

    def _resolve_adapter_select(self, adapter: str) -> str:
        """Resolve hciN to adapter MAC address for bluetoothctl 'select'.
        Falls back to the original name if resolution fails."""
        if not adapter or not adapter.startswith("hci"):
            return adapter  # Already a MAC or empty string
        try:
            idx = int(adapter[3:])  # N from hciN
        except ValueError:
            return adapter
        try:
            result = subprocess.run(
                ["bluetoothctl", "list"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # Parse "Controller <MAC> description [default]" lines
            macs = []
            for line in result.stdout.splitlines():
                if "Controller" in line:
                    for part in line.split():
                        if len(part) == 17 and part.count(":") == 5:
                            macs.append(part.upper())
                            break
            if idx < len(macs):
                logger.info("Resolved adapter %s → %s", adapter, macs[idx])
                return macs[idx]
        except Exception as e:
            logger.debug("Adapter MAC resolution failed: %s", e)
        return adapter  # Fall back to hciN name

    def _run_bluetoothctl(self, commands: list) -> tuple[bool, str]:
        """Run bluetoothctl commands, prepending 'select <adapter_mac>' if configured.
        Uses stdin pipe directly — no shell, no injection risk."""
        try:
            all_commands = []
            if self._adapter_select:
                all_commands.append(f"select {self._adapter_select}")
            all_commands.extend(commands)
            cmd_string = "\n".join(all_commands) + "\n"
            result = subprocess.run(
                ["bluetoothctl"],
                input=cmd_string,
                capture_output=True,
                text=True,
                timeout=10,
            )
            return result.returncode == 0, result.stdout
        except Exception as e:
            logger.error("Bluetoothctl error: %s", e)
            return False, str(e)

    def check_bluetooth_available(self) -> bool:
        """Check if Bluetooth is available on the system"""
        try:
            if self.adapter:
                # Check specific adapter via _run_bluetoothctl (includes select)
                success, output = self._run_bluetoothctl(["show"])
                return success and "Controller" in output
            # Default: check for any controller
            result = subprocess.run(["bluetoothctl", "show"], capture_output=True, text=True, timeout=5)
            if result.returncode == 0:
                output_lower = result.stdout.lower()
                return "controller" in output_lower and "no default controller" not in output_lower
            return False
        except Exception as e:
            logger.error("Bluetooth not available: %s", e)
            return False

    def is_device_paired(self) -> bool:
        """Check if device is paired via D-Bus; falls back to bluetoothctl."""
        val = _dbus_get_device_property(self._dbus_device_path, "Paired")
        if val is not None:
            return bool(val)
        success, output = self._run_bluetoothctl([f"info {self.mac_address}"])
        return success and "Paired: yes" in output

    def is_device_connected(self) -> bool:
        """Check if device is currently connected via D-Bus; falls back to bluetoothctl."""
        try:
            val = _dbus_get_device_property(self._dbus_device_path, "Connected")
            if val is not None:
                is_connected = bool(val)
            else:
                # D-Bus unavailable — fall back to bluetoothctl
                success, output = self._run_bluetoothctl([f"info {self.mac_address}"])
                is_connected = success and "Connected: yes" in output

            if is_connected != self.connected:
                if is_connected:
                    logger.info("✓ BT device %s (%s) connected", self.device_name, self.mac_address)
                else:
                    logger.warning("✗ BT device %s (%s) disconnected", self.device_name, self.mac_address)

            self.connected = is_connected
            return self.connected
        except Exception as e:
            logger.debug("Error checking Bluetooth connection: %s", e)
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
        if not re.fullmatch(r"([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}", mac):
            logger.error("Invalid MAC address format: %s", mac)
            return False

        logger.info("Pairing with %s...", mac)

        initial_cmds = []
        if self._adapter_select:
            initial_cmds.append(f"select {self._adapter_select}")
        initial_cmds.extend(["power on", "agent on", "default-agent", "scan on"])

        pair_cmds = [f"pair {mac}", f"trust {mac}", "scan off"]

        proc = None
        try:
            proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            # Send initial setup and start scanning
            if proc.stdin is None:
                raise RuntimeError("bluetoothctl subprocess stdin unavailable")
            proc.stdin.write("\n".join(initial_cmds) + "\n")
            proc.stdin.flush()
            # Wait for scan to discover device
            time.sleep(12)
            # Send pair/trust commands
            proc.stdin.write("\n".join(pair_cmds) + "\n")
            proc.stdin.flush()
            # Wait for pairing to complete
            time.sleep(10)

            out, _ = proc.communicate(timeout=5)
            logger.info("Pair output (last 600 chars): %s", out[-600:])
            ok = "Pairing successful" in out or "Already paired" in out or "Paired: yes" in out
            if ok:
                logger.info("Pairing successful")
            else:
                logger.warning("Pairing may have failed. Output: %s", out[-200:])
            return ok
        except Exception as e:
            logger.error("Pair error: %s", e)
            if proc is not None:
                try:
                    proc.kill()
                except Exception:
                    pass
            return False

    def trust_device(self) -> bool:
        """Trust the Bluetooth device"""
        success, _ = self._run_bluetoothctl([f"trust {self.mac_address}"])
        return success

    def configure_bluetooth_audio(self) -> bool:
        """Configure host's PipeWire/PulseAudio to use the Bluetooth device as audio output"""
        try:
            # Wait for PipeWire/PulseAudio to register the device.
            # A2DP profile takes a few seconds to appear after BT connects.
            time.sleep(3)

            pa_mac = self.mac_address.replace(":", "_")

            # Log available sinks for diagnostics
            sinks = list_sinks()
            logger.info("Available audio sinks: %s", [s["name"] for s in sinks])

            # Find the Bluetooth sink (do NOT change system default — PULSE_SINK handles per-process routing)
            sink_names = [
                f"bluez_output.{pa_mac}.1",  # PipeWire format
                f"bluez_output.{pa_mac}.a2dp-sink",
                f"bluez_sink.{pa_mac}.a2dp_sink",  # Legacy PulseAudio format
                f"bluez_sink.{pa_mac}",
            ]
            known_names = {s["name"] for s in sinks}

            success = False
            configured_sink = None
            # Retry up to 3 times — A2DP sink may take a few extra seconds to appear
            for attempt in range(3):
                for sink_name in sink_names:
                    if sink_name in known_names or get_sink_volume(sink_name) is not None:
                        logger.info("✓ Found audio sink: %s", sink_name)
                        configured_sink = sink_name
                        success = True
                        break
                    else:
                        logger.debug("Sink %s not found, trying next...", sink_name)
                if success:
                    break
                if attempt < 2:
                    logger.info("Sink not yet available, retrying in 3s... (attempt %s/3)", attempt + 1)
                    time.sleep(3)
                    sinks = list_sinks()
                    known_names = {s["name"] for s in sinks}

            if success and configured_sink:
                # Try to force SBC codec (lowest CPU A2DP codec) if requested
                if self.prefer_sbc:
                    _force_sbc_codec(pa_mac)

                # Resolve last saved volume for this device
                restored_volume = None
                try:
                    if _CONFIG_FILE.exists():
                        with config_lock, open(_CONFIG_FILE) as f:
                            cfg = json.load(f)
                        volumes = cfg.get("LAST_VOLUMES", {})
                        last_volume = volumes.get(self.mac_address) or cfg.get("LAST_VOLUME")
                        if last_volume is not None and isinstance(last_volume, int) and 0 <= last_volume <= 100:
                            if set_sink_volume(configured_sink, last_volume):
                                logger.info("✓ Restored volume to %s%% for %s", last_volume, self.mac_address)
                                restored_volume = last_volume
                except Exception as e:
                    logger.debug("Could not restore volume: %s", e)

                if not restored_volume:
                    logger.info("No saved volume to restore, will use current volume")

                # Notify caller via callback (decoupled) or fall back to direct client mutation
                if self.on_sink_found:
                    self.on_sink_found(configured_sink, restored_volume)
                elif self.client:
                    self.client.bluetooth_sink_name = configured_sink
                    logger.info("Stored Bluetooth sink for volume sync: %s", configured_sink)
                    if restored_volume is not None:
                        self.client._update_status({"volume": restored_volume})
                    if hasattr(self.client, "volume_restore_done"):
                        self.client.volume_restore_done = True
            elif not success:
                logger.warning("Could not find Bluetooth sink for %s", self.mac_address)
                logger.warning("Audio may play from default device instead of Bluetooth")

            return success

        except Exception as e:
            logger.error("Error configuring Bluetooth audio: %s", e)
            return False

    def connect_device(self) -> bool:
        """Connect to the Bluetooth device"""
        if not self._connect_lock.acquire(blocking=False):
            logger.debug("[%s] connect_device already in progress, waiting...", self.device_name)
            with self._connect_lock:  # wait for ongoing call to finish
                pass
            return self.is_device_connected()
        try:
            return self._connect_device_inner()
        finally:
            self._connect_lock.release()

    def _connect_device_inner(self) -> bool:
        """Connect to the Bluetooth device (called with _connect_lock held)"""
        # First check if already connected
        if self.is_device_connected():
            logger.info("Device already connected")
            self.connected = True
            # Ensure audio is configured
            self.configure_bluetooth_audio()
            return True

        logger.info("Connecting to %s...", self.mac_address)

        # Ensure paired and trusted (pair_device also runs trust)
        if not self.is_device_paired():
            logger.info("Device not paired, attempting to pair...")
            if not self.pair_device():
                return False

        # Power on bluetooth
        self._run_bluetoothctl(["power on"])
        time.sleep(1)

        # Try to connect
        _success, _output = self._run_bluetoothctl([f"connect {self.mac_address}"])

        # Wait for connection to establish
        for _i in range(5):
            time.sleep(1)
            if self.is_device_connected():
                logger.info("Successfully connected to Bluetooth speaker")
                self.connected = True
                # Configure audio routing
                self.configure_bluetooth_audio()
                return True

        logger.warning("Failed to connect after 5 attempts")
        return False

    def disconnect_device(self) -> bool:
        """Disconnect from the Bluetooth device via D-Bus; falls back to bluetoothctl."""
        if _dbus_call_device_method(self._dbus_device_path, "Disconnect"):
            self.connected = False
            return True
        success, _ = self._run_bluetoothctl([f"disconnect {self.mac_address}"])
        if success:
            self.connected = False
        return success

    def _reconnect_delay(self, attempt: int) -> float:
        """Exponential backoff delay after a failed reconnect attempt.

        Attempts 1-3 use check_interval; doubles every attempt thereafter,
        capped at 5 minutes. Reduces BT radio activity (and audio disruption
        on other devices sharing the same adapter) as failure count grows.
        """
        return min(self.check_interval * (2 ** max(0, attempt - 3)), 300.0)

    def _handle_reconnect_failure(self, attempt: int) -> bool:
        """Disable BT management after too many consecutive failed reconnects.

        Returns True if management was disabled (caller should stop reconnecting).
        Side-effects: sets self.management_enabled=False, updates client status,
        calls persist_device_enabled().
        """
        if self.max_reconnect_fails <= 0 or attempt < self.max_reconnect_fails:
            return False
        logger.warning(
            "[%s] %d consecutive failed reconnects (threshold=%d) — auto-disabling BT management",
            self.device_name,
            attempt,
            self.max_reconnect_fails,
        )
        self.management_enabled = False
        if self.client:
            self.client.bt_management_enabled = False
            self.client._update_status({"bt_management_enabled": False, "reconnecting": False})
        try:
            from services.bluetooth import persist_device_enabled

            persist_device_enabled(self.device_name, False)
        except Exception as _e:
            logger.debug("persist_device_enabled failed: %s", _e)
        return True

    async def monitor_and_reconnect(self):
        """Continuously monitor BT connection and reconnect if needed.

        Tries D-Bus PropertiesChanged signals (dbus-fast) for instant disconnect
        detection; falls back to bluetoothctl polling if dbus-fast is unavailable
        or if the D-Bus environment doesn't support signal subscriptions.
        """
        logger.info("[%s] monitor_and_reconnect task started", self.device_name)
        try:
            from dbus_fast import BusType
            from dbus_fast.aio import MessageBus

            await self._monitor_dbus(MessageBus, BusType)
        except (ImportError, RuntimeError) as e:
            logger.info("[%s] D-Bus monitor unavailable (%s) — using bluetoothctl polling", self.device_name, e)
            await self._monitor_polling()

    async def _monitor_polling(self):
        """Legacy bluetoothctl polling-based monitor (fallback)."""
        loop = asyncio.get_running_loop()
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
                    logger.debug("[%s] BT poll #%s", self.device_name, iteration)

                    connected = await loop.run_in_executor(None, self.is_device_connected)
                    logger.debug("[%s] BT connected=%s", self.device_name, connected)

                    if self.client:
                        if connected != self.client.status.get("bluetooth_connected"):
                            self.client._update_status(
                                {
                                    "bluetooth_connected": connected,
                                    "bluetooth_connected_at": datetime.now().isoformat(),
                                }
                            )

                    if not connected:
                        reconnect_attempt += 1
                        if self.client:
                            self.client._update_status(
                                {
                                    "reconnecting": True,
                                    "reconnect_attempt": reconnect_attempt,
                                }
                            )

                        if self._handle_reconnect_failure(reconnect_attempt):
                            reconnect_attempt = 0
                            continue

                        if self.client and self.client.is_running():
                            logger.info("BT disconnected for %s, stopping sendspin daemon...", self.device_name)
                            with self.client._status_lock:
                                is_grouped = bool(self.client.status.get("group_id"))
                            if not is_grouped:
                                await self.client._send_subprocess_command({"cmd": "pause"})
                                await asyncio.sleep(0.2)
                            await self.client.stop_sendspin()

                        logger.warning(
                            "Bluetooth device %s disconnected, reconnecting... (attempt %s)",
                            self.device_name,
                            reconnect_attempt,
                        )
                        success = await loop.run_in_executor(None, self.connect_device)
                        if success and self.client:
                            reconnect_attempt = 0
                            self.client._update_status({"reconnecting": False, "reconnect_attempt": 0})
                            logger.info("BT reconnected for %s, starting sendspin...", self.device_name)
                            await self.client.start_sendspin()
                        else:
                            # Back off: delay next check proportional to failure count
                            delay = self._reconnect_delay(reconnect_attempt)
                            self.last_check = time.time() + delay - self.check_interval
                            logger.debug("[%s] Backoff: next attempt in %.0fs", self.device_name, delay)
                    else:
                        if self.client and self.client.status.get("reconnecting"):
                            self.client._update_status({"reconnecting": False, "reconnect_attempt": 0})
                        reconnect_attempt = 0

                await asyncio.sleep(5)
            except Exception as e:
                logger.error("Error in Bluetooth poll monitor: %s", e)
                await asyncio.sleep(10)

    async def _monitor_dbus(self, MessageBus, BusType):
        """D-Bus PropertiesChanged signal-based monitor (preferred path).

        Raises RuntimeError after 3 consecutive connection failures so
        monitor_and_reconnect() can fall back to bluetoothctl polling.
        """
        loop = asyncio.get_running_loop()
        reconnect_attempt = 0
        connect_failures = 0
        _MAX_CONNECT_FAILURES = 3
        logger.info("[%s] D-Bus monitor started (path=%s)", self.device_name, self._dbus_device_path)

        # Single bus connection reused across BT reconnect iterations.
        # Recreated only when it becomes unresponsive or on first start.
        bus = None

        while True:
            bus_needs_reconnect = bus is None or not bus.connected
            try:
                if bus_needs_reconnect:
                    if bus is not None:
                        try:
                            bus.disconnect()
                        except Exception:
                            pass
                    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

                # Introspect the device object (may fail if device not yet registered with BlueZ)
                try:
                    introspection = await bus.introspect("org.bluez", self._dbus_device_path)
                    proxy = bus.get_proxy_object("org.bluez", self._dbus_device_path, introspection)
                    device_iface = proxy.get_interface("org.bluez.Device1")
                    props_iface = proxy.get_interface("org.freedesktop.DBus.Properties")
                except Exception as e:
                    connect_failures += 1
                    logger.debug(
                        "[%s] D-Bus device not available (%s), attempt %s/%s",
                        self.device_name,
                        e,
                        connect_failures,
                        _MAX_CONNECT_FAILURES,
                    )
                    if connect_failures >= _MAX_CONNECT_FAILURES:
                        raise RuntimeError(f"D-Bus device introspection failed {connect_failures} times: {e}")
                    await asyncio.sleep(5)
                    continue

                # Successfully connected — reset failure counter
                connect_failures = 0

                # Read initial connected state
                try:
                    self.connected = bool(await device_iface.get_connected())
                except Exception:
                    self.connected = False
                if self.client:
                    self.client._update_status(
                        {
                            "bluetooth_connected": self.connected,
                            "bluetooth_connected_at": datetime.now().isoformat(),
                        }
                    )

                # asyncio.Event set from D-Bus signal callback (called in D-Bus reader task thread)
                disconnect_event = asyncio.Event()
                if not self.connected:
                    disconnect_event.set()

                def _make_props_handler(evt):
                    def on_props_changed(iface_name, changed, _invalidated):
                        if iface_name != "org.bluez.Device1" or "Connected" not in changed:
                            return
                        new_connected = bool(changed["Connected"].value)
                        if new_connected == self.connected:
                            return
                        self.connected = new_connected
                        ts = datetime.now().isoformat()
                        if self.client:
                            self.client._update_status(
                                {
                                    "bluetooth_connected": new_connected,
                                    "bluetooth_connected_at": ts,
                                }
                            )
                        if not new_connected:
                            loop.call_soon_threadsafe(evt.set)
                            logger.warning("[%s] PropertiesChanged: Disconnected!", self.device_name)
                        else:
                            logger.info("[%s] PropertiesChanged: Connected!", self.device_name)

                    return on_props_changed

                props_iface.on_properties_changed(_make_props_handler(disconnect_event))
                logger.info("[%s] D-Bus monitoring active (connected=%s)", self.device_name, self.connected)

                # Inner monitor loop
                restart_outer = False
                while not restart_outer:
                    if not self.management_enabled:
                        await asyncio.sleep(5)
                        continue

                    if self.connected:
                        # Clear reconnect state
                        if self.client and self.client.status.get("reconnecting"):
                            self.client._update_status({"reconnecting": False, "reconnect_attempt": 0})
                        reconnect_attempt = 0

                        # Wait for disconnect signal or heartbeat timeout
                        try:
                            await asyncio.wait_for(disconnect_event.wait(), timeout=self.check_interval * 3)
                        except TimeoutError:
                            # Heartbeat — verify state directly
                            try:
                                current_val = bool(await device_iface.get_connected())
                                if not current_val and self.connected:
                                    logger.warning("[%s] Heartbeat: missed disconnect signal", self.device_name)
                                    self.connected = False
                                    if self.client:
                                        self.client._update_status(
                                            {
                                                "bluetooth_connected": False,
                                                "bluetooth_connected_at": datetime.now().isoformat(),
                                            }
                                        )
                                    disconnect_event.set()
                            except Exception:
                                pass
                    else:
                        # Device is disconnected — attempt reconnect
                        disconnect_event.clear()
                        reconnect_attempt += 1
                        if self.client:
                            self.client._update_status(
                                {
                                    "reconnecting": True,
                                    "reconnect_attempt": reconnect_attempt,
                                }
                            )

                        # Auto-disable after too many failures
                        if self._handle_reconnect_failure(reconnect_attempt):
                            reconnect_attempt = 0
                            restart_outer = True
                            break

                        # Stop sendspin (BT sink is gone — would flood PortAudioErrors)
                        if self.client and self.client.is_running():
                            logger.info("BT disconnected for %s, stopping sendspin daemon...", self.device_name)
                            with self.client._status_lock:
                                is_grouped = bool(self.client.status.get("group_id"))
                            if not is_grouped:
                                await self.client._send_subprocess_command({"cmd": "pause"})
                                await asyncio.sleep(0.2)
                            await self.client.stop_sendspin()

                        logger.warning(
                            "[%s] Disconnected, reconnecting... (attempt %s)", self.device_name, reconnect_attempt
                        )
                        success = await loop.run_in_executor(None, self.connect_device)

                        if success:
                            reconnect_attempt = 0
                            self.connected = True
                            if self.client:
                                self.client._update_status(
                                    {
                                        "reconnecting": False,
                                        "reconnect_attempt": 0,
                                        "bluetooth_connected": True,
                                        "bluetooth_connected_at": datetime.now().isoformat(),
                                    }
                                )
                            # Re-subscribe signals — device object may have changed
                            logger.info("[%s] Reconnected, restarting D-Bus subscription...", self.device_name)
                            if self.client:
                                logger.info("BT reconnected for %s, starting sendspin...", self.device_name)
                                await self.client.start_sendspin()
                            restart_outer = True
                        else:
                            # Failed — back off proportional to failure count
                            delay = self._reconnect_delay(reconnect_attempt)
                            logger.debug("[%s] Backoff: next attempt in %.0fs", self.device_name, delay)
                            await asyncio.sleep(delay)
                            # Re-read state in case external reconnect happened
                            try:
                                self.connected = bool(await device_iface.get_connected())
                            except Exception:
                                pass
                            if self.connected:
                                # Device reconnected on its own while we were sleeping —
                                # configure audio and start sendspin, then restart D-Bus subscription
                                logger.info("[%s] External reconnect detected, configuring audio...", self.device_name)
                                await loop.run_in_executor(None, self.configure_bluetooth_audio)
                                reconnect_attempt = 0
                                if self.client:
                                    self.client._update_status(
                                        {
                                            "reconnecting": False,
                                            "reconnect_attempt": 0,
                                            "bluetooth_connected": True,
                                            "bluetooth_connected_at": datetime.now().isoformat(),
                                        }
                                    )
                                    logger.info("BT reconnected for %s, starting sendspin...", self.device_name)
                                    await self.client.start_sendspin()
                                restart_outer = True

            except RuntimeError:
                raise  # propagate to monitor_and_reconnect for polling fallback
            except Exception as e:
                connect_failures += 1
                logger.error(
                    "[%s] D-Bus monitor error (%s/%s): %s", self.device_name, connect_failures, _MAX_CONNECT_FAILURES, e
                )
                if connect_failures >= _MAX_CONNECT_FAILURES:
                    # Bus is likely broken; force reconnect on next iteration
                    if bus:
                        try:
                            bus.disconnect()
                        except Exception:
                            pass
                        bus = None
                    raise RuntimeError(f"D-Bus monitor failed {connect_failures} consecutive times: {e}")
            await asyncio.sleep(10)
