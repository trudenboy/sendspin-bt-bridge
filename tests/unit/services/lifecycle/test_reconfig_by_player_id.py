"""ReconfigOrchestrator finds clients by config id, falling back to MAC."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sendspin_bridge.services.bluetooth import device_registry
from sendspin_bridge.services.bluetooth.device_registry import build_device_registry_snapshot
from sendspin_bridge.services.infrastructure.config_diff import ActionKind, ReconfigAction
from sendspin_bridge.services.lifecycle.reconfig_orchestrator import ReconfigOrchestrator

MAC = "AA:BB:CC:DD:EE:FF"


def _hot_client(config_id, mac=None):
    calls: list[dict] = []

    async def apply_hot_config(payload):
        calls.append(payload)
        return list(payload)

    client = SimpleNamespace(
        config_id=config_id,
        bt_manager=SimpleNamespace(mac_address=mac) if mac else None,
        apply_hot_config=apply_hot_config,
    )
    return client, calls


@pytest.fixture
def loop():
    """A loop running in a thread, as the bridge's loop is to a request thread."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()


def _hot(player_id=None, mac=None):
    return ReconfigAction(
        kind=ActionKind.HOT_APPLY,
        player_id=player_id,
        mac=mac,
        fields=["static_delay_ms"],
        payload={"static_delay_ms": 120},
        label="DAC",
    )


def test_hot_apply_targets_the_client_with_that_config_id(loop):
    dac, dac_calls = _hot_client("dac-1")
    other, other_calls = _hot_client("dac-2")
    snapshot = build_device_registry_snapshot(active_clients=[other, dac], disabled_devices=[])

    summary = ReconfigOrchestrator(loop, snapshot).apply([_hot(player_id="dac-1")])

    assert dac_calls == [{"static_delay_ms": 120}]
    assert other_calls == []
    assert summary.errors == []


def test_a_client_without_config_id_is_found_by_mac(loop):
    speaker, calls = _hot_client(None, mac=MAC)
    snapshot = build_device_registry_snapshot(active_clients=[speaker], disabled_devices=[])

    ReconfigOrchestrator(loop, snapshot).apply([_hot(player_id="bt-1", mac=MAC)])

    assert calls == [{"static_delay_ms": 120}]


def test_a_mac_match_owned_by_another_player_id_is_not_used(loop):
    speaker, calls = _hot_client("bt-other", mac=MAC)
    snapshot = build_device_registry_snapshot(active_clients=[speaker], disabled_devices=[])

    summary = ReconfigOrchestrator(loop, snapshot).apply([_hot(player_id="bt-1", mac=MAC)])

    assert calls == []
    # Not running: the value is persisted, nothing to push.
    assert len(summary.hot_applied) == 1


def test_removing_a_player_drops_only_its_client_by_config_id():
    dac = MagicMock(config_id="dac-1", bt_manager=None)
    other = MagicMock(config_id="dac-2", bt_manager=None)
    device_registry.mutate_active_clients(lambda _current: [dac, other])
    try:
        snapshot = device_registry.get_device_registry_snapshot()
        summary = ReconfigOrchestrator(None, snapshot).apply(
            [ReconfigAction(kind=ActionKind.STOP_CLIENT, player_id="dac-1", fields=["removed"], label="DAC")]
        )

        active = device_registry.get_device_registry_snapshot().active_clients
        assert dac not in active
        assert other in active
        assert len(summary.stopped) == 1
    finally:
        device_registry.mutate_active_clients(lambda _current: [])


def test_global_restart_reaches_clients_without_a_mac(monkeypatch):
    dac = MagicMock(config_id="dac-1", bt_manager=None)
    speaker = MagicMock(config_id=None)
    speaker.bt_manager.mac_address = MAC
    for client in (dac, speaker):
        client.is_running.return_value = True
    snapshot = build_device_registry_snapshot(active_clients=[dac, speaker], disabled_devices=[])
    restarted: list[list] = []

    async def _sequence(clients):
        restarted.append(clients)

    loop = asyncio.new_event_loop()
    try:
        orch = ReconfigOrchestrator(loop, snapshot)
        monkeypatch.setattr(orch, "_global_restart_sequence", _sequence)
        monkeypatch.setattr(orch, "_schedule_background", lambda coro, *, description: loop.run_until_complete(coro))
        orch.apply([ReconfigAction(kind=ActionKind.GLOBAL_RESTART, fields=["SENDSPIN_PORT"], label="global")])
    finally:
        loop.close()

    assert len(restarted) == 1
    assert {id(c) for c in restarted[0]} == {id(dac), id(speaker)}


def test_a_player_moved_to_a_new_mac_stops_the_old_client_and_activates_a_new_one(monkeypatch):
    from sendspin_bridge.services.bluetooth.device_activation import ActivationResult

    old_client = MagicMock(config_id="bt-1")
    old_client.bt_manager.mac_address = MAC
    new_mac = "11:22:33:44:55:66"
    new_client = SimpleNamespace(
        config_id="bt-1", bt_manager=SimpleNamespace(mac_address=new_mac), run=lambda: _noop_coro()
    )
    activate = MagicMock(
        return_value=ActivationResult(
            client=new_client, bt_manager=new_client.bt_manager, bt_available=True, listen_port=8928
        )
    )
    monkeypatch.setattr("sendspin_bridge.services.bluetooth.device_activation.activate_device", activate)

    class _Future:
        def add_done_callback(self, _cb):
            pass

    def _schedule(coro, _loop):
        coro.close()
        return _Future()

    monkeypatch.setattr(
        "sendspin_bridge.services.lifecycle.reconfig_orchestrator.asyncio.run_coroutine_threadsafe", _schedule
    )
    device_registry.mutate_active_clients(lambda _current: [old_client])
    loop = asyncio.new_event_loop()
    try:
        orch = ReconfigOrchestrator(
            loop, device_registry.get_device_registry_snapshot(), activation_context=MagicMock()
        )
        summary = orch.apply(
            [
                ReconfigAction(kind=ActionKind.STOP_CLIENT, player_id="bt-1", mac=MAC, fields=["removed"], label="K"),
                ReconfigAction(
                    kind=ActionKind.START_CLIENT,
                    player_id="bt-1",
                    mac=new_mac,
                    fields=["added"],
                    payload={"device": {"id": "bt-1", "mac": new_mac, "player_name": "K"}, "device_index": 0},
                    label="K",
                ),
            ]
        )
        active = device_registry.get_device_registry_snapshot().active_clients
    finally:
        loop.close()
        device_registry.mutate_active_clients(lambda _current: [])

    old_client.bt_manager.shutdown.assert_called_once()
    old_client.set_bt_management_enabled.assert_called_once_with(False)
    activate.assert_called_once()
    assert activate.call_args.args[0]["mac"] == new_mac
    assert active == [new_client]
    assert summary.errors == []
    assert len(summary.started) == 1


async def _noop_coro():
    return None
