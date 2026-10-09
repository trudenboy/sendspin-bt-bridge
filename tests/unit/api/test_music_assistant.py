"""``/music-assistant``: discovery, sync groups, reload, queue commands."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import sendspin_bridge.application.music_assistant.common as ma_common
import sendspin_bridge.application.music_assistant.groups as ma_groups
import sendspin_bridge.bridge.state as state
import sendspin_bridge.services.music_assistant.ma_monitor as ma_monitor
from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot
from tests.support.api_client import wait_for_job

SOLO = "sendspin-yandex-mini-2---lxc"


@pytest.fixture(autouse=True)
def _clean_ma_state(tmp_config):
    yield
    state.clear_ma_now_playing()
    state.set_ma_groups({}, [])
    state.set_ma_api_credentials("", "")
    state.set_ma_connected(False)


@pytest.fixture
def kitchen(monkeypatch):
    snapshot = DeviceRegistrySnapshot(
        active_clients=[SimpleNamespace(player_id="sendspin-kitchen", player_name="Kitchen", status={})]
    )
    monkeypatch.setattr(ma_common, "get_device_registry_snapshot", lambda: snapshot)


# -- discovery ----------------------------------------------------------------------------------------


def _validated(url: str, *, addon: bool):
    async def _validate(_candidate):
        return {"url": url, "version": "2.0.0", "homeassistant_addon": addon}

    return _validate


def test_addon_discovery_names_its_source_and_the_token_state(api_client, monkeypatch, bridge_loop, tmp_config):
    import sendspin_bridge.application.music_assistant.auth as ma_auth
    import sendspin_bridge.services.music_assistant.ma_discovery as ma_discovery

    tmp_config.write_text(
        json.dumps({"MA_API_URL": "http://localhost:8095", "MA_API_TOKEN": "expired", "MA_AUTH_PROVIDER": "ha"})
    )
    monkeypatch.setattr(ma_groups, "_is_addon", lambda: True)
    monkeypatch.setattr(
        ma_groups,
        "get_ma_addon_discovery_candidates",
        lambda: [{"url": "http://ma-addon:8095", "source": "ha_addon_hostname", "summary": "Supervisor reported it."}],
    )
    monkeypatch.setattr(ma_discovery, "validate_ma_url", _validated("http://localhost:8095", addon=True))
    monkeypatch.setattr(ma_auth, "_validate_ma_token", lambda ma_url, token: False)

    resp = api_client.post("/api/v1/music-assistant/discovery")

    assert resp.status_code == 202
    job = wait_for_job(api_client, resp.json())
    data = job["result"]
    assert data["is_addon"] is True
    server = data["servers"][0]
    assert (server["url"], server["discovery_source"]) == ("http://localhost:8095", "ha_addon_hostname")
    assert "Supervisor" in server["discovery_summary"]
    assert data["integration"]["token_configured"] is True
    assert data["integration"]["token_valid"] is False
    assert data["integration"]["connected"] is False
    assert data["integration"]["matches_discovered_server"] is True


def test_discovery_prefers_the_saved_server_and_reports_a_live_connection(
    api_client, monkeypatch, bridge_loop, tmp_config
):
    import sendspin_bridge.application.music_assistant.auth as ma_auth
    import sendspin_bridge.services.music_assistant.ma_discovery as ma_discovery

    tmp_config.write_text(json.dumps({"MA_API_URL": "http://localhost:8095", "MA_API_TOKEN": "working"}))
    monkeypatch.setattr(ma_groups, "_is_addon", lambda: False)
    monkeypatch.setattr(ma_groups, "get_ma_api_credentials", lambda: ("http://localhost:8095", "working"))
    monkeypatch.setattr(ma_common, "is_ma_connected", lambda: True)
    monkeypatch.setattr(ma_discovery, "validate_ma_url", _validated("http://localhost:8095", addon=False))
    monkeypatch.setattr(ma_auth, "_validate_ma_token", lambda ma_url, token: False)

    data = wait_for_job(api_client, api_client.post("/api/v1/music-assistant/discovery").json())["result"]

    assert data["servers"][0]["discovery_source"] == "saved_config"
    assert "saved bridge configuration" in data["servers"][0]["discovery_summary"]
    assert data["integration"]["connected"] is True


def test_the_ma_host_comes_from_the_first_explicit_sendspin_server(monkeypatch):
    snapshot = DeviceRegistrySnapshot(
        active_clients=[
            SimpleNamespace(server_host="auto", connected_server_url="192.168.10.10:9000"),
            SimpleNamespace(server_host="music-assistant.local", connected_server_url=""),
        ]
    )
    monkeypatch.setattr(ma_common, "get_device_registry_snapshot", lambda: snapshot)
    assert ma_common.ma_host_from_sendspin_clients() == "music-assistant.local"


# -- groups ----------------------------------------------------------------------------------------------


@pytest.fixture
def fake_discover(monkeypatch):
    import sendspin_bridge.services.music_assistant.ma_client as ma_client

    captured: dict = {}

    async def _discover(ma_url, ma_token, player_info):
        captured.update(ma_url=ma_url, ma_token=ma_token, player_info=player_info)
        return {"sendspin-kitchen": {"id": "syncgroup_1", "name": "Kitchen"}}, [
            {"id": "syncgroup_1", "name": "Kitchen"}
        ]

    monkeypatch.setattr(ma_client, "discover_ma_groups", _discover)
    return captured


def test_group_refresh_sends_the_bridge_players_and_stores_the_groups(
    api_client, kitchen, fake_discover, bridge_loop, tmp_config
):
    tmp_config.write_text(json.dumps({"MA_API_URL": "http://ma.local:8095", "MA_API_TOKEN": "token"}))

    job = wait_for_job(api_client, api_client.post("/api/v1/music-assistant/groups/refresh").json())

    assert job["status"] == "succeeded"
    assert job["result"] == {"syncgroups": 1, "mapped_players": 1, "groups": [{"id": "syncgroup_1", "name": "Kitchen"}]}
    assert fake_discover == {
        "ma_url": "http://ma.local:8095",
        "ma_token": "token",
        "player_info": [{"player_id": "sendspin-kitchen", "player_name": "Kitchen"}],
    }
    assert api_client.get("/api/v1/music-assistant/groups").json()[0]["id"] == "syncgroup_1"


def test_group_refresh_without_credentials_is_409(api_client, bridge_loop):
    resp = api_client.post("/api/v1/music-assistant/groups/refresh")
    assert resp.status_code == 409
    assert resp.json()["code"] == "ma_not_configured"


def test_reload_reconnects_the_monitor_and_refreshes_groups(
    api_client, kitchen, fake_discover, monkeypatch, bridge_loop, tmp_config
):
    tmp_config.write_text(json.dumps({"MA_API_URL": "http://ma.local:8095", "MA_API_TOKEN": "token"}))
    reloaded: list[tuple] = []
    monkeypatch.setattr(
        ma_groups, "reload_monitor_credentials", lambda loop, url, token: reloaded.append((url, token)) or True
    )

    resp = api_client.post("/api/v1/music-assistant/reload")

    assert resp.status_code == 202
    assert resp.json()["monitor_reloaded"] is True
    assert wait_for_job(api_client, resp.json()["job"])["status"] == "succeeded"
    assert reloaded == [("http://ma.local:8095", "token")]
    assert fake_discover["player_info"] == [{"player_id": "sendspin-kitchen", "player_name": "Kitchen"}]


def test_debug_lists_the_bridge_clients(api_client, monkeypatch):
    snapshot = DeviceRegistrySnapshot(
        active_clients=[SimpleNamespace(player_name="Kitchen", player_id="sendspin-kitchen", status={"group_id": "g1"})]
    )
    monkeypatch.setattr(ma_common, "get_device_registry_snapshot", lambda: snapshot)

    data = api_client.get("/api/v1/music-assistant/debug").json()

    assert data["clients"] == [{"player_name": "Kitchen", "player_id": "sendspin-kitchen", "group_id": "g1"}]


# -- queue commands ---------------------------------------------------------------------------------------


@pytest.fixture
def monitor(monkeypatch):
    sent: dict = {}

    async def _send(action, value, queue_id, player_id=None):
        sent.update(action=action, value=value, queue_id=queue_id, player_id=player_id)
        return {
            "accepted": True,
            "queue_id": sent.get("accepted_queue_id") or queue_id,
            "ack_latency_ms": 42,
            "accepted_at": 123.45,
        }

    async def _refresh(queue_id):
        sent["refreshed"] = queue_id
        return True

    monkeypatch.setattr(ma_monitor, "send_queue_cmd", _send)
    monkeypatch.setattr(ma_monitor, "request_queue_refresh", _refresh)
    monkeypatch.setattr(ma_monitor, "get_monitor", lambda: SimpleNamespace(is_connected=lambda: True))
    state.set_ma_connected(True)
    return sent


def test_queue_command_answers_with_a_prediction_then_confirms(api_client, monitor, bridge_loop):
    state.set_ma_groups({}, [{"id": "syncgroup_1", "name": "Kitchen", "members": []}])
    state.set_ma_now_playing_for_group(
        "syncgroup_1", {"syncgroup_id": "syncgroup_1", "shuffle": False, "connected": True}
    )

    resp = api_client.post(
        "/api/v1/music-assistant/queue-commands",
        json={"action": "shuffle", "value": True, "syncgroup_id": "syncgroup_1"},
    )

    assert resp.status_code == 202
    data = resp.json()
    assert data["op_id"]
    assert data["syncgroup_id"] == "syncgroup_1"
    predicted = data["ma_now_playing"]
    assert predicted["shuffle"] is True
    assert predicted["_sync_meta"]["pending"] is True
    assert predicted["_sync_meta"]["pending_ops"][0]["action"] == "shuffle"

    job = wait_for_job(api_client, data["job"])
    assert job["status"] == "succeeded"
    assert job["result"]["accepted_at"] == 123.45
    assert job["result"]["ack_latency_ms"] == 42
    assert job["result"]["ma_now_playing"]["_sync_meta"]["last_accepted_at"] == 123.45


def test_a_solo_players_own_queue_wins_over_a_stale_group_id(api_client, monitor, bridge_loop):
    group = "4fd07f70-5da7-4bbb-8d0a-d6fb1478e798"
    state.set_ma_groups({}, [{"id": group, "name": "Living Room", "members": []}])

    resp = api_client.post(
        "/api/v1/music-assistant/queue-commands",
        json={"action": "repeat", "value": "all", "syncgroup_id": SOLO, "group_id": group, "device_id": SOLO},
    )

    data = resp.json()
    wait_for_job(api_client, data["job"])
    assert (data["syncgroup_id"], data["queue_id"]) == (SOLO, SOLO)
    assert (monitor["queue_id"], monitor["player_id"], monitor["refreshed"]) == (SOLO, SOLO, SOLO)


def test_the_refresh_follows_the_queue_ma_actually_accepted(api_client, monitor, bridge_loop):
    monitor["accepted_queue_id"] = "upsendspinyandexmini2lxc"

    resp = api_client.post(
        "/api/v1/music-assistant/queue-commands",
        json={"action": "shuffle", "value": True, "syncgroup_id": SOLO, "device_id": SOLO},
    )

    job = wait_for_job(api_client, resp.json()["job"])
    assert resp.json()["queue_id"] == SOLO
    assert monitor["refreshed"] == "upsendspinyandexmini2lxc"
    assert job["result"]["queue_id"] == "upsendspinyandexmini2lxc"


def test_a_rejected_command_rolls_the_prediction_back(api_client, monitor, monkeypatch, bridge_loop):
    async def _reject(*_a, **_kw):
        return {"accepted": False, "error": "queue is locked"}

    monkeypatch.setattr(ma_monitor, "send_queue_cmd", _reject)
    state.set_ma_groups({}, [{"id": "syncgroup_1", "name": "Kitchen", "members": []}])

    resp = api_client.post(
        "/api/v1/music-assistant/queue-commands", json={"action": "next", "syncgroup_id": "syncgroup_1"}
    )

    job = wait_for_job(api_client, resp.json()["job"])
    assert job["status"] == "failed"
    assert job["error"]["code"] == "command_rejected"
    assert job["error"]["detail"] == "queue is locked"


def test_queue_command_without_a_monitor_is_503(api_client, monkeypatch, bridge_loop):
    state.set_ma_connected(True)
    state.set_ma_groups({}, [{"id": "syncgroup_1", "name": "Kitchen", "members": []}])
    monkeypatch.setattr(ma_monitor, "get_monitor", lambda: SimpleNamespace(is_connected=lambda: False))

    resp = api_client.post(
        "/api/v1/music-assistant/queue-commands", json={"action": "next", "syncgroup_id": "syncgroup_1"}
    )

    assert resp.status_code == 503
    assert resp.json()["code"] == "monitor_unavailable"


def test_queue_command_without_a_queue_is_503(api_client, bridge_loop):
    state.set_ma_connected(True)
    resp = api_client.post("/api/v1/music-assistant/queue-commands", json={"action": "next"})
    assert resp.status_code == 503
    assert resp.json()["code"] == "queue_unavailable"


def test_queue_command_without_ma_is_503(api_client):
    resp = api_client.post("/api/v1/music-assistant/queue-commands", json={"action": "next"})
    assert resp.status_code == 503
    assert resp.json()["code"] == "ma_unavailable"


def test_now_playing_without_ma(api_client):
    assert api_client.get("/api/v1/music-assistant/now-playing").json() == {"connected": False}
