"""MaWsApi against the real MusicAssistantClient over an in-memory connection (no network)."""

import asyncio
import time

import pytest
from music_assistant_client.constants import API_SCHEMA_VERSION
from music_assistant_client.exceptions import CannotConnect, ConnectionClosed, InvalidState, NotConnected
from music_assistant_models.errors import InsufficientPermissions, InvalidCommand

from sendspin_bridge.sync_tuning import ma_api
from sendspin_bridge.sync_tuning.ma_api import MaApiError, MaWsApi, _classify

_CLOSE = object()


class FakeServer:
    """Plays Music Assistant's side of the socket: answers by message id, can hold, fail, push or drop."""

    def __init__(self):
        self.connections: list[FakeConnection] = []
        self.commands: list[tuple[str, dict]] = []
        self.held: dict[str, dict] = {}  # command -> last held message (no reply until released)
        self.hold: set[str] = set()
        self.errors: dict[str, tuple[int, str]] = {}

    def reply(self, command, args):
        if command == "auth":
            return {"user": "bridge"}
        if command in ("providers", "providers/manifests", "players/all", "player_queues/all"):
            return [] if command != "players/all" or not args else [{"player_id": "p1"}]
        return {"ok": command, **args}

    @property
    def live(self):
        return self.connections[-1]

    def push(self, raw):  # called from the test thread
        conn = self.live
        conn.loop.call_soon_threadsafe(conn.queue.put_nowait, raw)

    def drop(self):
        self.push(_CLOSE)


class FakeConnection:
    def __init__(self, server, url, token):
        self.server, self.url, self.auth_token = server, url, token
        self.queue: asyncio.Queue = asyncio.Queue()
        self.loop = asyncio.get_running_loop()  # built by the client on the bridge loop
        self._connected = False
        server.connections.append(self)

    @property
    def connected(self):
        return self._connected

    async def connect(self):
        self._connected = True
        return {
            "server_id": "fake",
            "server_version": "2.9.0",
            "schema_version": API_SCHEMA_VERSION,
            "min_supported_schema_version": 1,
            "sdk_version": "1",
        }

    async def disconnect(self):
        self._connected = False
        self.queue.put_nowait(_CLOSE)

    async def receive_message(self):
        raw = await self.queue.get()
        if raw is _CLOSE:
            self._connected = False
            raise ConnectionClosed("Connection was closed.")
        return raw

    async def send_message(self, message):
        if not self._connected:
            raise NotConnected
        command, args, mid = message["command"], message.get("args") or {}, message["message_id"]
        if command != "auth":
            self.server.commands.append((command, args))
        if command in self.server.hold:
            self.server.held[command] = message
            return
        if command in self.server.errors:
            code, details = self.server.errors[command]
            self.queue.put_nowait({"message_id": mid, "error_code": code, "details": details})
            return
        self.queue.put_nowait({"message_id": mid, "result": self.server.reply(command, args)})


@pytest.fixture
def server(monkeypatch):
    import music_assistant_client.client as client_mod

    srv = FakeServer()
    monkeypatch.setattr(
        client_mod, "WebsocketsConnection", lambda url, session, token, ssl: FakeConnection(srv, url, token)
    )
    return srv


def _wait_until(pred, timeout=2.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_call_runs_through_one_listening_client(bridge_loop, server):
    api = MaWsApi("192.168.1.5:8095", "tok")
    assert api.call("players/get", {"player_id": "a"}) == {"ok": "players/get", "player_id": "a"}
    assert api.call("players/get", {"player_id": "b"}) == {"ok": "players/get", "player_id": "b"}
    assert len(server.connections) == 1
    assert server.live.url == "http://192.168.1.5:8095" and server.live.auth_token == "tok"
    api.close()


def test_events_and_unmatched_replies_are_drained_and_ignored(bridge_loop, server):
    api = MaWsApi("http://ma:8095", "tok")
    api.call("players/get", {"player_id": "a"})
    server.push({"event": "providers_updated", "object_id": None, "data": []})
    server.push({"message_id": "stale-id", "result": {"late": True}})
    server.push({"message_id": "stale-err", "error_code": 11, "details": "late failure"})
    assert api.call("players/get", {"player_id": "b"}) == {"ok": "players/get", "player_id": "b"}
    assert _wait_until(server.live.queue.empty)
    api.close()


def test_concurrent_calls_get_their_own_replies(bridge_loop, server):
    from concurrent.futures import ThreadPoolExecutor

    api = MaWsApi("http://ma:8095", "tok")
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda i: api.call("players/get", {"player_id": str(i)}), range(8)))
    assert [r["player_id"] for r in results] == [str(i) for i in range(8)]
    assert len(server.connections) == 1
    api.close()


def test_a_closed_connection_is_rebuilt_on_the_next_call(bridge_loop, server):
    api = MaWsApi("http://ma:8095", "tok")
    api.call("players/get", {"player_id": "a"})
    first = server.live
    server.drop()
    assert _wait_until(lambda: not first.connected)
    assert api.call("players/get", {"player_id": "b"}) == {"ok": "players/get", "player_id": "b"}
    assert len(server.connections) == 2
    api.close()


def test_a_timed_out_call_keeps_the_connection(bridge_loop, server):
    api = MaWsApi("http://ma:8095", "tok")
    server.hold.add("players/cmd/stop")
    with pytest.raises(MaApiError) as exc:
        api.call("players/cmd/stop", {"player_id": "grp"}, timeout=0.2)
    assert exc.value.code == "ma_unavailable"
    assert api.call("players/get", {"player_id": "a"})["player_id"] == "a"
    assert len(server.connections) == 1
    api.close()


def test_close_stops_the_listener(bridge_loop, server):
    api = MaWsApi("http://ma:8095", "tok")
    api.call("players/get", {"player_id": "a"})
    listener = api._listener
    assert listener is not None and not listener.done()
    api.close()
    assert _wait_until(listener.done)
    assert not server.live.connected


def test_fire_raises_a_fast_rejection(bridge_loop, server):
    api = MaWsApi("http://ma:8095", "tok", fire_window=1.0)
    server.errors["players/cmd/play_announcement"] = (InsufficientPermissions.error_code, "admin role required")
    with pytest.raises(MaApiError) as exc:
        api.fire("players/cmd/play_announcement", {"player_id": "grp", "url": "u", "pre_announce": False})
    assert exc.value.code == "ma_forbidden"
    server.errors["players/cmd/play_announcement"] = (11, "Could not resolve media u")  # PlayerCommandFailed
    with pytest.raises(MaApiError) as exc:
        api.fire("players/cmd/play_announcement", {"player_id": "grp", "url": "u", "pre_announce": False})
    assert exc.value.code == "ma_error"
    api.close()


def test_fire_returns_once_the_clip_is_running_and_ignores_the_late_reply(bridge_loop, server):
    api = MaWsApi("http://ma:8095", "tok", fire_window=0.2)
    server.hold.add("players/cmd/play_announcement")
    started = time.monotonic()
    api.fire("players/cmd/play_announcement", {"player_id": "grp", "url": "u", "pre_announce": False})
    assert 0.15 <= time.monotonic() - started < 2.0
    held = server.held["players/cmd/play_announcement"]
    server.push({"message_id": held["message_id"], "error_code": 11, "details": "clip ended badly"})
    assert api.call("players/get", {"player_id": "a"})["player_id"] == "a"
    api.close()


def test_calling_from_the_bridge_loop_is_refused_at_once(bridge_loop, server):
    api = MaWsApi("http://ma:8095", "tok")

    async def on_loop():
        started = time.monotonic()
        with pytest.raises(RuntimeError):
            api.call("players/get", {"player_id": "a"}, timeout=5)
        with pytest.raises(RuntimeError):
            api.fire("players/cmd/stop", {"player_id": "a"})
        return time.monotonic() - started

    elapsed = asyncio.run_coroutine_threadsafe(on_loop(), bridge_loop).result(timeout=10)
    assert elapsed < 1.0


def test_call_without_bridge_loop_is_ma_unavailable(server, monkeypatch):
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
        (InvalidState("Not connected"), "ma_unavailable"),
        (ConnectionResetError("reset"), "ma_unavailable"),
        (TimeoutError(), "ma_unavailable"),
        (InvalidCommand("no such command"), "ma_error"),
        (InvalidCommand("Player 5f4013ab0403 not found"), "ma_error"),
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


@pytest.fixture
def shared_reset():
    yield
    ma_api._shared = None


def test_the_shared_api_keeps_one_connection_across_requests(bridge_loop, server, monkeypatch, shared_reset):
    monkeypatch.setattr(ma_api, "get_ma_api_credentials", lambda: ("http://ma:8095", "tok"))
    first = ma_api.shared_ma_api()
    first.call("players/get", {"player_id": "a"})
    second = ma_api.shared_ma_api()
    second.call("players/get", {"player_id": "b"})
    assert first is second and len(server.connections) == 1
    listener = first._listener
    monkeypatch.setattr(ma_api, "get_ma_api_credentials", lambda: ("http://ma:8095", "new-token"))
    third = ma_api.shared_ma_api()  # new credentials: a new client, the old one is closed
    assert third is not first
    assert _wait_until(listener.done)
    third.call("players/get", {"player_id": "c"})
    assert len(server.connections) == 2 and server.live.auth_token == "new-token"
    listener = third._listener
    ma_api.close_shared_ma_api()
    assert _wait_until(listener.done) and not server.live.connected
    assert ma_api.shared_ma_api() is not third


def test_the_shared_api_needs_credentials(monkeypatch, shared_reset):
    monkeypatch.setattr(ma_api, "get_ma_api_credentials", lambda: ("http://ma:8095", ""))
    with pytest.raises(MaApiError) as exc:
        ma_api.shared_ma_api()
    assert exc.value.code == "ma_unavailable"


def test_the_shared_api_is_closed_and_awaited_at_shutdown(bridge_loop, server, monkeypatch, shared_reset):
    monkeypatch.setattr(ma_api, "get_ma_api_credentials", lambda: ("http://ma:8095", "tok"))
    api = ma_api.shared_ma_api()
    api.call("players/get", {"player_id": "a"})
    listener = api._listener
    asyncio.run_coroutine_threadsafe(ma_api.aclose_shared_ma_api(), bridge_loop).result(timeout=5)
    assert listener.done() and not server.live.connected  # done when the coroutine returns
    asyncio.run_coroutine_threadsafe(ma_api.aclose_shared_ma_api(), bridge_loop).result(timeout=5)  # idempotent
