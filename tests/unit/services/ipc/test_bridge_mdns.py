"""Tests for ``services/bridge_mdns.py``."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sendspin_bridge.services.ipc import bridge_mdns as M


def test_derive_host_id_is_stable():
    a = M._derive_host_id("HAOS Bridge")
    b = M._derive_host_id("HAOS Bridge")
    assert a == b
    assert len(a) == 12


def test_derive_host_id_differs_between_bridges():
    a = M._derive_host_id("Bridge A")
    b = M._derive_host_id("Bridge B")
    assert a != b


def test_derive_host_id_handles_empty_name():
    assert M._derive_host_id("") == M._derive_host_id("sendspin-bridge")
    assert len(M._derive_host_id("")) == 12


@pytest.mark.asyncio
async def test_start_registers_zeroconf_service_with_expected_txt():
    with (
        patch.object(M, "AsyncZeroconf", create=True)
        if False
        else patch("sendspin_bridge.services.ipc.bridge_mdns.socket.gethostbyname", return_value="192.168.1.10")
    ):
        adv = M.BridgeMdnsAdvertiser(
            bridge_name="HAOS Bridge",
            version="2.65.0",
            web_port=8080,
            ingress_active=True,
        )
        # Stub the zeroconf imports inline so we don't need a real network.
        fake_zc = MagicMock()
        fake_zc.async_register_service = AsyncMock()
        fake_zc.async_unregister_service = AsyncMock()
        fake_zc.async_close = AsyncMock()

        with patch("zeroconf.asyncio.AsyncZeroconf", return_value=fake_zc):
            await adv.start()
        assert adv.advertisement is not None
        assert adv.advertisement.txt_records["version"] == "2.65.0"
        assert adv.advertisement.txt_records["web_port"] == "8080"
        assert adv.advertisement.txt_records["auth"] == "bearer"
        assert adv.advertisement.txt_records["ingress"] == "1"
        assert adv.advertisement.host_id == M._derive_host_id("HAOS Bridge")
        # Service name uses the canonical type so HA's Zeroconf integration matches.
        assert adv.advertisement.service_name.endswith("_sendspin-bridge._tcp.local.")


@pytest.mark.asyncio
async def test_start_logs_and_skips_when_zeroconf_missing():
    """Bridge MUST NOT crash if zeroconf isn't installed."""

    real_import = __builtins__["__import__"] if isinstance(__builtins__, dict) else __builtins__.__import__

    def fake_import(name, *a, **kw):
        if name.startswith("zeroconf"):
            raise ImportError(f"mocked: {name}")
        return real_import(name, *a, **kw)

    adv = M.BridgeMdnsAdvertiser(
        bridge_name="x",
        version="2.65.0",
        web_port=8080,
        ingress_active=False,
    )
    with patch("builtins.__import__", side_effect=fake_import):
        await adv.start()  # must not raise
    assert adv.advertisement is None


@pytest.mark.asyncio
async def test_stop_unregisters_and_closes():
    adv = M.BridgeMdnsAdvertiser(
        bridge_name="x",
        version="2.65.0",
        web_port=8080,
        ingress_active=False,
    )
    fake_zc = MagicMock()
    fake_zc.async_register_service = AsyncMock()
    fake_zc.async_unregister_service = AsyncMock()
    fake_zc.async_close = AsyncMock()
    with patch("zeroconf.asyncio.AsyncZeroconf", return_value=fake_zc):
        await adv.start()
    await adv.stop()
    fake_zc.async_unregister_service.assert_awaited()
    fake_zc.async_close.assert_awaited()
    assert adv.advertisement is None


@pytest.mark.asyncio
async def test_stop_idle_advertiser_is_safe():
    adv = M.BridgeMdnsAdvertiser(
        bridge_name="x",
        version="2.65.0",
        web_port=8080,
        ingress_active=False,
    )
    await adv.stop()  # never started; must not raise
    assert adv.advertisement is None


def test_default_advertiser_set_get_round_trip():
    M.set_default_advertiser(None)
    assert M.get_default_advertiser() is None
    adv = M.BridgeMdnsAdvertiser(bridge_name="x", version="x", web_port=1, ingress_active=False)
    M.set_default_advertiser(adv)
    assert M.get_default_advertiser() is adv
    M.set_default_advertiser(None)
