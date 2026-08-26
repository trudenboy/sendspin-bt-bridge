"""Tests for bluetooth_manager.py — BluetoothManager class.

bluetooth_manager.py imports services.pulse (which gracefully handles missing
pulsectl_asyncio) and only imports ``dbus`` inside function bodies.  No
module-level sys.modules stubbing is needed for Python 3.9 compatibility.
"""

from unittest.mock import MagicMock, patch

import pytest

from tests.support.fake_dbus import attach, bluez_knowing, silent, unreachable

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config and prevent accidental access to the host audio server."""
    import sendspin_bridge.config as config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")
    monkeypatch.setattr("sendspin_bridge.bluetooth.audio.cycle_card_profile", lambda *_args, **_kwargs: False)


@pytest.fixture()
def bt_manager(installed_bluez):
    """Create a BluetoothManager with reasonable defaults for testing.

    Experimental flags are enabled by default in this fixture so the existing
    recovery-path tests exercise the full code path. Tests that need to verify
    the default-off behavior should construct their own manager explicitly.

    The fake reports no default controller (``show`` → empty) so adapter
    resolution in ``__init__`` lands on no adapter / no D-Bus path, matching
    the historical ``check_output("")`` guard.
    """
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    installed_bluez.on("show", stdout="")
    mgr = BluetoothManager(
        mac_address="AA:BB:CC:DD:EE:FF",
        device_name="TestSpeaker",
        enable_a2dp_dance=True,
        enable_pa_module_reload=True,
    )
    # BlueZ knows nothing about this speaker unless a test says otherwise —
    # the state the bluetoothctl fallback exists for, and the fixture's
    # historical default.
    silent(mgr)
    return mgr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# BluetoothManager._resolve_adapter_select — hciN -> MAC resolution
# ---------------------------------------------------------------------------


def test_bt_executor_pool_size():
    """The module-level thread pool must have at least 4 workers."""
    from sendspin_bridge.bluetooth.manager import _bt_executor

    assert _bt_executor._max_workers >= 4


def test_running_flag_default(bt_manager):
    """BluetoothManager instances must start with _running = True."""
    assert bt_manager._running is True


def test_shutdown_sets_running_false(bt_manager):
    """shutdown() must set _running to False."""
    bt_manager.shutdown()
    assert bt_manager._running is False


def test_configure_bluetooth_audio_pipewire_pattern(bt_manager):
    """Finds a PipeWire-format sink (bluez_output.MAC.1)."""
    pa_mac = bt_manager.mac_address.replace(":", "_")
    sink_name = f"bluez_output.{pa_mac}.1"

    fake_sinks = [{"name": sink_name, "description": "BT Speaker"}]
    with (
        patch("sendspin_bridge.bluetooth.audio.list_sinks", return_value=fake_sinks),
        patch("sendspin_bridge.bluetooth.audio.get_sink_volume", return_value=50),
        patch("sendspin_bridge.bluetooth.audio.set_sink_mute", return_value=True),
        patch("sendspin_bridge.bluetooth.audio.set_sink_volume", return_value=True),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True


def test_configure_bluetooth_audio_pulseaudio_pattern(bt_manager):
    """Finds a PulseAudio-format sink (bluez_sink.MAC.a2dp_sink)."""
    pa_mac = bt_manager.mac_address.replace(":", "_")
    sink_name = f"bluez_sink.{pa_mac}.a2dp_sink"

    fake_sinks = [{"name": sink_name, "description": "BT Speaker"}]
    with (
        patch("sendspin_bridge.bluetooth.audio.list_sinks", return_value=fake_sinks),
        patch("sendspin_bridge.bluetooth.audio.get_sink_volume", return_value=50),
        patch("sendspin_bridge.bluetooth.audio.set_sink_mute", return_value=True),
        patch("sendspin_bridge.bluetooth.audio.set_sink_volume", return_value=True),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True


def test_configure_bluetooth_audio_pipewire_raw_mac_pattern(bt_manager):
    """Finds a WirePlumber-style sink published as ``bluez_output.{MAC_with_colons}``.

    Ubuntu 26.04 / PipeWire publishes BT sinks with the raw MAC (with
    colons) and no ``.1`` / ``.a2dp-sink`` suffix. Regression test for
    issue #314.
    """
    sink_name = f"bluez_output.{bt_manager.mac_address}"

    fake_sinks = [{"name": sink_name, "description": "BT Speaker"}]
    with (
        patch("sendspin_bridge.bluetooth.audio.list_sinks", return_value=fake_sinks),
        patch("sendspin_bridge.bluetooth.audio.get_sink_volume", return_value=50),
        patch("sendspin_bridge.bluetooth.audio.set_sink_mute", return_value=True),
        patch("sendspin_bridge.bluetooth.audio.set_sink_volume", return_value=True),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True


def test_configure_bluetooth_audio_no_sink(bt_manager):
    """Returns False when no matching sink is found."""
    with (
        patch("sendspin_bridge.bluetooth.audio.list_sinks", return_value=[]),
        patch("sendspin_bridge.bluetooth.audio.get_sink_volume", return_value=None),
        patch("sendspin_bridge.bluetooth.audio.set_sink_mute", return_value=True),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is False


@pytest.mark.parametrize("a2dp_profile_name", ["a2dp_sink", "a2dp-sink"])
def test_configure_bluetooth_audio_autoswitches_bluez_card_profile(bt_manager, a2dp_profile_name):
    """When no sink found but bluez_card exists with non-a2dp profile,
    switch to the A2DP-sink profile and retry once. Covers AKG Y500 /
    BlueZ 5.82 regression where the card connects in
    ``headset_head_unit``. Parametrized over both spellings of the
    profile name (PulseAudio underscore vs PipeWire dash). Issue #314.
    """
    pa_mac = bt_manager.mac_address.replace(":", "_")
    card_name = f"bluez_card.{pa_mac}"
    sink_name = f"bluez_sink.{pa_mac}.a2dp_sink"

    list_sinks_calls = {"count": 0}

    def _list_sinks():
        list_sinks_calls["count"] += 1
        # Sink appears only after profile switch
        if list_sinks_calls["count"] >= bt_audio_retry_threshold():
            return [{"name": sink_name, "description": "AKG Y500"}]
        return []

    def _get_sink_volume(name):
        # Sink only resolvable after profile switch
        if list_sinks_calls["count"] >= bt_audio_retry_threshold():
            return 50 if name == sink_name else None
        return None

    cards_payload = [
        {
            "name": card_name,
            "driver": "module-bluez5-device.c",
            "active_profile": "headset_head_unit",
            "profiles": ["off", a2dp_profile_name, "headset_head_unit"],
        }
    ]

    set_profile_calls = []

    def _set_card_profile(card, profile):
        set_profile_calls.append((card, profile))
        return True

    with (
        patch("sendspin_bridge.bluetooth.audio.list_sinks", side_effect=_list_sinks),
        patch("sendspin_bridge.bluetooth.audio.get_sink_volume", side_effect=_get_sink_volume),
        patch("sendspin_bridge.bluetooth.audio.set_sink_mute", return_value=True),
        patch("sendspin_bridge.bluetooth.audio.set_sink_volume", return_value=True),
        patch("sendspin_bridge.bluetooth.audio.list_cards", return_value=cards_payload),
        patch("sendspin_bridge.bluetooth.audio.set_card_profile", side_effect=_set_card_profile),
        patch("sendspin_bridge.bluetooth.audio._warn_pipewire_session"),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True
    # Whichever spelling the card advertises is the one passed to pactl.
    assert set_profile_calls == [(card_name, a2dp_profile_name)]


def bt_audio_retry_threshold():
    """Return a call count at which the sink should become visible (after switch)."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    # After retries exhaust, profile switch triggers one extra list_sinks refresh.
    return bt_audio._SINK_RETRY_COUNT + 1


@pytest.mark.parametrize("a2dp_profile_name", ["a2dp_sink", "a2dp-sink"])
def test_configure_bluetooth_audio_skips_profile_switch_when_already_a2dp(bt_manager, a2dp_profile_name):
    """If active_profile is already an A2DP-sink variant (either
    spelling), do not call set_card_profile. Issue #314."""
    pa_mac = bt_manager.mac_address.replace(":", "_")
    card_name = f"bluez_card.{pa_mac}"

    cards_payload = [
        {
            "name": card_name,
            "driver": "module-bluez5-device.c",
            "active_profile": a2dp_profile_name,
            "profiles": ["off", a2dp_profile_name, "headset_head_unit"],
        }
    ]

    set_profile_calls = []

    with (
        patch("sendspin_bridge.bluetooth.audio.list_sinks", return_value=[]),
        patch("sendspin_bridge.bluetooth.audio.get_sink_volume", return_value=None),
        patch("sendspin_bridge.bluetooth.audio.set_sink_mute", return_value=True),
        patch("sendspin_bridge.bluetooth.audio.list_cards", return_value=cards_payload),
        patch(
            "sendspin_bridge.bluetooth.audio.set_card_profile",
            side_effect=lambda c, p: set_profile_calls.append((c, p)) or True,
        ),
        patch("sendspin_bridge.bluetooth.audio._warn_pipewire_session"),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is False
    assert set_profile_calls == []


def test_configure_bluetooth_audio_retries_five_times(bt_manager):
    """Default retry count is 5 (env-configurable via SINK_RETRY_COUNT)."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    assert bt_audio._SINK_RETRY_COUNT >= 5

    call_count = 0

    def _counting_list_sinks():
        nonlocal call_count
        call_count += 1
        return []

    with (
        patch("sendspin_bridge.bluetooth.audio.list_sinks", side_effect=_counting_list_sinks),
        patch("sendspin_bridge.bluetooth.audio.get_sink_volume", return_value=None),
        patch("sendspin_bridge.bluetooth.audio.set_sink_mute", return_value=True),
        patch("sendspin_bridge.bluetooth.audio._warn_pipewire_session"),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is False
    # Initial call + (retry_count - 1) refreshes = retry_count total list_sinks calls
    assert call_count == bt_audio._SINK_RETRY_COUNT


def test_fast_path_falls_back_when_transport_active(bt_manager, monkeypatch, tmp_path):
    """When LAST_SINKS has the sink but MediaTransport1.State == 'active',
    skip the fast-path and go through the delayed-discovery branch.

    Reproduces the collision window from issue #269: on reconnect, the
    PA sink object exists but BlueZ AVDTP is still settling; taking the
    fast-path mutes too early and triggers AVDTP-Suspend from the peer.
    """
    import json

    import sendspin_bridge.bluetooth.audio as bt_audio

    bt_manager._dbus_device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    pa_mac = bt_manager.mac_address.replace(":", "_")
    sink_name = f"bluez_output.{pa_mac}.1"

    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"LAST_SINKS": {bt_manager.mac_address: sink_name}}))
    monkeypatch.setattr(bt_audio, "CONFIG_FILE", cfg_file)

    waits: list[float] = []

    def _wait_with_cancel(sec):
        waits.append(sec)
        return True

    fake_sinks = [{"name": sink_name, "description": "BT Speaker"}]
    bluez = bluez_knowing(bt_manager, connected=True)
    bluez.add_transport("/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF/fd0", "active")
    attach(bt_manager, bluez)
    with (
        patch("sendspin_bridge.bluetooth.audio.list_sinks", return_value=fake_sinks),
        patch("sendspin_bridge.bluetooth.audio.get_sink_volume", return_value=50),
        patch("sendspin_bridge.bluetooth.audio.set_sink_mute", return_value=True),
        patch("sendspin_bridge.bluetooth.audio.set_sink_volume", return_value=True),
        patch.object(bt_manager, "_wait_with_cancel", side_effect=_wait_with_cancel),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True
    assert any(abs(w - bt_audio._A2DP_PROFILE_DELAY) < 1e-6 for w in waits), (
        f"Expected A2DP profile delay to be taken when transport is 'active', got waits={waits!r}"
    )


def test_fast_path_taken_when_transport_idle(bt_manager, monkeypatch, tmp_path):
    """When transport state is 'idle', keep the existing fast-path:
    cached sink + no delay. This protects boot-time restarts."""
    import json

    import sendspin_bridge.bluetooth.audio as bt_audio

    bt_manager._dbus_device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    pa_mac = bt_manager.mac_address.replace(":", "_")
    sink_name = f"bluez_output.{pa_mac}.1"
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"LAST_SINKS": {bt_manager.mac_address: sink_name}}))
    monkeypatch.setattr(bt_audio, "CONFIG_FILE", cfg_file)

    waits: list[float] = []

    with (
        patch("sendspin_bridge.bluetooth.audio.list_sinks", return_value=[]),
        patch("sendspin_bridge.bluetooth.audio.get_sink_volume", return_value=50),
        patch("sendspin_bridge.bluetooth.audio.set_sink_mute", return_value=True),
        patch("sendspin_bridge.bluetooth.audio.set_sink_volume", return_value=True),
        # BlueZ reports the transport as "idle".
        patch.object(
            bt_manager,
            "_wait_with_cancel",
            side_effect=lambda s: waits.append(s) or True,
        ),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True
    assert waits == [], f"Fast-path should not have called wait_with_cancel when transport is idle, got waits={waits!r}"


def test_fast_path_taken_when_transport_unknown(bt_manager, monkeypatch, tmp_path):
    """Transport state == None (D-Bus unavailable or no transport object yet)
    preserves the pre-#269 fast-path behavior: don't penalize stacks that
    can't be introspected."""
    import json

    import sendspin_bridge.bluetooth.audio as bt_audio

    pa_mac = bt_manager.mac_address.replace(":", "_")
    sink_name = f"bluez_output.{pa_mac}.1"
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text(json.dumps({"LAST_SINKS": {bt_manager.mac_address: sink_name}}))
    monkeypatch.setattr(bt_audio, "CONFIG_FILE", cfg_file)

    waits: list[float] = []

    with (
        patch("sendspin_bridge.bluetooth.audio.list_sinks", return_value=[]),
        patch("sendspin_bridge.bluetooth.audio.get_sink_volume", return_value=50),
        patch("sendspin_bridge.bluetooth.audio.set_sink_mute", return_value=True),
        patch("sendspin_bridge.bluetooth.audio.set_sink_volume", return_value=True),
        patch.object(
            bt_manager,
            "_wait_with_cancel",
            side_effect=lambda s: waits.append(s) or True,
        ),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True
    assert waits == [], f"Fast-path must not delay when transport state is unknown, got waits={waits!r}"


def test_warn_pipewire_session_emits_on_pipewire_without_bt_sinks():
    """_warn_pipewire_session logs remediation when PipeWire has no BT sinks."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    with (
        patch("sendspin_bridge.services.audio.pulse.get_server_name", return_value="PulseAudio (on PipeWire 1.0.5)"),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_pipewire_session({"sendspin_fallback"})

    messages = [call.args[0] for call in mock_warn.call_args_list]
    assert any("WirePlumber" in m for m in messages)
    assert any("loginctl enable-linger" in m for m in messages)


def test_warn_pipewire_session_silent_on_pulseaudio():
    """_warn_pipewire_session does nothing on native PulseAudio."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    with (
        patch("sendspin_bridge.services.audio.pulse.get_server_name", return_value="pulseaudio 17.0"),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_pipewire_session(set())

    mock_warn.assert_not_called()


def test_warn_pipewire_session_silent_when_bt_sinks_present():
    """_warn_pipewire_session stays quiet if BT sinks exist (no false alarm)."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    sinks = {"bluez_output.AA_BB_CC_DD_EE_FF.1", "sendspin_fallback"}
    with (
        patch("sendspin_bridge.services.audio.pulse.get_server_name", return_value="PulseAudio (on PipeWire 1.0.5)"),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_pipewire_session(sinks)

    mock_warn.assert_not_called()


def _device_without_endpoint():
    """A speaker BlueZ knows, with no local A2DP endpoint registered for it."""
    from sendspin_bridge.bluetooth.address import DeviceAddress
    from sendspin_bridge.bluetooth.device import BluetoothDevice
    from tests.support.fake_dbus import FakeBlueZ

    address = DeviceAddress.require("AA:BB:CC:DD:EE:FF")
    bluez = FakeBlueZ()
    bluez.add_device(f"/org/bluez/hci0/{address.dbus_node}", address.colons, connected=True)
    return BluetoothDevice(address, controller="hci0", bus_factory=bluez.bus)


def test_warn_pipewire_session_emits_definitive_message_when_no_media_endpoint():
    """When BlueZ confirms no MediaEndpoint1 is registered for the device,
    the warning should say so directly rather than guessing "WirePlumber
    may not be running" — a message that doesn't apply to PulseAudio and
    is misleading when WirePlumber is running but its Bluetooth monitor
    just never registered an endpoint (headless seat-monitoring gap)."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    with (
        patch("sendspin_bridge.services.audio.pulse.get_server_name", return_value="PulseAudio (on PipeWire 1.0.5)"),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_pipewire_session({"sendspin_fallback"}, _device_without_endpoint())

    messages = [call.args[0] for call in mock_warn.call_args_list]
    assert any("no registered Bluetooth audio" in m for m in messages)
    assert not any("WirePlumber (the Bluetooth policy manager) may not be running" in m for m in messages)


def test_warn_pipewire_session_keeps_generic_message_when_endpoint_check_unknown():
    """Regression guard: when the D-Bus MediaEndpoint1 check can't determine
    an answer (e.g. no device_path yet), the original generic message must
    still fire so behavior for existing callers is unchanged."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    with (
        patch("sendspin_bridge.services.audio.pulse.get_server_name", return_value="PulseAudio (on PipeWire 1.0.5)"),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_pipewire_session({"sendspin_fallback"})

    messages = [call.args[0] for call in mock_warn.call_args_list]
    assert any("WirePlumber (the Bluetooth policy manager) may not be running" in m for m in messages)


def test_warn_pipewire_session_also_checks_wireplumber_logind():
    """_warn_pipewire_session calls _warn_wireplumber_logind when PipeWire has no BT sinks."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    with (
        patch("sendspin_bridge.services.audio.pulse.get_server_name", return_value="PulseAudio (on PipeWire 1.0.5)"),
        patch.object(bt_audio, "_warn_wireplumber_logind") as mock_logind,
        patch.object(bt_audio.logger, "warning"),
    ):
        bt_audio._warn_pipewire_session({"sendspin_fallback"})

    mock_logind.assert_called_once()


def test_is_wireplumber_logind_active_returns_false_when_override_exists(tmp_path):
    """_is_wireplumber_logind_active returns False when user override disables with-logind."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    override_dir = tmp_path / "override"
    override_dir.mkdir()
    (override_dir / "51-disable-logind.lua").write_text('bluez_monitor.properties["with-logind"] = false\n')

    result = bt_audio._is_wireplumber_logind_active(
        _override_dirs=[override_dir],
        _default_cfg_path=tmp_path / "nonexistent.lua",
    )
    assert result is False


def test_is_wireplumber_logind_active_returns_true_when_default_config(tmp_path):
    """_is_wireplumber_logind_active returns True when default config has with-logind = true."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    # No user overrides
    empty_override = tmp_path / "override"
    empty_override.mkdir()

    default_cfg = tmp_path / "50-bluez-config.lua"
    default_cfg.write_text('  ["with-logind"] = true,\n')

    result = bt_audio._is_wireplumber_logind_active(
        _override_dirs=[empty_override],
        _default_cfg_path=default_cfg,
    )
    assert result is True


def test_is_wireplumber_logind_active_returns_none_when_no_config(tmp_path):
    """_is_wireplumber_logind_active returns None when config files are unreadable."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    result = bt_audio._is_wireplumber_logind_active(
        _override_dirs=[tmp_path / "nonexistent"],
        _default_cfg_path=tmp_path / "nonexistent.lua",
    )
    assert result is None


def test_warn_wireplumber_logind_emits_warning_when_active():
    """_warn_wireplumber_logind logs fix instructions when logind is active."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    with (
        patch.object(bt_audio, "_is_wireplumber_logind_active", return_value=True),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_wireplumber_logind()

    messages = [call.args[0] for call in mock_warn.call_args_list]
    assert any("with-logind" in m for m in messages)
    assert any("51-disable-logind.lua" in m for m in messages)


def test_warn_wireplumber_logind_silent_when_disabled():
    """_warn_wireplumber_logind stays quiet when logind is already disabled."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    with (
        patch.object(bt_audio, "_is_wireplumber_logind_active", return_value=False),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_wireplumber_logind()

    mock_warn.assert_not_called()


def test_warn_wireplumber_logind_silent_when_unknown():
    """_warn_wireplumber_logind stays quiet when detection returns None."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    with (
        patch.object(bt_audio, "_is_wireplumber_logind_active", return_value=None),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_wireplumber_logind()

    mock_warn.assert_not_called()


# ---------------------------------------------------------------------------
# WirePlumber 0.5 seat-monitoring (issue #347) — the 0.5 series replaced
# the bluetooth.lua.d "with-logind" knob with the SPA-JSON
# "monitor.bluez.seat-monitoring" setting; on headless systems it causes
# the same A2DP endpoint churn, so the same class of warning applies.
# ---------------------------------------------------------------------------


def test_is_wireplumber_seat_monitoring_returns_false_when_override_disables(tmp_path):
    import sendspin_bridge.bluetooth.audio as bt_audio

    override_dir = tmp_path / "wireplumber.conf.d"
    override_dir.mkdir()
    (override_dir / "51-disable-seat-monitoring.conf").write_text(
        "wireplumber.profiles = {\n  main = {\n    monitor.bluez.seat-monitoring = disabled\n  }\n}\n"
    )

    result = bt_audio._is_wireplumber_seat_monitoring_active(
        _override_dirs=[override_dir],
        _default_cfg_path=tmp_path / "wireplumber.conf",
    )
    assert result is False


def test_is_wireplumber_seat_monitoring_returns_true_on_05_layout_without_override(tmp_path):
    import sendspin_bridge.bluetooth.audio as bt_audio

    empty_override = tmp_path / "wireplumber.conf.d"
    empty_override.mkdir()
    # WirePlumber 0.5 default config exists and does not mention the
    # knob — seat-monitoring defaults to enabled in that series.
    default_cfg = tmp_path / "wireplumber.conf"
    default_cfg.write_text("context.properties = {}\n")

    result = bt_audio._is_wireplumber_seat_monitoring_active(
        _override_dirs=[empty_override],
        _default_cfg_path=default_cfg,
    )
    assert result is True


def test_is_wireplumber_seat_monitoring_returns_none_without_05_layout(tmp_path):
    import sendspin_bridge.bluetooth.audio as bt_audio

    result = bt_audio._is_wireplumber_seat_monitoring_active(
        _override_dirs=[tmp_path / "nonexistent"],
        _default_cfg_path=tmp_path / "nonexistent.conf",
    )
    assert result is None


def test_warn_wireplumber_seat_monitoring_emits_fix_when_active():
    import sendspin_bridge.bluetooth.audio as bt_audio

    with (
        patch.object(bt_audio, "_is_wireplumber_seat_monitoring_active", return_value=True),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_wireplumber_seat_monitoring()

    messages = [call.args[0] for call in mock_warn.call_args_list]
    assert any("seat-monitoring" in m for m in messages)
    assert any("51-disable-seat-monitoring.conf" in m for m in messages)


def test_warn_wireplumber_seat_monitoring_silent_when_disabled_or_unknown():
    import sendspin_bridge.bluetooth.audio as bt_audio

    for detection in (False, None):
        with (
            patch.object(bt_audio, "_is_wireplumber_seat_monitoring_active", return_value=detection),
            patch.object(bt_audio.logger, "warning") as mock_warn,
        ):
            bt_audio._warn_wireplumber_seat_monitoring()
        mock_warn.assert_not_called()


def test_warn_pipewire_session_also_checks_seat_monitoring():
    """The PipeWire no-sinks warning path must run the 0.5 check too."""
    import sendspin_bridge.bluetooth.audio as bt_audio

    with (
        patch("sendspin_bridge.services.audio.pulse.get_server_name", return_value="PulseAudio (on PipeWire 1.4.2)"),
        patch.object(bt_audio, "_warn_wireplumber_logind") as warn_logind,
        patch.object(bt_audio, "_warn_wireplumber_seat_monitoring") as warn_seat,
        patch.object(bt_audio.logger, "warning"),
    ):
        bt_audio._warn_pipewire_session(set())

    warn_logind.assert_called_once()
    warn_seat.assert_called_once()


def test_device_name_fallback(installed_bluez):
    """When no device_name is given, it falls back to the MAC address."""
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    installed_bluez.on("show", stdout="")
    mgr = BluetoothManager(mac_address="11:22:33:44:55:66")

    assert mgr.device_name == "11:22:33:44:55:66"


def test_unresolved_adapter_disables_dbus_path():
    """When adapter resolution fails, D-Bus path should remain unavailable."""
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    with (
        patch.object(BluetoothManager, "_detect_default_adapter_mac", return_value=""),
        patch("subprocess.check_output", return_value=""),
    ):
        mgr = BluetoothManager(mac_address="AA:BB:CC:DD:EE:FF", device_name="TestSpeaker")

    assert mgr.adapter_hci_name == ""
    assert mgr._dbus_device_path is None


@pytest.mark.asyncio
async def test_monitor_dbus_raises_when_device_path_unavailable(bt_manager):
    bt_manager._dbus_device_path = None

    from sendspin_bridge.bluetooth.monitor import _monitor_dbus

    with pytest.raises(RuntimeError, match="adapter resolution failed"):
        await _monitor_dbus(bt_manager, None, None)


def test_cancel_reconnect_clears_runtime_reconnect_status(bt_manager):
    mock_host = MagicMock()
    mock_host.get_status_value = MagicMock(return_value=True)
    bt_manager.host = mock_host

    bt_manager.cancel_reconnect()

    assert bt_manager.management_enabled is False
    assert bt_manager._cancel_reconnect.is_set() is True
    bt_manager.host.update_status.assert_called_once_with({"reconnecting": False, "reconnect_attempt": 0})


def test_connect_device_aborts_when_release_cancels_active_reconnect(bt_manager, installed_bluez):
    from sendspin_bridge.bluetooth.bluez import get_bluez

    control = get_bluez()  # the installed singleton (FakeBluez.control builds a fresh one per access)
    real_connect = control.connect

    def _connect_then_cancel(mac, adapter=None, **kwargs):
        bt_manager.cancel_reconnect()
        return real_connect(mac, adapter)

    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "disconnect_device", return_value=True) as disconnect_device,
        patch.object(bt_manager, "configure_bluetooth_audio"),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(control, "connect", side_effect=_connect_then_cancel),
    ):
        assert bt_manager.connect_device() is False

    disconnect_device.assert_called_once()


def test_is_device_paired_returns_none_when_device_not_available(bt_manager, installed_bluez, caplog):
    import logging

    installed_bluez.on("info AA:BB:CC:DD:EE:FF", stdout="Device AA:BB:CC:DD:EE:FF not available\n")
    with caplog.at_level(logging.INFO, logger="sendspin_bridge.bluetooth.manager"):
        assert bt_manager.is_device_paired() is None
    assert "no current device object" in caplog.text


# ---------------------------------------------------------------------------
# is_device_paired — the BluezControl tri-state contract (batch 2)
# ``paired`` may collapse to True/False only on an explicit Paired: field;
# transport failures and a missing device object must all yield None so the
# monitor never escalates to pair_device() on a transient timeout.
# ---------------------------------------------------------------------------


def test_is_device_paired_true_when_bluez_reports_paired(bt_manager, installed_bluez):
    installed_bluez.on(
        "info AA:BB:CC:DD:EE:FF",
        stdout="Device AA:BB:CC:DD:EE:FF (public)\n\tName: TestSpeaker\n\tPaired: yes\n",
    )
    silent(bt_manager)
    assert bt_manager.is_device_paired() is True


def test_is_device_paired_false_when_bluez_reports_not_paired(bt_manager, installed_bluez):
    installed_bluez.on(
        "info AA:BB:CC:DD:EE:FF",
        stdout="Device AA:BB:CC:DD:EE:FF (public)\n\tName: TestSpeaker\n\tPaired: no\n",
    )
    silent(bt_manager)
    assert bt_manager.is_device_paired() is False


def test_is_device_paired_none_on_timeout_without_unknown_device_log(bt_manager, installed_bluez, caplog):
    import logging

    installed_bluez.timeout("info")
    with caplog.at_level(logging.INFO, logger="sendspin_bridge.bluetooth.manager"):
        assert bt_manager.is_device_paired() is None
    assert "no current device object" not in caplog.text


def test_is_device_paired_none_when_bluetoothctl_unavailable(bt_manager, installed_bluez, caplog):
    import logging

    installed_bluez.fail("info")
    with caplog.at_level(logging.INFO, logger="sendspin_bridge.bluetooth.manager"):
        assert bt_manager.is_device_paired() is None
    assert "no current device object" not in caplog.text


def test_connect_device_does_not_repair_when_pairing_state_unknown(bt_manager):
    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=None),
        patch.object(bt_manager, "pair_device", return_value=True) as pair_device,
        patch.object(bt_manager, "configure_bluetooth_audio"),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        assert bt_manager.connect_device() is True

    pair_device.assert_not_called()


def _pair_script(fake_bluez, mac: str, *, lines: list[str] | None = None) -> None:
    fake_bluez.session_script([(f"pair {mac}", lines if lines is not None else ["Pairing successful"])])


def _sent(fake_bluez) -> list[str]:
    return [c.script for c in fake_bluez.commands if c.kind in ("send", "reply")]


def test_pair_device_trusts_only_after_pair_success(bt_manager, installed_bluez):
    installed_bluez.session_script(
        [
            (f"pair {bt_manager.mac_address}", ["Confirm passkey 123456 (yes/no):", "Pairing successful"]),
        ]
    )

    assert bt_manager.pair_device() is True

    sent = _sent(installed_bluez)
    # `scan bredr` — constrains discovery to the BR/EDR transport for A2DP
    # sinks; LE-only devices (beacons, BLE wearables) are filtered out
    # upstream by the kernel so they don't compete in the scan window.
    assert any("scan bredr" in s for s in sent)
    pair_at = next(i for i, s in enumerate(sent) if f"pair {bt_manager.mac_address}" in s)
    yes_at = next(i for i, s in enumerate(sent) if s.strip() == "yes")
    trust_at = next(i for i, s in enumerate(sent) if f"trust {bt_manager.mac_address}" in s)
    assert pair_at < yes_at < trust_at


def test_pair_device_does_not_trust_on_failure(bt_manager, installed_bluez):
    _pair_script(
        installed_bluez, bt_manager.mac_address, lines=["Failed to pair: org.bluez.Error.AuthenticationFailed"]
    )

    assert bt_manager.pair_device() is False

    assert not any(f"trust {bt_manager.mac_address}" in s for s in _sent(installed_bluez))


def test_pair_device_issues_explicit_a2dp_sink_profile_on_success(bt_manager, installed_bluez):
    """Right after bluetoothctl reports `Pairing successful`, pair_device
    must issue an explicit ``ConnectProfile(A2DP_SINK_UUID)`` via D-Bus —
    before returning to the connect loop. This narrows the window where
    BlueZ 5.86's dual-role auto-negotiation (bluez/bluez#1922) can settle
    on the wrong profile and leave the device with no A2DP sink. On a
    healthy stack the underlying D-Bus call may respond with
    ``org.bluez.Error.AlreadyConnected``, which the helper treats as
    benign — making the call a cheap no-op. The call is best-effort and
    must not affect the pair success boolean."""
    _pair_script(installed_bluez, bt_manager.mac_address)

    with patch.object(bt_manager, "_force_a2dp_sink_profile", return_value=True) as mock_force:
        assert bt_manager.pair_device() is True

    mock_force.assert_called_once_with()


def test_pair_device_does_not_issue_a2dp_sink_profile_on_failure(bt_manager, installed_bluez):
    """If pair_device fails to reach the `Pairing successful` (or
    ``Paired: yes``) state, the explicit ConnectProfile call must NOT
    run — issuing it against a device that isn't paired yet would fail
    with org.bluez.Error.Failed and just add noise to the logs for a
    failure we've already reported cleanly."""
    _pair_script(
        installed_bluez, bt_manager.mac_address, lines=["Failed to pair: org.bluez.Error.AuthenticationFailed"]
    )

    with patch.object(bt_manager, "_force_a2dp_sink_profile", return_value=True) as mock_force:
        assert bt_manager.pair_device() is False

    mock_force.assert_not_called()


def test_pair_device_pair_success_value_is_unaffected_by_profile_hint_failure(bt_manager, installed_bluez):
    """The explicit ConnectProfile call is a best-effort hint. If it
    raises or returns False (e.g. device object gone, D-Bus missing),
    pair_device must still report ``True`` — pairing itself did succeed,
    and the later ``_force_a2dp_sink_profile`` call from
    ``_connect_device_inner`` gets another chance after the generic
    Connect()."""
    _pair_script(installed_bluez, bt_manager.mac_address)

    with patch.object(bt_manager, "_force_a2dp_sink_profile", side_effect=RuntimeError("dbus gone")):
        assert bt_manager.pair_device() is True


def test_pair_device_applies_class_of_device_override_before_pairing(bt_manager, installed_bluez):
    """Samsung Q-series soundbars filter incoming connections by initiator
    Class of Device (bluez/bluez#1025), and bluetoothd can reset CoD on the
    ``power on`` the pair session opens with — so the override has to be
    re-applied between ``power on`` and ``pair``."""
    _pair_script(installed_bluez, bt_manager.mac_address)
    applied: list[float] = []

    with patch.object(
        bt_manager,
        "_maybe_apply_cod_override_pre_pair",
        side_effect=lambda: applied.append(installed_bluez.now()),
    ):
        assert bt_manager.pair_device() is True

    assert applied, "the CoD override hook never ran"
    pair_at = next(
        c.at for c in installed_bluez.commands if c.kind == "send" and f"pair {bt_manager.mac_address}" in c.script
    )
    power_at = next(c.at for c in installed_bluez.commands if c.kind == "send" and "power on" in c.script)
    assert power_at <= applied[0] <= pair_at


# ---------------------------------------------------------------------------
# pair_device — cancel-after-spawn race protection
# ---------------------------------------------------------------------------


def test_pair_device_cancelled_before_session(bt_manager, installed_bluez):
    """pair_device returns False immediately when cancelled up front."""
    bt_manager._cancel_reconnect.set()

    assert bt_manager.pair_device() is False

    assert not [c for c in installed_bluez.commands if c.kind == "popen"]


def test_pair_device_cancelled_after_session_spawn(bt_manager, installed_bluez):
    """Cancelling between spawn and the first write must abort the attempt
    and leave no bluetoothctl process running."""
    _pair_script(installed_bluez, bt_manager.mac_address)
    spawned: list[object] = []
    real_popen = installed_bluez.popen

    def _cancel_on_spawn(argv):
        proc = real_popen(argv)
        spawned.append(proc)
        bt_manager._cancel_reconnect.set()
        return proc

    with patch.object(installed_bluez, "popen", side_effect=_cancel_on_spawn):
        assert bt_manager.pair_device() is False

    assert spawned, "the session never spawned"
    assert spawned[0].poll() is not None, "the aborted session left its process running"


def test_pair_device_clears_stale_agent_before_pairing(bt_manager, installed_bluez):
    """pair_device must run `agent off` cleanup before the main pair session.

    Without it, a leftover D-Bus agent object from a previous bluetoothctl
    session causes `Failed to register agent object` and pairing fails with
    org.bluez.Error.ConnectionAttemptFailed (issue #162 — same root cause as
    the standalone pair flow).
    """
    _pair_script(installed_bluez, bt_manager.mac_address)

    assert bt_manager.pair_device() is True

    cleanup = next(c for c in installed_bluez.commands if c.kind == "run" and "agent off" in c.script)
    assert cleanup.script.index("agent off") < cleanup.script.index(f"remove {bt_manager.mac_address}")
    spawn_at = next(c.at for c in installed_bluez.commands if c.kind == "popen")
    assert cleanup.at < spawn_at, "the cleanup must run before the pair session spawns"


def test_connect_device_clears_stale_bluez_entry_after_repeated_unknown_pairing(bt_manager, installed_bluez):
    """After K consecutive failed reconnects where BlueZ has no device object,
    purge the stale cache entry so the next cycle can trigger pair_device.

    KALLSUP-class issue (#162): some speakers leave BlueZ with no current device
    object after disconnect. is_device_paired() returns None, connect fails,
    monitor loops forever logging `Failed to connect (not connected after 5
    status checks)`. Forcing `bluetoothctl remove {mac}` lets the next reconnect
    see paired==False and escalate to pair_device (which now has #162 cleanups).
    """

    def _remove_commands():
        return [c for c in installed_bluez.commands if c.verb == "remove" and bt_manager.mac_address in c.script]

    with (
        patch.object(bt_manager, "is_device_connected", return_value=False),
        patch.object(bt_manager, "is_device_paired", return_value=None),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        for _ in range(2):
            bt_manager.connect_device()

        assert _remove_commands() == [], (
            f"Stale-cache cleanup must not trigger before threshold; got {_remove_commands()!r}"
        )

        bt_manager.connect_device()

        assert _remove_commands(), "Expected `remove {mac}` cleanup once paired-unknown count reached threshold"


def test_connect_device_resets_paired_unknown_count_on_success(bt_manager):
    """A successful connect must reset the paired-unknown counter so a later
    transient None doesn't trigger spurious cache purges.
    """
    bt_manager._paired_unknown_count = 2  # simulate prior history

    with (
        patch.object(bt_manager, "is_device_connected", return_value=True),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio"),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        assert bt_manager.connect_device() is True

    assert bt_manager._paired_unknown_count == 0


@pytest.mark.parametrize(
    "prompt_line",
    [
        "[agent] Enter PIN code:",
        "[agent] Enter passkey (number in 0-999999):",
    ],
    ids=["enter_pin_code", "enter_passkey"],
)
def test_pair_device_auto_answers_legacy_pin_prompt(bt_manager, installed_bluez, prompt_line):
    """pair_device must auto-enter a PIN when bluetoothctl asks for one.

    Legacy BT 2.x devices (e.g. HMDX JAM, `LegacyPairing: yes`) prompt
    `[agent] Enter PIN code:` or `[agent] Enter passkey:` depending on the
    device profile and BlueZ version. Both must be answered so pairing
    doesn't time out (issue #162).
    """
    installed_bluez.session_script(
        [
            (f"pair {bt_manager.mac_address}", [prompt_line]),
            ("0000", ["Pairing successful"]),
        ]
    )

    assert bt_manager.pair_device() is True

    replied = [c.script.strip() for c in installed_bluez.commands if c.kind == "reply"]
    assert replied == ["0000"], f"Expected the first popular PIN to be entered for {prompt_line!r}, got {replied!r}"


def test_pair_device_walks_the_pin_ladder(bt_manager, installed_bluez):
    """The monitor-loop re-pair used to try `0000` and give up; it now
    shares the ladder with the manual pair flow."""
    installed_bluez.session_script(
        [
            (f"pair {bt_manager.mac_address}", ["[agent] Enter PIN code:"]),
            ("0000", ["Failed to pair: org.bluez.Error.AuthenticationFailed"]),
            ("1234", ["Pairing successful"]),
        ]
    )

    assert bt_manager.pair_device() is True

    replied = [c.script.strip() for c in installed_bluez.commands if c.kind == "reply"]
    assert replied == ["0000", "1234"]


# ---------------------------------------------------------------------------
# _resolve_adapter_hci_name — fallback paths
# ---------------------------------------------------------------------------


def test_resolve_adapter_hci_name_returns_config_adapter_directly():
    """When adapter is already hciN, _resolve_adapter_hci_name returns it as-is."""
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    with patch("subprocess.check_output", return_value=""):
        mgr = BluetoothManager(mac_address="AA:BB:CC:DD:EE:FF", adapter="hci1")

    assert mgr.adapter_hci_name == "hci1"


def test_resolve_adapter_hci_name_empty_when_all_fail(installed_bluez):
    """Returns empty string when both sysfs and the controller list lack the MAC."""
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    installed_bluez.on("list", stdout="Controller AA:BB:CC:DD:EE:00 Adapter\n")

    with (
        patch("subprocess.check_output", return_value=""),
        patch.object(BluetoothManager, "_detect_default_adapter_mac", return_value="FF:FF:FF:FF:FF:FF"),
        patch("pathlib.Path.iterdir", side_effect=OSError("no sysfs")),
        patch("subprocess.run", return_value=MagicMock(stdout="")),
    ):
        mgr = BluetoothManager(mac_address="AA:BB:CC:DD:EE:FF")

    assert mgr.adapter_hci_name == ""
    assert mgr._dbus_device_path is None


# ---------------------------------------------------------------------------
# _detect_default_adapter_mac / check_bluetooth_available via BluezControl
# (batch 3 — both now read ``show`` through get_bluez() instead of running
# bluetoothctl directly)
# ---------------------------------------------------------------------------


_SHOW_DEFAULT_CTRL = "Controller 11:22:33:44:55:66 (public)\n\tName: test-host\n\tAlias: test-host\n\tPowered: yes\n"


def test_detect_default_adapter_mac_reads_default_controller(bt_manager, installed_bluez):
    """``show`` with a Controller header yields the default adapter MAC."""
    installed_bluez.on("show", stdout=_SHOW_DEFAULT_CTRL)

    assert bt_manager._detect_default_adapter_mac() == "11:22:33:44:55:66"


def test_detect_default_adapter_mac_empty_when_no_default_controller(bt_manager, installed_bluez):
    """``No default controller available`` → empty string, never an exception."""
    installed_bluez.on("show", stdout="No default controller available\n")

    assert bt_manager._detect_default_adapter_mac() == ""


def test_check_bluetooth_available_true_with_default_controller(bt_manager, installed_bluez):
    """No adapter configured: any present default controller means available."""
    installed_bluez.on("show", stdout=_SHOW_DEFAULT_CTRL)

    assert bt_manager.check_bluetooth_available() is True


def test_check_bluetooth_available_false_without_default_controller(bt_manager, installed_bluez):
    installed_bluez.on("show", stdout="No default controller available\n")

    assert bt_manager.check_bluetooth_available() is False


def test_check_bluetooth_available_adapter_branch_scopes_show(installed_bluez):
    """With an adapter configured, ``show`` is scoped via ``select <MAC>``."""
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    installed_bluez.on("show", stdout=_SHOW_DEFAULT_CTRL)
    mgr = BluetoothManager(
        mac_address="AA:BB:CC:DD:EE:FF",
        device_name="TestSpeaker",
        adapter="11:22:33:44:55:66",
    )

    assert mgr.check_bluetooth_available() is True
    scoped = installed_bluez.scoped("11:22:33:44:55:66")
    assert any(c.verb == "show" for c in scoped)


def test_check_bluetooth_available_false_on_transport_failure(bt_manager, installed_bluez):
    """A wedged/absent controller (timeout) reports unavailable, not a crash."""
    installed_bluez.timeout("show")

    assert bt_manager.check_bluetooth_available() is False


# ---------------------------------------------------------------------------
# connect_device — timeout and retry behaviour
# ---------------------------------------------------------------------------


def test_connect_device_retries_status_checks(bt_manager):
    """connect_device polls is_device_connected up to _CONNECT_CHECK_RETRIES times."""
    check_calls = []

    def _fake_connected():
        check_calls.append(1)
        # Succeed on the 4th check
        return len(check_calls) >= 4

    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=_fake_connected),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio"),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        result = bt_manager.connect_device()

    assert result is True
    # 1 initial check (returns False) + retries until 4th total check succeeds
    assert len(check_calls) >= 4


def test_connect_device_fails_after_all_retries(bt_manager):
    """connect_device returns False when all status checks report disconnected."""
    with (
        patch.object(bt_manager, "is_device_connected", return_value=False),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        result = bt_manager.connect_device()

    assert result is False


def test_connect_device_failure_log_surfaces_bluetoothctl_output(bt_manager, installed_bluez, caplog):
    """When status checks fail, the warning must include the bluetoothctl
    connect stdout so the actual BlueZ error reaches the operator.

    Regression for #302: a Paired/Bonded/Trusted device that won't connect
    used to log only "not connected after 5 status checks", discarding the
    BlueZ-side reason (page-timeout / already-active / profile-unavailable
    / link-key-mismatch / ...). Without it, neither the operator nor the
    maintainer can distinguish the candidate causes.
    """
    installed_bluez.on(
        f"connect {bt_manager.mac_address}",
        stdout=(
            "Attempting to connect to AA:BB:CC:DD:EE:FF\n"
            "Failed to connect: org.bluez.Error.Failed br-connection-page-timeout\n"
        ),
        returncode=1,
    )

    with (
        patch.object(bt_manager, "is_device_connected", return_value=False),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        caplog.at_level("WARNING", logger="sendspin_bridge.bluetooth.manager"),
    ):
        result = bt_manager.connect_device()

    assert result is False
    assert any("br-connection-page-timeout" in msg for msg in caplog.messages), (
        f"connect stdout missing from warning log; got: {caplog.messages!r}"
    )


def test_connect_diagnostics_preserve_stderr(bt_manager, installed_bluez, caplog):
    """BlueZ versions disagree on whether the connect failure lands on stdout
    or stderr; the failure warning must surface it either way.

    The stdout+stderr merge itself is owned by the transport contract
    (``BluezResult.text``, covered in test_bluez_control.py) — this pins the
    manager-level regression from #302: a stderr-only error still reaches
    the operator's log.
    """
    installed_bluez.on(
        f"connect {bt_manager.mac_address}",
        stdout="Agent registered\n",
        stderr="Failed to connect: org.bluez.Error.Failed br-connection-page-timeout\n",
        returncode=1,
    )
    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(bt_manager, "is_device_connected", return_value=False),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        caplog.at_level("WARNING", logger="sendspin_bridge.bluetooth.manager"),
    ):
        assert bt_manager.connect_device() is False

    assert any("br-connection-page-timeout" in msg for msg in caplog.messages), (
        f"stderr connect diagnostics missing from warning log; got: {caplog.messages!r}"
    )


# ---------------------------------------------------------------------------
# is_device_connected — various bluetoothctl output formats
# ---------------------------------------------------------------------------


def test_is_device_connected_dbus_true(bt_manager):
    """D-Bus returns True — should report connected."""
    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True))
    assert bt_manager.is_device_connected() is True
    assert bt_manager.connected is True


def test_is_device_connected_dbus_false(bt_manager):
    """D-Bus returns False — should report disconnected."""
    bt_manager.connected = True  # was previously connected
    attach(bt_manager, bluez_knowing(bt_manager, connected=False, paired=True))
    assert bt_manager.is_device_connected() is False
    assert bt_manager.connected is False


def test_is_device_connected_bluetoothctl_fallback(bt_manager, installed_bluez):
    """When D-Bus is unavailable, falls back to bluetoothctl output."""
    installed_bluez.on(
        "info AA:BB:CC:DD:EE:FF",
        stdout="Device AA:BB:CC:DD:EE:FF (public)\n\tName: TestSpeaker\n\tConnected: yes\n",
    )
    silent(bt_manager)
    assert bt_manager.is_device_connected() is True


def test_is_device_connected_bluetoothctl_fallback_disconnected(bt_manager, installed_bluez):
    """The bluetoothctl fallback reports Connected: no as disconnected."""
    installed_bluez.on(
        "info AA:BB:CC:DD:EE:FF",
        stdout="Device AA:BB:CC:DD:EE:FF (public)\n\tName: TestSpeaker\n\tConnected: no\n",
    )
    silent(bt_manager)
    assert bt_manager.is_device_connected() is False


# ---------------------------------------------------------------------------
# Mutation verbs via BluezControl (batch 5) — trust/connect/disconnect/remove
# ---------------------------------------------------------------------------


def test_trust_device_success(bt_manager, installed_bluez):
    assert bt_manager.trust_device() is True
    assert any(c.verb == "trust" for c in installed_bluez.commands)


def test_trust_device_false_on_transport_failure(bt_manager, installed_bluez):
    installed_bluez.fail("trust")
    assert bt_manager.trust_device() is False


def test_disconnect_device_bluetoothctl_fallback_success(bt_manager, installed_bluez):
    """D-Bus Disconnect unavailable → the scoped ``disconnect`` verb runs and
    the connected state flips to False."""
    bt_manager.connected = True
    assert bt_manager.disconnect_device() is True
    assert bt_manager.connected is False
    assert any(c.verb == "disconnect" for c in installed_bluez.commands)


def test_disconnect_device_false_on_transport_failure(bt_manager, installed_bluez):
    bt_manager.connected = True
    installed_bluez.fail("disconnect")
    assert bt_manager.disconnect_device() is False
    assert bt_manager.connected is True  # state untouched on failure


def test_purge_stale_bluez_entry_removes_device(bt_manager, installed_bluez):
    """The #162 stale-entry purge issues a scoped ``remove``."""
    bt_manager._purge_stale_bluez_entry()
    assert any(c.verb == "remove" and "AA:BB:CC:DD:EE:FF" in c.script for c in installed_bluez.commands)


def test_is_device_connected_falls_back_to_the_transport_when_dbus_raises(bt_manager):
    """A raising D-Bus probe no longer decides the link state.

    The transport answers instead — here BlueZ has no device object for the
    MAC, which is a genuine "not connected".  A transport that cannot answer
    at all is covered in test_manager_link_state.py.
    """
    bt_manager.connected = True
    unreachable(bt_manager)
    assert bt_manager.is_device_connected() is False
    assert bt_manager.connected is False


# ---------------------------------------------------------------------------
# Exponential backoff in reconnect logic
# ---------------------------------------------------------------------------


def test_reconnect_delay_first_three_attempts_use_check_interval(bt_manager):
    """Attempts 1-3 should use check_interval without escalation."""
    bt_manager.check_interval = 10
    assert bt_manager._reconnect_delay(1) == 10
    assert bt_manager._reconnect_delay(2) == 10
    assert bt_manager._reconnect_delay(3) == 10


def test_reconnect_delay_doubles_after_third_attempt(bt_manager):
    """Attempts 4+ should double the delay each time."""
    bt_manager.check_interval = 10
    assert bt_manager._reconnect_delay(4) == 20  # 10 * 2^1
    assert bt_manager._reconnect_delay(5) == 40  # 10 * 2^2
    assert bt_manager._reconnect_delay(6) == 80  # 10 * 2^3


def test_reconnect_delay_capped_at_max(bt_manager):
    """Delay must never exceed _MAX_RECONNECT_DELAY_S (300s)."""
    bt_manager.check_interval = 10
    assert bt_manager._reconnect_delay(50) == 300.0


def test_handle_reconnect_failure_releases_after_threshold(bt_manager):
    """Management is released after max_reconnect_fails consecutive failures.

    Models a previously-paired device that has gone flaky (auto-release path).
    Distinct from the v2.70.0-rc.2 never-paired auto-disable path, which
    requires `paired is None` AND `_has_ever_paired_since_start is False`.
    """
    bt_manager.max_reconnect_fails = 3
    bt_manager.host = MagicMock()
    bt_manager.host.bt_management_enabled = True
    bt_manager._has_ever_paired_since_start = True  # avoid never-paired auto-disable (#263)

    with patch("sendspin_bridge.services.bluetooth.persist_device_released"):
        released = bt_manager._handle_reconnect_failure(3)

    assert released is True
    assert bt_manager.management_enabled is False


def test_handle_reconnect_failure_does_not_release_below_threshold(bt_manager):
    """Management stays active below max_reconnect_fails."""
    bt_manager.max_reconnect_fails = 5

    released = bt_manager._handle_reconnect_failure(4)

    assert released is False
    assert bt_manager.management_enabled is True


# ---------------------------------------------------------------------------
# Adapter auto-recovery ladder (EXPERIMENTAL_ADAPTER_AUTO_RECOVERY) — at
# threshold, before auto-releasing, try bluetooth-auto-recovery's
# mgmt-reset/rfkill/USB-bounce ladder one last time. Flag-gated because a
# USB bounce briefly disconnects every device on the same controller.
# ---------------------------------------------------------------------------


def test_handle_reconnect_failure_skips_adapter_recovery_when_flag_off(bt_manager):
    """Default behavior (flag off): even at threshold with an adapter MAC
    configured, the recovery helper must not be called — the USB-bounce
    step is disruptive enough that users must opt in explicitly."""
    bt_manager.max_reconnect_fails = 3
    bt_manager.host = MagicMock()
    bt_manager.host.bt_management_enabled = True
    bt_manager._enable_adapter_auto_recovery = False
    bt_manager.effective_adapter_mac = "C0:FB:F9:62:D6:9D"
    bt_manager.adapter_hci_name = "hci0"
    bt_manager._has_ever_paired_since_start = True  # avoid never-paired auto-disable (#263)

    with (
        patch("sendspin_bridge.services.bluetooth.persist_device_released"),
        patch("sendspin_bridge.services.bluetooth.adapter_recovery.recover_adapter_blocking") as mock_rec,
    ):
        released = bt_manager._handle_reconnect_failure(3)

    mock_rec.assert_not_called()
    assert released is True
    assert bt_manager.management_enabled is False


def test_handle_reconnect_failure_runs_adapter_recovery_when_flag_on(bt_manager):
    """Flag on + threshold hit + adapter resolved: the recovery helper
    is invoked with the hci index parsed from ``adapter_hci_name`` and
    the adapter MAC, as a last-ditch before auto-releasing."""
    bt_manager.max_reconnect_fails = 3
    bt_manager.host = MagicMock()
    bt_manager.host.bt_management_enabled = True
    bt_manager._enable_adapter_auto_recovery = True
    bt_manager.effective_adapter_mac = "C0:FB:F9:62:D6:9D"
    bt_manager.adapter_hci_name = "hci1"
    bt_manager._has_ever_paired_since_start = True  # avoid never-paired auto-disable (#263)

    with (
        patch("sendspin_bridge.services.bluetooth.persist_device_released"),
        patch("sendspin_bridge.services.bluetooth.adapter_recovery.recover_adapter_blocking") as mock_rec,
    ):
        mock_rec.return_value = False  # recovery failed → still release
        released = bt_manager._handle_reconnect_failure(3)

    mock_rec.assert_called_once_with(hci_index=1, adapter_mac="C0:FB:F9:62:D6:9D")
    assert released is True
    assert bt_manager.management_enabled is False


def test_handle_reconnect_failure_keeps_management_when_recovery_succeeds(bt_manager):
    """If the recovery ladder actually recovers the adapter, do NOT
    auto-release — give the reconnect loop another chance."""
    bt_manager.max_reconnect_fails = 3
    bt_manager.host = MagicMock()
    bt_manager.host.bt_management_enabled = True
    bt_manager._enable_adapter_auto_recovery = True
    bt_manager.effective_adapter_mac = "C0:FB:F9:62:D6:9D"
    bt_manager.adapter_hci_name = "hci0"

    with (
        patch("sendspin_bridge.services.bluetooth.persist_device_released") as mock_persist,
        patch("sendspin_bridge.services.bluetooth.adapter_recovery.recover_adapter_blocking", return_value=True),
    ):
        released = bt_manager._handle_reconnect_failure(3)

    assert released is False
    assert bt_manager.management_enabled is True
    mock_persist.assert_not_called()


def test_adapter_recovery_publishes_structured_status(bt_manager):
    """Recovery progress is observable without scraping log messages."""
    bt_manager.host = MagicMock()
    bt_manager.effective_adapter_mac = "C0:FB:F9:62:D6:9D"
    bt_manager.adapter_hci_name = "hci2"

    with patch(
        "sendspin_bridge.services.bluetooth.adapter_recovery.recover_adapter_blocking",
        return_value=True,
    ):
        assert bt_manager._try_adapter_auto_recovery() is True

    updates = [call.args[0] for call in bt_manager.host.update_status.call_args_list]
    assert updates[0]["adapter_recovery_stage"] == "running"
    assert updates[0]["adapter_recovery_adapter"] == "C0:FB:F9:62:D6:9D"
    assert updates[0]["adapter_recovery_last_at"]
    assert updates[-1] == {
        "adapter_recovery_stage": "completed",
        "adapter_recovery_result": "success",
        "adapter_recovery_failure_reason": None,
    }


def test_handle_reconnect_failure_skips_recovery_when_no_adapter_mac(bt_manager):
    """Recovery needs the adapter MAC (and hci index). If we never
    resolved the adapter at all — no default controller detected, no
    sysfs hit — don't call into the library; its netlink/USB paths key
    on hci index, and we can't safely pick one blindly."""
    bt_manager.max_reconnect_fails = 3
    bt_manager.host = MagicMock()
    bt_manager.host.bt_management_enabled = True
    bt_manager._enable_adapter_auto_recovery = True
    bt_manager.effective_adapter_mac = ""  # unresolved
    bt_manager.adapter_hci_name = ""

    with (
        patch("sendspin_bridge.services.bluetooth.persist_device_released"),
        patch("sendspin_bridge.services.bluetooth.adapter_recovery.recover_adapter_blocking") as mock_rec,
    ):
        released = bt_manager._handle_reconnect_failure(3)

    mock_rec.assert_not_called()
    assert released is True


def test_handle_reconnect_failure_runs_adapter_recovery_for_default_adapter_device(bt_manager):
    """Default-adapter case: the user left ``adapter`` empty so the
    configured ``self.adapter`` / ``self._adapter_select`` are both
    blank, but ``effective_adapter_mac`` and ``adapter_hci_name`` were
    resolved from the controller ``bluetoothctl list`` or sysfs
    reported. Recovery must still trigger — gating on the raw config
    fields would skip the (common) default-adapter case entirely."""
    bt_manager.max_reconnect_fails = 3
    bt_manager.host = MagicMock()
    bt_manager.host.bt_management_enabled = True
    bt_manager._enable_adapter_auto_recovery = True
    bt_manager.adapter = ""  # user didn't pin an adapter
    bt_manager._adapter_select = ""  # empty because adapter is empty
    bt_manager.effective_adapter_mac = "C0:FB:F9:62:D6:9D"  # resolved from default controller
    bt_manager.adapter_hci_name = "hci0"  # resolved via sysfs

    with (
        patch("sendspin_bridge.services.bluetooth.persist_device_released"),
        patch("sendspin_bridge.services.bluetooth.adapter_recovery.recover_adapter_blocking") as mock_rec,
    ):
        mock_rec.return_value = True
        released = bt_manager._handle_reconnect_failure(3)

    mock_rec.assert_called_once_with(hci_index=0, adapter_mac="C0:FB:F9:62:D6:9D")
    assert released is False
    assert bt_manager.management_enabled is True


# ---------------------------------------------------------------------------
# bluez/bluez#1922 A2DP Sink workarounds (5.86 dual-role regression)
# ---------------------------------------------------------------------------


def test_force_a2dp_sink_profile_calls_connect_profile_with_sink_uuid(bt_manager):
    """The A2DP Sink UUID is what reaches BlueZ."""
    from sendspin_bridge.bluetooth.dbus import A2DP_SINK_UUID

    bluez = bluez_knowing(bt_manager, connected=True)
    attach(bt_manager, bluez)

    assert bt_manager._force_a2dp_sink_profile() is True
    assert [call[1:] for call in bluez.calls] == [("ConnectProfile", (A2DP_SINK_UUID,))]


def test_force_a2dp_sink_profile_returns_false_on_error(bt_manager):
    """Returns False when ConnectProfile raises a non-AlreadyConnected error."""
    bluez = bluez_knowing(bt_manager, connected=True)
    bluez.fail["ConnectProfile"] = RuntimeError("org.bluez.Error.NotSupported")
    attach(bt_manager, bluez)

    assert bt_manager._force_a2dp_sink_profile() is False


def test_force_a2dp_sink_profile_treats_already_connected_as_benign(bt_manager):
    """AlreadyConnected error on a healthy stack must not be logged as a warning."""
    bluez = bluez_knowing(bt_manager, connected=True)
    bluez.fail["ConnectProfile"] = RuntimeError("org.bluez.Error.AlreadyConnected")
    attach(bt_manager, bluez)

    with patch("sendspin_bridge.bluetooth.manager.logger.info") as mock_info:
        result = bt_manager._force_a2dp_sink_profile()

    assert result is False
    assert mock_info.call_count == 0


def test_connect_device_force_a2dp_sink_profile_after_successful_connect(bt_manager):
    """After the generic Connect() succeeds, the A2DP Sink profile hint is issued."""
    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio", return_value=True),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile") as mock_force,
    ):
        assert bt_manager.connect_device() is True

    mock_force.assert_called_once()


def test_connect_device_triggers_a2dp_dance_when_no_sink_appears(bt_manager):
    """If sink discovery fails, _a2dp_recovery_dance runs once and sink is retried."""
    configure_calls = []

    def _configure():
        configure_calls.append(1)
        # First call (post-connect) → no sink; second call (post-dance) → sink ok
        return len(configure_calls) > 1

    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio", side_effect=_configure),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile"),
        patch.object(bt_manager, "_a2dp_recovery_dance", return_value=True) as mock_dance,
    ):
        assert bt_manager.connect_device() is True

    mock_dance.assert_called_once()
    # configure_bluetooth_audio runs once before the dance and once after
    assert len(configure_calls) == 2


def test_connect_device_does_not_dance_when_sink_appears_immediately(bt_manager):
    """Healthy stack: sink found on first try → no dance."""
    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio", return_value=True),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile"),
        patch.object(bt_manager, "_a2dp_recovery_dance") as mock_dance,
    ):
        assert bt_manager.connect_device() is True

    mock_dance.assert_not_called()


def test_connect_device_dance_runs_at_most_once_per_connect_cycle(bt_manager):
    """The dance counter must block a second dance within the same connect cycle."""
    # Pre-exhaust the dance credit as if a prior path consumed it.
    bt_manager._a2dp_dance_remaining = 0

    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio", return_value=False),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile"),
        patch.object(bt_manager, "_a2dp_recovery_dance") as mock_dance,
    ):
        # connect_device resets the counter on entry, so the dance still runs once.
        bt_manager.connect_device()

    assert mock_dance.call_count == 1
    # After the call the credit must be consumed, not negative.
    assert bt_manager._a2dp_dance_remaining == 0


def test_connect_device_resets_dance_credit_on_fresh_cycle(bt_manager):
    """Each top-level connect_device call must refresh the dance credit to 1."""
    bt_manager._a2dp_dance_remaining = 0

    with (
        patch.object(bt_manager, "is_device_connected", return_value=True),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio", return_value=True),
    ):
        bt_manager.connect_device()

    # Reset happens at top of connect_device before delegating to _inner.
    # Even though the inner never consumed it (already-connected short path),
    # the credit must have been set to 1 at the top.
    assert bt_manager._a2dp_dance_remaining == 1


def test_a2dp_recovery_dance_returns_true_on_successful_reconnect(bt_manager):
    """Successful dance: disconnect → wait → reconnect establishes the link again."""
    is_connected_results = iter([False, True])

    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=lambda: next(is_connected_results)),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile") as mock_force,
    ):
        result = bt_manager._a2dp_recovery_dance()

    assert result is True
    assert bt_manager.connected is True
    # After the reconnect succeeds, the dance re-issues the A2DP Sink hint.
    mock_force.assert_called_once()


def test_a2dp_recovery_dance_returns_false_when_reconnect_never_succeeds(bt_manager):
    """If the link never comes back up, the dance reports failure."""
    with (
        patch.object(bt_manager, "is_device_connected", return_value=False),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile"),
    ):
        assert bt_manager._a2dp_recovery_dance() is False


# ---------------------------------------------------------------------------
# Experimental flag: A2DP recovery dance gating
# ---------------------------------------------------------------------------


def test_a2dp_dance_default_is_disabled_for_new_manager_instance(installed_bluez):
    """New managers created without kwargs must have dance disabled by default."""
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    installed_bluez.on("show", stdout="")
    mgr = BluetoothManager(mac_address="AA:BB:CC:DD:EE:FF", device_name="Default")
    assert mgr._enable_a2dp_dance is False
    assert mgr._enable_pa_module_reload is False


def test_connect_device_does_not_dance_when_flag_disabled(installed_bluez):
    """With `enable_a2dp_dance=False`, a missing sink must not trigger the dance."""
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    installed_bluez.on("show", stdout="")
    mgr = BluetoothManager(
        mac_address="AA:BB:CC:DD:EE:FF",
        device_name="Default",
        enable_a2dp_dance=False,
    )
    attach(mgr, bluez_knowing(mgr, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(mgr, "is_device_connected", side_effect=[False, True]),
        patch.object(mgr, "is_device_paired", return_value=True),
        patch.object(mgr, "configure_bluetooth_audio", return_value=False),
        patch.object(mgr, "_wait_with_cancel", return_value=True),
        patch.object(mgr, "_force_a2dp_sink_profile"),
        patch.object(mgr, "_a2dp_recovery_dance") as mock_dance,
    ):
        mgr.connect_device()
    mock_dance.assert_not_called()


def test_connect_device_does_not_reload_pa_module_when_flag_disabled(installed_bluez):
    """With `enable_pa_module_reload=False`, the last-resort reload must not fire."""
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    installed_bluez.on("show", stdout="")
    mgr = BluetoothManager(
        mac_address="AA:BB:CC:DD:EE:FF",
        device_name="Default",
        enable_a2dp_dance=False,
        enable_pa_module_reload=False,
    )
    attach(mgr, bluez_knowing(mgr, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(mgr, "is_device_connected", side_effect=[False, True]),
        patch.object(mgr, "is_device_paired", return_value=True),
        patch.object(mgr, "configure_bluetooth_audio", return_value=False),
        patch.object(mgr, "_wait_with_cancel", return_value=True),
        patch.object(mgr, "_force_a2dp_sink_profile"),
        patch.object(mgr, "_reload_pa_bluez5_module") as mock_reload,
    ):
        mgr.connect_device()
    mock_reload.assert_not_called()


def test_connect_device_reloads_pa_module_as_last_resort_when_flag_enabled(bt_manager):
    """Dance failure + flag=True triggers module reload, then reconfigures audio."""
    configure_calls: list[bool] = []

    def _configure():
        configure_calls.append(True)
        # 1st: no sink (post-connect); 2nd: no sink after dance; 3rd: sink ok after reload
        return len(configure_calls) > 2

    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio", side_effect=_configure),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile"),
        patch.object(bt_manager, "_a2dp_recovery_dance", return_value=True),
        patch.object(bt_manager, "_reload_pa_bluez5_module", return_value=True) as mock_reload,
    ):
        assert bt_manager.connect_device() is True

    mock_reload.assert_called_once()
    assert len(configure_calls) == 3


# ---------------------------------------------------------------------------
# ServicesResolved D-Bus gate
# ---------------------------------------------------------------------------


def test_connect_device_waits_for_services_resolved_before_configure_audio(bt_manager):
    """The gate must be invoked once after Connected=True and before audio configuration."""
    order: list[str] = []

    def _wait(*_args, **_kwargs):
        order.append("services_resolved")
        return True

    def _force():
        order.append("force_a2dp")

    def _configure():
        order.append("configure_audio")
        return True

    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio", side_effect=_configure),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile", side_effect=_force),
        patch.object(type(bt_manager.device), "wait_for_services_blocking", side_effect=_wait),
    ):
        assert bt_manager.connect_device() is True

    assert order.index("services_resolved") < order.index("force_a2dp") < order.index("configure_audio")


def test_connect_device_proceeds_when_services_resolved_timeout(bt_manager):
    """ServicesResolved timeout is a timing hint — connect flow must still continue."""
    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio", return_value=True) as mock_conf,
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile") as mock_force,
    ):
        assert bt_manager.connect_device() is True

    mock_force.assert_called_once()
    mock_conf.assert_called()


# ---------------------------------------------------------------------------
# Post-pair UUID sanity check
# ---------------------------------------------------------------------------


def test_check_audio_profiles_after_pair_warns_when_no_audio_uuid_advertised(bt_manager):
    """Non-audio devices surface `last_error=no_audio_profiles_advertised` to host status."""
    updates: list[dict] = []
    bt_manager.host = MagicMock()
    bt_manager.host.update_status.side_effect = lambda d: updates.append(d)

    attach(bt_manager, bluez_knowing(bt_manager, connected=True, uuids=("0000180f-0000-1000-8000-00805f9b34fb",)))

    bt_manager._check_audio_profiles_after_pair()

    assert updates, "expected host.update_status to be called with an error payload"
    assert updates[0].get("last_error") == "no_audio_profiles_advertised"


def test_check_audio_profiles_after_pair_no_warn_for_audio_device(bt_manager):
    """A2DP-capable peer must not trigger the no-audio warning on status."""
    from sendspin_bridge.bluetooth.dbus import A2DP_SINK_UUID

    bt_manager.host = MagicMock()

    attach(bt_manager, bluez_knowing(bt_manager, connected=True, uuids=(A2DP_SINK_UUID,)))
    bt_manager._check_audio_profiles_after_pair()

    bt_manager.host.update_status.assert_not_called()


def test_check_audio_profiles_after_pair_no_warn_when_uuid_read_empty(bt_manager):
    """Empty UUID list (D-Bus unavailable) must not trigger warning — nothing actionable."""
    bt_manager.host = MagicMock()

    attach(bt_manager, bluez_knowing(bt_manager, connected=True, uuids=()))
    bt_manager._check_audio_profiles_after_pair()

    bt_manager.host.update_status.assert_not_called()


# ---------------------------------------------------------------------------
# bt_dbus._dbus_connect_profile — low-level D-Bus wrapper
# ---------------------------------------------------------------------------


def test_dbus_connect_profile_returns_false_when_dbus_module_missing():
    """With dbus-python unavailable the helper reports dbus-unavailable reason."""
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    with patch.object(bt_dbus, "dbus", None):
        ok, reason = bt_dbus._dbus_connect_profile("/org/bluez/hci0/dev_X", "uuid")

    assert ok is False
    assert "dbus" in reason.lower()


def test_dbus_connect_profile_returns_false_for_empty_device_path():
    """Empty device path short-circuits to False without touching the bus."""
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    ok, reason = bt_dbus._dbus_connect_profile(None, bt_dbus.A2DP_SINK_UUID)
    assert ok is False
    assert reason


# ---------------------------------------------------------------------------
# bt_dbus._dbus_get_adapter_address — hciN -> MAC resolution
# ---------------------------------------------------------------------------


def test_dbus_get_adapter_address_returns_none_when_dbus_module_missing():
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    with patch.object(bt_dbus, "dbus", None):
        assert bt_dbus._dbus_get_adapter_address("hci1") is None


def test_dbus_get_adapter_address_returns_none_for_empty_name():
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    assert bt_dbus._dbus_get_adapter_address("") is None


def test_dbus_get_adapter_address_reads_address_property_at_hci_path():
    """The BlueZ object path /org/bluez/<hciN> matches the kernel hci index
    exactly, unlike bluetoothctl list's registration-order output."""
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    fake_dbus = MagicMock()
    fake_bus = MagicMock()
    fake_adapter_obj = MagicMock()
    fake_props = MagicMock()
    fake_props.Get.return_value = "F0:2F:74:6B:3C:BD"
    fake_dbus.SystemBus.return_value = fake_bus
    fake_bus.get_object.return_value = fake_adapter_obj
    fake_dbus.Interface.return_value = fake_props

    with patch.object(bt_dbus, "dbus", fake_dbus):
        # The bus is cached per thread; a neighbour test may have filled it.
        bt_dbus._reset_thread_buses()
        addr = bt_dbus._dbus_get_adapter_address("hci1")

    assert addr == "F0:2F:74:6B:3C:BD"
    fake_bus.get_object.assert_called_once_with("org.bluez", "/org/bluez/hci1")
    fake_props.Get.assert_called_once_with("org.bluez.Adapter1", "Address")


# ---------------------------------------------------------------------------
# bt_dbus._dbus_has_media_endpoint — BlueZ MediaEndpoint1 registration check
# ---------------------------------------------------------------------------


def test_dbus_has_media_endpoint_returns_none_when_dbus_module_missing():
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    with patch.object(bt_dbus, "dbus", None):
        assert bt_dbus._dbus_has_media_endpoint("/org/bluez/hci0/dev_AA_BB") is None


def test_dbus_has_media_endpoint_returns_none_for_empty_device_path():
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    assert bt_dbus._dbus_has_media_endpoint(None) is None


def test_dbus_has_media_endpoint_returns_true_when_sep_object_present():
    """A <device_path>/sepN object implementing MediaEndpoint1 means BlueZ
    successfully matched the peer's AVDTP capabilities against a locally
    registered audio backend endpoint."""
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    fake_dbus = MagicMock()
    fake_bus = MagicMock()
    fake_om = MagicMock()
    fake_om.GetManagedObjects.return_value = {
        device_path: {"org.bluez.Device1": {"ServicesResolved": True}},
        f"{device_path}/sep1": {"org.bluez.MediaEndpoint1": {"UUID": "0000110b-..."}},
    }
    fake_dbus.SystemBus.return_value = fake_bus
    fake_dbus.Interface.return_value = fake_om

    with patch.object(bt_dbus, "dbus", fake_dbus):
        assert bt_dbus._dbus_has_media_endpoint(device_path) is True

    fake_om.GetManagedObjects.assert_called_once_with()


def test_dbus_has_media_endpoint_returns_none_until_services_resolved():
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    fake_dbus = MagicMock()
    fake_om = MagicMock()
    fake_om.GetManagedObjects.return_value = {
        device_path: {"org.bluez.Device1": {"ServicesResolved": False}},
    }
    fake_dbus.Interface.return_value = fake_om

    with patch.object(bt_dbus, "dbus", fake_dbus):
        assert bt_dbus._dbus_has_media_endpoint(device_path) is None

    fake_om.GetManagedObjects.assert_called_once_with()


def test_dbus_has_media_endpoint_returns_none_when_device_missing_from_snapshot():
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    fake_dbus = MagicMock()
    fake_om = MagicMock()
    fake_om.GetManagedObjects.return_value = {}
    fake_dbus.Interface.return_value = fake_om

    with patch.object(bt_dbus, "dbus", fake_dbus):
        assert bt_dbus._dbus_has_media_endpoint(device_path) is None

    fake_om.GetManagedObjects.assert_called_once_with()


def test_dbus_has_media_endpoint_returns_false_when_resolved_device_has_no_sep_objects():
    """An endpoint for another device must not affect the requested device."""
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    fake_dbus = MagicMock()
    fake_bus = MagicMock()
    fake_om = MagicMock()
    fake_om.GetManagedObjects.return_value = {
        device_path: {"org.bluez.Device1": {"Connected": True, "ServicesResolved": True}},
        "/org/bluez/hci0/dev_11_22_33_44_55_66/sep1": {"org.bluez.MediaEndpoint1": {}},
    }
    fake_dbus.SystemBus.return_value = fake_bus
    fake_dbus.Interface.return_value = fake_om

    with patch.object(bt_dbus, "dbus", fake_dbus):
        assert bt_dbus._dbus_has_media_endpoint(device_path) is False

    fake_om.GetManagedObjects.assert_called_once_with()


# ---------------------------------------------------------------------------
# bt_dbus._dbus_wait_services_resolved — ServicesResolved polling helper
# ---------------------------------------------------------------------------


def test_dbus_wait_services_resolved_returns_true_when_property_already_set():
    """When ServicesResolved is already True, the helper returns immediately.

    ``wait_with_cancel`` contract matches ``BluetoothManager._wait_with_cancel``:
    True = waited uninterrupted, False = cancelled.
    """
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    with patch.object(bt_dbus, "_dbus_get_device_property", return_value=True):
        ok = bt_dbus._dbus_wait_services_resolved(
            "/org/bluez/hci0/dev_X",
            is_connected_check=lambda: True,
            wait_with_cancel=lambda _: True,
            timeout=1.0,
            poll_interval=0.01,
        )
    assert ok is True


def test_dbus_wait_services_resolved_bails_out_on_disconnect():
    """If is_connected_check returns False the helper reports failure."""
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    with patch.object(bt_dbus, "_dbus_get_device_property", return_value=False):
        ok = bt_dbus._dbus_wait_services_resolved(
            "/org/bluez/hci0/dev_X",
            is_connected_check=lambda: False,
            wait_with_cancel=lambda _: True,
            timeout=1.0,
            poll_interval=0.01,
        )
    assert ok is False


def test_dbus_wait_services_resolved_returns_false_on_timeout():
    """With timeout=0 and property not resolved, helper returns False without blocking."""
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    with patch.object(bt_dbus, "_dbus_get_device_property", return_value=False):
        ok = bt_dbus._dbus_wait_services_resolved(
            "/org/bluez/hci0/dev_X",
            is_connected_check=lambda: True,
            wait_with_cancel=lambda _: True,
            timeout=0.0,
            poll_interval=0.01,
        )
    assert ok is False


def test_dbus_wait_services_resolved_respects_cancellation():
    """Caller-driven cancellation (wait_with_cancel returns False) exits with False."""
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    with patch.object(bt_dbus, "_dbus_get_device_property", return_value=False):
        ok = bt_dbus._dbus_wait_services_resolved(
            "/org/bluez/hci0/dev_X",
            is_connected_check=lambda: True,
            wait_with_cancel=lambda _: False,
            timeout=10.0,
            poll_interval=0.01,
        )
    assert ok is False


def test_dbus_wait_services_resolved_returns_none_when_dbus_unavailable():
    """When ``dbus`` module isn't importable, helper must return ``None`` (not False).

    Returning False would look indistinguishable from a real 10s timeout and
    caused misleading "did not reach True within 10s" warnings on systems
    without dbus-python. The caller distinguishes "could not check" from
    "checked and timed out" by the ``None`` sentinel.
    """
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    with patch.object(bt_dbus, "dbus", None):
        result = bt_dbus._dbus_wait_services_resolved(
            "/org/bluez/hci0/dev_X",
            is_connected_check=lambda: True,
            wait_with_cancel=lambda _: True,
            timeout=1.0,
            poll_interval=0.01,
        )
    assert result is None


def test_dbus_wait_services_resolved_returns_none_when_device_path_missing():
    """Missing device_path is a not-actually-checked case — must be ``None``."""
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    result = bt_dbus._dbus_wait_services_resolved(
        None,
        is_connected_check=lambda: True,
        wait_with_cancel=lambda _: True,
        timeout=1.0,
        poll_interval=0.01,
    )
    assert result is None


def test_connect_device_does_not_warn_when_services_resolved_unchecked(bt_manager):
    """If ``_dbus_wait_services_resolved`` reports unchecked (None), no warning.

    Otherwise systems without dbus-python see spurious "did not reach True
    within 10s" entries on every reconnect.
    """
    attach(bt_manager, bluez_knowing(bt_manager, connected=True, paired=True, services_resolved=True))
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio", return_value=True),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile"),
        patch("sendspin_bridge.bluetooth.manager.logger") as mock_logger,
    ):
        assert bt_manager.connect_device() is True

    warn_msgs = [str(call) for call in mock_logger.warning.call_args_list]
    assert not any("ServicesResolved" in m for m in warn_msgs), (
        f"no ServicesResolved warning expected when dbus unavailable; got: {warn_msgs}"
    )


def test_dbus_wait_services_resolved_polls_multiple_times_until_resolved():
    """Uninterrupted wait (wait_with_cancel returns True) must keep polling.

    Regression test: previously the contract was inverted and the helper exited
    after the first non-True property read, even when the caller had not
    cancelled. This verifies the loop actually iterates until ServicesResolved
    flips to True.
    """
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    property_calls = {"count": 0}

    def _fake_property(_path, _name, adapter_hci="hci0"):
        property_calls["count"] += 1
        # Flip to True on the 3rd read — forcing the loop to iterate twice.
        return property_calls["count"] >= 3

    with patch.object(bt_dbus, "_dbus_get_device_property", side_effect=_fake_property):
        ok = bt_dbus._dbus_wait_services_resolved(
            "/org/bluez/hci0/dev_X",
            is_connected_check=lambda: True,
            wait_with_cancel=lambda _: True,
            timeout=5.0,
            poll_interval=0.01,
        )
    assert ok is True
    assert property_calls["count"] >= 3


# ---------------------------------------------------------------------------
# bt_dbus._dbus_get_device_uuids — UUID listing helper
# ---------------------------------------------------------------------------


def test_dbus_get_device_uuids_normalizes_to_lowercase():
    """UUIDs from BlueZ are lowercased so set intersection with AUDIO_SINK_UUIDS works."""
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    raw = ["0000110B-0000-1000-8000-00805F9B34FB", "0000180F-0000-1000-8000-00805F9B34FB"]
    with patch.object(bt_dbus, "_dbus_get_device_property", return_value=raw):
        uuids = bt_dbus._dbus_get_device_uuids("/org/bluez/hci0/dev_X")
    assert uuids == [u.lower() for u in raw]


def test_dbus_get_device_uuids_returns_empty_on_missing_path():
    """Empty path → empty list; no dbus interaction."""
    import sendspin_bridge.bluetooth.dbus as bt_dbus

    assert bt_dbus._dbus_get_device_uuids(None) == []


# ---------------------------------------------------------------------------
# Connect/disconnect transition callbacks — wires MprisPlayer create/destroy
# (services/mpris_player.py) into the BT lifecycle so AVRCP buttons + speaker
# display follow the connection state without mpris_player having to poll.
# ---------------------------------------------------------------------------


@pytest.fixture()
def make_transition_manager(installed_bluez):
    """Factory fixture: BluetoothManager with transition callbacks wired.

    The fake reports no default controller (``show`` → empty) so adapter
    resolution lands on no adapter / no D-Bus path, matching the
    historical ``check_output("")`` guard.
    """
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    installed_bluez.on("show", stdout="")

    def _make(on_connected=None, on_disconnected=None):
        return BluetoothManager(
            mac_address="AA:BB:CC:DD:EE:FF",
            device_name="TestSpeaker",
            on_connected=on_connected,
            on_disconnected=on_disconnected,
        )

    return _make


def test_is_device_connected_fires_on_connected_on_false_to_true_transition(make_transition_manager):
    """First successful is_device_connected() call after construction (False→True)
    must fire ``on_connected``.  Closure target: spawn MprisPlayer for the MAC."""
    fired: list[str] = []
    mgr = make_transition_manager(on_connected=lambda: fired.append("up"))
    # mgr.connected starts False; flip the underlying check to True.
    attach(mgr, bluez_knowing(mgr, connected=True, paired=True))
    assert mgr.is_device_connected() is True

    assert fired == ["up"]


def test_is_device_connected_does_not_refire_on_connected_when_already_connected(make_transition_manager):
    """Subsequent is_device_connected() calls while already connected must NOT
    re-fire ``on_connected`` (would create duplicate MprisPlayer registrations
    on the system bus and crash dbus_fast)."""
    fired: list[str] = []
    mgr = make_transition_manager(on_connected=lambda: fired.append("up"))
    attach(mgr, bluez_knowing(mgr, connected=True, paired=True))
    mgr.is_device_connected()
    mgr.is_device_connected()
    mgr.is_device_connected()

    assert fired == ["up"]  # exactly one transition fire


def test_is_device_connected_fires_on_disconnected_on_true_to_false_transition(make_transition_manager):
    """Once connected, an is_device_connected() that returns False must fire
    ``on_disconnected``.  Closure target: tear down the MprisPlayer for the MAC."""
    fired: list[str] = []
    mgr = make_transition_manager(
        on_disconnected=lambda: fired.append("down"),
    )
    mgr.connected = True  # simulate prior connected state
    attach(mgr, bluez_knowing(mgr, connected=False, paired=True))
    assert mgr.is_device_connected() is False

    assert fired == ["down"]


def test_is_device_connected_does_not_refire_on_disconnected_when_already_disconnected(make_transition_manager):
    """Polling while disconnected must not re-fire on_disconnected (would
    repeatedly try to unregister an already-gone D-Bus path)."""
    fired: list[str] = []
    mgr = make_transition_manager(
        on_disconnected=lambda: fired.append("down"),
    )
    # mgr.connected starts False
    attach(mgr, bluez_knowing(mgr, connected=False, paired=True))
    mgr.is_device_connected()
    mgr.is_device_connected()

    assert fired == []  # no transition, no fire


def test_disconnect_device_fires_on_disconnected(make_transition_manager):
    """Explicit disconnect_device() must fire ``on_disconnected`` exactly once
    (operator pressed Disconnect or release flow ran)."""
    fired: list[str] = []
    mgr = make_transition_manager(
        on_disconnected=lambda: fired.append("down"),
    )
    mgr.connected = True
    attach(mgr, bluez_knowing(mgr, connected=True))
    assert mgr.disconnect_device() is True

    assert fired == ["down"]


def test_disconnect_device_does_not_refire_when_already_disconnected(make_transition_manager):
    """If disconnect_device runs but we were already disconnected (e.g. cleanup
    after the speaker dropped on its own), the disconnect callback already
    fired via is_device_connected — don't double-fire here."""
    fired: list[str] = []
    mgr = make_transition_manager(
        on_disconnected=lambda: fired.append("down"),
    )
    # mgr.connected starts False (already disconnected)
    attach(mgr, bluez_knowing(mgr, connected=True))
    mgr.disconnect_device()

    assert fired == []


def test_transition_callbacks_swallow_exceptions(make_transition_manager):
    """Callbacks crashing must NOT break the BT manager connection-state
    machine. (MprisPlayer registration failure should log and continue, not
    leave self.connected in an inconsistent state.)"""

    def _boom():
        raise RuntimeError("MprisPlayer registration failed")

    mgr = make_transition_manager(
        on_connected=_boom,
        on_disconnected=_boom,
    )
    attach(mgr, bluez_knowing(mgr, connected=True, paired=True))
    # Must not raise even though the callback explodes.
    assert mgr.is_device_connected() is True
    assert mgr.connected is True

    attach(mgr, bluez_knowing(mgr, connected=False, paired=True))
    assert mgr.is_device_connected() is False
    assert mgr.connected is False


def test_transition_callbacks_optional_when_unset(installed_bluez):
    """No callbacks supplied → behaviour identical to pre-2.63 (no AttributeError,
    no spurious calls).  Backwards-compat smoke."""
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    installed_bluez.on("show", stdout="")
    mgr = BluetoothManager(mac_address="AA:BB:CC:DD:EE:FF", device_name="TestSpeaker")
    attach(mgr, bluez_knowing(mgr, connected=True, paired=True))
    mgr.is_device_connected()
    attach(mgr, bluez_knowing(mgr, connected=False, paired=True))
    mgr.is_device_connected()
    # No exception → pass.


def test_apply_connected_state_fires_transition_on_change(make_transition_manager):
    """Regression for v2.63.0-rc.5: ``_apply_connected_state`` is the
    single setter that bookkeeps both ``self.connected`` and the
    transition callbacks.  Direct ``mgr.connected = X`` assignments
    in ``bt_monitor.py`` and ``_connect_device_inner`` previously
    bypassed the callback fire, leaving MprisPlayer unregistered
    when the D-Bus PropertiesChanged path drove the connect
    (production case on VM 105).
    """
    fired_up: list[str] = []
    fired_down: list[str] = []
    mgr = make_transition_manager(
        on_connected=lambda: fired_up.append("up"),
        on_disconnected=lambda: fired_down.append("down"),
    )

    mgr._apply_connected_state(True)
    mgr._apply_connected_state(True)  # idempotent
    mgr._apply_connected_state(False)
    mgr._apply_connected_state(False)  # idempotent
    mgr._apply_connected_state(True)

    assert fired_up == ["up", "up"]
    assert fired_down == ["down"]
    assert mgr.connected is True


def test_apply_connected_state_thread_safe_under_concurrent_calls(make_transition_manager):
    """Regression for Copilot review on PR #199: ``_apply_connected_state``
    is invoked from both the asyncio D-Bus monitor thread and the BT
    executor thread (running blocking ``connect_device`` /
    ``is_device_connected`` work).  Without serialisation the
    check-then-set window opens a race where two threads both observe
    ``self.connected==False``, both pass the check, and both fire
    ``on_connected`` — violating the ``exactly once per transition``
    guarantee MprisPlayer registration relies on (would surface as
    duplicate D-Bus exports / ``BadAddress`` from BlueZ).

    Stress: 8 threads racing to flip the state to True simultaneously
    must result in exactly one ``on_connected`` fire.  Without the
    lock this fails non-deterministically under load.
    """
    import threading as _t

    fired: list[str] = []
    fire_lock = _t.Lock()

    def _on_up() -> None:
        with fire_lock:
            fired.append("up")

    mgr = make_transition_manager(on_connected=_on_up)
    barrier = _t.Barrier(8)

    def _hit() -> None:
        barrier.wait()
        mgr._apply_connected_state(True)

    threads = [_t.Thread(target=_hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert fired == ["up"], f"on_connected fired {len(fired)} times under concurrent flips: {fired}"
    assert mgr.connected is True


def test_apply_connected_state_called_by_dbus_props_changed_path():
    """``bt_monitor`` D-Bus PropertiesChanged path must route
    ``connected`` mutations through ``_apply_connected_state`` so the
    on_connected hook fires when the device connects via D-Bus signal
    (the primary connect path on Linux hosts).

    Source-audit uses an AST walk (not a substring search) so docstrings
    or string literals containing the phrase ``mgr.connected = X``
    cannot create false positives, and assignments with non-standard
    spacing cannot create false negatives.  Catches ``Assign`` /
    ``AugAssign`` / ``AnnAssign`` nodes targeting ``mgr.connected``.
    """
    import ast
    from pathlib import Path

    import sendspin_bridge.bluetooth.monitor as bt_monitor

    tree = ast.parse(Path(bt_monitor.__file__).read_text())

    def _is_mgr_connected_target(node: ast.expr) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "connected"
            and isinstance(node.value, ast.Name)
            and node.value.id == "mgr"
        )

    direct: list[str] = []
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for t in targets:
            if _is_mgr_connected_target(t):
                direct.append(f"line {node.lineno}: {ast.dump(node)[:120]}")

    assert direct == [], f"bt_monitor still has direct mgr.connected assignments: {direct}"
    assert "_apply_connected_state" in Path(bt_monitor.__file__).read_text(), (
        "bt_monitor must call _apply_connected_state for the on_connected hook to fire"
    )


# ---------------------------------------------------------------------------
# CoD override: __init__ pre-validation and pre-pair hook
# ---------------------------------------------------------------------------


def _make_cod_manager(adapter_device_class_hex: str, adapter: str = "hci0"):
    from sendspin_bridge.bluetooth.manager import BluetoothManager

    with patch("subprocess.check_output", return_value=""):
        return BluetoothManager(
            mac_address="AA:BB:CC:DD:EE:FF",
            device_name="TestSpeaker",
            adapter_device_class_hex=adapter_device_class_hex,
            adapter=adapter,
        )


def test_cod_valid_hex_pre_parsed_to_int():
    mgr = _make_cod_manager("0x00010c")
    assert mgr._cod_override_int == 0x00010C


def test_cod_invalid_hex_pre_parsed_to_none_and_warns():
    import logging

    with (
        patch("subprocess.check_output", return_value=""),
        patch.object(logging.getLogger("sendspin_bridge.bluetooth.manager"), "warning") as mock_warn,
    ):
        mgr = _make_cod_manager("not-valid-hex")
    assert mgr._cod_override_int is None
    mock_warn.assert_called_once()


def test_cod_empty_hex_leaves_none():
    mgr = _make_cod_manager("")
    assert mgr._cod_override_int is None


def test_pre_pair_hook_calls_set_device_class_with_int(monkeypatch):
    mgr = _make_cod_manager("0x00010c", adapter="hci2")
    mgr.adapter_hci_name = "hci2"

    calls = []

    def fake_set_device_class(adapter_index, cod):
        calls.append((adapter_index, cod))
        return True

    monkeypatch.setattr(
        "sendspin_bridge.services.bluetooth.bt_class_of_device.set_device_class",
        fake_set_device_class,
    )
    mgr._maybe_apply_cod_override_pre_pair()
    assert calls == [(2, 0x00010C)]


def test_pre_pair_hook_no_op_when_cod_override_int_is_none(monkeypatch):
    mgr = _make_cod_manager("invalid")
    mgr.adapter_hci_name = "hci0"

    calls = []
    monkeypatch.setattr(
        "sendspin_bridge.services.bluetooth.bt_class_of_device.set_device_class",
        lambda *a: calls.append(a) or True,
    )
    mgr._maybe_apply_cod_override_pre_pair()
    assert calls == []


def test_pre_pair_hook_no_op_when_hex_empty(monkeypatch):
    mgr = _make_cod_manager("")
    mgr.adapter_hci_name = "hci0"

    calls = []
    monkeypatch.setattr(
        "sendspin_bridge.services.bluetooth.bt_class_of_device.set_device_class",
        lambda *a: calls.append(a) or True,
    )
    mgr._maybe_apply_cod_override_pre_pair()
    assert calls == []
