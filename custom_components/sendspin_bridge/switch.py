"""Switches for Sendspin BT Bridge — config knobs (enabled, BT management)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity
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

_DEVICE_SWITCHES = [s for s in DEVICE_ENTITIES if s.kind == "switch"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: SendspinDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        _SendspinSwitch(coordinator, player_id, spec)
        for player_id in coordinator.known_player_ids()
        for spec in _DEVICE_SWITCHES
    ]
    async_add_entities(entities)


class _SendspinSwitch(SendspinDeviceEntity, SwitchEntity):
    def __init__(self, coordinator, player_id, spec: EntitySpec):
        super().__init__(coordinator, player_id, spec.object_id, spec.name, availability_class=spec.availability_class)
        self._spec = spec
        self._attr_icon = spec.icon
        if spec.entity_category == "sendspin_bridge.config":
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def is_on(self) -> bool | None:
        value = self._state_value()
        if value is None:
            return None
        if isinstance(value, str):
            return value.upper() in ("ON", "TRUE", "1", "YES")
        return bool(value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        if not self._spec.command:
            return
        await api.device_command(self.coordinator, self._player_id, self._spec.command, "ON")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        if not self._spec.command:
            return
        await api.device_command(self.coordinator, self._player_id, self._spec.command, "OFF")
        await self.coordinator.async_request_refresh()
