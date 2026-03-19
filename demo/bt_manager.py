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
        if self.client and hasattr(self.client, "_update_status"):
            self.client._update_status(
                {
                    "bluetooth_available": True,
                    "bluetooth_connected": self.connected,
                    "server_connected": initial.get("server_connected", False),
                    "connected": initial.get("connected", self.connected),
                    "playing": initial.get("playing", False),
                    "volume": initial.get("volume", 100),
                    "muted": initial.get("muted", False),
                    "battery_level": initial.get("battery_level"),
                    "audio_format": initial.get("audio_format"),
                    "current_track": initial.get("current_track"),
                    "current_artist": initial.get("current_artist"),
                    "track_duration_ms": initial.get("track_duration_ms"),
                    "track_progress_ms": initial.get("track_progress_ms"),
                    "buffering": initial.get("buffering", False),
                    "reanchor_count": initial.get("reanchor_count", 0),
                    "last_sync_error_ms": initial.get("last_sync_error_ms"),
                    "last_reanchor_at": initial.get("last_reanchor_at"),
                    "reanchoring": initial.get("reanchoring", False),
                    "group_id": initial.get("group_id"),
                    "group_name": initial.get("group_name"),
                    "reconnecting": initial.get("reconnecting", False),
                    "reconnect_attempt": initial.get("reconnect_attempt", 0),
                    "stopping": initial.get("stopping", False),
                    "bt_management_enabled": initial.get("bt_management_enabled", True),
                    "bt_released_by": initial.get("bt_released_by"),
                }
            )

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
        initial = DEMO_DEVICE_STATUS.get(self.mac_address, {})
        if initial.get("reconnecting"):
            logger.info("[demo] Reconnect still pending for %s", self.device_name or self.mac_address)
            self.connected = False
            if self.client and hasattr(self.client, "_update_status"):
                self.client._update_status(
                    {
                        "bluetooth_connected": False,
                        "connected": False,
                        "server_connected": False,
                        "playing": False,
                        "audio_streaming": False,
                        "reconnecting": True,
                        "reconnect_attempt": initial.get("reconnect_attempt", 1),
                    }
                )
            return False
        self.connected = True
        self.battery_level = initial.get("battery_level", 75)
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
