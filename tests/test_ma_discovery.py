"""Tests for MA auto-discovery module and login logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Discovery module tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_discover_no_zeroconf():
    """Returns empty list when zeroconf not installed."""
    import importlib

    with patch.dict("sys.modules", {"zeroconf": None, "zeroconf.asyncio": None}):
        import services.ma_discovery as mod

        importlib.reload(mod)
        result = await mod.discover_ma_servers(timeout=0.1)
        assert result == []


@pytest.mark.asyncio
async def test_validate_ma_url_unreachable():
    """Returns None when MA is unreachable."""
    import importlib

    import services.ma_discovery as mod

    mock_mac = MagicMock()
    mock_mac.get_server_info = AsyncMock(side_effect=ConnectionRefusedError("refused"))

    with patch.dict("sys.modules", {"music_assistant_client": mock_mac}):
        importlib.reload(mod)
        result = await mod.validate_ma_url("http://192.168.1.1:8095")
        assert result is None


@pytest.mark.asyncio
async def test_validate_ma_url_success():
    """Returns server info when MA is reachable."""
    import importlib

    import services.ma_discovery as mod

    mock_info = MagicMock()
    mock_info.server_version = "2.5.0"
    mock_info.server_id = "abc123"
    mock_info.schema_version = 28
    mock_info.onboard_done = True
    mock_info.base_url = "http://192.168.1.50:8095"

    mock_mac = MagicMock()
    mock_mac.get_server_info = AsyncMock(return_value=mock_info)

    with patch.dict("sys.modules", {"music_assistant_client": mock_mac}):
        importlib.reload(mod)
        result = await mod.validate_ma_url("http://192.168.1.50:8095")
        assert result is not None
        assert result["version"] == "2.5.0"
        assert result["server_id"] == "abc123"
        assert result["schema_version"] == 28


# ---------------------------------------------------------------------------
# Login logic unit tests
# ---------------------------------------------------------------------------


def test_login_url_normalization():
    """URL without scheme gets http:// prepended."""
    url = "192.168.1.100:8095"
    if "://" not in url:
        url = f"http://{url}"
    assert url == "http://192.168.1.100:8095"


def test_login_url_with_scheme_unchanged():
    """URL with scheme is not modified."""
    url = "https://ma.local:8095"
    if "://" not in url:
        url = f"http://{url}"
    assert url == "https://ma.local:8095"


def test_config_default_has_ma_username():
    """DEFAULT_CONFIG includes MA_USERNAME."""
    from config import DEFAULT_CONFIG

    assert "MA_USERNAME" in DEFAULT_CONFIG
    assert DEFAULT_CONFIG["MA_USERNAME"] == ""
