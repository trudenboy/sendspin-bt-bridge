"""Local audio outputs: the inventory and who uses each sink."""

from __future__ import annotations

import asyncio
import logging

from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.models.audio import AudioSinkOut
from sendspin_bridge.application.runtime import run_on_loop
from sendspin_bridge.config import load_config
from sendspin_bridge.services.audio.sink_inventory import LocalSink, list_local_sinks

logger = logging.getLogger(__name__)

_READ_TIMEOUT_S = 3.0


async def list_sinks() -> list[AudioSinkOut]:
    """The local sinks (one inventory read), each with the pulse_sink player using it."""
    sinks = await list_local_sinks()
    config = await asyncio.to_thread(load_config)
    users: dict[str, str] = {}
    for player in config.get("players") or []:
        backend = player.get("backend") if isinstance(player, dict) else None
        if isinstance(backend, dict) and backend.get("type") == "pulse_sink" and backend.get("sink"):
            users.setdefault(backend["sink"], player.get("id"))
    return [
        AudioSinkOut(
            name=s.name,
            description=s.description,
            state=s.state,
            sample_spec=s.sample_spec,
            muted=s.muted,
            volume=s.volume,
            active_port_available=s.active_port_available,
            identifiers=dict(s.identifiers),
            in_use_by=users.get(s.name),
        )
        for s in sinks
    ]


def read_local_sinks() -> list[LocalSink]:
    """The sink inventory for a worker thread (a config save); empty when it cannot be read.

    Never call this while holding ``config_lock``: it waits on the bridge loop.
    """
    coro = list_local_sinks()
    try:
        return run_on_loop(coro, timeout=_READ_TIMEOUT_S, description="local sink inventory")
    except UseCaseError as exc:
        coro.close()  # never started when the bridge loop is not there
        if exc.code != "not_ready":
            logger.warning("Could not read the local sinks: %s", exc)
            return []
    try:
        fallback = list_local_sinks()
        try:
            return asyncio.run(fallback)
        finally:
            fallback.close()
    except Exception as exc:
        logger.warning("Could not read the local sinks: %s", exc)
        return []
