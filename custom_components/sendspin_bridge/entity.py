"""Base entity class for Sendspin BT Bridge.

Per-device entities declare ``connections=[(CONNECTION_BLUETOOTH, mac)]``
in ``device_info`` so Home Assistant's device registry merges them with
the existing ``media_player.<name>`` device card created by the Music
Assistant integration.  This is the load-bearing line of code for the
"single device card per speaker" UX promise.
"""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, UNIQUE_ID_PREFIX
from .coordinator import SendspinDataUpdateCoordinator


class SendspinDeviceEntity(CoordinatorEntity[SendspinDataUpdateCoordinator]):
    """Base class for per-device entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SendspinDataUpdateCoordinator,
        player_id: str,
        object_id: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._player_id = player_id
        self._object_id = object_id
        self._attr_name = name
        self._attr_unique_id = f"{UNIQUE_ID_PREFIX}_{player_id}_{object_id}"

    @property
    def device_info(self) -> DeviceInfo:
        meta = self.coordinator.device_meta(self._player_id)
        mac = (meta.get("mac") or "").lower()
        identifiers = {(DOMAIN, f"{UNIQUE_ID_PREFIX}_{self._player_id}")}
        connections: set[tuple[str, str]] = set()
        if mac:
            connections.add((CONNECTION_BLUETOOTH, mac))
        bridge_meta = self.coordinator.bridge_meta()
        bridge_id = bridge_meta.get("bridge_id") or ""
        return DeviceInfo(
            identifiers=identifiers,
            connections=connections,
            manufacturer="Sendspin",
            model="BT Speaker via Sendspin Bridge",
            name=meta.get("player_name") or self._player_id,
            via_device=(DOMAIN, f"sendspin_bridge_{bridge_id}") if bridge_id else None,
            suggested_area=meta.get("room_name") or None,
        )

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success and self.coordinator.device_available(self._player_id)

    def _state_value(self) -> Any:
        record = self.coordinator.device_state(self._player_id, self._object_id)
        if not record:
            return None
        return record.get("value")

    def _state_attrs(self) -> dict[str, Any]:
        record = self.coordinator.device_state(self._player_id, self._object_id)
        if not record:
            return {}
        return dict(record.get("attrs") or {})


class SendspinBridgeEntity(CoordinatorEntity[SendspinDataUpdateCoordinator]):
    """Base class for the bridge-level (single) HA Device card."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: SendspinDataUpdateCoordinator,
        object_id: str,
        name: str,
    ) -> None:
        super().__init__(coordinator)
        self._object_id = object_id
        self._attr_name = name
        bridge_meta = coordinator.bridge_meta()
        bridge_id = bridge_meta.get("bridge_id") or "bridge"
        self._attr_unique_id = f"{UNIQUE_ID_PREFIX}_bridge_{bridge_id}_{object_id}"

    @property
    def device_info(self) -> DeviceInfo:
        meta = self.coordinator.bridge_meta()
        bridge_id = meta.get("bridge_id") or "bridge"
        return DeviceInfo(
            identifiers={(DOMAIN, f"sendspin_bridge_{bridge_id}")},
            manufacturer="Sendspin",
            model="Music Assistant ↔ Bluetooth Bridge",
            name=meta.get("bridge_name") or "Sendspin Bridge",
            sw_version=meta.get("version") or None,
            configuration_url=meta.get("web_url") or None,
        )

    def _state_value(self) -> Any:
        record = self.coordinator.bridge_state(self._object_id)
        if not record:
            return None
        return record.get("value")

    def _state_attrs(self) -> dict[str, Any]:
        record = self.coordinator.bridge_state(self._object_id)
        if not record:
            return {}
        return dict(record.get("attrs") or {})
