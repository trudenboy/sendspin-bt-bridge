from types import SimpleNamespace

import pytest

from sendspin_bridge.services.diagnostics.guidance_issue_registry import issue_definition


def test_suspend_issue_is_registered_as_audio_warning():
    d = issue_definition("bt_sink_suspend_in_gaps")
    assert d is not None and d.layer == "audio" and d.severity == "warning"
    assert set(d.default_reason_codes) >= {"observed_suspend", "module_suspend_on_idle", "wireplumber_suspend_timeout"}
    assert d.priority == 34


def _device(**extra):
    # the recovery assistant reads device snapshots by attribute (see test_recovery_assistant.py)
    return SimpleNamespace(
        player_name="ENEBY20",
        bluetooth_connected=True,
        has_sink=True,
        server_connected=True,
        health_summary={},
        **extra,
    )


def test_recovery_assistant_reports_a_device_with_observed_suspends():
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_device_issues

    dev = _device(host_sink_suspends={"observed": 2, "warn": True, "reason": "observed_suspend", "policy": None})
    assert "bt_sink_suspend_in_gaps" in [i.key for i in _build_device_issues([dev])]


def test_recovery_assistant_is_quiet_without_a_warning():
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_device_issues

    dev = _device(host_sink_suspends={"observed": 0, "warn": False, "reason": None, "policy": None})
    assert "bt_sink_suspend_in_gaps" not in [i.key for i in _build_device_issues([dev])]
    assert "bt_sink_suspend_in_gaps" not in [i.key for i in _build_device_issues([_device()])]


def test_recovery_assistant_reads_the_normalized_audio_state():
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_device_issues

    audio = {"has_sink": True, "host_sink_suspends": {"warn": True, "reason": "wireplumber_suspend_timeout"}}
    dev = SimpleNamespace(
        player_name="X",
        state_model={"bluetooth": {"connected": True}, "audio": audio},
        health_summary={},
    )
    assert "bt_sink_suspend_in_gaps" in [i.key for i in _build_device_issues([dev])]


@pytest.fixture
def client_factory():
    from sendspin_bridge.bridge.client import SendspinClient

    def make():
        return SendspinClient("Test Player", "localhost", 9000)

    return make


def test_client_marks_its_own_suspend_requests(client_factory):
    c = client_factory()
    assert c._suspend_requested_at is None  # created in __init__
    c._mark_suspend_requested(now=100.0)
    assert c._bridge_requested_suspend(now=101.5) is True
    assert c._bridge_requested_suspend(now=103.0) is False


def test_client_listener_publishes_host_suspends(client_factory):
    c = client_factory()
    c.bt_manager = SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF")
    c._update_status({"bluetooth_connected": True})
    c._on_sink_state_change("AA:BB:CC:DD:EE:FF", "idle", "suspended", "bluez_sink.X")
    c._on_sink_state_change("11:22:33:44:55:66", "idle", "suspended", "bluez_sink.Y")  # not ours
    assert c.status["host_sink_suspends"]["observed"] == 1


def test_client_listener_ignores_its_own_power_save(client_factory):
    c = client_factory()
    c.bt_manager = SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF")
    c._update_status({"bluetooth_connected": True, "bt_power_save": True})
    c._on_sink_state_change("AA:BB:CC:DD:EE:FF", "running", "suspended", "bluez_sink.X")
    assert (c.status.get("host_sink_suspends") or {}).get("observed", 0) == 0


def test_pulse_sink_client_never_warns(client_factory):
    from sendspin_bridge.config.players import PULSE_SINK
    from sendspin_bridge.services.audio.sink_suspend_policy import SuspendPolicy

    c = client_factory()
    c.backend_type = PULSE_SINK
    c.set_suspend_policy(SuspendPolicy("pipewire", True, 5.0, "wireplumber"))
    assert not c.status.get("host_sink_suspends")


def test_bluetooth_client_warns_on_a_static_policy(client_factory):
    from sendspin_bridge.services.audio.sink_suspend_policy import SuspendPolicy

    c = client_factory()
    c.set_suspend_policy(SuspendPolicy("pipewire", True, 5.0, "wireplumber"))
    assert c.status["host_sink_suspends"]["warn"] is True


def test_attach_is_idempotent_and_hands_over_the_cached_policy(client_factory, monkeypatch):
    from sendspin_bridge.services.audio import sink_suspend_policy as m

    policy = m.SuspendPolicy("pulseaudio", True, 3.0, "module-suspend-on-idle")
    monkeypatch.setattr(m, "_last_policy", policy)

    class Monitor:
        def __init__(self):
            self.listeners = []

        def add_state_listener(self, fn):
            self.listeners.append(fn)

    mon = Monitor()
    c = client_factory()
    c._attach_suspend_listener(mon)
    c._attach_suspend_listener(mon)
    assert len(mon.listeners) == 1
    assert c.status["host_sink_suspends"]["reason"] == "module_suspend_on_idle"


@pytest.mark.asyncio
async def test_enter_power_save_mark_suppresses_an_observed_suspend(client_factory, monkeypatch):
    from sendspin_bridge.services.audio import pulse

    async def fake_suspend(sink, suspend):
        return True

    monkeypatch.setattr(pulse, "asuspend_sink", fake_suspend)
    c = client_factory()
    c.bt_manager = SimpleNamespace(mac_address="AA:BB:CC:DD:EE:FF")
    c.bluetooth_sink_name = "bluez_sink.X"
    c._update_status({"bluetooth_connected": True})
    await c._enter_power_save()
    c._update_status({"bt_power_save": False})  # only the 2 s mark is left to tell it is ours
    c._on_sink_state_change("AA:BB:CC:DD:EE:FF", "running", "suspended", "bluez_sink.X")
    assert (c.status.get("host_sink_suspends") or {}).get("observed", 0) == 0


def test_recovery_assistant_skips_disconnected_or_released_devices():
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_sink_suspend_issues

    facts = {"warn": True, "reason": "observed_suspend"}
    off = _device(host_sink_suspends=facts)
    off.bluetooth_connected = False
    released = _device(host_sink_suspends=facts, bt_management_enabled=False)
    assert _build_sink_suspend_issues([off, released]) == []


def test_real_normalized_state_carries_the_warning_to_the_producer():
    from sendspin_bridge.services.diagnostics.recovery_assistant import (
        NormalizedRecoveryDevice,
        _build_sink_suspend_issues,
    )
    from sendspin_bridge.services.ipc.bridge_state_model import build_normalized_device_state

    status = {
        "player_name": "ENEBY20",
        "bluetooth_connected": True,
        "has_sink": True,
        "host_sink_suspends": {"warn": True, "reason": "observed_suspend"},
    }
    state = build_normalized_device_state(status)
    dev = NormalizedRecoveryDevice(
        player_name=state.player_name, state_model=state.to_dict(), health_summary={}, recent_events=[]
    )
    assert [i.key for i in _build_sink_suspend_issues([dev])] == ["bt_sink_suspend_in_gaps"]


def test_a_device_snapshot_carries_the_warning_to_the_producer():
    """Production hands the bridge state model ``DeviceSnapshot`` s: the status lives in ``extra``."""
    from sendspin_bridge.services.diagnostics.recovery_assistant import (
        NormalizedRecoveryDevice,
        _build_sink_suspend_issues,
    )
    from sendspin_bridge.services.ipc.bridge_state_model import build_normalized_device_state
    from sendspin_bridge.services.lifecycle.status_snapshot import DeviceSnapshot

    snap = DeviceSnapshot(
        player_name="ENEBY20",
        bluetooth_connected=True,
        has_sink=True,
        extra={"host_sink_suspends": {"warn": True, "reason": "observed_suspend"}},
    )
    state = build_normalized_device_state(snap)
    dev = NormalizedRecoveryDevice(
        player_name=state.player_name, state_model=state.to_dict(), health_summary={}, recent_events=[]
    )
    assert [i.key for i in _build_sink_suspend_issues([dev])] == ["bt_sink_suspend_in_gaps"]


# ── air sensor guidance (A13) ────────────────────────────────────────────


def _air_issues(devs):
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_device_issues

    return [i for i in _build_device_issues(devs) if i.key.startswith("air_sensor")]


def test_air_issues_are_registered():
    unavailable = issue_definition("air_sensor_unavailable")
    codec = issue_definition("air_sensor_codec")
    assert unavailable is not None and codec is not None
    assert unavailable.severity == "info" and codec.severity == "info"
    assert unavailable.layer == "bluetooth" and codec.layer == "bluetooth"
    assert set(unavailable.default_reason_codes) == {
        "no_cap_net_raw",
        "unsupported_platform",
        "monitor_error",
        "cpu_budget",
        "unsupported_input",
        "playout_fallback",
        "internal_error",
    }
    assert codec.default_reason_codes == ("non_sbc_codec",)


def test_unsupported_codec_suggests_prefer_sbc():
    dev = _device(air_sensor={"state": "unsupported_codec", "reason": "codec:aac"})
    issue = next(i for i in _air_issues([dev]) if i.key == "air_sensor_codec")
    assert issue.severity == "info" and issue.device_name == "ENEBY20"
    assert issue.reason_codes == ["non_sbc_codec"]
    assert "PREFER_SBC_CODEC" in issue.summary
    # The settings section that holds PREFER_SBC_CODEC (the UI maps this key to "audio").
    assert issue.primary_action is not None and issue.primary_action.key == "open_latency_settings"
    assert issue.primary_action.value == "PREFER_SBC_CODEC"


def test_unreadable_sbc_frames_are_not_told_to_prefer_sbc():
    dev = _device(air_sensor={"state": "unsupported_codec", "reason": "sbc_params"})
    issue = next(i for i in _air_issues([dev]) if i.key == "air_sensor_codec")
    assert issue.reason_codes == ["sbc_params"]
    assert "PREFER_SBC_CODEC" not in issue.summary
    assert issue.primary_action is not None and issue.primary_action.key == "open_diagnostics"


def test_codec_issue_needs_a_connected_speaker():
    dev = _device(air_sensor={"state": "unsupported_codec", "reason": "codec:aac"})
    dev.bluetooth_connected = False
    assert _air_issues([dev]) == []


def test_missing_capability_is_one_bridge_level_issue():
    devs = [
        SimpleNamespace(
            player_name=f"P{n}",
            bluetooth_connected=True,
            has_sink=True,
            server_connected=True,
            health_summary={},
            air_sensor={"state": "unavailable", "reason": "no_cap_net_raw"},
        )
        for n in range(3)
    ]
    issues = [i for i in _air_issues(devs) if i.key == "air_sensor_unavailable"]
    assert len(issues) == 1
    issue = issues[0]
    assert issue.reason_codes == ["no_cap_net_raw"] and issue.severity == "info"
    assert issue.device_name is None and "NET_RAW" in issue.summary
    assert issue.primary_action is not None and issue.primary_action.device_names == ["P0", "P1", "P2"]


def test_mixed_unavailable_reasons_share_the_one_issue():
    a = _device(air_sensor={"state": "unavailable", "reason": "playout_fallback"})
    b = _device(air_sensor={"state": "unsupported_input", "reason": "input:96000/24"})
    b.player_name = "Lenco"
    issues = [i for i in _air_issues([a, b]) if i.key == "air_sensor_unavailable"]
    assert len(issues) == 1 and issues[0].reason_codes == ["unsupported_input", "playout_fallback"]


def test_one_device_unavailable_names_the_device():
    dev = _device(air_sensor={"state": "unavailable", "reason": "cpu_budget"})
    (issue,) = _air_issues([dev])
    assert issue.device_name == "ENEBY20" and issue.reason_codes == ["cpu_budget"]
    assert "ENEBY20" in issue.title


def test_an_unknown_unavailable_reason_reads_as_internal_error():
    dev = _device(air_sensor={"state": "unavailable", "reason": None})
    (issue,) = _air_issues([dev])
    assert issue.reason_codes == ["internal_error"]


def test_switch_off_raises_nothing():
    for dev in (
        _device(),
        _device(air_sensor=None),
        _device(air_sensor={"state": "off", "reason": "needs_measure"}),
        _device(air_sensor="garbage"),
    ):
        assert _air_issues([dev]) == []


def test_working_states_raise_nothing():
    for state in ("locked", "holding", "no_lock", "acquiring", "no_signal"):
        assert _air_issues([_device(air_sensor={"state": state, "reason": None})]) == []


def test_unsupported_input_is_reported():
    dev = _device(air_sensor={"state": "unsupported_input", "reason": "input:96000/24"})
    issues = [i for i in _air_issues([dev]) if i.key == "air_sensor_unavailable"]
    assert issues and issues[0].reason_codes == ["unsupported_input"]


def test_released_speakers_raise_nothing():
    dev = _device(air_sensor={"state": "unavailable", "reason": "no_cap_net_raw"}, bt_management_enabled=False)
    assert _air_issues([dev]) == []


def test_reason_codes_reach_the_issue_dict_only_when_set():
    from sendspin_bridge.services.diagnostics.recovery_assistant import RecoveryIssue

    dev = _device(air_sensor={"state": "unavailable", "reason": "no_cap_net_raw"})
    (issue,) = _air_issues([dev])
    assert issue.to_dict()["reason_codes"] == ["no_cap_net_raw"]
    assert "reason_codes" not in RecoveryIssue(key="k", severity="warning", title="t", summary="s").to_dict()


def test_a_device_snapshot_carries_the_air_state_to_the_producer():
    from sendspin_bridge.services.diagnostics.recovery_assistant import NormalizedRecoveryDevice
    from sendspin_bridge.services.ipc.bridge_state_model import build_normalized_device_state
    from sendspin_bridge.services.lifecycle.status_snapshot import DeviceSnapshot

    snap = DeviceSnapshot(
        player_name="ENEBY20",
        bluetooth_connected=True,
        has_sink=True,
        extra={"air_sensor": {"state": "unsupported_codec", "reason": "codec:aac"}},
    )
    state = build_normalized_device_state(snap)
    dev = NormalizedRecoveryDevice(
        player_name=state.player_name, state_model=state.to_dict(), health_summary={}, recent_events=[]
    )
    assert [i.key for i in _air_issues([dev])] == ["air_sensor_codec"]


def _snapshot_with(issues_devices):
    from sendspin_bridge.services.diagnostics.recovery_assistant import build_recovery_assistant_snapshot

    return build_recovery_assistant_snapshot(
        config={}, devices=issues_devices, onboarding_assistant={}, startup_progress={}
    ).to_dict()


def test_info_issues_alone_do_not_make_the_bridge_look_degraded(monkeypatch):
    from sendspin_bridge.services.diagnostics import recovery_assistant as ra

    monkeypatch.setattr(ra, "_build_duplicate_device_issues", lambda: [])
    monkeypatch.setattr(ra, "_build_config_writable_issue", lambda preflight=None: None)
    monkeypatch.setattr(ra, "_build_config_migration_issue", lambda: None)
    data = _snapshot_with([_device(air_sensor={"state": "unavailable", "reason": "no_cap_net_raw"})])
    assert [i["key"] for i in data["issues"]] == ["air_sensor_unavailable"]
    assert data["summary"]["highest_severity"] == "info"


def test_info_issues_sort_after_warnings(monkeypatch):
    from sendspin_bridge.services.diagnostics import recovery_assistant as ra

    monkeypatch.setattr(ra, "_build_duplicate_device_issues", lambda: [])
    monkeypatch.setattr(ra, "_build_config_writable_issue", lambda preflight=None: None)
    monkeypatch.setattr(ra, "_build_config_migration_issue", lambda: None)
    dev = _device(
        air_sensor={"state": "unavailable", "reason": "no_cap_net_raw"},
        host_sink_suspends={"warn": True, "reason": "observed_suspend"},
    )
    data = _snapshot_with([dev])
    keys = [i["key"] for i in data["issues"]]
    assert keys.index("bt_sink_suspend_in_gaps") < keys.index("air_sensor_unavailable")
    assert data["summary"]["highest_severity"] == "warning"
    assert data["summary"]["headline"] != next(
        i["title"] for i in data["issues"] if i["key"] == "air_sensor_unavailable"
    )


# ── diagnostics bundle (A13) ─────────────────────────────────────────────


class _BundleHub:
    available = False
    unavailable_reason = "no_cap_net_raw"

    def counters(self):
        return {
            "records": 1200,
            "drops": 3,
            "drop_source": "meminfo",
            "clock_steps": 0,
            "cpu_fraction": 0.012,
            "streaming_links": 1,
            "recv_stamped": 0,
            "generation": 2,
        }


@pytest.fixture
def diagnostics_bundle_for(monkeypatch):
    import sendspin_bridge.application.diagnostics as diag_mod
    from sendspin_bridge.services.bluetooth import air_sensor_service, hci_monitor_hub
    from sendspin_bridge.services.bluetooth.device_registry import DeviceRegistrySnapshot

    for name, value in {
        "get_server_name": lambda: "pulseaudio",
        "list_sinks": lambda: [],
        "list_cards": lambda: [],
        "_collect_environment": lambda: {},
        "_collect_subprocess_info": lambda: [],
        "_collect_portaudio_device_diagnostics": lambda: [],
        "_collect_sink_input_diagnostics": lambda: [],
        "_collect_adapter_diagnostics": lambda: [],
        "_collect_bluetooth_daemon_status": lambda: "active",
        "_collect_preflight_status": lambda: {},
        "_collect_sendspin_connection_info": lambda config=None: {},
        "_collect_last_run_summary": lambda: None,
        "_build_onboarding_assistant_payload": lambda **kw: {},
        "_build_recovery_assistant_payload": lambda **kw: {},
        "_build_operator_guidance_payload": lambda **kw: {},
        "load_config": lambda: {},
    }.items():
        monkeypatch.setattr(diag_mod, name, value)

    def build(*, air_sensor=None, hub=None, service=None):
        status = {"bluetooth_connected": True, "server_connected": True}
        if air_sensor is not None:
            status["air_sensor"] = dict(air_sensor, accepts=[1])
        fake_client = SimpleNamespace(
            player_name="ENEBY20",
            status=status,
            bt_management_enabled=True,
            bluetooth_sink_name="bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink",
            bt_manager=SimpleNamespace(mac_address="FC:58:FA:EB:08:6C"),
            player_id="sendspin-eneby",
        )
        monkeypatch.setattr(
            diag_mod, "get_device_registry_snapshot", lambda: DeviceRegistrySnapshot(active_clients=[fake_client])
        )
        monkeypatch.setattr(hci_monitor_hub, "peek_hub", lambda: hub)
        monkeypatch.setattr(air_sensor_service, "get_air_sensor_service", lambda: service)
        return diag_mod.diagnostics()

    return build


def test_bundle_contains_air_snapshot(diagnostics_bundle_for):
    bundle = diagnostics_bundle_for(air_sensor={"state": "locked", "host_placement_ms": 3.0}, hub=_BundleHub())
    air = bundle["devices"][0]["air_sensor"]
    assert air["state"] == "locked" and air["host_placement_ms"] == 3.0
    assert "accepts" not in air  # the IPC gate is not a diagnostic
    assert "hci_monitor" in bundle


def test_bundle_carries_the_hub_counters_and_state(diagnostics_bundle_for):
    service = SimpleNamespace(status=lambda: {"state": "running", "reason": None})
    hub = diagnostics_bundle_for(air_sensor={"state": "unavailable"}, hub=_BundleHub(), service=service)["hci_monitor"]
    assert {
        k: hub[k] for k in ("records", "drops", "drop_source", "clock_steps", "cpu_fraction", "streaming_links")
    } == {
        "records": 1200,
        "drops": 3,
        "drop_source": "meminfo",
        "clock_steps": 0,
        "cpu_fraction": 0.012,
        "streaming_links": 1,
    }
    assert hub["available"] is False and hub["reason"] == "no_cap_net_raw"
    assert hub["air_service"] == {"state": "running", "reason": None}


def test_bundle_without_a_service_says_so(diagnostics_bundle_for):
    hub = diagnostics_bundle_for(hub=_BundleHub())["hci_monitor"]
    assert hub["air_service"] is None


def test_switch_off_bundle_has_null_air_sections(diagnostics_bundle_for):
    bundle = diagnostics_bundle_for()
    assert bundle["devices"][0]["air_sensor"] is None
    assert bundle["hci_monitor"] is None
    assert "hci_monitor" not in bundle["failed_collections"]


def test_a_failing_hub_is_a_failed_collection_not_a_failed_bundle(diagnostics_bundle_for):
    class Broken(_BundleHub):
        def counters(self):
            raise RuntimeError("boom")

    bundle = diagnostics_bundle_for(hub=Broken())
    assert bundle["status"] == "degraded" and "hci_monitor" in bundle["failed_collections"]
    assert bundle["devices"][0]["name"] == "ENEBY20"


def test_bug_report_masking_redacts_macs_in_the_air_sections():
    from sendspin_bridge.application.diagnostics import _mask_obj

    masked = _mask_obj({"devices": [{"air_sensor": {"adapter": "C0:FB:F9:62:D6:9D"}}]})
    assert masked["devices"][0]["air_sensor"]["adapter"] != "C0:FB:F9:62:D6:9D"


def test_the_hub_is_peeked_never_created():
    from sendspin_bridge.services.bluetooth import hci_monitor_hub

    before = hci_monitor_hub._HUB
    try:
        hci_monitor_hub._HUB = None
        assert hci_monitor_hub.peek_hub() is None
        assert hci_monitor_hub._HUB is None
    finally:
        hci_monitor_hub._HUB = before
