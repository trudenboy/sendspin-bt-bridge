"""Tests for ``_resolve_user_facing_url`` — the CTA-link helper.

When the bridge runs as an HAOS addon, HA Core's zeroconf discovery
hands the custom_component a Supervisor-internal host like
``172.30.32.1:62144``.  That URL is reachable from HA Core but NOT
from the operator's browser, so a CTA-link in the config-flow
dialog with that URL would 404.

The helper must translate Supervisor-internal hosts to the addon's
HA Frontend ingress URL (``/api/hassio_ingress/<token>/``), which IS
user-reachable.  Standalone deployments advertise their real LAN IP
via mDNS, so ``http://host:port/`` works without translation.

The custom_component package's ``__init__.py`` imports the
homeassistant runtime, which isn't available in the bridge test env,
so we load ``config_flow`` directly via importlib.
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[3]


def _load_config_flow():
    """Load ``config_flow.py`` standalone.

    Stub every external runtime the file imports at top so we don't
    drag HA Core into the bridge test env.
    """
    # Create stub modules for HA + voluptuous imports.
    for mod_name in (
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.helpers",
        "homeassistant.helpers.aiohttp_client",
        "homeassistant.helpers.network",
        "homeassistant.helpers.service_info",
        "homeassistant.helpers.service_info.zeroconf",
        "voluptuous",
    ):
        if mod_name not in sys.modules:
            sys.modules[mod_name] = types.ModuleType(mod_name)
    sys.modules["homeassistant.config_entries"].ConfigFlow = type(
        "ConfigFlow", (), {"__init_subclass__": classmethod(lambda cls, **k: None)}
    )
    sys.modules["homeassistant.config_entries"].ConfigFlowResult = dict
    sys.modules["homeassistant.helpers.aiohttp_client"].async_get_clientsession = MagicMock()

    class _NoURLAvailableError(Exception):
        pass

    sys.modules["homeassistant.helpers.network"].NoURLAvailableError = _NoURLAvailableError
    sys.modules["homeassistant.helpers.network"].get_url = MagicMock(side_effect=_NoURLAvailableError)
    # voluptuous: only ``Schema``, ``Required``, ``Optional`` are referenced.
    vol = sys.modules["voluptuous"]
    vol.Schema = lambda x: x
    vol.Required = lambda x: x
    vol.Optional = lambda *a, **k: a[0] if a else None

    # Stub the .const module that config_flow imports relatively.
    const_path = ROOT / "custom_components" / "sendspin_bridge" / "const.py"
    spec = importlib.util.spec_from_file_location("cc_const_test", const_path)
    const_mod = importlib.util.module_from_spec(spec)
    sys.modules["cc_const_test"] = const_mod
    spec.loader.exec_module(const_mod)

    # Build a fake ``custom_components.sendspin_bridge`` package whose
    # ``.const`` resolves to the stub above.
    pkg = types.ModuleType("cf_test_pkg")
    pkg.__path__ = []
    pkg.const = const_mod
    sys.modules["cf_test_pkg"] = pkg
    sys.modules["cf_test_pkg.const"] = const_mod

    # Read the file, rewrite the relative import to point at our pkg,
    # exec it.  Simpler than dancing with importlib for relative imports.
    path = ROOT / "custom_components" / "sendspin_bridge" / "config_flow.py"
    src = path.read_text(encoding="utf-8").replace("from .const", "from cf_test_pkg.const")
    spec = importlib.util.spec_from_loader("cf_test_module", loader=None)
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules["cf_test_module"] = module
    exec(compile(src, str(path), "exec"), module.__dict__)
    return module


_cf = _load_config_flow()
_resolve_user_facing_url = _cf._resolve_user_facing_url


class _FakeResponse:
    def __init__(self, status: int, payload: dict):
        self.status = status
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def json(self):
        return self._payload


class _FakeSession:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self._status = status
        self.calls: list[str] = []

    def get(self, url, **_):
        self.calls.append(url)
        return _FakeResponse(self._status, self._payload)


# ---------------------------------------------------------------------------
# Standalone path — host is a real LAN IP, no Supervisor lookup needed.
# ---------------------------------------------------------------------------


def test_lan_host_returns_plain_http_url(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    hass = MagicMock()
    url = asyncio.run(_resolve_user_facing_url(hass, "192.168.10.10", 8080))
    assert url == "http://192.168.10.10:8080/"


def test_lan_host_with_https_keeps_scheme(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    hass = MagicMock()
    url = asyncio.run(_resolve_user_facing_url(hass, "bridge.local", 8443, use_https=True))
    assert url == "https://bridge.local:8443/"


def test_supervisor_token_present_but_lan_host_skips_lookup(monkeypatch):
    """No Supervisor lookup for an off-network host even on HAOS."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    hass = MagicMock()
    session = _FakeSession({})
    monkeypatch.setattr(_cf, "async_get_clientsession", lambda h: session)
    url = asyncio.run(_resolve_user_facing_url(hass, "10.0.0.5", 8080))
    assert url == "http://10.0.0.5:8080/"
    # Supervisor was NOT queried.
    assert session.calls == []


# ---------------------------------------------------------------------------
# HAOS path — Supervisor-internal host, swap for ingress URL.
# ---------------------------------------------------------------------------


def test_supervisor_host_maps_to_ingress_url(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    hass = MagicMock()
    session = _FakeSession(
        {
            "data": {
                "addons": [
                    {
                        "slug": "core_mosquitto",
                        "ingress_port": 1883,
                        "ingress_url": "/api/hassio_ingress/m_token/",
                    },
                    {
                        "slug": "85b1ecde_sendspin_bt_bridge",
                        "ingress_port": 62144,
                        "ingress_url": "/api/hassio_ingress/W9Jplf4BZFm8PGkdTnP4l5K_xMmq490UFnlopJ_bbjU/",
                    },
                ]
            }
        }
    )
    monkeypatch.setattr(_cf, "async_get_clientsession", lambda h: session)
    url = asyncio.run(_resolve_user_facing_url(hass, "172.30.32.1", 62144))
    assert url == "/api/hassio_ingress/W9Jplf4BZFm8PGkdTnP4l5K_xMmq490UFnlopJ_bbjU/"
    assert session.calls == ["http://supervisor/addons"]


def test_supervisor_host_falls_back_when_addon_not_found(monkeypatch):
    """Discovered host is on hassio network but no addon matches the
    port — fall back to plain http://host:port/."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    hass = MagicMock()
    session = _FakeSession({"data": {"addons": [{"slug": "other", "ingress_port": 99}]}})
    monkeypatch.setattr(_cf, "async_get_clientsession", lambda h: session)
    url = asyncio.run(_resolve_user_facing_url(hass, "172.30.32.1", 62144))
    assert url == "http://172.30.32.1:62144/"


def test_supervisor_query_failure_falls_back(monkeypatch):
    """Supervisor unreachable / non-200 — same fallback."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    hass = MagicMock()
    session = _FakeSession({}, status=502)
    monkeypatch.setattr(_cf, "async_get_clientsession", lambda h: session)
    url = asyncio.run(_resolve_user_facing_url(hass, "172.30.32.1", 62144))
    assert url == "http://172.30.32.1:62144/"


# ---------------------------------------------------------------------------
# When HA Frontend URL is configured, prepend it so the markdown link in
# the form description shows the operator's actual HA hostname instead of
# a bare ingress path.
# ---------------------------------------------------------------------------


def test_supervisor_host_with_ha_frontend_url_returns_absolute(monkeypatch):
    """``get_url(hass)`` succeeds → absolute URL with HA hostname."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    hass = MagicMock()
    session = _FakeSession(
        {
            "data": {
                "addons": [
                    {
                        "slug": "85b1ecde_sendspin_bt_bridge",
                        "ingress_port": 62144,
                        "ingress_url": "/api/hassio_ingress/abc123/",
                    },
                ]
            }
        }
    )
    monkeypatch.setattr(_cf, "async_get_clientsession", lambda h: session)
    monkeypatch.setattr(_cf, "get_url", lambda h, **kw: "https://ha.example.com/")
    url = asyncio.run(_resolve_user_facing_url(hass, "172.30.32.1", 62144))
    assert url == "https://ha.example.com/api/hassio_ingress/abc123/"


def test_supervisor_host_no_ha_url_falls_back_to_path_only(monkeypatch):
    """``get_url`` raises NoURLAvailableError → return path-only ingress."""
    monkeypatch.setenv("SUPERVISOR_TOKEN", "tok")
    hass = MagicMock()
    session = _FakeSession(
        {
            "data": {
                "addons": [
                    {
                        "slug": "85b1ecde_sendspin_bt_bridge",
                        "ingress_port": 62144,
                        "ingress_url": "/api/hassio_ingress/abc123/",
                    },
                ]
            }
        }
    )
    monkeypatch.setattr(_cf, "async_get_clientsession", lambda h: session)

    def _raise(*_a, **_k):
        raise _cf.NoURLAvailableError

    monkeypatch.setattr(_cf, "get_url", _raise)
    url = asyncio.run(_resolve_user_facing_url(hass, "172.30.32.1", 62144))
    assert url == "/api/hassio_ingress/abc123/"


# ---------------------------------------------------------------------------
# Standalone with mDNS hostname — prefer the friendly hostname over the IP.
# ---------------------------------------------------------------------------


def test_lan_host_prefers_mdns_display_host(monkeypatch):
    """``display_host`` (from zeroconf SRV target) wins over raw IP."""
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    hass = MagicMock()
    url = asyncio.run(_resolve_user_facing_url(hass, "192.168.10.10", 8080, display_host="bridge-7af3.local"))
    assert url == "http://bridge-7af3.local:8080/"


def test_lan_host_display_host_none_keeps_ip(monkeypatch):
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    hass = MagicMock()
    url = asyncio.run(_resolve_user_facing_url(hass, "192.168.10.10", 8080, display_host=None))
    assert url == "http://192.168.10.10:8080/"
