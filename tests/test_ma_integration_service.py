from __future__ import annotations

import asyncio

import pytest

from services.ma_integration_service import BridgeMaIntegrationService


@pytest.mark.asyncio
async def test_initialize_autodetects_addon_url_and_warns_when_token_missing(monkeypatch, caplog):
    service = BridgeMaIntegrationService()
    monkeypatch.setenv("SUPERVISOR_TOKEN", "demo-supervisor-token")

    with caplog.at_level("WARNING"):
        resolved = await service.initialize(
            {
                "MA_API_URL": "",
                "MA_API_TOKEN": "",
                "MA_WEBSOCKET_MONITOR": False,
            },
            [],
            server_host="ma-host.local",
        )

    assert resolved.ma_api_url == "http://ma-host.local:8095"
    assert resolved.ma_api_token == ""
    assert resolved.groups_loaded is False
    assert resolved.ma_monitor_task is None
    assert "no 'ma_api_token' configured" in caplog.text


@pytest.mark.asyncio
async def test_initialize_discovers_groups_and_starts_monitor(monkeypatch):
    service = BridgeMaIntegrationService()
    monitor_started = asyncio.Event()

    async def fake_discover(_url, _token, player_info):
        assert player_info == [{"player_id": "sendspin-kitchen", "player_name": "Kitchen"}]
        return (
            {"sendspin-kitchen": {"id": "syncgroup_1", "name": "Kitchen Group"}},
            [{"id": "syncgroup_1", "name": "Kitchen Group", "members": []}],
        )

    class FakeMonitor:
        async def run(self) -> None:
            monitor_started.set()
            await asyncio.sleep(3600)

    monkeypatch.setattr("services.ma_client.discover_ma_groups", fake_discover)
    monkeypatch.setattr("services.ma_monitor.start_monitor", lambda _url, _token: FakeMonitor())

    resolved = await service.initialize(
        {
            "MA_API_URL": "http://ma.local:8095",
            "MA_API_TOKEN": "token",
            "MA_WEBSOCKET_MONITOR": True,
        },
        [type("Client", (), {"player_id": "sendspin-kitchen", "player_name": "Kitchen"})()],
        server_host="music-assistant.local",
    )

    assert resolved.ma_api_url == "http://ma.local:8095"
    assert resolved.ma_api_token == "token"
    assert resolved.groups_loaded is True
    assert resolved.name_map["sendspin-kitchen"]["id"] == "syncgroup_1"
    assert resolved.all_groups[0]["name"] == "Kitchen Group"
    assert resolved.ma_monitor_task is not None
    await asyncio.wait_for(monitor_started.wait(), timeout=0.2)

    resolved.ma_monitor_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await resolved.ma_monitor_task
