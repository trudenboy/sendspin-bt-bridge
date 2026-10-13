"""Activating a ``pulse_sink`` player: a client without Bluetooth, fixed to its matched sink."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import sendspin_bridge.bridge.state as state
from sendspin_bridge.services.audio.sink_inventory import LocalSink
from sendspin_bridge.services.bluetooth.device_activation import DeviceActivationContext, activate_player

PLAYER_ID = "5f1c3a52-7f7e-4c55-9d0e-0c2f1f0a9b11"
USB_SINK = "alsa_output.usb-Generic_USB_DAC_0000A1-00.analog-stereo"


def _sink(name: str, **ids: str) -> LocalSink:
    return LocalSink(name, name, "idle", "s16le 2ch 48000Hz", False, 100, None, None, dict(ids))


def _fake_client(player_name: str, server_host, server_port, bt_manager, **kwargs) -> SimpleNamespace:
    status: dict[str, object] = {}
    return SimpleNamespace(
        player_name=player_name,
        player_id=f"uuid5-of-{player_name}",
        listen_port=kwargs.get("listen_port"),
        keep_alive_method=kwargs.get("keep_alive_method"),
        bt_manager=bt_manager,
        bluetooth_sink_name=None,
        status=status,
        _update_status=status.update,
        set_bt_management_enabled=MagicMock(),
    )


def _context(bt_manager_factory=None, base_listen_port: int = 8928) -> DeviceActivationContext:
    return DeviceActivationContext(
        server_host="auto",
        server_port=9000,
        effective_bridge="",
        prefer_sbc=True,
        bt_check_interval=15,
        bt_max_reconnect_fails=10,
        bt_churn_threshold=0,
        bt_churn_window=300.0,
        enable_a2dp_sink_recovery_dance=False,
        enable_pa_module_reload=False,
        enable_adapter_auto_recovery=False,
        base_listen_port=base_listen_port,
        client_factory=_fake_client,
        bt_manager_factory=bt_manager_factory or MagicMock(side_effect=AssertionError("no Bluetooth for pulse_sink")),
    )


def _player(**overrides) -> dict:
    player = {
        "id": PLAYER_ID,
        "name": "Desk DAC",
        "enabled": True,
        "backend": {"type": "pulse_sink", "sink": USB_SINK, "match": {"serial": "Generic_USB_DAC_0000A1"}},
    }
    player.update(overrides)
    return player


def test_a_present_sink_gives_a_client_fixed_to_it_with_the_config_id_as_its_identity():
    sinks = [_sink(USB_SINK, serial="Generic_USB_DAC_0000A1"), _sink("alsa_output.pci.analog-stereo")]

    result = activate_player(_player(), index=2, context=_context(), default_player_name="Fallback", local_sinks=sinks)

    client = result.client
    assert result.bt_manager is None
    assert client.bt_manager is None
    assert client.player_id == PLAYER_ID
    assert client.config_id == PLAYER_ID
    assert client.backend_type == "pulse_sink"
    assert client.bluetooth_sink_name == USB_SINK
    assert client.volume_controller == "sendspin"
    assert client.status["backend_type"] == "pulse_sink"
    assert client.status["audio_sink"] == USB_SINK
    assert client.status["sink_missing"] is False
    assert client.status["sink_match_status"] == "ok"
    assert result.listen_port == 8928 + 2


def test_an_explicit_volume_controller_is_kept():
    sinks = [_sink(USB_SINK, serial="Generic_USB_DAC_0000A1")]

    result = activate_player(
        _player(volume_controller="pa"), index=0, context=_context(), default_player_name="F", local_sinks=sinks
    )

    assert result.client.volume_controller == "pa"


def test_keep_alive_never_defaults_to_infrasound_for_an_audio_output():
    result = activate_player(
        _player(idle_mode="keep_alive"), index=0, context=_context(), default_player_name="F", local_sinks=[]
    )

    assert result.client.keep_alive_method == "silence"


def test_a_missing_sink_leaves_the_client_without_a_sink():
    result = activate_player(
        _player(), index=0, context=_context(), default_player_name="F", local_sinks=[_sink("alsa_output.pci.hdmi")]
    )

    client = result.client
    assert client.bluetooth_sink_name is None
    assert client.status["sink_missing"] is True
    assert client.status["sink_match_status"] == "sink_missing"
    assert client.status["audio_sink"] == USB_SINK
    assert client.player_id == PLAYER_ID


def test_a_sink_claimed_by_an_earlier_player_is_not_shared():
    sinks = [_sink(USB_SINK, serial="Generic_USB_DAC_0000A1")]

    result = activate_player(
        _player(), index=0, context=_context(), default_player_name="F", local_sinks=sinks, claimed={USB_SINK}
    )

    assert result.client.bluetooth_sink_name is None
    assert result.client.status["sink_missing"] is True
    assert result.client.status["sink_match_status"] == "sink_in_use"


def test_a_renamed_sink_found_by_serial_updates_the_config_and_is_reported(tmp_config):
    tmp_config.write_text(json.dumps({"CONFIG_SCHEMA_VERSION": 6, "players": [_player()], "PLAYER_STATE": {}}))
    renamed = "alsa_output.usb-Generic_USB_DAC_0000A1-01.analog-stereo"
    state.clear_device_events(PLAYER_ID)
    try:
        result = activate_player(
            _player(),
            index=0,
            context=_context(),
            default_player_name="F",
            local_sinks=[_sink(renamed, serial="Generic_USB_DAC_0000A1")],
        )
        events = state.get_device_events(PLAYER_ID)
    finally:
        state.clear_device_events(PLAYER_ID)

    assert result.client.bluetooth_sink_name == renamed
    assert result.client.status["audio_sink"] == renamed
    saved = json.loads(tmp_config.read_text())["players"][0]["backend"]
    assert saved["sink"] == renamed
    assert saved["match"] == {"serial": "Generic_USB_DAC_0000A1"}
    assert [e["event_type"] for e in events] == ["sink-rematched"]
    assert events[0]["details"]["sink"] == renamed
    assert events[0]["details"]["previous_sink"] == USB_SINK
    assert events[0]["details"]["method"] == "serial"


def test_a_sink_found_under_its_stored_name_writes_nothing(tmp_config):
    tmp_config.write_text(json.dumps({"CONFIG_SCHEMA_VERSION": 6, "players": [_player()], "PLAYER_STATE": {}}))
    before = tmp_config.read_text()

    activate_player(
        _player(),
        index=0,
        context=_context(),
        default_player_name="F",
        local_sinks=[_sink(USB_SINK, serial="Generic_USB_DAC_0000A1")],
    )

    assert tmp_config.read_text() == before


def test_a_bluetooth_player_takes_the_bluetooth_path():
    created: list[str] = []

    def bt_factory(mac, **_kwargs):
        created.append(mac)
        return SimpleNamespace(mac_address=mac, check_bluetooth_available=lambda: True)

    device = {"id": "bt-1", "mac": "AA:BB:CC:DD:EE:FF", "player_name": "Kitchen"}

    result = activate_player(device, index=0, context=_context(bt_factory), default_player_name="F")

    assert created == ["AA:BB:CC:DD:EE:FF"]
    assert result.client.bt_manager is result.bt_manager
    # The Bluetooth runtime id is left as the client computed it (Music Assistant identity).
    assert result.client.player_id == "uuid5-of-Kitchen"
    assert result.client.config_id == "bt-1"


def test_the_saved_volume_is_restored_by_player_id():
    from dataclasses import replace

    sinks = [_sink(USB_SINK, serial="Generic_USB_DAC_0000A1")]
    asked: list[str] = []

    def load_volume(player_id: str) -> int | None:
        asked.append(player_id)
        return 42

    ctx = replace(_context(), load_player_volume_fn=load_volume)

    result = activate_player(_player(), index=0, context=ctx, default_player_name="F", local_sinks=sinks)

    assert asked == [PLAYER_ID]
    # The start volume the daemon applies to its stream (``volume_controller: sendspin``).
    assert result.client.status["volume"] == 42


def test_an_out_of_range_saved_volume_is_ignored():
    from dataclasses import replace

    ctx = replace(_context(), load_player_volume_fn=lambda _pid: 140)

    result = activate_player(_player(), index=0, context=ctx, default_player_name="F", local_sinks=[])

    assert "volume" not in result.client.status


def test_the_client_keeps_its_match_and_configured_sink_for_hotplug():
    result = activate_player(_player(), index=0, context=_context(), default_player_name="F", local_sinks=[])

    assert result.client.sink_match == {"serial": "Generic_USB_DAC_0000A1"}
    assert result.client.configured_sink == USB_SINK


def test_the_startup_reset_is_asked_for_only_when_the_setting_is_on():
    sinks = [_sink(USB_SINK, serial="Generic_USB_DAC_0000A1")]
    from dataclasses import replace

    off = activate_player(_player(), index=0, context=_context(), default_player_name="x", local_sinks=sinks)
    on = activate_player(
        _player(),
        index=0,
        context=replace(_context(), pulse_sink_reset_on_start=True),
        default_player_name="x",
        local_sinks=sinks,
    )

    assert off.client.reset_output_on_start is False
    assert on.client.reset_output_on_start is True
