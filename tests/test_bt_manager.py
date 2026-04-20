"""Tests for bluetooth_manager.py — BluetoothManager class.

bluetooth_manager.py imports services.pulse (which gracefully handles missing
pulsectl_asyncio) and only imports ``dbus`` inside function bodies.  No
module-level sys.modules stubbing is needed for Python 3.9 compatibility.
"""

from unittest.mock import MagicMock, patch

import pytest


class _FakeStdout:
    def __init__(self, lines):
        self._lines = list(lines)

    def readline(self):
        if self._lines:
            return self._lines.pop(0)
        return ""


class _FakeStdin:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    def flush(self):
        return None


class _FakeProc:
    def __init__(self, stdout_lines, tail=""):
        self.stdin = _FakeStdin()
        self.stdout = _FakeStdout(stdout_lines)
        self._tail = tail
        self._returncode = None

    def poll(self):
        return self._returncode

    def communicate(self, timeout=None):
        self._returncode = 0
        return self._tail, ""

    def terminate(self):
        self._returncode = -15

    def kill(self):
        self._returncode = -9

    def wait(self, timeout=None):
        return 0


class _FakeSelector:
    def __init__(self, stdout):
        self._stdout = stdout

    def register(self, *_args, **_kwargs):
        return None

    def select(self, timeout=None):
        return [object()] if self._stdout._lines else []

    def close(self):
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    """Redirect config to a temp directory."""
    import config

    monkeypatch.setattr(config, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config, "CONFIG_FILE", tmp_path / "config.json")


@pytest.fixture()
def bt_manager():
    """Create a BluetoothManager with reasonable defaults for testing."""
    from bluetooth_manager import BluetoothManager

    # Mock subprocess calls that happen in __init__ (adapter resolution)
    with patch("subprocess.check_output", return_value=""):
        mgr = BluetoothManager(
            mac_address="AA:BB:CC:DD:EE:FF",
            device_name="TestSpeaker",
        )
    return mgr


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_bt_executor_pool_size():
    """The module-level thread pool must have at least 4 workers."""
    from bluetooth_manager import _bt_executor

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
        patch("bt_audio.list_sinks", return_value=fake_sinks),
        patch("bt_audio.get_sink_volume", return_value=50),
        patch("bt_audio.set_sink_mute", return_value=True),
        patch("bt_audio.set_sink_volume", return_value=True),
        patch("time.sleep"),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True


def test_configure_bluetooth_audio_pulseaudio_pattern(bt_manager):
    """Finds a PulseAudio-format sink (bluez_sink.MAC.a2dp_sink)."""
    pa_mac = bt_manager.mac_address.replace(":", "_")
    sink_name = f"bluez_sink.{pa_mac}.a2dp_sink"

    fake_sinks = [{"name": sink_name, "description": "BT Speaker"}]
    with (
        patch("bt_audio.list_sinks", return_value=fake_sinks),
        patch("bt_audio.get_sink_volume", return_value=50),
        patch("bt_audio.set_sink_mute", return_value=True),
        patch("bt_audio.set_sink_volume", return_value=True),
        patch("time.sleep"),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True


def test_configure_bluetooth_audio_no_sink(bt_manager):
    """Returns False when no matching sink is found."""
    with (
        patch("bt_audio.list_sinks", return_value=[]),
        patch("bt_audio.get_sink_volume", return_value=None),
        patch("bt_audio.set_sink_mute", return_value=True),
        patch("time.sleep"),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is False


def test_configure_bluetooth_audio_autoswitches_bluez_card_profile(bt_manager):
    """When no sink found but bluez_card exists with non-a2dp profile, switch
    to a2dp_sink and retry once. Covers AKG Y500 / BlueZ 5.82 regression where
    the card connects in headset_head_unit profile and no sink is exposed."""
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
            "profiles": ["off", "a2dp_sink", "headset_head_unit"],
        }
    ]

    set_profile_calls = []

    def _set_card_profile(card, profile):
        set_profile_calls.append((card, profile))
        return True

    with (
        patch("bt_audio.list_sinks", side_effect=_list_sinks),
        patch("bt_audio.get_sink_volume", side_effect=_get_sink_volume),
        patch("bt_audio.set_sink_mute", return_value=True),
        patch("bt_audio.set_sink_volume", return_value=True),
        patch("bt_audio.list_cards", return_value=cards_payload),
        patch("bt_audio.set_card_profile", side_effect=_set_card_profile),
        patch("bt_audio._warn_pipewire_session"),
        patch("time.sleep"),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is True
    assert set_profile_calls == [(card_name, "a2dp_sink")]


def bt_audio_retry_threshold():
    """Return a call count at which the sink should become visible (after switch)."""
    import bt_audio

    # After retries exhaust, profile switch triggers one extra list_sinks refresh.
    return bt_audio._SINK_RETRY_COUNT + 1


def test_configure_bluetooth_audio_skips_profile_switch_when_already_a2dp(bt_manager):
    """If active_profile is already a2dp_sink, do not call set_card_profile."""
    pa_mac = bt_manager.mac_address.replace(":", "_")
    card_name = f"bluez_card.{pa_mac}"

    cards_payload = [
        {
            "name": card_name,
            "driver": "module-bluez5-device.c",
            "active_profile": "a2dp_sink",
            "profiles": ["off", "a2dp_sink", "headset_head_unit"],
        }
    ]

    set_profile_calls = []

    with (
        patch("bt_audio.list_sinks", return_value=[]),
        patch("bt_audio.get_sink_volume", return_value=None),
        patch("bt_audio.set_sink_mute", return_value=True),
        patch("bt_audio.list_cards", return_value=cards_payload),
        patch("bt_audio.set_card_profile", side_effect=lambda c, p: set_profile_calls.append((c, p)) or True),
        patch("bt_audio._warn_pipewire_session"),
        patch("time.sleep"),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is False
    assert set_profile_calls == []


def test_configure_bluetooth_audio_retries_five_times(bt_manager):
    """Default retry count is 5 (env-configurable via SINK_RETRY_COUNT)."""
    import bt_audio

    assert bt_audio._SINK_RETRY_COUNT >= 5

    call_count = 0

    def _counting_list_sinks():
        nonlocal call_count
        call_count += 1
        return []

    with (
        patch("bt_audio.list_sinks", side_effect=_counting_list_sinks),
        patch("bt_audio.get_sink_volume", return_value=None),
        patch("bt_audio.set_sink_mute", return_value=True),
        patch("bt_audio._warn_pipewire_session"),
        patch("time.sleep"),
    ):
        result = bt_manager.configure_bluetooth_audio()

    assert result is False
    # Initial call + (retry_count - 1) refreshes = retry_count total list_sinks calls
    assert call_count == bt_audio._SINK_RETRY_COUNT


def test_warn_pipewire_session_emits_on_pipewire_without_bt_sinks():
    """_warn_pipewire_session logs remediation when PipeWire has no BT sinks."""
    import bt_audio

    with (
        patch("services.pulse.get_server_name", return_value="PulseAudio (on PipeWire 1.0.5)"),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_pipewire_session({"sendspin_fallback"})

    messages = [call.args[0] for call in mock_warn.call_args_list]
    assert any("WirePlumber" in m for m in messages)
    assert any("loginctl enable-linger" in m for m in messages)


def test_warn_pipewire_session_silent_on_pulseaudio():
    """_warn_pipewire_session does nothing on native PulseAudio."""
    import bt_audio

    with (
        patch("services.pulse.get_server_name", return_value="pulseaudio 17.0"),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_pipewire_session(set())

    mock_warn.assert_not_called()


def test_warn_pipewire_session_silent_when_bt_sinks_present():
    """_warn_pipewire_session stays quiet if BT sinks exist (no false alarm)."""
    import bt_audio

    sinks = {"bluez_output.AA_BB_CC_DD_EE_FF.1", "sendspin_fallback"}
    with (
        patch("services.pulse.get_server_name", return_value="PulseAudio (on PipeWire 1.0.5)"),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_pipewire_session(sinks)

    mock_warn.assert_not_called()


def test_warn_pipewire_session_also_checks_wireplumber_logind():
    """_warn_pipewire_session calls _warn_wireplumber_logind when PipeWire has no BT sinks."""
    import bt_audio

    with (
        patch("services.pulse.get_server_name", return_value="PulseAudio (on PipeWire 1.0.5)"),
        patch.object(bt_audio, "_warn_wireplumber_logind") as mock_logind,
        patch.object(bt_audio.logger, "warning"),
    ):
        bt_audio._warn_pipewire_session({"sendspin_fallback"})

    mock_logind.assert_called_once()


def test_is_wireplumber_logind_active_returns_false_when_override_exists(tmp_path):
    """_is_wireplumber_logind_active returns False when user override disables with-logind."""
    import bt_audio

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
    import bt_audio

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
    import bt_audio

    result = bt_audio._is_wireplumber_logind_active(
        _override_dirs=[tmp_path / "nonexistent"],
        _default_cfg_path=tmp_path / "nonexistent.lua",
    )
    assert result is None


def test_warn_wireplumber_logind_emits_warning_when_active():
    """_warn_wireplumber_logind logs fix instructions when logind is active."""
    import bt_audio

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
    import bt_audio

    with (
        patch.object(bt_audio, "_is_wireplumber_logind_active", return_value=False),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_wireplumber_logind()

    mock_warn.assert_not_called()


def test_warn_wireplumber_logind_silent_when_unknown():
    """_warn_wireplumber_logind stays quiet when detection returns None."""
    import bt_audio

    with (
        patch.object(bt_audio, "_is_wireplumber_logind_active", return_value=None),
        patch.object(bt_audio.logger, "warning") as mock_warn,
    ):
        bt_audio._warn_wireplumber_logind()

    mock_warn.assert_not_called()


def test_device_name_fallback():
    """When no device_name is given, it falls back to the MAC address."""
    from bluetooth_manager import BluetoothManager

    with patch("subprocess.check_output", return_value=""):
        mgr = BluetoothManager(mac_address="11:22:33:44:55:66")

    assert mgr.device_name == "11:22:33:44:55:66"


def test_unresolved_adapter_disables_dbus_path():
    """When adapter resolution fails, D-Bus path should remain unavailable."""
    from bluetooth_manager import BluetoothManager

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

    from bt_monitor import _monitor_dbus

    with pytest.raises(RuntimeError, match="adapter resolution failed"):
        await _monitor_dbus(bt_manager, None, None)


def test_record_reconnect_prunes_old_entries(bt_manager):
    """Only reconnects inside the churn window should be retained."""
    bt_manager._CHURN_WINDOW = 10
    with patch("bluetooth_manager.time.monotonic", side_effect=[100.0, 111.0]):
        bt_manager._record_reconnect()
        bt_manager._record_reconnect()

    assert bt_manager._reconnect_timestamps == [111.0]


def test_check_reconnect_churn_disables_management(bt_manager):
    """Churn threshold should auto-disable management and update host status."""
    bt_manager._CHURN_THRESHOLD = 2
    bt_manager._CHURN_WINDOW = 30
    bt_manager._reconnect_timestamps = [90.0, 99.0]
    bt_manager.host = MagicMock()
    bt_manager.host.bt_management_enabled = True

    with (
        patch("bluetooth_manager.time.monotonic", return_value=100.0),
        patch("services.bluetooth.persist_device_released") as persist_released,
    ):
        assert bt_manager._check_reconnect_churn() is True

    assert bt_manager.management_enabled is False
    assert bt_manager.host.bt_management_enabled is False
    bt_manager.host.update_status.assert_called_once()
    persist_released.assert_called_once_with("TestSpeaker", True)


def test_cancel_reconnect_clears_runtime_reconnect_status(bt_manager):
    mock_host = MagicMock()
    mock_host.get_status_value = MagicMock(return_value=True)
    bt_manager.host = mock_host

    bt_manager.cancel_reconnect()

    assert bt_manager.management_enabled is False
    assert bt_manager._cancel_reconnect.is_set() is True
    bt_manager.host.update_status.assert_called_once_with({"reconnecting": False, "reconnect_attempt": 0})


def test_connect_device_aborts_when_release_cancels_active_reconnect(bt_manager):
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "disconnect_device", return_value=True) as disconnect_device,
        patch.object(bt_manager, "configure_bluetooth_audio"),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):

        def _run_side_effect(commands):
            if commands == [f"connect {bt_manager.mac_address}"]:
                bt_manager.cancel_reconnect()
            return True, ""

        bt_manager._run_bluetoothctl = MagicMock(side_effect=_run_side_effect)

        assert bt_manager.connect_device() is False

    disconnect_device.assert_called_once()


def test_is_device_paired_returns_none_when_device_not_available(bt_manager):
    with (
        patch("bluetooth_manager._dbus_get_device_property", return_value=None),
        patch.object(bt_manager, "_run_bluetoothctl", return_value=(False, "Device AA:BB:CC:DD:EE:FF not available")),
    ):
        assert bt_manager.is_device_paired() is None


def test_connect_device_does_not_repair_when_pairing_state_unknown(bt_manager):
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=None),
        patch.object(bt_manager, "pair_device", return_value=True) as pair_device,
        patch.object(bt_manager, "configure_bluetooth_audio"),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        bt_manager._run_bluetoothctl = MagicMock(return_value=(True, ""))

        assert bt_manager.connect_device() is True

    pair_device.assert_not_called()


def test_pair_device_trusts_only_after_pair_success(bt_manager):
    fake_proc = _FakeProc(
        stdout_lines=["Confirm passkey 123456 (yes/no):\n", "Pairing successful\n"],
        tail="Trusted: yes\nPaired: yes\n",
    )

    with (
        patch("bluetooth_manager.subprocess.run"),
        patch("bluetooth_manager.subprocess.Popen", return_value=fake_proc),
        patch("selectors.DefaultSelector", side_effect=lambda: _FakeSelector(fake_proc.stdout)),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        assert bt_manager.pair_device() is True

    assert fake_proc.stdin.writes[0].endswith("scan on\n")
    assert fake_proc.stdin.writes[1] == f"pair {bt_manager.mac_address}\n"
    assert fake_proc.stdin.writes[2] == "yes\n"
    assert fake_proc.stdin.writes[3].startswith(f"trust {bt_manager.mac_address}\n")
    assert "trust" not in fake_proc.stdin.writes[1]


# ---------------------------------------------------------------------------
# pair_device — cancel-after-spawn race protection
# ---------------------------------------------------------------------------


def test_pair_device_cancelled_before_popen(bt_manager):
    """pair_device returns False immediately if cancelled before Popen."""
    bt_manager._cancel_reconnect.set()

    with patch("bluetooth_manager.subprocess.Popen") as mock_popen:
        assert bt_manager.pair_device() is False

    mock_popen.assert_not_called()


def test_pair_device_cancelled_after_popen(bt_manager):
    """pair_device terminates the subprocess if cancelled between Popen and first write."""
    fake_proc = _FakeProc(stdout_lines=[], tail="")

    def _set_cancelled(*_a, **_kw):
        bt_manager._cancel_reconnect.set()
        return fake_proc

    with (
        patch("bluetooth_manager.subprocess.run"),
        patch("bluetooth_manager.subprocess.Popen", side_effect=_set_cancelled),
        patch("selectors.DefaultSelector", side_effect=lambda: _FakeSelector(fake_proc.stdout)),
    ):
        assert bt_manager.pair_device() is False

    # The process should have been terminated (wait sets _returncode)
    assert fake_proc._returncode is not None


def test_pair_device_clears_stale_agent_before_pairing(bt_manager):
    """pair_device must run `agent off` cleanup before the main pair session.

    Without it, a leftover D-Bus agent object from a previous bluetoothctl
    session causes `Failed to register agent object` and pairing fails with
    org.bluez.Error.ConnectionAttemptFailed (issue #162 — same root cause as
    the standalone pair flow). Mirrors the fix in routes/api_bt.py.
    """
    fake_proc = _FakeProc(
        stdout_lines=["Pairing successful\n"],
        tail="Trusted: yes\nPaired: yes\n",
    )
    cleanup_calls = []

    def _capture_run(cmd, **kwargs):
        cleanup_calls.append({"cmd": cmd, "input": kwargs.get("input", "")})
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    with (
        patch("bluetooth_manager.subprocess.run", side_effect=_capture_run),
        patch("bluetooth_manager.subprocess.Popen", return_value=fake_proc),
        patch("selectors.DefaultSelector", side_effect=lambda: _FakeSelector(fake_proc.stdout)),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        assert bt_manager.pair_device() is True

    assert cleanup_calls, "Expected a subprocess.run cleanup call before Popen"
    cleanup_input = cleanup_calls[0]["input"]
    assert "agent off\n" in cleanup_input
    # `agent off` must come before any `remove` so the next `agent on` is clean
    assert cleanup_input.index("agent off") < cleanup_input.index(f"remove {bt_manager.mac_address}")


def test_connect_device_clears_stale_bluez_entry_after_repeated_unknown_pairing(bt_manager):
    """After K consecutive failed reconnects where BlueZ has no device object,
    purge the stale cache entry so the next cycle can trigger pair_device.

    KALLSUP-class issue (#162): some speakers leave BlueZ with no current device
    object after disconnect. is_device_paired() returns None, connect fails,
    monitor loops forever logging `Failed to connect (not connected after 5
    status checks)`. Forcing `bluetoothctl remove {mac}` lets the next reconnect
    see paired==False and escalate to pair_device (which now has #162 cleanups).
    """
    run_calls: list[str] = []

    def _capture_run(cmd, **kwargs):
        run_calls.append(kwargs.get("input", ""))
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    def _remove_inputs():
        return [s for s in run_calls if f"remove {bt_manager.mac_address}" in s]

    with (
        patch.object(bt_manager, "is_device_connected", return_value=False),
        patch.object(bt_manager, "is_device_paired", return_value=None),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch("bluetooth_manager.subprocess.run", side_effect=_capture_run),
    ):
        bt_manager._run_bluetoothctl = MagicMock(return_value=(True, ""))

        for _ in range(2):
            bt_manager.connect_device()

        assert _remove_inputs() == [], (
            f"Stale-cache cleanup must not trigger before threshold; got {_remove_inputs()!r}"
        )

        bt_manager.connect_device()

        assert _remove_inputs(), "Expected `remove {mac}` cleanup once paired-unknown count reached threshold"


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
        bt_manager._run_bluetoothctl = MagicMock(return_value=(True, ""))
        assert bt_manager.connect_device() is True

    assert bt_manager._paired_unknown_count == 0


@pytest.mark.parametrize(
    "prompt_line",
    [
        "[agent] Enter PIN code:\n",
        "[agent] Enter passkey (number in 0-999999):\n",
    ],
    ids=["enter_pin_code", "enter_passkey"],
)
def test_pair_device_auto_answers_legacy_pin_prompt(bt_manager, prompt_line):
    """pair_device must auto-enter `0000` when bluetoothctl asks for a legacy PIN.

    Legacy BT 2.x devices (e.g. HMDX JAM, `LegacyPairing: yes`) prompt
    `[agent] Enter PIN code:` or `[agent] Enter passkey:` depending on the
    device profile and BlueZ version. Both must auto-answer with `0000` so
    pairing doesn't time out (issue #162). Mirrors the fix in routes/api_bt.py.
    """
    fake_proc = _FakeProc(
        stdout_lines=[
            prompt_line,
            "Pairing successful\n",
        ],
        tail="Paired: yes\n",
    )

    with (
        patch("bluetooth_manager.subprocess.run"),
        patch("bluetooth_manager.subprocess.Popen", return_value=fake_proc),
        patch("selectors.DefaultSelector", side_effect=lambda: _FakeSelector(fake_proc.stdout)),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        assert bt_manager.pair_device() is True

    assert "0000\n" in fake_proc.stdin.writes, (
        f"Expected `0000\\n` to be written to bluetoothctl stdin in response to "
        f"{prompt_line!r}, got {fake_proc.stdin.writes!r}"
    )


# ---------------------------------------------------------------------------
# _resolve_adapter_hci_name — fallback paths
# ---------------------------------------------------------------------------


def test_resolve_adapter_hci_name_returns_config_adapter_directly():
    """When adapter is already hciN, _resolve_adapter_hci_name returns it as-is."""
    from bluetooth_manager import BluetoothManager

    with patch("subprocess.check_output", return_value=""):
        mgr = BluetoothManager(mac_address="AA:BB:CC:DD:EE:FF", adapter="hci1")

    assert mgr.adapter_hci_name == "hci1"


def test_resolve_adapter_hci_name_bluetoothctl_fallback():
    """When sysfs is unavailable, falls back to bluetoothctl list output."""
    from bluetooth_manager import BluetoothManager

    bt_list_output = "Controller C0:FB:F9:62:D6:9D MyAdapter1 [default]\nController C0:FB:F9:62:D7:D6 MyAdapter2\n"

    with (
        patch("subprocess.check_output", return_value=""),
        patch.object(BluetoothManager, "_detect_default_adapter_mac", return_value="C0:FB:F9:62:D7:D6"),
        patch("pathlib.Path.iterdir", side_effect=OSError("no sysfs")),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(stdout=bt_list_output, returncode=0)
        mgr = BluetoothManager(mac_address="AA:BB:CC:DD:EE:FF")

    assert mgr.adapter_hci_name == "hci1"


def test_resolve_adapter_hci_name_empty_when_all_fail():
    """Returns empty string when both sysfs and bluetoothctl fail."""
    from bluetooth_manager import BluetoothManager

    with (
        patch("subprocess.check_output", return_value=""),
        patch.object(BluetoothManager, "_detect_default_adapter_mac", return_value="FF:FF:FF:FF:FF:FF"),
        patch("pathlib.Path.iterdir", side_effect=OSError("no sysfs")),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = MagicMock(stdout="Controller AA:BB:CC:DD:EE:00 Adapter\n", returncode=0)
        mgr = BluetoothManager(mac_address="AA:BB:CC:DD:EE:FF")

    assert mgr.adapter_hci_name == ""
    assert mgr._dbus_device_path is None


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

    with (
        patch.object(bt_manager, "is_device_connected", side_effect=_fake_connected),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio"),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
    ):
        bt_manager._run_bluetoothctl = MagicMock(return_value=(True, ""))

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
        bt_manager._run_bluetoothctl = MagicMock(return_value=(True, ""))

        result = bt_manager.connect_device()

    assert result is False


# ---------------------------------------------------------------------------
# is_device_connected — various bluetoothctl output formats
# ---------------------------------------------------------------------------


def test_is_device_connected_dbus_true(bt_manager):
    """D-Bus returns True — should report connected."""
    with patch("bluetooth_manager._dbus_get_device_property", return_value=True):
        assert bt_manager.is_device_connected() is True
    assert bt_manager.connected is True


def test_is_device_connected_dbus_false(bt_manager):
    """D-Bus returns False — should report disconnected."""
    bt_manager.connected = True  # was previously connected
    with patch("bluetooth_manager._dbus_get_device_property", return_value=False):
        assert bt_manager.is_device_connected() is False
    assert bt_manager.connected is False


def test_is_device_connected_bluetoothctl_fallback(bt_manager):
    """When D-Bus is unavailable, falls back to bluetoothctl output."""
    with (
        patch("bluetooth_manager._dbus_get_device_property", return_value=None),
        patch.object(bt_manager, "_run_bluetoothctl", return_value=(True, "Connected: yes")),
    ):
        assert bt_manager.is_device_connected() is True


def test_is_device_connected_exception_returns_false(bt_manager):
    """Exceptions in connection check should return False."""
    bt_manager.connected = True
    with patch("bluetooth_manager._dbus_get_device_property", side_effect=RuntimeError("D-Bus exploded")):
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
    """Management is released after max_reconnect_fails consecutive failures."""
    bt_manager.max_reconnect_fails = 3
    bt_manager.host = MagicMock()
    bt_manager.host.bt_management_enabled = True

    with patch("services.bluetooth.persist_device_released"):
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
# bluez/bluez#1922 A2DP Sink workarounds (5.86 dual-role regression)
# ---------------------------------------------------------------------------


def test_force_a2dp_sink_profile_calls_connect_profile_with_sink_uuid(bt_manager):
    """_force_a2dp_sink_profile invokes _dbus_connect_profile with the A2DP Sink UUID."""
    from bt_dbus import A2DP_SINK_UUID

    bt_manager._dbus_device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    with patch("bluetooth_manager._dbus_connect_profile", return_value=(True, "")) as mock_cp:
        assert bt_manager._force_a2dp_sink_profile() is True

    mock_cp.assert_called_once_with(bt_manager._dbus_device_path, A2DP_SINK_UUID)


def test_force_a2dp_sink_profile_returns_false_on_error(bt_manager):
    """Returns False when ConnectProfile raises a non-AlreadyConnected error."""
    bt_manager._dbus_device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    with patch(
        "bluetooth_manager._dbus_connect_profile",
        return_value=(False, "org.bluez.Error.NotSupported"),
    ):
        assert bt_manager._force_a2dp_sink_profile() is False


def test_force_a2dp_sink_profile_treats_already_connected_as_benign(bt_manager):
    """AlreadyConnected error on a healthy stack must not be logged as a warning."""
    bt_manager._dbus_device_path = "/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF"
    with (
        patch(
            "bluetooth_manager._dbus_connect_profile",
            return_value=(False, "org.bluez.Error.AlreadyConnected"),
        ),
        patch("bluetooth_manager.logger.info") as mock_info,
    ):
        result = bt_manager._force_a2dp_sink_profile()

    assert result is False
    assert mock_info.call_count == 0


def test_connect_device_force_a2dp_sink_profile_after_successful_connect(bt_manager):
    """After the generic Connect() succeeds, the A2DP Sink profile hint is issued."""
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio", return_value=True),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile") as mock_force,
    ):
        bt_manager._run_bluetoothctl = MagicMock(return_value=(True, ""))

        assert bt_manager.connect_device() is True

    mock_force.assert_called_once()


def test_connect_device_triggers_a2dp_dance_when_no_sink_appears(bt_manager):
    """If sink discovery fails, _a2dp_recovery_dance runs once and sink is retried."""
    configure_calls = []

    def _configure():
        configure_calls.append(1)
        # First call (post-connect) → no sink; second call (post-dance) → sink ok
        return len(configure_calls) > 1

    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio", side_effect=_configure),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile"),
        patch.object(bt_manager, "_a2dp_recovery_dance", return_value=True) as mock_dance,
    ):
        bt_manager._run_bluetoothctl = MagicMock(return_value=(True, ""))

        assert bt_manager.connect_device() is True

    mock_dance.assert_called_once()
    # configure_bluetooth_audio runs once before the dance and once after
    assert len(configure_calls) == 2


def test_connect_device_does_not_dance_when_sink_appears_immediately(bt_manager):
    """Healthy stack: sink found on first try → no dance."""
    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio", return_value=True),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile"),
        patch.object(bt_manager, "_a2dp_recovery_dance") as mock_dance,
    ):
        bt_manager._run_bluetoothctl = MagicMock(return_value=(True, ""))

        assert bt_manager.connect_device() is True

    mock_dance.assert_not_called()


def test_connect_device_dance_runs_at_most_once_per_connect_cycle(bt_manager):
    """The dance counter must block a second dance within the same connect cycle."""
    # Pre-exhaust the dance credit as if a prior path consumed it.
    bt_manager._a2dp_dance_remaining = 0

    with (
        patch.object(bt_manager, "is_device_connected", side_effect=[False, True]),
        patch.object(bt_manager, "is_device_paired", return_value=True),
        patch.object(bt_manager, "configure_bluetooth_audio", return_value=False),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile"),
        patch.object(bt_manager, "_a2dp_recovery_dance") as mock_dance,
    ):
        bt_manager._run_bluetoothctl = MagicMock(return_value=(True, ""))

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

    with (
        patch("bluetooth_manager._dbus_call_device_method", return_value=True),
        patch.object(bt_manager, "is_device_connected", side_effect=lambda: next(is_connected_results)),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile") as mock_force,
    ):
        bt_manager._run_bluetoothctl = MagicMock(return_value=(True, ""))

        result = bt_manager._a2dp_recovery_dance()

    assert result is True
    assert bt_manager.connected is True
    # After the reconnect succeeds, the dance re-issues the A2DP Sink hint.
    mock_force.assert_called_once()


def test_a2dp_recovery_dance_returns_false_when_reconnect_never_succeeds(bt_manager):
    """If the link never comes back up, the dance reports failure."""
    with (
        patch("bluetooth_manager._dbus_call_device_method", return_value=True),
        patch.object(bt_manager, "is_device_connected", return_value=False),
        patch.object(bt_manager, "_wait_with_cancel", return_value=True),
        patch.object(bt_manager, "_force_a2dp_sink_profile"),
    ):
        bt_manager._run_bluetoothctl = MagicMock(return_value=(True, ""))

        assert bt_manager._a2dp_recovery_dance() is False


# ---------------------------------------------------------------------------
# bt_dbus._dbus_connect_profile — low-level D-Bus wrapper
# ---------------------------------------------------------------------------


def test_dbus_connect_profile_returns_false_when_dbus_module_missing():
    """With dbus-python unavailable the helper reports dbus-unavailable reason."""
    import bt_dbus

    with patch.object(bt_dbus, "dbus", None):
        ok, reason = bt_dbus._dbus_connect_profile("/org/bluez/hci0/dev_X", "uuid")

    assert ok is False
    assert "dbus" in reason.lower()


def test_dbus_connect_profile_returns_false_for_empty_device_path():
    """Empty device path short-circuits to False without touching the bus."""
    import bt_dbus

    ok, reason = bt_dbus._dbus_connect_profile(None, bt_dbus.A2DP_SINK_UUID)
    assert ok is False
    assert reason
