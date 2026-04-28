"""Sendspin BT Bridge — Home Assistant custom_component (HACS).

Lives in the same git repository as the bridge itself.  HACS picks it up
via ``hacs.json`` at the repo root.

Architecture:

* The bridge advertises ``_sendspin-bridge._tcp.local.`` on the LAN.
* HA's Zeroconf integration discovers the advertisement and triggers
  this integration's ``config_flow``.
* On HAOS, ``config_flow`` mints a long-lived token via
  ``POST /api/auth/ha-pair`` (Supervisor-side endpoint).  Off-HAOS,
  the user pastes a token they generated in the bridge web UI.
* The ``SendspinDataUpdateCoordinator`` bootstraps with
  ``GET /api/ha/state`` and listens to ``/api/status/events`` for
  real-time deltas.  All entities under the integration read from this
  coordinator's ``data`` dict.

The integration does NOT expose ``media_player`` — that's owned by
Music Assistant's own HA integration.  See ``services/ha_entity_model.py``
in the bridge for the deduplication rule.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from homeassistant.const import Platform

from .const import DOMAIN
from .coordinator import SendspinDataUpdateCoordinator

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)

# Platforms registered.  Conspicuously: NO Platform.MEDIA_PLAYER —
# Music Assistant's own integration owns that surface for Sendspin
# speakers it knows about.
PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.UPDATE,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Sendspin BT Bridge from a config entry."""
    coordinator = SendspinDataUpdateCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.start_event_listener()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    coordinator: SendspinDataUpdateCoordinator | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is not None:
        await coordinator.async_stop()
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok
