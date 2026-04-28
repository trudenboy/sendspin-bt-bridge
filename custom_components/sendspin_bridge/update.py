"""Update entity for the bridge — surfaces the auto-update notice in HA."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.update import UpdateEntity
from homeassistant.const import EntityCategory

from ._specs import BRIDGE_ENTITIES, EntitySpec
from .const import DOMAIN
from .entity import SendspinBridgeEntity

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from .coordinator import SendspinDataUpdateCoordinator

_BRIDGE_UPDATES = [s for s in BRIDGE_ENTITIES if s.kind == "update"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator: SendspinDataUpdateCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities = [_SendspinUpdate(coordinator, spec) for spec in _BRIDGE_UPDATES]
    async_add_entities(entities)


class _SendspinUpdate(SendspinBridgeEntity, UpdateEntity):
    def __init__(self, coordinator, spec: EntitySpec):
        super().__init__(coordinator, spec.object_id, spec.name)
        self._spec = spec
        self._attr_icon = spec.icon
        self._attr_entity_category = EntityCategory.DIAGNOSTIC if spec.entity_category == "diagnostic" else None

    @property
    def installed_version(self) -> str | None:
        attrs = self._state_attrs()
        return attrs.get("installed_version") or self.coordinator.bridge_meta().get("version")

    @property
    def latest_version(self) -> str | None:
        attrs = self._state_attrs()
        return attrs.get("latest_version") or self.installed_version
