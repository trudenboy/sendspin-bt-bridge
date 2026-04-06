"""Tests for services.sendspin_port_probe."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from services.sendspin_port_probe import probe_sendspin_port


@pytest.mark.asyncio
async def test_probe_returns_default_port_when_it_responds():
    """When the default port accepts TCP, return it immediately."""
    with patch("services.sendspin_port_probe._tcp_probe", new_callable=AsyncMock) as mock_probe:
        mock_probe.return_value = True
        result = await probe_sendspin_port("192.168.1.10", default_port=9000)
        assert result == 9000
        mock_probe.assert_awaited_once_with("192.168.1.10", 9000, 3.0)


@pytest.mark.asyncio
async def test_probe_returns_alternative_when_default_fails():
    """When default port fails, try alternatives and return the first responding."""

    async def _probe_side_effect(host, port, timeout):
        return port == 8927

    with patch("services.sendspin_port_probe._tcp_probe", side_effect=_probe_side_effect):
        result = await probe_sendspin_port("192.168.1.10", default_port=9000)
        assert result == 8927


@pytest.mark.asyncio
async def test_probe_returns_none_when_no_port_responds():
    """When no candidate port responds, return None."""
    with patch("services.sendspin_port_probe._tcp_probe", new_callable=AsyncMock) as mock_probe:
        mock_probe.return_value = False
        result = await probe_sendspin_port("192.168.1.10", default_port=9000)
        assert result is None


@pytest.mark.asyncio
async def test_probe_returns_none_for_empty_host():
    """Empty host should return None without probing."""
    result = await probe_sendspin_port("", default_port=9000)
    assert result is None


@pytest.mark.asyncio
async def test_probe_deduplicates_default_port():
    """Default port should not be tried twice if it's also in candidates."""
    calls = []

    async def _probe_side_effect(host, port, timeout):
        calls.append(port)
        return False

    with patch("services.sendspin_port_probe._tcp_probe", side_effect=_probe_side_effect):
        await probe_sendspin_port("192.168.1.10", default_port=9000, candidates=(9000, 8927))
    assert calls == [9000, 8927]


@pytest.mark.asyncio
async def test_probe_port_if_default_returns_none_on_exception():
    """_probe_port_if_default should return None on any exception."""
    from sendspin_client import _probe_port_if_default

    with patch("sendspin_client.probe_sendspin_port", side_effect=RuntimeError("boom")):
        result = await _probe_port_if_default("192.168.1.10", 9000)
    assert result is None
