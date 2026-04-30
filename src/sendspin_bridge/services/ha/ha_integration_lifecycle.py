"""Lifecycle owner for the HA integration subsystem (MQTT publisher + mDNS).

Sits between ``bridge_orchestrator`` (which assembles runtime tasks at
startup) and ``reconfig_orchestrator`` (which dispatches config-change
actions).  Owns the long-lived ``HaMqttPublisher`` task and exposes a
small API for start / stop / reload that both call sites use.

This module is intentionally tiny: it only orchestrates other modules
and has no business logic of its own.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sendspin_bridge.config import load_config
from sendspin_bridge.services.ha.ha_addon import get_mqtt_addon_credentials
from sendspin_bridge.services.ha.ha_command_dispatcher import get_default_dispatcher
from sendspin_bridge.services.ha.ha_mqtt_publisher import HaMqttPublisher, resolve_mqtt_config

if TYPE_CHECKING:
    from sendspin_bridge.services.diagnostics.internal_events import InternalEventPublisher

logger = logging.getLogger(__name__)


class HaIntegrationLifecycle:
    """Singleton-style holder for the HA-integration runtime task.

    Construct once at boot (``bridge_orchestrator``).  Call ``start()`` to
    kick off the publisher, ``reload()`` after a config diff applies the
    HA_INTEGRATION block, and ``stop()`` on bridge shutdown.
    """

    def __init__(
        self,
        *,
        event_publisher: InternalEventPublisher,
        projection_provider,  # Callable[[], HAStateProjection]
        bridge_id_provider,  # Callable[[], str]
        bridge_name_provider,  # Callable[[], str]
    ) -> None:
        self._event_publisher = event_publisher
        self._projection_provider = projection_provider
        self._bridge_id_provider = bridge_id_provider
        self._bridge_name_provider = bridge_name_provider
        self._publisher: HaMqttPublisher | None = None

    # -- public API -----------------------------------------------------

    def start(self) -> HaMqttPublisher | None:
        """Start the publisher task if HA_INTEGRATION is enabled in config.

        Returns the publisher (which carries the asyncio task) so callers
        can register it with their gather() set; or None when disabled.
        """
        if self._publisher is not None:
            return self._publisher
        publisher = self._build_publisher()
        if publisher is None:
            logger.info("HA integration disabled (no MQTT config); not starting publisher")
            return None
        publisher.start()
        self._publisher = publisher
        return publisher

    async def reload(self) -> None:
        """Stop + re-create the publisher with the latest config.

        Called by ``reconfig_orchestrator`` when an
        ``HA_INTEGRATION_LIFECYCLE`` action arrives.  Always cleanly
        terminates the existing task before starting a new one so a
        broker-URL change cannot leave two publishers fighting for the
        same topic namespace.
        """
        if self._publisher is not None:
            await self._publisher.stop()
            self._publisher = None
        publisher = self._build_publisher()
        if publisher is None:
            logger.info("HA integration disabled after reload")
            return
        publisher.start()
        self._publisher = publisher

    async def stop(self) -> None:
        if self._publisher is None:
            return
        await self._publisher.stop()
        self._publisher = None

    @property
    def publisher(self) -> HaMqttPublisher | None:
        return self._publisher

    # -- internals ------------------------------------------------------

    def _build_publisher(self) -> HaMqttPublisher | None:
        config = load_config()
        block = config.get("HA_INTEGRATION") or {}
        ma_api_url = str(config.get("MA_API_URL") or "").strip()
        bridge_id = self._bridge_id_provider() or "bridge"
        bridge_name = self._bridge_name_provider() or "Sendspin Bridge"

        cfg = resolve_mqtt_config(
            block,
            bridge_id=bridge_id,
            bridge_name=bridge_name,
            auto_lookup=get_mqtt_addon_credentials,
            ma_api_url=ma_api_url,
        )
        if cfg is None:
            return None

        # Closure-style providers so the publisher always sees the latest
        # state on reconnect / heartbeat republish.  ``config_provider``
        # re-reads ``config.json`` from disk on every call and re-extracts
        # MA_API_URL so a mid-session change is picked up by the
        # standalone-fallback resolver.  The publisher only invokes this
        # at reconnect / heartbeat boundaries (single-digit Hz at most),
        # so the per-tick disk read is negligible.
        def _config_provider():
            cfg_now = load_config()
            return resolve_mqtt_config(
                cfg_now.get("HA_INTEGRATION") or {},
                bridge_id=bridge_id,
                bridge_name=bridge_name,
                auto_lookup=get_mqtt_addon_credentials,
                ma_api_url=str(cfg_now.get("MA_API_URL") or "").strip(),
            )

        return HaMqttPublisher(
            config_provider=_config_provider,
            projection_provider=self._projection_provider,
            dispatcher=get_default_dispatcher(),
            event_subscribe=self._event_publisher.subscribe,
        )


# Module-level singleton (created by bridge_orchestrator on boot).
_default_lifecycle: HaIntegrationLifecycle | None = None


def set_default_lifecycle(lifecycle: HaIntegrationLifecycle | None) -> None:
    global _default_lifecycle
    _default_lifecycle = lifecycle


def get_default_lifecycle() -> HaIntegrationLifecycle | None:
    return _default_lifecycle


__all__ = [
    "HaIntegrationLifecycle",
    "get_default_lifecycle",
    "set_default_lifecycle",
]
