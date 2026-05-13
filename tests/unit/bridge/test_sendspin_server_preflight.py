"""Pre-flight validation of SENDSPIN_SERVER before daemon spawn (issue #291)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from sendspin_bridge.bridge.client import SendspinClient


@pytest.mark.asyncio
async def test_start_sendspin_inner_refuses_malformed_server_host():
    """Pre-flight gate: malformed SENDSPIN_SERVER → no subprocess, last_error set."""
    client = SendspinClient("Test Player", "http://192.168.1.11:8095", 8927)

    with (
        patch("asyncio.create_subprocess_exec") as mock_spawn,
        patch.object(client, "stop_sendspin"),
    ):
        # No bt_manager + not running → falls straight through to the URL branch
        client.bt_manager = None
        await client._start_sendspin_inner()

    # Subprocess MUST NOT be spawned — the malformed URL would have caused the
    # 10-second silent-exit loop documented in issue #291.
    mock_spawn.assert_not_called()
    # last_error surfaces to the device card so the user can self-correct.
    assert client.status.get("last_error")
    assert "SENDSPIN_SERVER" in client.status["last_error"]


@pytest.mark.asyncio
async def test_start_sendspin_inner_allows_auto_mode():
    """`auto` is not malformed — it triggers in-daemon mDNS discovery and must pass the gate."""
    client = SendspinClient("Test Player", "auto", 8927)

    # The gate must NOT fire for auto. We don't run the full _start_sendspin_inner
    # here (it would try to spawn a real subprocess); we just call the gate
    # directly via the module-level helper.
    from sendspin_bridge.services.infrastructure.config_validation import is_valid_sendspin_host

    assert is_valid_sendspin_host(client.server_host) is True


@pytest.mark.asyncio
async def test_start_sendspin_inner_allows_bare_ip():
    """Bare IP must pass the gate."""
    client = SendspinClient("Test Player", "192.168.1.11", 8927)

    from sendspin_bridge.services.infrastructure.config_validation import is_valid_sendspin_host

    assert is_valid_sendspin_host(client.server_host) is True
