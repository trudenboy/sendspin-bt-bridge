"""Binary sensors for Sendspin BT Bridge."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.const import EntityCategory

from ._specs import BRIDGE_ENTITIES, DEVICE_ENTITIES, EntitySpec
from .const import DOMAIN
from .entity import SendspinBridgeEntity, SendspinDeviceEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import SendspinDataUpdateCoordinator

_DEVICE_BIN_SENSORS = [s for s in DEVICE_ENTITIES if s.kind == "binary_sensor"]
_BRIDGE_BIN_SENSORS = [s for s in BRIDGE_ENTITIES if s.kind == "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: SendspinDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[BinarySensorEntity] = []
    for player_id in coordinator.known_player_ids():
        for spec in _DEVICE_BIN_SENSORS:
            entities.append(_SendspinDeviceBinary(coordinator, player_id, spec))
    for spec in _BRIDGE_BIN_SENSORS:
        entities.append(_SendspinBridgeBinary(coordinator, spec))
    async_add_entities(entities)


def _device_class(value: str | None) -> BinarySensorDeviceClass | None:
    if value == "connectivity":
        return BinarySensorDeviceClass.CONNECTIVITY
    return None


def _entity_category(spec: EntitySpec) -> EntityCategory | None:
    if spec.entity_category == "diagnostic":
        return EntityCategory.DIAGNOSTIC
    return None


class _SendspinDeviceBinary(SendspinDeviceEntity, BinarySensorEntity):
    def __init__(self, coordinator, player_id, spec: EntitySpec):
        super().__init__(coordinator, player_id, spec.object_id, spec.name)
        self._spec = spec
        self._attr_icon = spec.icon
        self._attr_device_class = _device_class(spec.device_class)
        self._attr_entity_category = _entity_category(spec)

    @property
    def is_on(self) -> bool | None:
        value = self._state_value()
        if value is None:
            return None
        if isinstance(value, str):
            return value.upper() in ("ON", "TRUE", "1", "YES")
        return bool(value)


class _SendspinBridgeBinary(SendspinBridgeEntity, BinarySensorEntity):
    def __init__(self, coordinator, spec: EntitySpec):
        super().__init__(coordinator, spec.object_id, spec.name)
        self._spec = spec
        self._attr_icon = spec.icon
        self._attr_device_class = _device_class(spec.device_class)
        self._attr_entity_category = _entity_category(spec)

    @property
    def is_on(self) -> bool | None:
        value = self._state_value()
        if value is None:
            return None
        if isinstance(value, str):
            return value.upper() in ("ON", "TRUE", "1", "YES")
        return bool(value)
