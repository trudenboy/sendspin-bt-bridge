from __future__ import annotations

import asyncio
import concurrent.futures
from types import SimpleNamespace

from flask import Flask

import sendspin_bridge.web.routes.api as api
from sendspin_bridge.services.ipc.commands import OpenPairingWindow


def _app_client():
    app = Flask(__name__)
    app.register_blueprint(api.api_bp)
    return app.test_client()


def _runtime_client(*, running: bool = True):
    commands = []

    async def send(command):
        commands.append(command)

    return SimpleNamespace(
        player_id="stable-player-id",
        player_name="Kitchen",
        is_running=lambda: running,
        _send_subprocess_command=send,
        commands=commands,
    )


def _run_immediately(coro, _loop):
    future = concurrent.futures.Future()
    try:
        future.set_result(asyncio.run(coro))
    except Exception as exc:
        future.set_exception(exc)
    return future


def test_pairing_window_targets_stable_player_id(monkeypatch):
    runtime_client = _runtime_client()
    monkeypatch.setattr(api, "load_config", lambda: {"SENDSPIN_PAIRING": True})
    monkeypatch.setattr(
        api,
        "get_device_registry_snapshot",
        lambda: SimpleNamespace(active_clients=[runtime_client]),
    )
    monkeypatch.setattr(api, "get_main_loop", lambda: object())
    monkeypatch.setattr(api.asyncio, "run_coroutine_threadsafe", _run_immediately)

    response = _app_client().post("/api/pairing/window", json={"player_id": "stable-player-id"})

    assert response.status_code == 202
    assert response.get_json() == {"success": True, "player_id": "stable-player-id"}
    assert len(runtime_client.commands) == 1
    assert isinstance(runtime_client.commands[0], OpenPairingWindow)


def test_pairing_window_requires_stable_player_id(monkeypatch):
    monkeypatch.setattr(api, "load_config", lambda: {"SENDSPIN_PAIRING": True})

    response = _app_client().post("/api/pairing/window", json={"player_name": "Kitchen"})

    assert response.status_code == 400


def test_pairing_window_rejects_disabled_pairing(monkeypatch):
    monkeypatch.setattr(api, "load_config", lambda: {"SENDSPIN_PAIRING": False})

    response = _app_client().post("/api/pairing/window", json={"player_id": "stable-player-id"})

    assert response.status_code == 409
    assert response.get_json()["success"] is False


def test_pairing_window_rejects_missing_runtime_loop(monkeypatch):
    runtime_client = _runtime_client()
    monkeypatch.setattr(api, "load_config", lambda: {"SENDSPIN_PAIRING": True})
    monkeypatch.setattr(
        api,
        "get_device_registry_snapshot",
        lambda: SimpleNamespace(active_clients=[runtime_client]),
    )
    monkeypatch.setattr(api, "get_main_loop", lambda: None)

    response = _app_client().post("/api/pairing/window", json={"player_id": "stable-player-id"})

    assert response.status_code == 503
    assert runtime_client.commands == []


def test_pairing_window_rejects_missing_daemon(monkeypatch):
    runtime_client = _runtime_client(running=False)
    monkeypatch.setattr(api, "load_config", lambda: {"SENDSPIN_PAIRING": True})
    monkeypatch.setattr(
        api,
        "get_device_registry_snapshot",
        lambda: SimpleNamespace(active_clients=[runtime_client]),
    )

    response = _app_client().post("/api/pairing/window", json={"player_id": "stable-player-id"})

    assert response.status_code == 409
    assert runtime_client.commands == []


def test_pairing_window_does_not_claim_success_when_scheduling_fails(monkeypatch):
    runtime_client = _runtime_client()
    monkeypatch.setattr(api, "load_config", lambda: {"SENDSPIN_PAIRING": True})
    monkeypatch.setattr(
        api,
        "get_device_registry_snapshot",
        lambda: SimpleNamespace(active_clients=[runtime_client]),
    )
    monkeypatch.setattr(api, "get_main_loop", lambda: object())
    monkeypatch.setattr(
        api.asyncio, "run_coroutine_threadsafe", lambda _coro, _loop: (_ for _ in ()).throw(RuntimeError())
    )

    response = _app_client().post("/api/pairing/window", json={"player_id": "stable-player-id"})

    assert response.status_code == 503
    assert response.get_json()["success"] is False
