"""Buttons for Sendspin BT Bridge — BT-level commands MA can't perform."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory

from . import api
from ._specs import BRIDGE_ENTITIES, DEVICE_ENTITIES, EntitySpec
from .const import DOMAIN
from .entity import SendspinBridgeEntity, SendspinDeviceEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import SendspinDataUpdateCoordinator

_DEVICE_BUTTONS = [s for s in DEVICE_ENTITIES if s.kind == "button"]
_BRIDGE_BUTTONS = [s for s in BRIDGE_ENTITIES if s.kind == "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: SendspinDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[ButtonEntity] = []
    for player_id in coordinator.known_player_ids():
        for spec in _DEVICE_BUTTONS:
            entities.append(_SendspinDeviceButton(coordinator, player_id, spec))
    for spec in _BRIDGE_BUTTONS:
        entities.append(_SendspinBridgeButton(coordinator, spec))
    async_add_entities(entities)


def _entity_category(spec: EntitySpec) -> EntityCategory | None:
    if spec.entity_category == "diagnostic":
        return EntityCategory.DIAGNOSTIC
    if spec.entity_category == "config":
        return EntityCategory.CONFIG
    return None


class _SendspinDeviceButton(SendspinDeviceEntity, ButtonEntity):
    def __init__(self, coordinator, player_id, spec: EntitySpec):
        super().__init__(coordinator, player_id, spec.object_id, spec.name, availability_class=spec.availability_class)
        self._spec = spec
        self._attr_icon = spec.icon
        self._attr_entity_category = _entity_category(spec)

    async def async_press(self) -> None:
        if not self._spec.command:
            return
        await api.device_command(self.coordinator, self._player_id, self._spec.command)


class _SendspinBridgeButton(SendspinBridgeEntity, ButtonEntity):
    def __init__(self, coordinator, spec: EntitySpec):
        super().__init__(coordinator, spec.object_id, spec.name)
        self._spec = spec
        self._attr_icon = spec.icon
        self._attr_entity_category = _entity_category(spec)

    async def async_press(self) -> None:
        if not self._spec.command:
            return
        await api.bridge_command(self.coordinator, self._spec.command)
