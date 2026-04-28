"""Numbers for Sendspin BT Bridge — static delay, power-save delay."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.const import EntityCategory

from . import api
from ._specs import DEVICE_ENTITIES, EntitySpec
from .const import DOMAIN
from .entity import SendspinDeviceEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import SendspinDataUpdateCoordinator

_DEVICE_NUMBERS = [s for s in DEVICE_ENTITIES if s.kind == "number"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: SendspinDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        _SendspinNumber(coordinator, player_id, spec)
        for player_id in coordinator.known_player_ids()
        for spec in _DEVICE_NUMBERS
    ]
    async_add_entities(entities)


class _SendspinNumber(SendspinDeviceEntity, NumberEntity):
    def __init__(self, coordinator, player_id, spec: EntitySpec):
        super().__init__(coordinator, player_id, spec.object_id, spec.name)
        self._spec = spec
        self._attr_icon = spec.icon
        self._attr_native_min_value = spec.min_value or 0
        self._attr_native_max_value = spec.max_value or 100
        self._attr_native_step = spec.step or 1
        self._attr_native_unit_of_measurement = spec.unit
        self._attr_mode = NumberMode.BOX
        if spec.entity_category == "config":
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def native_value(self) -> float | None:
        value = self._state_value()
        if value is None:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    async def async_set_native_value(self, value: float) -> None:
        if not self._spec.command:
            return
        await api.device_command(self.coordinator, self._player_id, self._spec.command, value)
        await self.coordinator.async_request_refresh()
