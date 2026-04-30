"""Config flow for Sendspin BT Bridge.

Three branches:

1. **Zeroconf-discovered** — bridge advertises ``_sendspin-bridge._tcp.local.``,
   HA's discovery framework launches us with host/port pre-filled.  We
   try the Supervisor pair endpoint first (zero-input on HAOS), fall back
   to a manual token form on failure.

2. **Manual** — user enters host/port/token directly (works off HAOS too).

3. **Reauth** — when the bearer token is rejected (rotated, revoked).
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_BRIDGE_ID,
    CONF_BRIDGE_NAME,
    CONF_HOST,
    CONF_PORT,
    CONF_TOKEN,
    CONF_USE_HTTPS,
    DEFAULT_PORT,
    DOMAIN,
    ENDPOINT_HA_PAIR,
    ENDPOINT_HA_STATE,
)

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigFlowResult
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

_LOGGER = logging.getLogger(__name__)


async def _validate_token(
    hass, host: str, port: int, token: str, use_https: bool = False
) -> tuple[bool, dict[str, Any] | None]:
    """Try to fetch /api/ha/state with the candidate token.

    Returns ``(success, projection)``.  Projection is None when validation
    failed.
    """
    scheme = "https" if use_https else "http"
    url = f"{scheme}://{host}:{port}{ENDPOINT_HA_STATE}"
    session = async_get_clientsession(hass)
    try:
        async with session.get(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return False, None
            return True, await resp.json()
    except aiohttp.ClientError:
        return False, None


async def _attempt_supervisor_pair(hass, host: str, port: int, use_https: bool) -> str | None:
    """Try to mint a token via the bridge's Supervisor pair endpoint.

    Only succeeds when HA's running on HAOS and the bridge is reachable
    through the Supervisor proxy chain.
    """
    if not os.environ.get("SUPERVISOR_TOKEN"):
        return None
    scheme = "https" if use_https else "http"
    url = f"{scheme}://{host}:{port}{ENDPOINT_HA_PAIR}"
    session = async_get_clientsession(hass)
    try:
        async with session.post(
            url,
            headers={"X-Ingress-Path": "/api/auth/ha-pair"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                return None
            body = await resp.json()
            return body.get("token") if isinstance(body, dict) else None
    except aiohttp.ClientError:
        return None


class SendspinBridgeConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):  # type: ignore[call-arg]
    """Handle config flow for Sendspin BT Bridge."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered: dict[str, Any] = {}

    # -- Manual entry ---------------------------------------------------

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Handle the manual user-init step.

        Token is *optional* in the form: if the user leaves it blank
        and the bridge is reachable, we try the same Supervisor pair
        flow that the zeroconf path uses — on HAOS the bridge mints
        a token via its ``/api/auth/ha-pair`` endpoint without any
        further user interaction.  Otherwise the form re-renders
        with a clear error pointing at the Generate Token button.
        """
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input[CONF_HOST]
            port = int(user_input.get(CONF_PORT, DEFAULT_PORT))
            use_https = bool(user_input.get(CONF_USE_HTTPS, False))
            token = (user_input.get(CONF_TOKEN) or "").strip()

            if not token:
                token = await _attempt_supervisor_pair(self.hass, host, port, use_https=use_https) or ""
                if not token:
                    errors["base"] = "auto_pair_failed"

            ok = False
            projection: dict[str, Any] | None = None
            if token and not errors:
                ok, projection = await _validate_token(self.hass, host, port, token, use_https)
                if not ok:
                    errors["base"] = "auth_failed"

            if ok and token:
                bridge_meta = (projection or {}).get("bridge_meta") or {}
                bridge_id = bridge_meta.get("bridge_id") or host
                bridge_name = bridge_meta.get("bridge_name") or "Sendspin Bridge"
                await self.async_set_unique_id(f"sendspin_bridge_{bridge_id}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=bridge_name,
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_TOKEN: token,
                        CONF_USE_HTTPS: use_https,
                        CONF_BRIDGE_ID: bridge_id,
                        CONF_BRIDGE_NAME: bridge_name,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Optional(CONF_TOKEN, default=""): str,
                vol.Optional(CONF_USE_HTTPS, default=False): bool,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    # -- Zeroconf discovery --------------------------------------------

    async def async_step_zeroconf(self, discovery_info: ZeroconfServiceInfo) -> ConfigFlowResult:
        """Handle a discovered bridge."""
        host = str(discovery_info.host or discovery_info.ip_address or "")
        port = int(discovery_info.port or DEFAULT_PORT)
        properties = dict(discovery_info.properties or {})
        host_id = str(properties.get("host_id") or "")
        if not host_id:
            return self.async_abort(reason="missing_host_id")

        await self.async_set_unique_id(f"sendspin_bridge_{host_id}")
        self._abort_if_unique_id_configured(updates={CONF_HOST: host, CONF_PORT: port})

        self._discovered = {
            CONF_HOST: host,
            CONF_PORT: port,
            CONF_BRIDGE_ID: host_id,
        }
        # Try one-click Supervisor pairing first.
        token = await _attempt_supervisor_pair(self.hass, host, port, use_https=False)
        if token:
            ok, projection = await _validate_token(self.hass, host, port, token, False)
            if ok:
                bridge_meta = (projection or {}).get("bridge_meta") or {}
                bridge_name = bridge_meta.get("bridge_name") or "Sendspin Bridge"
                return self.async_create_entry(
                    title=bridge_name,
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_TOKEN: token,
                        CONF_USE_HTTPS: False,
                        CONF_BRIDGE_ID: host_id,
                        CONF_BRIDGE_NAME: bridge_name,
                    },
                )
        # Fall through to manual confirmation if supervisor pair didn't work.
        return await self.async_step_pair()

    async def async_step_pair(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Manual token entry confirmation step after discovery."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = self._discovered[CONF_HOST]
            port = self._discovered[CONF_PORT]
            token = user_input[CONF_TOKEN]
            use_https = user_input.get(CONF_USE_HTTPS, False)
            ok, projection = await _validate_token(self.hass, host, port, token, use_https)
            if not ok:
                errors["base"] = "auth_failed"
            else:
                bridge_meta = (projection or {}).get("bridge_meta") or {}
                bridge_name = bridge_meta.get("bridge_name") or "Sendspin Bridge"
                return self.async_create_entry(
                    title=bridge_name,
                    data={
                        CONF_HOST: host,
                        CONF_PORT: port,
                        CONF_TOKEN: token,
                        CONF_USE_HTTPS: use_https,
                        CONF_BRIDGE_ID: self._discovered[CONF_BRIDGE_ID],
                        CONF_BRIDGE_NAME: bridge_name,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(CONF_TOKEN): str,
                vol.Optional(CONF_USE_HTTPS, default=False): bool,
            }
        )
        host = self._discovered.get(CONF_HOST, "")
        port = self._discovered.get(CONF_PORT, "")
        return self.async_show_form(
            step_id="pair",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "host": host,
                "port": str(port),
                "ui_url": f"http://{host}:{port}/" if host else "",
            },
        )

    # -- Reauth ---------------------------------------------------------

    async def async_step_reauth(self, _user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if user_input is not None and entry is not None:
            ok, _ = await _validate_token(
                self.hass,
                entry.data[CONF_HOST],
                entry.data.get(CONF_PORT, DEFAULT_PORT),
                user_input[CONF_TOKEN],
                entry.data.get(CONF_USE_HTTPS, False),
            )
            if not ok:
                errors["base"] = "auth_failed"
            else:
                self.hass.config_entries.async_update_entry(
                    entry, data={**entry.data, CONF_TOKEN: user_input[CONF_TOKEN]}
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        schema = vol.Schema({vol.Required(CONF_TOKEN): str})
        host = entry.data[CONF_HOST] if entry else ""
        port = entry.data.get(CONF_PORT, DEFAULT_PORT) if entry else DEFAULT_PORT
        scheme = "https" if (entry and entry.data.get(CONF_USE_HTTPS, False)) else "http"
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            errors=errors,
            description_placeholders={
                "host": host,
                "port": str(port),
                "ui_url": f"{scheme}://{host}:{port}/" if host else "",
            },
        )
