import asyncio

import pytest
from music_assistant_client.exceptions import CannotConnect, InvalidState
from music_assistant_models.errors import InsufficientPermissions, InvalidCommand

from sendspin_bridge.sync_tuning import ma_api
from sendspin_bridge.sync_tuning.ma_api import MaApiError, MaWsApi, _classify


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self, url, session, token=None):
        self.url, self.token = url, token
        self.connects = 0
        self.closed = False
        self.sent: list[tuple[str, str, dict]] = []
        self.fail: Exception | None = None
        self.in_flight = 0
        self.max_in_flight = 0
        FakeClient.instances.append(self)

    async def connect(self):
        self.connects += 1

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def send_command(self, command, **kwargs):
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            if self.fail is not None:
                raise self.fail
            self.sent.append(("call", command, kwargs))
            return {"ok": command}
        finally:
            self.in_flight -= 1

    async def send_command_no_wait(self, command, **kwargs):
        self.sent.append(("fire", command, kwargs))


@pytest.fixture
def fake_client(monkeypatch):
    import music_assistant_client

    FakeClient.instances = []
    monkeypatch.setattr(music_assistant_client, "MusicAssistantClient", FakeClient)
    return FakeClient


def _wait_until(pred, timeout=2.0):
    import time

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_call_and_fire_run_on_the_bridge_loop_through_one_client(bridge_loop, fake_client):
    api = MaWsApi("192.168.1.5:8095", "tok")
    assert api.call("players/all", {"return_protocol_players": True}) == {"ok": "players/all"}
    api.fire("players/cmd/play_announcement", {"player_id": "g", "url": "u", "pre_announce": False})
    assert _wait_until(lambda: len(fake_client.instances[0].sent) == 2)
    (client,) = fake_client.instances
    assert client.url == "http://192.168.1.5:8095" and client.token == "tok"
    assert client.connects == 1  # __aenter__ connects; no second connect()
    assert client.sent == [
        ("call", "players/all", {"return_protocol_players": True}),
        ("fire", "players/cmd/play_announcement", {"player_id": "g", "url": "u", "pre_announce": False}),
    ]
    api.close()
    assert _wait_until(lambda: client.closed)


def test_concurrent_calls_are_serialised(bridge_loop, fake_client):
    from concurrent.futures import ThreadPoolExecutor

    api = MaWsApi("http://ma:8095", "tok")
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda i: api.call("players/get", {"player_id": str(i)}), range(4)))
    assert fake_client.instances[0].max_in_flight == 1


def test_call_errors_are_classified_and_a_dead_client_is_replaced(bridge_loop, fake_client):
    api = MaWsApi("http://ma:8095", "tok")
    api.call("players/all", {})
    fake_client.instances[0].fail = InvalidState("Not connected")
    with pytest.raises(MaApiError) as exc:
        api.call("players/all", {})
    assert exc.value.code == "ma_unavailable"
    assert api.call("players/all", {}) == {"ok": "players/all"}
    assert len(fake_client.instances) == 2


def test_call_without_bridge_loop_is_ma_unavailable(fake_client, monkeypatch):
    from sendspin_bridge.services.lifecycle import bridge_runtime_state as runtime

    monkeypatch.setattr(runtime, "_main_loop", None)
    api = MaWsApi("http://ma:8095", "tok")
    with pytest.raises(MaApiError) as exc:
        api.call("players/all", {})
    assert exc.value.code == "ma_unavailable"
    with pytest.raises(MaApiError) as exc:
        api.fire("players/cmd/stop", {"player_id": "g"})
    assert exc.value.code == "ma_unavailable"


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (InsufficientPermissions("nope"), "ma_forbidden"),
        (InvalidCommand("permission denied for config/players/save"), "ma_forbidden"),
        (CannotConnect("refused"), "ma_unavailable"),
        (ConnectionResetError("reset"), "ma_unavailable"),
        (TimeoutError(), "ma_unavailable"),
        (InvalidCommand("no such command"), "ma_error"),
    ],
)
def test_classify(exc, code):
    assert _classify(exc).code == code


def test_from_config_requires_credentials(monkeypatch):
    monkeypatch.setattr(ma_api, "get_ma_api_credentials", lambda: ("http://ma:8095", ""))
    with pytest.raises(MaApiError) as exc:
        ma_api.ma_api_from_config()
    assert exc.value.code == "ma_unavailable"
    monkeypatch.setattr(ma_api, "get_ma_api_credentials", lambda: ("http://ma:8095", "t"))
    assert isinstance(ma_api.ma_api_from_config(), MaWsApi)
