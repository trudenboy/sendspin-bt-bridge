"""Bluetooth A2DP audio backend wrapping BluetoothManager."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from services.audio_backend import AudioBackend, BackendCapability, BackendStatus, BackendType

if TYPE_CHECKING:
    from bluetooth_manager import BluetoothManager

logger = logging.getLogger(__name__)


class BluetoothA2dpBackend(AudioBackend):
    """AudioBackend implementation for Bluetooth A2DP speakers."""

    def __init__(self, bt_manager: BluetoothManager) -> None:
        self._bt_manager = bt_manager
        self._sink_name: str | None = None
        self._volume: int | None = None
        # Chain into bt_manager's on_sink_found so we capture the sink name.
        original_callback = bt_manager.on_sink_found

        def _capture_sink(sink_name: str, index: int | None = None) -> None:
            self._sink_name = sink_name
            if original_callback:
                original_callback(sink_name, index)

        bt_manager.on_sink_found = _capture_sink

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    @property
    def backend_type(self) -> BackendType:
        return BackendType.BLUETOOTH_A2DP

    @property
    def backend_id(self) -> str:
        return f"bt-a2dp-{self._bt_manager.mac_address}"

    @property
    def mac(self) -> str:
        return self._bt_manager.mac_address

    @property
    def adapter(self) -> str:
        return self._bt_manager.adapter

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        if not self._bt_manager.connect_device():
            return False
        if not self._bt_manager.configure_bluetooth_audio():
            logger.warning(
                "[%s] BT connected but audio sink not found",
                self._bt_manager.device_name,
            )
        return True

    def disconnect(self) -> bool:
        try:
            result = self._bt_manager.disconnect_device()
            self._sink_name = None
            return result
        except Exception:
            logger.exception("[%s] Error disconnecting", self._bt_manager.device_name)
            return False

    def is_ready(self) -> bool:
        return bool(self._bt_manager.connected and self._sink_name)

    # ------------------------------------------------------------------
    # Audio
    # ------------------------------------------------------------------

    def get_audio_destination(self) -> str | None:
        return self._sink_name

    def set_volume(self, level: int) -> None:
        self._volume = max(0, min(100, level))

    def get_volume(self) -> int | None:
        return self._volume

    # ------------------------------------------------------------------
    # Status / capabilities
    # ------------------------------------------------------------------

    def get_status(self) -> BackendStatus:
        return BackendStatus(
            connected=self._bt_manager.connected,
            available=self._sink_name is not None,
            error=None,
            battery_level=self._bt_manager.battery_level,
        )

    def get_capabilities(self) -> set[BackendCapability]:
        return {
            BackendCapability.VOLUME_CONTROL,
            BackendCapability.DEVICE_DISCOVERY,
            BackendCapability.BATTERY_REPORTING,
            BackendCapability.CODEC_SELECTION,
        }
