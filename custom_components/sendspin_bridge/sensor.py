"""Sensors for Sendspin BT Bridge — diagnostics only.

MA owns ``volume`` / ``current_track`` / queue metadata via its own HA
integration; we expose only fields MA does not know about (BT signal,
battery, sync health, idle state, …).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory

from ._specs import BRIDGE_ENTITIES, DEVICE_ENTITIES, EntitySpec
from .const import DOMAIN
from .entity import SendspinBridgeEntity, SendspinDeviceEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import SendspinDataUpdateCoordinator

_DEVICE_SENSORS = [s for s in DEVICE_ENTITIES if s.kind == "sensor"]
_BRIDGE_SENSORS = [s for s in BRIDGE_ENTITIES if s.kind == "sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: SendspinDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = []
    for player_id in coordinator.known_player_ids():
        for spec in _DEVICE_SENSORS:
            entities.append(_SendspinDeviceSensor(coordinator, player_id, spec))
    for spec in _BRIDGE_SENSORS:
        entities.append(_SendspinBridgeSensor(coordinator, spec))
    async_add_entities(entities)


def _entity_category(spec: EntitySpec) -> EntityCategory | None:
    if spec.entity_category == "diagnostic":
        return EntityCategory.DIAGNOSTIC
    if spec.entity_category == "sendspin_bridge.config":
        return EntityCategory.CONFIG
    return None


class _SendspinDeviceSensor(SendspinDeviceEntity, SensorEntity):
    def __init__(self, coordinator, player_id, spec: EntitySpec):
        super().__init__(coordinator, player_id, spec.object_id, spec.name, availability_class=spec.availability_class)
        self._spec = spec
        self._attr_icon = spec.icon
        self._attr_device_class = spec.device_class  # type: ignore[assignment]
        self._attr_native_unit_of_measurement = spec.unit
        if spec.state_class == "measurement":
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif spec.state_class == "total_increasing":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_entity_category = _entity_category(spec)

    @property
    def native_value(self) -> Any:
        return self._state_value()


class _SendspinBridgeSensor(SendspinBridgeEntity, SensorEntity):
    def __init__(self, coordinator, spec: EntitySpec):
        super().__init__(coordinator, spec.object_id, spec.name)
        self._spec = spec
        self._attr_icon = spec.icon
        self._attr_entity_category = _entity_category(spec)

    @property
    def native_value(self) -> Any:
        return self._state_value()
