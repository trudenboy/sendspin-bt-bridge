"""Selects for Sendspin BT Bridge — idle mode, keep-alive method."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.select import SelectEntity
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

_DEVICE_SELECTS = [s for s in DEVICE_ENTITIES if s.kind == "select"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: SendspinDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [
        _SendspinSelect(coordinator, player_id, spec)
        for player_id in coordinator.known_player_ids()
        for spec in _DEVICE_SELECTS
    ]
    async_add_entities(entities)


class _SendspinSelect(SendspinDeviceEntity, SelectEntity):
    def __init__(self, coordinator, player_id, spec: EntitySpec):
        super().__init__(coordinator, player_id, spec.object_id, spec.name, availability_class=spec.availability_class)
        self._spec = spec
        self._attr_icon = spec.icon
        self._attr_options = list(spec.options)
        if spec.entity_category == "config":
            self._attr_entity_category = EntityCategory.CONFIG

    @property
    def current_option(self) -> str | None:
        value = self._state_value()
        if value is None:
            return None
        text = str(value)
        return text if text in self._attr_options else None

    async def async_select_option(self, option: str) -> None:
        if not self._spec.command:
            return
        await api.device_command(self.coordinator, self._player_id, self._spec.command, option)
        await self.coordinator.async_request_refresh()
