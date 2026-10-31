"""Runtime owner of the Sendspin source: start and stop it without restarting the bridge (#563)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_source: Any = None


def running() -> bool:
    return _source is not None


async def start(config: dict[str, Any], config_dir: Any) -> dict[str, Any]:
    """Start the source from the bridge config; a second call is a no-op."""
    global _source
    from dataclasses import replace

    from sendspin_bridge.services.source.bt_source import BtSendspinSource, SourceConfig

    if _source is not None:
        return {"started": True, "already": True}
    configuration = SourceConfig.from_env()
    port = config.get("SENDSPIN_SOURCE_PORT")
    if port:
        configuration = replace(configuration, port=int(port))
    source = BtSendspinSource(
        configuration,
        identity_path=config_dir / "source_identity.key",
        pairing_path=config_dir / "source_pairing.json",
    )
    from sendspin_bridge.services.source import relay

    relay.configure(config_dir / "source_relays.json")
    relay.load()
    await source.start()
    _source = source
    relay.start_watcher()
    from sendspin_bridge.services.source.pairing import ensure_agent

    await ensure_agent()
    return {"started": True, "port": configuration.port}


async def stop() -> dict[str, Any]:
    global _source
    source, _source = _source, None
    if source is None:
        return {"started": False, "already": True}
    await source.stop()
    from sendspin_bridge.services.source import relay
    from sendspin_bridge.services.source.pairing import close_agent

    await relay.stop_watcher()
    await relay.stop_all()

    await close_agent()
    return {"started": False}
