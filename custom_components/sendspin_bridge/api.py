"""Thin REST helper for issuing commands to the bridge.

The bridge exposes one REST endpoint per command surface
(``/api/bt/reconnect``, ``/api/transport/cmd``, ``/api/config``, etc.).
Rather than dispatching every entity class through its own URL, we POST
to a single dispatch endpoint that maps ``(player_id, command, value)``
to the bridge's internal ``HaCommandDispatcher``.

For v2.65.0 we use the existing per-command REST endpoints because
they're stable and individually authorised.  When v2.66+ ships a unified
``/api/ha/command`` endpoint we'll switch over.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

if TYPE_CHECKING:
    from .coordinator import SendspinDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def post(
    coordinator: SendspinDataUpdateCoordinator,
    path: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """POST to the bridge with the bearer token and return the JSON body."""
    session = async_get_clientsession(coordinator.hass)
    url = f"{coordinator.base_url}{path}"
    try:
        async with session.post(
            url,
            headers=coordinator.headers,
            json=payload or {},
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            try:
                body = await resp.json(content_type=None)
            except aiohttp.ContentTypeError:
                body = {}
            if resp.status >= 400:
                raise HomeAssistantError(f"Bridge {path} returned {resp.status}: {body.get('error') or body}")
            return body if isinstance(body, dict) else {}
    except aiohttp.ClientError as exc:
        raise HomeAssistantError(f"Bridge {path} request failed: {exc}") from exc


async def device_command(
    coordinator: SendspinDataUpdateCoordinator,
    player_id: str,
    command: str,
    value: Any | None = None,
) -> dict[str, Any]:
    """Hit ``/api/ha/command`` with a typed device command.

    Note: in v2.65.0 we ship this against a unified routing endpoint that
    delegates to ``services.ha_command_dispatcher.HaCommandDispatcher``;
    if that endpoint is missing on older bridges this raises so the
    operator sees the version mismatch immediately.
    """
    body = {"command": command, "value": value, "player_id": player_id}
    return await post(coordinator, "/api/ha/command", body)


async def bridge_command(
    coordinator: SendspinDataUpdateCoordinator,
    command: str,
    value: Any | None = None,
) -> dict[str, Any]:
    body = {"command": command, "value": value}
    return await post(coordinator, "/api/ha/command/bridge", body)
