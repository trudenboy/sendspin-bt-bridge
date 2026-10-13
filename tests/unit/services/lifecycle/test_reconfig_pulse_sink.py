"""Live reconfig of ``pulse_sink`` players: start, stop and restart without Bluetooth."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import sendspin_bridge.services.lifecycle.reconfig_orchestrator as reconfig_module
from sendspin_bridge.services.audio.sink_inventory import LocalSink
from sendspin_bridge.services.bluetooth import device_registry
from sendspin_bridge.services.bluetooth.device_activation import DeviceActivationContext
from sendspin_bridge.services.infrastructure.config_diff import ActionKind, ReconfigAction
from sendspin_bridge.services.lifecycle.reconfig_orchestrator import ReconfigOrchestrator

SINK = "alsa_output.usb-Generic_USB_DAC_0000A1-00.analog-stereo"


def _sink(name: str, **ids: str) -> LocalSink:
    return LocalSink(name, name, "idle", "s16le 2ch 48000Hz", False, 100, None, None, dict(ids))


class _Client:
    """Stands in for SendspinClient: records what the orchestrator does to it."""

    def __init__(self, player_name, server_host, server_port, bt_manager, **kwargs):
        self.player_name = player_name
        self.player_id = f"uuid5-{player_name}"
        self.listen_port = kwargs.get("listen_port")
        self.bt_manager = bt_manager
        self.bluetooth_sink_name = None
        self.status: dict = {}
        self.events: list[str] = []
        self.running = True

    def _update_status(self, updates):
        self.status.update(updates)

    def set_bt_management_enabled(self, _enabled):
        raise AssertionError("no Bluetooth calls on a pulse_sink client")

    async def stop_sendspin(self):
        self.events.append("stop_sendspin")

    async def run(self):
        self.events.append("run")


def _context() -> DeviceActivationContext:
    return DeviceActivationContext(
        server_host="auto",
        server_port=9000,
        effective_bridge="",
        prefer_sbc=False,
        bt_check_interval=15,
        bt_max_reconnect_fails=10,
        bt_churn_threshold=0,
        bt_churn_window=300.0,
        enable_a2dp_sink_recovery_dance=False,
        enable_pa_module_reload=False,
        enable_adapter_auto_recovery=False,
        base_listen_port=8928,
        client_factory=_Client,
        bt_manager_factory=MagicMock(side_effect=AssertionError("no Bluetooth for pulse_sink")),
    )


@pytest.fixture
def loop():
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop
    loop.call_soon_threadsafe(loop.stop)
    thread.join()
    loop.close()


@pytest.fixture
def registry():
    device_registry.mutate_active_clients(lambda _current: [])
    yield device_registry
    device_registry.mutate_active_clients(lambda _current: [])


@pytest.fixture
def sinks(monkeypatch):
    present: list[LocalSink] = [_sink(SINK, serial="Generic_USB_DAC_0000A1")]
    reads: list[int] = []

    def _read(_loop):
        reads.append(1)
        return list(present)

    monkeypatch.setattr(reconfig_module, "_read_local_sinks", _read)
    return SimpleNamespace(present=present, reads=reads)


def _player(player_id="dac-1", name="Desk DAC", sink=SINK, **extra) -> dict:
    return {"id": player_id, "name": name, "backend": {"type": "pulse_sink", "sink": sink, "match": {}}, **extra}


def _start(player: dict, index: int = 1) -> ReconfigAction:
    return ReconfigAction(
        kind=ActionKind.START_CLIENT,
        player_id=player["id"],
        fields=["added"],
        payload={"device": player, "device_index": index},
        label=player["name"],
    )


def _wait_for(predicate, timeout=2.0):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_adding_a_pulse_sink_player_activates_and_runs_it(loop, registry, sinks):
    orch = ReconfigOrchestrator(loop, registry.get_device_registry_snapshot(), activation_context=_context())

    summary = orch.apply([_start(_player(), index=3)])

    active = registry.get_device_registry_snapshot().active_clients
    assert len(active) == 1
    client = active[0]
    assert client.player_id == "dac-1"
    assert client.bluetooth_sink_name == SINK
    assert client.listen_port == 8928 + 3
    assert summary.errors == []
    assert [s["label"] for s in summary.started] == ["Desk DAC"]
    assert summary.restart_required == []
    assert _wait_for(lambda: client.events == ["run"])


def test_a_sink_already_played_by_a_running_player_is_not_taken(loop, registry, sinks):
    first = _Client("Desk DAC", "auto", 9000, None)
    first.config_id = "dac-1"
    first.backend_type = "pulse_sink"
    first.bluetooth_sink_name = SINK
    registry.mutate_active_clients(lambda _current: [first])
    orch = ReconfigOrchestrator(loop, registry.get_device_registry_snapshot(), activation_context=_context())

    orch.apply([_start(_player("dac-2", "Twin"))])

    second = next(c for c in registry.get_device_registry_snapshot().active_clients if c is not first)
    assert second.bluetooth_sink_name is None
    assert second.status["sink_match_status"] == "sink_in_use"
    assert _wait_for(lambda: second.events == ["run"])


def test_an_added_player_does_not_take_a_twin_dac_a_running_player_without_output_owns(
    loop, registry, sinks, monkeypatch
):
    """B's player runs without its output; B's DAC is present under the name A stored."""
    twin_ids = {"vendor_id": "0x262a", "product_id": "0x3b3c"}
    base = "alsa_output.usb-Generic_USB_DAC-00.analog-stereo"
    port_a, port_b = "pci-0000:00:14.0-usb-0:2:1.0", "pci-0000:00:14.0-usb-0:3:1.0"
    monkeypatch.setattr("sendspin_bridge.services.bluetooth.device_activation.record_rematched_sink", lambda *a: None)
    running_b = _Client("Right", "auto", 9000, None)
    running_b.config_id = "dac-b"
    running_b.backend_type = "pulse_sink"
    running_b.sink_match = {**twin_ids, "bus_path": port_b}
    running_b.configured_sink = base + ".2"
    registry.mutate_active_clients(lambda _current: [running_b])
    sinks.present[:] = [_sink(base, **twin_ids, bus_path=port_b)]
    orch = ReconfigOrchestrator(loop, registry.get_device_registry_snapshot(), activation_context=_context())

    player_a = _player("dac-a", "Left", sink=base)
    player_a["backend"]["match"] = {**twin_ids, "bus_path": port_a}
    orch.apply([_start(player_a)])

    added = next(c for c in registry.get_device_registry_snapshot().active_clients if c is not running_b)
    assert added.bluetooth_sink_name is None
    assert added.status["sink_match_status"] == "sink_missing"


def test_an_added_player_whose_output_is_unplugged_is_saved_and_runs_without_a_sink(loop, registry, sinks):
    sinks.present.clear()
    orch = ReconfigOrchestrator(loop, registry.get_device_registry_snapshot(), activation_context=_context())

    summary = orch.apply([_start(_player())])

    (client,) = registry.get_device_registry_snapshot().active_clients
    assert client.bluetooth_sink_name is None
    assert client.status["sink_missing"] is True
    assert summary.errors == []
    assert _wait_for(lambda: client.events == ["run"])


@pytest.mark.parametrize("fields", [["enabled"], ["removed"]])
def test_stopping_a_pulse_sink_player_stops_its_daemon_without_bluetooth(loop, registry, sinks, fields):
    orch = ReconfigOrchestrator(loop, registry.get_device_registry_snapshot(), activation_context=_context())
    orch.apply([_start(_player())])
    (client,) = registry.get_device_registry_snapshot().active_clients

    orch = ReconfigOrchestrator(loop, registry.get_device_registry_snapshot(), activation_context=_context())
    summary = orch.apply(
        [ReconfigAction(kind=ActionKind.STOP_CLIENT, player_id="dac-1", fields=fields, label="Desk DAC")]
    )

    assert summary.errors == []
    assert len(summary.stopped) == 1
    assert client.running is False
    assert _wait_for(lambda: "stop_sendspin" in client.events)
    assert registry.get_device_registry_snapshot().active_clients == []


def test_enabling_a_stopped_pulse_sink_player_again_starts_a_fresh_client(loop, registry, sinks):
    orch = ReconfigOrchestrator(loop, registry.get_device_registry_snapshot(), activation_context=_context())
    orch.apply([_start(_player())])
    orch = ReconfigOrchestrator(loop, registry.get_device_registry_snapshot(), activation_context=_context())
    orch.apply([ReconfigAction(kind=ActionKind.STOP_CLIENT, player_id="dac-1", fields=["enabled"], label="D")])

    orch = ReconfigOrchestrator(loop, registry.get_device_registry_snapshot(), activation_context=_context())
    summary = orch.apply([_start(_player())])

    (client,) = registry.get_device_registry_snapshot().active_clients
    assert client.bluetooth_sink_name == SINK
    assert len(summary.started) == 1
    assert _wait_for(lambda: client.events == ["run"])


def test_a_restart_field_replaces_the_pulse_sink_client_after_the_old_daemon_stopped(loop, registry, sinks):
    other = "alsa_output.pci-0000_00_1b.0.analog-stereo"
    sinks.present.append(_sink(other))
    orch = ReconfigOrchestrator(loop, registry.get_device_registry_snapshot(), activation_context=_context())
    orch.apply([_start(_player(), index=2)])
    (old,) = registry.get_device_registry_snapshot().active_clients
    assert _wait_for(lambda: old.events == ["run"])

    moved = _player(sink=other, name="Desk DAC 2")
    orch = ReconfigOrchestrator(loop, registry.get_device_registry_snapshot(), activation_context=_context())
    summary = orch.apply(
        [
            ReconfigAction(
                kind=ActionKind.WARM_RESTART,
                player_id="dac-1",
                fields=["player_name", "sink"],
                payload={"device": moved, "device_index": 2},
                label="Desk DAC 2",
            )
        ]
    )

    (new,) = registry.get_device_registry_snapshot().active_clients
    assert new is not old
    assert new.player_id == "dac-1"
    assert new.player_name == "Desk DAC 2"
    assert new.bluetooth_sink_name == other
    assert new.listen_port == 8928 + 2
    assert old.running is False
    assert summary.errors == []
    assert len(summary.warm_restarting) == 1
    # The old daemon is gone before the new one starts.
    assert _wait_for(lambda: new.events == ["run"])
    assert old.events == ["run", "stop_sendspin"]
