"""A ``pulse_sink`` client: its daemon plays to its own sink, or does not run at all."""

from __future__ import annotations

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from sendspin_bridge.bridge.client import SendspinClient
from sendspin_bridge.bridge.orchestrator import BridgeOrchestrator
from sendspin_bridge.services.audio.sink_inventory import LocalSink

SINK = "alsa_output.usb-Generic_USB_DAC_0000A1-00.analog-stereo"


def _pulse_client(sink: str | None) -> SendspinClient:
    client = SendspinClient("Desk DAC", "localhost", 9000)
    client.player_id = client.config_id = "dac-1"
    client.backend_type = "pulse_sink"
    client.volume_controller = "sendspin"
    client.bluetooth_sink_name = sink
    client._start_sendspin_lock = asyncio.Lock()
    return client


def _capture_spawn(monkeypatch):
    monkeypatch.setattr("sendspin_bridge.bridge.client.find_available_bind_port", lambda *a, **kw: 8930)
    spawned: list[tuple[dict, dict]] = []

    async def _fake_exec(*args, env=None, **_kw):
        spawned.append((json.loads(args[3]), dict(env or {})))
        raise RuntimeError("intentional short-circuit")

    return spawned, _fake_exec


@pytest.mark.asyncio
async def test_a_pulse_sink_daemon_is_spawned_on_its_sink_with_the_sendspin_volume_controller(monkeypatch):
    client = _pulse_client(SINK)
    spawned, fake_exec = _capture_spawn(monkeypatch)

    with (
        patch("sendspin_bridge.bridge.client.asyncio.create_subprocess_exec", side_effect=fake_exec),
        patch.object(client, "stop_sendspin", return_value=None),
    ):
        await client.start_sendspin()

    assert len(spawned) == 1
    params, env = spawned[0]
    assert params["bluetooth_sink_name"] == SINK
    assert params["client_id"] == "dac-1"
    assert params["volume_controller"] == "sendspin"
    assert env["PULSE_SINK"] == SINK


@pytest.mark.asyncio
async def test_a_pulse_sink_daemon_never_starts_without_its_sink(monkeypatch):
    client = _pulse_client(None)
    spawned, fake_exec = _capture_spawn(monkeypatch)

    with patch("sendspin_bridge.bridge.client.asyncio.create_subprocess_exec", side_effect=fake_exec):
        await client.start_sendspin()

    assert spawned == []
    assert client.status["sink_missing"] is True


@pytest.mark.asyncio
async def test_a_bluetooth_daemon_keeps_the_pulseaudio_volume_controller(monkeypatch):
    client = SendspinClient("Kitchen", "localhost", 9000)
    client._start_sendspin_lock = asyncio.Lock()
    client.bluetooth_sink_name = "bluez_sink.AA_BB_CC_DD_EE_FF.a2dp_sink"
    spawned, fake_exec = _capture_spawn(monkeypatch)

    with (
        patch("sendspin_bridge.bridge.client.asyncio.create_subprocess_exec", side_effect=fake_exec),
        patch.object(client, "stop_sendspin", return_value=None),
    ):
        await client.start_sendspin()

    params, env = spawned[0]
    assert params["volume_controller"] == "pa"
    # The Bluetooth spawn environment is left as it was.
    assert env.get("PULSE_SINK") == os.environ.get("PULSE_SINK")


@pytest.mark.asyncio
async def test_shutdown_never_mutes_an_output_whose_volume_the_bridge_does_not_control():
    orchestrator = BridgeOrchestrator()
    muted: list[str] = []

    async def fake_mute(sink: str, _flag: bool) -> bool:
        muted.append(sink)
        return True

    async def no_volume(_sink: str) -> int | None:
        return None

    async def _stop() -> None:
        return None

    shared = SimpleNamespace(
        player_name="HA output",
        bluetooth_sink_name="alsa_output.pci.analog-stereo",
        bt_manager=None,
        volume_controller="sendspin",
        running=True,
        stop_sendspin=_stop,
    )
    own = SimpleNamespace(
        player_name="Desk DAC",
        bluetooth_sink_name=SINK,
        bt_manager=None,
        volume_controller="pa",
        running=True,
        stop_sendspin=_stop,
    )
    speaker = SimpleNamespace(
        player_name="Kitchen",
        bluetooth_sink_name="bluez_sink.AA.a2dp_sink",
        bt_manager=SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF"),
        running=True,
        stop_sendspin=_stop,
    )

    await orchestrator.graceful_shutdown(
        clients=[shared, own, speaker], mute_sink=fake_mute, get_sink_volume=no_volume, save_volume=lambda *_: None
    )

    assert muted == [SINK, "bluez_sink.AA.a2dp_sink"]


# ── Startup: initialize_devices ──────────────────────────────────────────────


class _FakeClient:
    def __init__(self, player_name, server_host, server_port, bt_manager, **kwargs):
        self.player_name = player_name
        self.player_id = f"uuid5-{player_name}"
        self.listen_port = kwargs["listen_port"]
        self.bt_manager = bt_manager
        self.bluetooth_sink_name = None
        self.bt_management_enabled = True
        self.status: dict = {}

    def _update_status(self, updates):
        self.status.update(updates)


def _sink(name: str, **ids: str) -> LocalSink:
    return LocalSink(name, name, "idle", "s16le 2ch 48000Hz", False, 100, None, None, dict(ids))


def _bootstrap(players: list[dict]):
    from sendspin_bridge.config.players import device_entries

    config = {"CONFIG_SCHEMA_VERSION": 6, "players": players, "PLAYER_STATE": {}}
    return SimpleNamespace(
        config=config,
        device_configs=device_entries(config),
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
        enable_rssi_badge=False,
        bluetooth_adapters=[],
        log_level="INFO",
        pulse_latency_msec=600,
    )


def _bt_factory(mac, **_kwargs):
    return SimpleNamespace(mac_address=mac, check_bluetooth_available=lambda: True)


def test_startup_activates_pulse_sink_players_in_list_order_after_every_bluetooth_port():
    players = [
        {"id": "dac-1", "name": "Desk", "backend": {"type": "pulse_sink", "sink": "alsa_output.a"}},
        {"id": "bt-1", "name": "Kitchen", "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:01"}},
        {"id": "dac-2", "name": "Twin", "backend": {"type": "pulse_sink", "sink": "alsa_output.a"}},
        {"id": "bt-2", "name": "Bath", "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:02"}},
    ]

    result = BridgeOrchestrator().initialize_devices(
        _bootstrap(players),
        client_factory=_FakeClient,
        bt_manager_factory=_bt_factory,
        base_listen_port=9000,
        default_player_name="Sendspin",
        local_sinks=[_sink("alsa_output.a")],
    )

    by_name = {c.player_name: c for c in result.clients}
    assert set(by_name) == {"Desk", "Kitchen", "Twin", "Bath"}
    # Bluetooth ports do not move; the others follow every Bluetooth player.
    assert by_name["Kitchen"].listen_port == 9000
    assert by_name["Bath"].listen_port == 9001
    assert by_name["Desk"].listen_port == 9002
    assert by_name["Twin"].listen_port == 9003
    # The first player in list order gets the sink; the other reports it in use.
    assert by_name["Desk"].bluetooth_sink_name == "alsa_output.a"
    assert by_name["Twin"].bluetooth_sink_name is None
    assert by_name["Twin"].status["sink_match_status"] == "sink_in_use"
    assert by_name["Desk"].player_id == "dac-1"


def test_startup_lists_a_disabled_pulse_sink_player_with_the_disabled_devices():
    players = [
        {"id": "dac-1", "name": "Desk", "enabled": False, "backend": {"type": "pulse_sink", "sink": "alsa_output.a"}},
    ]

    result = BridgeOrchestrator().initialize_devices(
        _bootstrap(players),
        client_factory=_FakeClient,
        bt_manager_factory=_bt_factory,
        default_player_name="Sendspin",
        local_sinks=[_sink("alsa_output.a")],
    )

    assert result.clients == []
    assert result.disabled_devices == [
        {"id": "dac-1", "player_name": "Desk", "backend_type": "pulse_sink", "mac": None, "enabled": False}
    ]


@pytest.mark.asyncio
async def test_startup_reads_the_local_sinks_only_when_a_pulse_sink_player_is_configured(monkeypatch):
    import sendspin_bridge.bridge.orchestrator as orch_module

    reads: list[int] = []

    async def fake_list():
        reads.append(1)
        return [_sink("alsa_output.a")]

    monkeypatch.setattr(orch_module, "list_local_sinks", fake_list)
    orchestrator = BridgeOrchestrator()

    bt_only = _bootstrap(
        [{"id": "bt-1", "name": "K", "backend": {"type": "bluetooth_a2dp", "mac": "AA:BB:CC:DD:EE:01"}}]
    )
    assert await orchestrator.read_local_sinks_for(bt_only) == []
    assert reads == []

    with_dac = _bootstrap([{"id": "dac-1", "name": "D", "backend": {"type": "pulse_sink", "sink": "alsa_output.a"}}])
    assert [s.name for s in await orchestrator.read_local_sinks_for(with_dac)] == ["alsa_output.a"]
    assert reads == [1]
