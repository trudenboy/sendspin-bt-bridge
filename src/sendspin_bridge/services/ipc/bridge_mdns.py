"""Self-publish the bridge as a Zeroconf service for HA discovery.

Service: ``_sendspin-bridge._tcp.local.`` — when the HA custom_component
is installed, HA's Zeroconf integration sees this advertisement and
prompts the user to add the bridge as a new integration.

TXT records expose enough metadata for HA's config_flow to validate the
bridge identity without a round-trip:

  * ``version``   — bridge software version
  * ``host_id``   — stable bridge identifier (HMAC of bridge_name)
  * ``web_port``  — port the bridge web UI listens on
  * ``auth``      — accepted auth scheme(s) (``bearer``)
  * ``ingress``   — ``1`` when running behind HA Supervisor ingress

The advertisement is best-effort: failures (no zeroconf, no network) log
at INFO and skip — the bridge does not crash if it can't advertise.
"""

from __future__ import annotations

import hashlib
import logging
import socket
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_SERVICE_TYPE = "_sendspin-bridge._tcp.local."


@dataclass
class MdnsAdvertisement:
    """Minimal record for diagnostics + idempotent re-registration."""

    service_name: str
    host_id: str
    port: int
    txt_records: dict[str, str]


def _resolve_host_address() -> str:
    """Best-effort local host address for the SRV record.

    Returns ``0.0.0.0`` when nothing better is available — most operators
    are on a LAN where Zeroconf will advertise on whatever interface is
    listening.  We avoid forcing a specific interface here because the
    bridge often runs on hosts with multiple BT-capable adapters.
    """
    try:
        host = socket.gethostbyname(socket.gethostname())
    except OSError:
        host = ""
    return host or "0.0.0.0"


def _derive_host_id(bridge_name: str) -> str:
    """Stable per-bridge identifier — same algorithm as the MQTT publisher."""
    return hashlib.sha1((bridge_name or "sendspin-bridge").encode("utf-8")).hexdigest()[:12]


class BridgeMdnsAdvertiser:
    """Owner of the Zeroconf registration for the bridge web UI.

    Lifecycle: ``await start()`` registers, ``await stop()`` unregisters.
    Re-registration on config change is implemented by ``stop() + start()``
    rather than mutating in place — Zeroconf's update model is finicky and
    a round-trip is cheap (sub-second).
    """

    def __init__(
        self,
        *,
        bridge_name: str,
        version: str,
        web_port: int,
        ingress_active: bool,
        host_override: str = "",
        port_override: int = 0,
    ) -> None:
        self._bridge_name = bridge_name or "Sendspin Bridge"
        self._version = version or ""
        self._web_port = int(web_port or 8080)
        self._ingress_active = bool(ingress_active)
        # Optional explicit overrides for setups behind a reverse proxy or
        # NAT — empty string / 0 means "auto-resolve".  When set the value
        # is published in the mDNS SRV/TXT records so HA Core's Zeroconf
        # discovery hands the HACS integration a reachable address even
        # when the bridge's own hostname is not directly routable from HA.
        self._host_override = (host_override or "").strip()
        try:
            self._port_override = int(port_override or 0)
        except (TypeError, ValueError):
            self._port_override = 0
        self._zc: Any = None
        self._info: Any = None
        self._advertisement: MdnsAdvertisement | None = None

    @property
    def advertisement(self) -> MdnsAdvertisement | None:
        return self._advertisement

    async def start(self) -> None:
        try:
            from zeroconf import ServiceInfo
            from zeroconf.asyncio import AsyncZeroconf
        except ImportError:
            logger.info("Bridge mDNS: zeroconf not installed; skipping advertisement")
            return

        host_id = _derive_host_id(self._bridge_name)
        instance_name = f"sendspin-bridge-{host_id}._sendspin-bridge._tcp.local."
        host = self._host_override or _resolve_host_address()
        advertised_port = self._port_override if self._port_override > 0 else self._web_port

        txt: dict[str, str] = {
            "version": self._version,
            "host_id": host_id,
            "web_port": str(advertised_port),
            "auth": "bearer",
            "ingress": "1" if self._ingress_active else "0",
        }
        # zeroconf wants bytes for the property values.
        properties = {k.encode("utf-8"): v.encode("utf-8") for k, v in txt.items()}

        try:
            # Pack only when ``host`` is actually a dotted-quad IPv4
            # literal — operator-supplied FQDN overrides (e.g.
            # ``bridge.example.com``) would crash ``inet_aton`` here, but
            # zeroconf is fine with an empty address list and a populated
            # ``server=`` field in that case.
            try:
                address_packed = socket.inet_aton(host) if host else b""
            except OSError:
                address_packed = b""
            addresses = [address_packed] if address_packed else []
            info = ServiceInfo(
                type_=_SERVICE_TYPE,
                name=instance_name,
                addresses=addresses,
                port=advertised_port,
                properties=properties,
                server=f"sendspin-bridge-{host_id}.local.",
            )
            self._zc = AsyncZeroconf()
            await self._zc.async_register_service(info)
            self._info = info
        except Exception as exc:
            logger.warning("Bridge mDNS: registration failed: %s", exc)
            await self._safe_close()
            return

        self._advertisement = MdnsAdvertisement(
            service_name=instance_name,
            host_id=host_id,
            port=advertised_port,
            txt_records=txt,
        )
        host_suffix = f" host={self._host_override}" if self._host_override else ""
        logger.info(
            "Bridge mDNS: advertised %s on :%s (host_id=%s)%s",
            _SERVICE_TYPE,
            advertised_port,
            host_id,
            host_suffix,
        )

    async def stop(self) -> None:
        if self._zc is None:
            return
        try:
            if self._info is not None:
                await self._zc.async_unregister_service(self._info)
        except Exception as exc:
            logger.debug("Bridge mDNS: unregister failed: %s", exc)
        await self._safe_close()
        self._advertisement = None

    async def _safe_close(self) -> None:
        try:
            if self._zc is not None:
                await self._zc.async_close()
        except Exception as exc:
            logger.debug("Bridge mDNS: close failed: %s", exc)
        finally:
            self._zc = None
            self._info = None


# ---------------------------------------------------------------------------
# Module-level singleton (one advertiser per bridge process)
# ---------------------------------------------------------------------------


_default_advertiser: BridgeMdnsAdvertiser | None = None


def get_default_advertiser() -> BridgeMdnsAdvertiser | None:
    return _default_advertiser


def set_default_advertiser(advertiser: BridgeMdnsAdvertiser | None) -> None:
    global _default_advertiser
    _default_advertiser = advertiser


__all__ = [
    "BridgeMdnsAdvertiser",
    "MdnsAdvertisement",
    "get_default_advertiser",
    "set_default_advertiser",
]
