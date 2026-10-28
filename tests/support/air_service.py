"""Spawn tests: a bridge started with the config's EXPERIMENTAL_AIR_SENSOR.

The ``air_sensor`` spawn param follows the running air sensor service (the switch needs a
restart), so a spawn test that means "switch on" installs a stand-in service.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any


def bridge_started_with(monkeypatch: Any, config: dict) -> None:
    """The air sensor service runs iff the config's switch is on (call it on the test's loop)."""
    from sendspin_bridge.services.bluetooth import air_sensor_service as air_mod

    fake = SimpleNamespace(loop=asyncio.get_running_loop(), subscribe=lambda _m: None, unsubscribe=lambda _m: None)
    monkeypatch.setattr(air_mod, "_current", fake if config.get("EXPERIMENTAL_AIR_SENSOR") else None)
