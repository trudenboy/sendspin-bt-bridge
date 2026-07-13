"""Tests for the HACS coordinator's SSE reconnect + reauth behaviour.

The custom_component imports the Home Assistant runtime, which isn't available
in the bridge test env, so we stub the HA modules the coordinator touches and
load ``coordinator.py`` standalone (same approach as the config-flow tests).
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[3]


def _load_coordinator():
    package_names = {"homeassistant", "homeassistant.helpers"}
    for mod_name in (
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.core",
        "homeassistant.exceptions",
        "homeassistant.helpers",
        "homeassistant.helpers.aiohttp_client",
        "homeassistant.helpers.update_coordinator",
    ):
        if mod_name not in sys.modules:
            mod = types.ModuleType(mod_name)
            if mod_name in package_names:
                mod.__path__ = []
            sys.modules[mod_name] = mod

    class _ConfigEntryAuthFailed(Exception):
        pass

    class _UpdateFailed(Exception):
        pass

    class _DataUpdateCoordinator:
        def __init__(self, hass, logger, *, name=None, update_interval=None):
            self.hass = hass
            self.logger = logger
            self.name = name
            self.update_interval = update_interval
            self.last_update_success = True
            self.data = None

        def __class_getitem__(cls, _item):
            return cls

        async def async_request_refresh(self):
            return None

        def async_update_listeners(self):
            return None

    sys.modules["homeassistant.exceptions"].ConfigEntryAuthFailed = _ConfigEntryAuthFailed
    sys.modules["homeassistant.helpers.update_coordinator"].DataUpdateCoordinator = _DataUpdateCoordinator
    sys.modules["homeassistant.helpers.update_coordinator"].UpdateFailed = _UpdateFailed
    sys.modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = MagicMock()

    # Load .const directly (no HA imports there).
    const_path = ROOT / "custom_components" / "sendspin_bridge" / "const.py"
    spec = importlib.util.spec_from_file_location("cc_const_coord_test", const_path)
    const_mod = importlib.util.module_from_spec(spec)
    sys.modules["cc_const_coord_test"] = const_mod
    spec.loader.exec_module(const_mod)

    pkg = types.ModuleType("coord_test_pkg")
    pkg.__path__ = []
    pkg.const = const_mod
    sys.modules["coord_test_pkg"] = pkg
    sys.modules["coord_test_pkg.const"] = const_mod

    path = ROOT / "custom_components" / "sendspin_bridge" / "coordinator.py"
    src = path.read_text(encoding="utf-8").replace("from .const", "from coord_test_pkg.const")
    spec = importlib.util.spec_from_loader("coord_test_module", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules["coord_test_module"] = module
    exec(compile(src, str(path), "exec"), module.__dict__)
    return module


_mod = _load_coordinator()
SendspinDataUpdateCoordinator = _mod.SendspinDataUpdateCoordinator


def _make_coordinator():
    scheduled = []

    hass = MagicMock()

    def _create_task(coro, *a, **k):
        # Close the coroutine so we don't leak an un-awaited warning.
        if asyncio.iscoroutine(coro):
            coro.close()
        scheduled.append(coro)
        return MagicMock()

    hass.async_create_task.side_effect = _create_task

    entry = MagicMock()
    entry.entry_id = "abcd1234efgh"
    entry.data = {"host": "192.168.1.10", "port": 8080, "token": "tok", "use_https": False}

    coord = SendspinDataUpdateCoordinator(hass, entry)
    return coord, entry, hass, scheduled


def test_slow_poll_interval_is_set_as_safety_net():
    coord, _entry, _hass, _scheduled = _make_coordinator()
    assert coord.update_interval == _mod._SLOW_POLL_INTERVAL
    assert coord.update_interval is not None


def test_auth_failure_starts_reauth():
    """A rejected bearer token must trigger HA's reauth flow, not a silent
    freeze."""
    coord, entry, hass, _scheduled = _make_coordinator()
    coord._async_log_auth_failure()
    entry.async_start_reauth.assert_called_once_with(hass)


@pytest.mark.asyncio
async def test_sse_connect_refreshes_even_without_events():
    """After a (re)connect the coordinator must pull a fresh snapshot even if
    no event arrives — otherwise entities stay stale after a bridge restart."""
    coord, _entry, _hass, scheduled = _make_coordinator()

    class _Content:
        def __aiter__(self):
            return self

        async def __anext__(self):
            # No events; end the stream and stop the loop so _run_sse returns.
            coord._stopped.set()
            raise StopAsyncIteration

    class _Resp:
        status = 200
        content = _Content()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

    class _Session:
        def get(self, *_a, **_k):
            return _Resp()

    _mod.async_get_clientsession = MagicMock(return_value=_Session())

    await asyncio.wait_for(coord._run_sse(), timeout=2)

    # A refresh coroutine was scheduled on connect, with zero events delivered.
    assert scheduled, "no refresh scheduled on SSE (re)connect"
