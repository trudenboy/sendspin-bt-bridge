"""mDNS discovery for Music Assistant servers on the local network."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_MA_SERVICE_TYPE = "_mass._tcp.local."
_DEFAULT_TIMEOUT = 5.0


async def discover_ma_servers(timeout: float = _DEFAULT_TIMEOUT) -> list[dict[str, Any]]:
    """Discover Music Assistant servers via mDNS.

    Returns a list of dicts with keys: url, server_id, version, server_name.
    Empty list if none found or zeroconf unavailable.
    """
    try:
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncZeroconf
    except ImportError:
        logger.warning("zeroconf not installed — MA discovery unavailable")
        return []

    found: dict[str, dict[str, Any]] = {}
    event = asyncio.Event()

    def _on_service_state_change(
        zeroconf: Any = None,
        service_type: str = "",
        name: str = "",
        state_change: Any = None,
        **_kwargs: Any,
    ) -> None:
        if state_change is ServiceStateChange.Added:
            asyncio.ensure_future(_resolve(zeroconf, service_type, name, found, event))

    async def _resolve(
        zc: Any,
        service_type: str,
        name: str,
        results: dict[str, dict[str, Any]],
        evt: asyncio.Event,
    ) -> None:
        info = await zc.async_get_service_info(service_type, name)
        if info is None:
            return

        addresses = info.parsed_scoped_addresses()
        if not addresses:
            return

        host = addresses[0]
        port = info.port or 8095
        props = {
            k.decode("utf-8", errors="replace"): v.decode("utf-8", errors="replace") if isinstance(v, bytes) else str(v)
            for k, v in (info.properties or {}).items()
        }

        url = f"http://{host}:{port}"
        entry = {
            "url": url,
            "server_name": info.server or name,
            "server_id": props.get("server_id", ""),
            "version": props.get("version", ""),
        }

        # Validate via /info endpoint (no auth required)
        entry = await _enrich_with_server_info(entry)

        results[url] = entry
        evt.set()
        logger.info("Discovered MA server: %s (v%s)", url, entry.get("version", "?"))

    azc = AsyncZeroconf()
    try:
        browser = AsyncServiceBrowser(  # noqa: F841
            azc.zeroconf,
            _MA_SERVICE_TYPE,
            handlers=[_on_service_state_change],
        )
        # Wait up to timeout for at least one result
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
        except TimeoutError:
            pass
        # Brief extra wait for additional servers
        if found:
            await asyncio.sleep(0.5)
    finally:
        await azc.async_close()

    return list(found.values())


async def _enrich_with_server_info(entry: dict[str, Any]) -> dict[str, Any]:
    """Call MA /info endpoint to get accurate server metadata."""
    try:
        from music_assistant_client import get_server_info

        info = await get_server_info(entry["url"])
        entry["version"] = info.server_version
        entry["server_id"] = info.server_id
        entry["schema_version"] = info.schema_version
        entry["onboard_done"] = info.onboard_done
        entry["homeassistant_addon"] = info.homeassistant_addon
        if info.base_url:
            entry["url"] = info.base_url
    except Exception:
        logger.debug("Could not reach MA /info at %s", entry["url"], exc_info=True)
    return entry


async def validate_ma_url(url: str) -> dict[str, Any] | None:
    """Validate a manually entered MA URL by calling /info.

    Returns server info dict on success, None on failure.
    """
    try:
        from music_assistant_client import get_server_info

        info = await get_server_info(url)
        return {
            "url": info.base_url or url,
            "version": info.server_version,
            "server_id": info.server_id,
            "schema_version": info.schema_version,
            "onboard_done": info.onboard_done,
            "homeassistant_addon": info.homeassistant_addon,
        }
    except Exception:
        logger.debug("MA server not reachable at %s", url, exc_info=True)
        return None
