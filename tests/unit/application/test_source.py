"""The Sendspin source as a use case: start/stop, Music Assistant pairing and status."""

from __future__ import annotations

import pytest

from sendspin_bridge.application import source as source_uc
from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.services.source import manager, pairing
from sendspin_bridge.services.source import state as source_state

SERVER_URL = "ws://ma.local:8927/sendspin"


@pytest.fixture(autouse=True)
def _clean_state(monkeypatch, tmp_path):
    source_state.reset()
    manager.reset()
    monkeypatch.setattr("sendspin_bridge.bridge.orchestrator._resolve_config_dir", lambda: tmp_path)
    yield
    source_state.reset()
    manager.reset()


def test_status_reads_the_typed_snapshot():
    source_state.publish(
        enabled=True,
        state="streaming",
        phone="bluez_source.34_C7_E9_25_A3_A3",
        phone_mac="34:C7:E9:25:A3:A3",
        streaming=True,
        server=SERVER_URL,
        ma_connected=True,
        ma_paired=True,
    )

    view = source_uc.status({"SENDSPIN_SOURCE": True})

    assert view.state == "streaming"
    assert view.phone_mac == "34:C7:E9:25:A3:A3"
    assert view.as_status_dict()["ma_connected"] is True


def test_status_reports_a_missing_pairing_as_required():
    source_state.publish(enabled=True, state="pairing", ma_paired=False)

    view = source_uc.status({})

    assert view.ma_pairing_required is True


async def test_start_picks_the_backend_and_persists_the_choice(monkeypatch, tmp_path):
    recorded: list[dict] = []
    started: list[tuple] = []

    async def fake_manager_start(settings, backend):
        started.append((settings, backend))
        return {"started": True, "server": settings.server_url}

    monkeypatch.setattr("sendspin_bridge.services.hardware.capabilities.probe_audio_server", lambda: "PulseAudio")
    monkeypatch.setattr("sendspin_bridge.services.hardware.capabilities.probe_capture", lambda _name: True)
    monkeypatch.setattr("sendspin_bridge.services.source.backends.backend_for", lambda _name: object())
    monkeypatch.setattr("sendspin_bridge.services.source.manager.start", fake_manager_start)

    def record(mutator):
        config: dict = {}
        mutator(config)
        recorded.append(config)

    monkeypatch.setattr("sendspin_bridge.config.update_config", record)
    monkeypatch.setattr(source_uc, "resolve_server_url", lambda _config: _async(SERVER_URL))

    result = await source_uc.start(
        config={"SENDSPIN_SOURCE": False, "BRIDGE_NAME": "Flat", "SENDSPIN_SOURCE_TARGET": "kitchen"}
    )

    assert result["started"] is True
    assert recorded == [{"SENDSPIN_SOURCE": True}]
    settings, backend = started[0]
    assert settings.server_url == SERVER_URL
    assert settings.client_name == "Sendspin BT Bridge (Flat)"
    assert settings.target_player == "kitchen"
    assert backend is not None


async def test_start_refuses_a_host_that_cannot_capture(monkeypatch):
    monkeypatch.setattr("sendspin_bridge.services.hardware.capabilities.probe_audio_server", lambda: "not available")
    monkeypatch.setattr("sendspin_bridge.services.hardware.capabilities.probe_capture", lambda _name: False)

    with pytest.raises(UseCaseError) as raised:
        await source_uc.start(config={})

    assert raised.value.status == 422
    assert raised.value.code == "capture_unavailable"


async def test_start_refuses_when_music_assistant_is_unknown(monkeypatch):
    monkeypatch.setattr("sendspin_bridge.services.hardware.capabilities.probe_audio_server", lambda: "PipeWire")
    monkeypatch.setattr("sendspin_bridge.services.hardware.capabilities.probe_capture", lambda _name: True)
    monkeypatch.setattr("sendspin_bridge.services.source.backends.backend_for", lambda _name: object())
    monkeypatch.setattr(source_uc, "resolve_server_url", lambda _config: _async(None))

    with pytest.raises(UseCaseError) as raised:
        await source_uc.start(config={})

    assert raised.value.code == "ma_unreachable"


async def test_stop_keeps_the_disabled_choice_in_the_config(monkeypatch):
    written: list[bool] = []

    async def fake_stop():
        return {"started": False}

    monkeypatch.setattr("sendspin_bridge.services.source.manager.stop", fake_stop)

    def record(mutator):
        config: dict = {}
        mutator(config)
        written.append(config["SENDSPIN_SOURCE"])

    monkeypatch.setattr("sendspin_bridge.config.update_config", record)

    result = await source_uc.stop()

    assert result == {"started": False}
    assert written == [False]


async def test_pairing_with_music_assistant_needs_a_running_source():
    with pytest.raises(UseCaseError) as raised:
        await source_uc.open_ma_pairing()

    assert raised.value.status == 409
    assert raised.value.code == "source_off"


async def test_forgetting_the_music_assistant_pairing_needs_a_running_source():
    with pytest.raises(UseCaseError) as raised:
        await source_uc.cancel_ma_pairing(forget=True)

    assert raised.value.status == 409


async def test_the_phone_pairing_window_is_delegated(monkeypatch):
    seen: list[int] = []

    async def fake_open(seconds):
        seen.append(seconds)
        return {"pairing_seconds_left": seconds}

    monkeypatch.setattr(pairing, "open_window", fake_open)

    result = await source_uc.open_pairing_window(30)

    assert seen == [30]
    assert result == {"pairing_seconds_left": 30}


async def test_resolve_server_url_prefers_the_url_a_connected_player_dialled(monkeypatch):
    class _Client:
        connected_server_url = SERVER_URL

    class _Snapshot:
        active_clients = [_Client()]

    monkeypatch.setattr(
        "sendspin_bridge.services.bluetooth.device_registry.get_device_registry_snapshot",
        lambda: _Snapshot(),
    )

    assert await source_uc.resolve_server_url({"SENDSPIN_SERVER": "auto"}) == SERVER_URL


async def test_resolve_server_url_uses_an_explicit_host_and_port(monkeypatch):
    class _Snapshot:
        active_clients: list = []

    monkeypatch.setattr(
        "sendspin_bridge.services.bluetooth.device_registry.get_device_registry_snapshot",
        lambda: _Snapshot(),
    )

    assert await source_uc.resolve_server_url({"SENDSPIN_SERVER": "ma.local", "SENDSPIN_PORT": 9001}) == (
        "ws://ma.local:9001/sendspin"
    )


async def test_resolve_server_url_falls_back_to_discovery_for_auto(monkeypatch):
    class _Snapshot:
        active_clients: list = []

    async def fake_discovery(timeout=5.0):
        del timeout
        return [{"url": "http://ma.local:8095"}]

    monkeypatch.setattr(
        "sendspin_bridge.services.bluetooth.device_registry.get_device_registry_snapshot",
        lambda: _Snapshot(),
    )
    monkeypatch.setattr("sendspin_bridge.services.music_assistant.ma_discovery.discover_ma_servers", fake_discovery)

    assert await source_uc.resolve_server_url({"SENDSPIN_SERVER": "auto"}) == "ws://ma.local:8927/sendspin"


async def _async(value):
    return value


class _FakeSourceClient:
    client_id = "peer-1"

    def __init__(self) -> None:
        self.targets: list[str | None] = []

    async def set_target(self, target: str | None) -> str | None:
        self.targets.append(target)
        return None


async def test_target_list_without_ma_api_credentials_keeps_the_saved_choice(monkeypatch):
    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: {"SENDSPIN_SOURCE_TARGET": "kitchen"})
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_runtime_state.get_ma_api_credentials",
        lambda: ("", ""),
    )

    result = await source_uc.targets()

    assert result == {"targets": [], "selected": "kitchen", "ma_api_available": False, "error": None}


async def test_target_list_names_the_players_and_groups(monkeypatch):
    async def fake_fetch(_url, _token, _client_id):
        return ([{"player_id": "house", "name": "House", "type": "group", "available": True}], "uri")

    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: {})
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_runtime_state.get_ma_api_credentials",
        lambda: ("http://ma:8095", "token"),
    )
    monkeypatch.setattr("sendspin_bridge.services.source.target.fetch_targets", fake_fetch)
    monkeypatch.setattr(source_uc.manager, "client", lambda: _FakeSourceClient())

    result = await source_uc.targets()

    assert result["ma_api_available"] is True
    assert result["targets"][0]["name"] == "House"


async def test_set_target_validates_persists_and_asks_the_client_to_play(monkeypatch):
    client = _FakeSourceClient()
    saved: list[str | None] = []

    async def fake_fetch(_url, _token, _client_id):
        return ([{"player_id": "kitchen", "name": "Kitchen", "type": "player", "available": True}], "uri")

    def save(mutator):
        config: dict = {}
        mutator(config)
        saved.append(config["SENDSPIN_SOURCE_TARGET"])

    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_runtime_state.get_ma_api_credentials",
        lambda: ("http://ma:8095", "token"),
    )
    monkeypatch.setattr("sendspin_bridge.services.source.target.fetch_targets", fake_fetch)
    monkeypatch.setattr("sendspin_bridge.config.update_config", save)
    monkeypatch.setattr(source_uc.manager, "client", lambda: client)

    result = await source_uc.set_target("kitchen")

    assert result == {"target_player": "kitchen", "target_name": "Kitchen", "target_error": None}
    assert saved == ["kitchen"]
    assert client.targets == ["kitchen"]


async def test_set_target_rejects_an_unknown_player(monkeypatch):
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_runtime_state.get_ma_api_credentials",
        lambda: ("http://ma:8095", "token"),
    )

    async def fake_fetch(_url, _token, _client_id):
        return ([{"player_id": "kitchen", "name": "Kitchen", "type": "player", "available": True}], "uri")

    monkeypatch.setattr("sendspin_bridge.services.source.target.fetch_targets", fake_fetch)
    monkeypatch.setattr(source_uc.manager, "client", lambda: _FakeSourceClient())

    with pytest.raises(UseCaseError) as raised:
        await source_uc.set_target("garage")

    assert raised.value.status == 404
    assert raised.value.code == "unknown_target"


async def test_target_list_reports_an_unreachable_ma_api(monkeypatch):
    async def fail(_url, _token, _client_id):
        raise OSError("connection refused")

    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: {})
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_runtime_state.get_ma_api_credentials",
        lambda: ("http://ma:8095", "token"),
    )
    monkeypatch.setattr("sendspin_bridge.services.source.target.fetch_targets", fail)
    monkeypatch.setattr(source_uc.manager, "client", lambda: None)

    result = await source_uc.targets()

    assert result["ma_api_available"] is True
    assert result["error"] == "ma_api_unreachable"


async def test_set_target_without_an_ma_token_is_refused(monkeypatch):
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_runtime_state.get_ma_api_credentials",
        lambda: ("", ""),
    )
    monkeypatch.setattr(source_uc.manager, "client", lambda: _FakeSourceClient())

    with pytest.raises(UseCaseError) as raised:
        await source_uc.set_target("kitchen")

    assert raised.value.status == 409
    assert raised.value.code == "ma_api_unavailable"


def _snapshot(sources):
    from types import SimpleNamespace

    return SimpleNamespace(
        audio=SimpleNamespace(sources=sources),
        capabilities=SimpleNamespace(capture=True, audio_server="pulseaudio"),
    )


async def test_inputs_lists_configured_sources_and_local_candidates(monkeypatch):
    from types import SimpleNamespace

    local = SimpleNamespace(name="alsa_input.line", description="Line In", monitor_of=None)
    monkeypatch.setattr(source_uc, "_hardware_audio", lambda: _async(_snapshot([local])))
    monkeypatch.setattr(source_uc.source_devices, "list_source_devices", lambda **_kw: ())
    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: {})

    result = await source_uc.inputs()

    assert result["inputs"] == []
    assert [entry["node"] for entry in result["local_candidates"]] == ["alsa_input.line"]
    assert result["capture_available"] is True


async def test_adding_a_local_input_makes_it_active_when_nothing_is_selected(monkeypatch):
    from types import SimpleNamespace

    local = SimpleNamespace(name="alsa_input.line", description="Line In", monitor_of=None)
    saved: list[dict] = []
    monkeypatch.setattr(source_uc, "_hardware_audio", lambda: _async(_snapshot([local])))
    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: {})
    monkeypatch.setattr(
        "sendspin_bridge.config.update_config",
        lambda mutator: (lambda config: (mutator(config), saved.append(config)))(
            {"SENDSPIN_SOURCES": [], "SENDSPIN_SOURCE_ACTIVE": None}
        ),
    )
    monkeypatch.setattr(source_uc.manager, "client", lambda: None)

    result = await source_uc.add_local_input("alsa_input.line", "Turntable")

    assert result["id"] == "local:alsa_input.line"
    assert saved[0]["SENDSPIN_SOURCE_ACTIVE"] == "local:alsa_input.line"
    assert saved[0]["SENDSPIN_SOURCES"][0]["name"] == "Turntable"


async def test_removing_the_active_local_input_selects_nothing(monkeypatch):
    config = {
        "SENDSPIN_SOURCES": [
            {"id": "local:alsa_input.line", "kind": "local", "node": "alsa_input.line", "name": "Line"}
        ],
        "SENDSPIN_SOURCE_ACTIVE": "local:alsa_input.line",
    }
    saved: list[dict] = []
    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: config)
    monkeypatch.setattr(
        "sendspin_bridge.config.update_config",
        lambda mutator: (lambda target: (mutator(target), saved.append(target)))(
            {"SENDSPIN_SOURCES": config["SENDSPIN_SOURCES"], "SENDSPIN_SOURCE_ACTIVE": config["SENDSPIN_SOURCE_ACTIVE"]}
        ),
    )
    monkeypatch.setattr(source_uc.manager, "client", lambda: None)

    await source_uc.remove_input("local:alsa_input.line")

    assert saved[0]["SENDSPIN_SOURCES"] == []
    assert saved[0]["SENDSPIN_SOURCE_ACTIVE"] == "none"


async def test_selecting_an_unknown_source_is_refused(monkeypatch):
    monkeypatch.setattr("sendspin_bridge.config.load_config", lambda: {"SENDSPIN_SOURCES": []})
    monkeypatch.setattr(source_uc.manager, "client", lambda: None)

    with pytest.raises(UseCaseError) as raised:
        await source_uc.select_input("local:missing")

    assert raised.value.status == 404


async def test_status_exposes_local_inputs_and_marks_the_active_one(monkeypatch):
    from sendspin_bridge.services.source.state import SourceDevice

    monkeypatch.setattr(
        source_uc.source_devices,
        "list_source_devices",
        lambda **_kw: (SourceDevice("34:C7:E9:25:A3:A3", "Pixel", True),),
    )
    source_state.publish(enabled=True, state="waiting", active_input="local:alsa_input.line")

    view = source_uc.status(
        {
            "SENDSPIN_SOURCE": True,
            "SENDSPIN_SOURCES": [
                {"id": "local:alsa_input.line", "kind": "local", "node": "alsa_input.line", "name": "Turntable"}
            ],
        }
    )

    assert view.active_input == "local:alsa_input.line"
    assert [(entry.kind, entry.name, entry.active) for entry in view.inputs] == [
        ("bluetooth", "Pixel", False),
        ("local", "Turntable", True),
    ]


async def test_set_target_rejects_a_player_without_play_media(monkeypatch):
    monkeypatch.setattr(
        "sendspin_bridge.services.music_assistant.ma_runtime_state.get_ma_api_credentials",
        lambda: ("http://ma:8095", "token"),
    )

    async def fake_fetch(_url, _token, _client_id):
        return (
            [
                {
                    "player_id": "voice-pe",
                    "name": "Voice PE",
                    "type": "player",
                    "available": True,
                    "supported": False,
                }
            ],
            "uri",
        )

    monkeypatch.setattr("sendspin_bridge.services.source.target.fetch_targets", fake_fetch)
    monkeypatch.setattr(source_uc.manager, "client", lambda: _FakeSourceClient())

    with pytest.raises(UseCaseError) as raised:
        await source_uc.set_target("voice-pe")

    assert raised.value.status == 422
    assert raised.value.code == "target_unsupported"
