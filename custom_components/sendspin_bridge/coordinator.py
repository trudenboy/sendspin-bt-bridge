"""DataUpdateCoordinator for the Sendspin BT Bridge integration.

Bootstraps with ``GET /api/ha/state`` and listens on
``/api/status/events`` (SSE) for typed event deltas.  When the SSE
connection drops, falls back to periodic polling until reconnect.

The coordinator's ``data`` dict mirrors ``HAStateProjection.to_json()``
shape, which is what every entity platform (sensor / switch / etc.)
reads from.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

import aiohttp
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_HOST,
    CONF_PORT,
    CONF_TOKEN,
    CONF_USE_HTTPS,
    DEFAULT_RECONNECT_BACKOFF_MAX_SECS,
    DEFAULT_RECONNECT_BACKOFF_SECS,
    DOMAIN,
    ENDPOINT_HA_STATE,
    ENDPOINT_STATUS_EVENTS,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class SendspinDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Pulls state from the bridge and pushes entity-state deltas to HA."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}:{entry.entry_id[:8]}",
            update_interval=None,  # SSE drives updates; polling kicks in only on drops
        )
        self.entry = entry
        self.host: str = entry.data[CONF_HOST]
        self.port: int = entry.data.get(CONF_PORT, 8080)
        self.token: str = entry.data[CONF_TOKEN]
        self.use_https: bool = entry.data.get(CONF_USE_HTTPS, False)
        self._sse_task: asyncio.Task | None = None
        self._stopped = asyncio.Event()

    @property
    def base_url(self) -> str:
        scheme = "https" if self.use_https else "http"
        return f"{scheme}://{self.host}:{self.port}"

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    # -- DataUpdateCoordinator overrides --------------------------------

    async def _async_update_data(self) -> dict[str, Any]:
        """Single-shot REST fetch — used as bootstrap + polling fallback."""
        session = async_get_clientsession(self.hass)
        try:
            async with session.get(
                f"{self.base_url}{ENDPOINT_HA_STATE}",
                headers=self.headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 401:
                    raise ConfigEntryAuthFailed("Bearer token rejected")
                if resp.status != 200:
                    raise UpdateFailed(f"Bridge returned {resp.status}")
                return await resp.json()
        except aiohttp.ClientError as exc:
            raise UpdateFailed(f"Connection error: {exc}") from exc

    # -- SSE event listener --------------------------------------------

    def start_event_listener(self) -> None:
        if self._sse_task is None or self._sse_task.done():
            self._stopped.clear()
            self._sse_task = self.hass.async_create_task(self._run_sse())

    async def async_stop(self) -> None:
        self._stopped.set()
        if self._sse_task is not None:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except (asyncio.CancelledError, Exception):
                pass
            self._sse_task = None

    async def _run_sse(self) -> None:
        """Background task: stream typed events from the bridge.

        Each ``InternalEvent`` triggers a coordinator refresh — we don't
        try to apply event deltas client-side because the bridge already
        does the projection work and its full snapshot is one round-trip
        away.  This keeps the integration simple at the cost of slightly
        more REST traffic on event-heavy speakers (still tiny).
        """
        backoff = DEFAULT_RECONNECT_BACKOFF_SECS
        session = async_get_clientsession(self.hass)
        while not self._stopped.is_set():
            try:
                async with session.get(
                    f"{self.base_url}{ENDPOINT_STATUS_EVENTS}",
                    headers={**self.headers, "Accept": "text/event-stream"},
                    timeout=aiohttp.ClientTimeout(total=None, sock_read=60),
                ) as resp:
                    if resp.status == 401:
                        raise ConfigEntryAuthFailed("Bearer token rejected on SSE stream")
                    if resp.status != 200:
                        _LOGGER.warning(
                            "SSE event stream returned %s; retrying in %ss",
                            resp.status,
                            backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, DEFAULT_RECONNECT_BACKOFF_MAX_SECS)
                        continue

                    backoff = DEFAULT_RECONNECT_BACKOFF_SECS  # reset on success

                    async for raw in resp.content:
                        if self._stopped.is_set():
                            break
                        line = raw.decode("utf-8", errors="replace").strip()
                        if not line or line.startswith(":"):
                            continue
                        if line.startswith("data:"):
                            payload = line[5:].strip()
                            try:
                                event = json.loads(payload)
                            except ValueError:
                                continue
                            event_type = event.get("event_type") or ""
                            # Trigger a full refresh — entities re-derive
                            # from the new projection.
                            _LOGGER.debug("Sendspin event %s; refreshing", event_type)
                            self.hass.async_create_task(self.async_request_refresh())
            except ConfigEntryAuthFailed:
                self.last_update_success = False
                self._async_log_auth_failure()
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOGGER.info("SSE event stream error (%s); reconnecting in %ss", exc, backoff)
                try:
                    await asyncio.wait_for(self._stopped.wait(), timeout=backoff)
                    return
                except TimeoutError:
                    pass
                backoff = min(backoff * 2, DEFAULT_RECONNECT_BACKOFF_MAX_SECS)

    def _async_log_auth_failure(self) -> None:
        _LOGGER.error("Bearer token rejected by bridge — re-pair the integration in HA")

    # -- Convenience accessors used by entity platforms ----------------

    def device_meta(self, player_id: str) -> dict[str, Any]:
        return (self.data or {}).get("device_meta", {}).get(player_id, {})

    def device_state(self, player_id: str, object_id: str) -> dict[str, Any] | None:
        return (self.data or {}).get("devices", {}).get(player_id, {}).get(object_id)

    def bridge_state(self, object_id: str) -> dict[str, Any] | None:
        return (self.data or {}).get("bridge", {}).get(object_id)

    def device_available(self, player_id: str) -> bool:
        return bool((self.data or {}).get("availability", {}).get(player_id, False))

    def known_player_ids(self) -> list[str]:
        return list((self.data or {}).get("devices", {}).keys())

    def bridge_meta(self) -> dict[str, Any]:
        return (self.data or {}).get("bridge_meta") or {}
