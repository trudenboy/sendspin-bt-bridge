"""Mock BluetoothManager for demo mode."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from demo.fixtures import DEMO_DEVICE_STATUS, get_demo_adapter

if TYPE_CHECKING:
    from collections.abc import Callable

    from sendspin_client import SendspinClient

logger = logging.getLogger(__name__)


class DemoBluetoothManager:
    """Drop-in replacement for BluetoothManager that simulates BT behavior."""

    def __init__(
        self,
        mac_address: str,
        adapter: str = "",
        device_name: str = "",
        client: SendspinClient | None = None,
        prefer_sbc: bool = False,
        check_interval: int = 10,
        max_reconnect_fails: int = 0,
        on_sink_found: Callable[[str, int | None], None] | None = None,
        churn_threshold: int = 0,
        churn_window: float = 300.0,
    ):
        self.mac_address = mac_address
        adapter_info = get_demo_adapter(adapter)
        self.adapter = str(adapter_info["id"])
        self.device_name = device_name
        self.client = client
        self.on_sink_found = on_sink_found
        self.management_enabled = True
        self.effective_adapter_mac = str(adapter_info["mac"])
        self.adapter_hci_name = str(adapter_info["id"])

        initial = DEMO_DEVICE_STATUS.get(mac_address, {})
        self.connected: bool = initial.get("bluetooth_connected", False)
        self.battery_level: int | None = initial.get("battery_level")
        self._battery_start = time.monotonic()

        self._running = True
        self._sink_name = self._make_sink_name()

    def _make_sink_name(self) -> str:
        mac_under = self.mac_address.replace(":", "_")
        return f"bluez_output.{mac_under}.1"

    # -- Public interface (matches BluetoothManager) --------------------------

    def check_bluetooth_available(self) -> bool:
        return True

    def is_device_connected(self) -> bool:
        return self.connected

    def is_device_paired(self) -> bool:
        return True

    def trust_device(self) -> bool:
        logger.debug("[demo] trust_device(%s)", self.mac_address)
        return True

    def pair_device(self) -> bool:
        logger.info("[demo] Pairing %s ...", self.device_name or self.mac_address)
        time.sleep(2)
        return True

    def connect_device(self) -> bool:
        logger.info("[demo] Connecting %s ...", self.device_name or self.mac_address)
        time.sleep(1.5)
        self.connected = True
        self.battery_level = DEMO_DEVICE_STATUS.get(self.mac_address, {}).get("battery_level", 75)
        # Trigger sink discovery so the client sets bluetooth_sink_name
        self.configure_bluetooth_audio()
        return True

    def disconnect_device(self) -> bool:
        logger.info("[demo] Disconnecting %s", self.device_name or self.mac_address)
        self.connected = False
        self.battery_level = None
        return True

    def configure_bluetooth_audio(self) -> bool:
        if not self.connected:
            return False
        logger.info("[demo] Audio configured: %s", self._sink_name)
        initial = DEMO_DEVICE_STATUS.get(self.mac_address, {})
        restored_vol = initial.get("volume")
        if self.on_sink_found:
            self.on_sink_found(self._sink_name, restored_vol)
        return True

    async def monitor_and_reconnect(self) -> None:
        """Async no-op loop — keeps the task alive without real BT polling."""
        while self._running:
            await asyncio.sleep(10)

    def shutdown(self) -> None:
        self._running = False
