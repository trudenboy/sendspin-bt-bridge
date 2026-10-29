"""Every user-facing text the bridge writes carries a stable code the web UI can translate.

The English ``title`` / ``summary`` / ``label`` stay as the fallback; these
tests pin the codes, their params, and that title and summary of one item are
produced from the same state (an "is disconnected" card cannot say "Connected
and ready").
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from sendspin_bridge.services.bluetooth.device_health_state import (
    build_device_capabilities,
    compute_device_health_state,
)
from sendspin_bridge.services.diagnostics import server_text
from sendspin_bridge.services.diagnostics.onboarding_assistant import build_onboarding_assistant_snapshot
from sendspin_bridge.services.diagnostics.operator_guidance import build_operator_guidance_snapshot
from sendspin_bridge.services.diagnostics.recovery_assistant import build_recovery_assistant_snapshot
from sendspin_bridge.services.diagnostics.recovery_timeline import build_recovery_timeline
from sendspin_bridge.services.diagnostics.server_text import (
    SERVER_TEXT,
    action_code,
    action_fields,
    event_fields,
    has_code,
    has_text,
    registry_keys,
)
from tests.support.config_v6 import v6_players

ROOT = Path(__file__).resolve().parents[4]
SRC = ROOT / "src" / "sendspin_bridge"


@pytest.fixture(autouse=True)
def _config_writable(monkeypatch):
    import sendspin_bridge.services.diagnostics.recovery_assistant as module

    monkeypatch.setattr(module, "collect_preflight_status", lambda: {"config_writable": {"status": "ok"}})


def _device(name="Kitchen", *, connected=True, sink=True, daemon=True, health=None, paired=None, **extra):
    bluetooth = {"connected": connected, "paired": paired, "reconnect_attempt": extra.pop("attempt", 0), **extra}
    return SimpleNamespace(
        player_name=name,
        enabled=True,
        bt_management_enabled=extra.pop("managed", True),
        bluetooth_connected=connected,
        has_sink=sink,
        server_connected=daemon,
        static_delay_ms=0.0,
        recent_events=[],
        health_summary=health or {"state": "ready", "severity": "info", "summary": "Connected and ready"},
        extra={"bluetooth_connected": connected, "bluetooth_paired": paired, **extra},
        state_model={
            "bluetooth": bluetooth,
            "management": {},
            "audio": {"has_sink": sink},
            "transport": {"daemon_connected": daemon},
        },
    )


def _snapshots(devices, preflight=None, config=None):
    config = config or {
        "BLUETOOTH_ADAPTERS": [{"id": "hci0"}],
        **v6_players([{"mac": f"AA:{i}"} for i in range(len(devices))]),
    }
    preflight = preflight or {
        "audio": {"system": "pulseaudio", "sinks": 1},
        "bluetooth": {"controller": True, "paired_devices": 1},
    }
    onboarding = build_onboarding_assistant_snapshot(
        config=config, preflight=preflight, devices=devices, ma_connected=True, runtime_mode="production"
    ).to_dict()
    recovery = build_recovery_assistant_snapshot(
        config=config, devices=devices, onboarding_assistant=onboarding, startup_progress={"status": "ready"}
    ).to_dict()
    guidance = build_operator_guidance_snapshot(
        config=config,
        onboarding_assistant=onboarding,
        recovery_assistant=recovery,
        startup_progress={"status": "ready", "completed_at": "2020-01-01T00:00:00+00:00"},
        devices=devices,
    ).to_dict()
    return onboarding, recovery, guidance


SCENARIOS: dict[str, Any] = {
    "disconnected": lambda: ([_device(connected=False)], None),
    "disconnected_attempts": lambda: ([_device(connected=False, attempt=2, max_reconnect_fails=5)], None),
    "never_paired": lambda: ([_device(connected=False, never_paired=True)], None),
    "repair": lambda: ([_device(connected=False, paired=False)], None),
    "missing_sink": lambda: ([_device(sink=False)], None),
    "transport_down": lambda: ([_device(daemon=False)], None),
    "two_disconnected": lambda: ([_device("A", connected=False), _device("B", connected=False)], None),
    "auto_released": lambda: ([_device(managed=False, bt_released_by="auto")], None),
    "with_error": lambda: (
        [
            _device(
                connected=False,
                health={"state": "degraded", "severity": "error", "summary": "boom", "reasons": ["last_error"]},
            )
        ],
        None,
    ),
    "audio_refused": lambda: (
        [],
        {
            "audio": {
                "system": "unreachable",
                "sinks": 0,
                "socket": "unix:/run/pulse",
                "socket_exists": True,
                "socket_reachable": False,
                "last_error": "Connection refused",
            },
            "bluetooth": {"controller": True, "paired_devices": 0},
        },
    ),
    "audio_unreachable": lambda: (
        [],
        {
            "audio": {"system": "unreachable", "sinks": 0, "socket": "unix:/run/pulse", "last_error": "denied"},
            "bluetooth": {"controller": True, "paired_devices": 0},
        },
    ),
    "audio_missing": lambda: (
        [],
        {
            "audio": {"system": "unknown", "sinks": 0, "socket": "unix:/run/pulse", "socket_exists": False},
            "bluetooth": {"controller": True, "paired_devices": 0},
        },
    ),
    "audio_none": lambda: (
        [],
        {"audio": {"system": "unknown", "sinks": 0}, "bluetooth": {"controller": True, "paired_devices": 0}},
    ),
    "no_controller": lambda: (
        [_device()],
        {"audio": {"system": "pulseaudio", "sinks": 1}, "bluetooth": {"controller": False}},
    ),
    "daemon_down": lambda: (
        [_device()],
        {"audio": {"system": "pulseaudio", "sinks": 1}, "bluetooth": {"controller": False, "daemon": "inactive"}},
    ),
    "no_dbus": lambda: (
        [_device()],
        {
            "dbus": False,
            "audio": {"system": "pulseaudio", "sinks": 1},
            "bluetooth": {"controller": True, "paired_devices": 1},
        },
    ),
    "no_paired": lambda: (
        [],
        {"audio": {"system": "pulseaudio", "sinks": 1}, "bluetooth": {"controller": True, "paired_devices": 0}},
    ),
}


def _scenario(name):
    devices, preflight = SCENARIOS[name]()
    config = None
    if name == "no_paired":
        config = {"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], **v6_players([{"mac": "AA:1"}])}
    return _snapshots(devices, preflight, config)


def _walk_actions(item: dict[str, Any]):
    for key in ("primary_action", "recommended_action"):
        if isinstance(item.get(key), dict):
            yield item[key]
    yield from item.get("secondary_actions") or []


def _assert_issue(item: dict[str, Any], where: str) -> None:
    code = item.get("code")
    assert code, f"{where}: no code on {item.get('key')}"
    assert has_text("issue", code, "title") or code.startswith("setup_"), f"{where}: unknown issue code {code}"
    assert has_text("issue", code, "title"), f"{where}: no title for {code}"
    variant = item.get("summary_variant")
    leaf = f"summary_{variant}" if variant else "summary"
    assert has_text("issue", code, leaf) or (code.startswith("setup_") and not variant), (
        f"{where}: {code} has no {leaf}"
    )
    params = item.get("params")
    assert isinstance(params, dict)
    assert all(isinstance(value, (str, int, float, bool, list)) for value in params.values())
    for action in _walk_actions(item):
        assert has_code("action", action["code"]), f"{where}: unknown action code {action['code']} ({action['label']})"
        assert isinstance(action["params"].get("count"), int)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_every_issue_header_and_action_has_a_registered_code(name):
    _onboarding, recovery, guidance = _scenario(name)

    assert recovery["issues"] or guidance["issue_groups"] or name in {"no_paired"}, name
    for issue in recovery["issues"]:
        _assert_issue(issue, f"{name}/recovery")
    for group in guidance["issue_groups"]:
        _assert_issue(group, f"{name}/guidance")
    header = guidance["header_status"]
    assert header["code"], name
    assert has_code(header["area"], header["code"]), f"{name}: header {header['area']}.{header['code']}"
    if header["area"] == "issue":
        assert header["code"] == guidance["issue_groups"][0]["code"]


def test_the_same_condition_has_the_same_code_in_guidance_and_recovery():
    _o, recovery, guidance = _scenario("disconnected")

    assert {i["code"] for i in recovery["issues"]} >= {"disconnected"}
    assert guidance["issue_groups"][0]["code"] == "disconnected"
    assert guidance["issue_groups"][0]["params"]["device"] == "Kitchen"
    assert guidance["issue_groups"][0]["params"]["count"] == 1


def test_a_group_of_speakers_carries_the_count_and_every_name():
    _o, _r, guidance = _scenario("two_disconnected")

    group = guidance["issue_groups"][0]
    assert group["code"] == "disconnected"
    assert group["params"]["count"] == 2
    assert group["params"]["device_names"] == ["A", "B"]


def test_the_reconnect_attempt_is_a_number_not_a_sentence():
    _o, _r, guidance = _scenario("disconnected_attempts")

    assert guidance["issue_groups"][0]["params"]["attempt"] == 2
    assert guidance["issue_groups"][0]["params"]["max_attempts"] == 5


def test_a_card_never_contradicts_itself_with_a_health_verdict_about_another_state():
    """The audit's X1: "is disconnected" over "Connected and ready"."""
    _o, recovery, _g = _scenario("disconnected")

    card = next(i for i in recovery["issues"] if i["code"] == "disconnected")
    assert "is disconnected" in card["title"]
    assert "Connected and ready" not in card["summary"]
    assert card.get("summary_variant") is None


def test_a_raw_error_is_a_value_and_picks_the_error_wording():
    _o, recovery, _g = _scenario("with_error")

    card = next(i for i in recovery["issues"] if i["code"] == "disconnected")
    assert card["summary_variant"] == "error"
    assert card["params"]["error"] == "boom"
    assert card["summary"].startswith("boom")


@pytest.mark.parametrize(
    ("name", "variant"),
    [
        ("audio_unreachable", "socket_unreachable"),
        ("audio_none", "no_server_env"),
        ("no_controller", None),
        ("daemon_down", "daemon_down"),
        ("no_dbus", None),
    ],
)
def test_a_check_derived_summary_names_its_wording(name, variant):
    onboarding, recovery, guidance = _scenario(name)

    step_code = onboarding["checklist"]["current_step_code"]
    assert step_code and step_code.startswith("setup_")
    setup = next(i for i in recovery["issues"] if i["code"] == step_code)
    assert setup["summary_variant"] == onboarding["checklist"]["current_step_variant"]
    assert has_text("issue", step_code, f"summary_{setup['summary_variant']}")
    if variant is not None:
        assert guidance["issue_groups"][0]["summary_variant"] in {variant, "all_disabled"}


@pytest.mark.parametrize(
    ("name", "code"), [("audio_refused", "pa_socket_refused"), ("audio_missing", "pa_socket_missing")]
)
def test_a_headless_audio_socket_has_its_own_group_code_with_the_socket_as_a_value(name, code):
    _o, _r, guidance = _scenario(name)

    group = guidance["issue_groups"][0]
    assert group["code"] == code
    assert group["params"]["socket"] == "unix:/run/pulse"


def test_the_pairing_variant_of_the_bluetooth_step_has_its_own_title_code():
    onboarding, recovery, _g = _scenario("no_paired")

    assert onboarding["checklist"]["current_step_code"] == "setup_bluetooth_pair"
    assert any(i["code"] == "setup_bluetooth_pair" for i in recovery["issues"])


# ------------------------------------------------------------------ actions


def test_actions_with_one_key_and_several_labels_get_distinct_codes():
    assert action_code("toggle_bt_management", "Release Bluetooth") == "release_bluetooth"
    assert action_code("toggle_bt_management", "Reclaim Bluetooth") == "reclaim_bluetooth"
    assert action_code("toggle_bt_management_devices", "Reclaim 3 devices") == "reclaim_bluetooth"
    assert action_code("reconnect_devices", "Reconnect 4 devices") == "reconnect_device"
    assert action_code("brand_new_action", "Whatever") == "brand_new_action"


def test_an_action_carries_its_count_and_value():
    fields = action_fields("apply_latency_recommended", "Apply 600 ms latency", [], 600)

    assert fields == {"code": "apply_latency_recommended", "params": {"count": 1, "value": 600}}
    assert action_fields("reconnect_devices", "Reconnect 2 devices", ["A", "B"])["params"]["count"] == 2


def test_every_action_label_written_in_the_backend_has_a_code():
    """A new label for an existing key must be registered, or the UI shows the key's wording."""
    pattern = re.compile(
        r"(?:GuidanceAction|RecoveryAction|OnboardingChecklistAction)\(\s*(?:key=)?\"([a-z_]+)\",?\s*(?:label=)?(f?)\"([^\"]+)\""
        r"|_recovery_action\(\s*\"([a-z_]+)\",\s*(f?)\"([^\"]+)\""
    )
    missing = []
    for path in (SRC / "services" / "diagnostics").glob("*.py"):
        for match in pattern.finditer(path.read_text()):
            key = match.group(1) or match.group(4)
            label = match.group(3) or match.group(6)
            label = re.sub(r"\{[^}]*\}", "{n}", label)
            if (key, label) not in server_text._ACTION_CODES and action_code(key, label) == key:
                if not has_code("action", key):
                    missing.append((path.name, key, label))
    assert missing == []


# ------------------------------------------------------------------- events


def test_a_device_event_has_its_type_as_code_and_its_details_as_params():
    event = {
        "event_type": "bluetooth-reconnected",
        "level": "info",
        "message": "Bluetooth reconnect succeeded",
        "details": {"attempt": 2, "nested": {"x": 1}},
    }

    fields = event_fields(event)

    assert fields == {"code": "bluetooth-reconnected", "params": {"attempt": 2}}


def test_a_runtime_error_event_carries_the_error_text_as_a_value():
    fields = event_fields({"event_type": "runtime-error", "message": "Route degraded", "details": {}})

    assert fields["params"]["error"] == "Route degraded"


def test_every_timeline_entry_has_a_registered_code_and_source():
    device = _device(
        connected=False,
        health={
            "state": "offline",
            "severity": "warning",
            "summary": "Bluetooth speaker disconnected",
            "code": "bluetooth_disconnected",
        },
    )
    noisy = _device("Other")
    noisy.recent_events = [
        {
            "at": "2026-01-01T00:00:00+00:00",
            "event_type": "playback-started",
            "level": "info",
            "message": "Playback started",
            "details": {},
        },
        {
            "at": "2026-01-01T00:00:01+00:00",
            "event_type": "runtime-error",
            "level": "error",
            "message": "x",
            "details": {},
        },
        {
            "at": "2026-01-01T00:00:02+00:00",
            "event_type": "runtime-error-cleared",
            "level": "info",
            "message": "Runtime error cleared",
            "details": {},
        },
    ]

    timeline = build_recovery_timeline(
        [device, noisy],
        {"status": "ready", "phase": "ready", "message": "Startup complete", "updated_at": "2026-01-01T00:00:00+00:00"},
    )

    codes = {entry["code"] for entry in timeline["entries"]}
    assert {"startup-ready", "playback-started", "health-bluetooth_disconnected", "runtime-error"} <= codes
    for entry in timeline["entries"]:
        assert has_text("event", entry["code"], "title"), entry
        assert has_text("event", entry["code"], "summary"), entry
    startup = next(e for e in timeline["entries"] if e["code"] == "startup-ready")
    assert startup["source_code"] == "bridge_startup"
    recovered = next(e for e in timeline["entries"] if e["code"] == "runtime-error")
    assert recovered["params"]["recovered"] is True


def test_a_failed_startup_entry_carries_the_message():
    timeline = build_recovery_timeline([], {"status": "error", "phase": "web", "message": "port busy"})

    entry = timeline["entries"][0]
    assert entry["code"] == "startup-failed"
    assert entry["params"] == {"message": "port busy"}


def test_every_device_event_type_has_event_text():
    from sendspin_bridge.services.diagnostics.internal_events import DeviceEventType

    for event_type in DeviceEventType:
        assert has_text("event", event_type.value, "title"), event_type
        assert has_text("event", event_type.value, "summary"), event_type


# -------------------------------------------------------------------- health


def test_a_health_summary_has_a_stable_code():
    device = SimpleNamespace(
        player_name="K",
        bt_management_enabled=True,
        bluetooth_connected=False,
        has_sink=False,
        server_connected=False,
        recent_events=[],
        extra={},
    )

    health = compute_device_health_state(device)

    assert health.code == "bluetooth_disconnected"
    assert health.to_dict()["code"] == "bluetooth_disconnected"


def test_every_health_code_the_backend_writes_has_event_text():
    source = (SRC / "services" / "bluetooth" / "device_health_state.py").read_text()
    codes = set(re.findall(r'^\s+code="([a-z_]+)",$', source, re.M)) - {"released_not_health"}
    block_codes = set(re.findall(r'code="([a-z_]+)"', source[source.index("def _block_reason_payload") :]))
    health_codes = {c for c in codes if c not in block_codes} | {"error"}
    for code in health_codes - {"streaming", "ready"}:
        assert has_text("event", f"health-{code}", "title") or has_code("blocked", code), code


# ------------------------------------------------------------------- blocked


def test_every_blocked_reason_code_has_text():
    source = (SRC / "services" / "bluetooth" / "device_health_state.py").read_text()
    codes = set(re.findall(r'code="([a-z_]+)"', source[source.index("def build_device_capabilities") :]))
    codes |= {"no_sink", "released"}
    for code in codes:
        assert has_code("blocked", code), code


def test_a_blocked_capability_exposes_its_code():
    device = _device(managed=False)

    capabilities = build_device_capabilities(device)

    detail = capabilities["actions"]["reconnect"]["blocked_reason_detail"]
    assert detail["code"] == "released"


# ------------------------------------------------------------------ registry


def test_the_ui_fixture_lists_exactly_the_registry():
    """Regenerate with ``uv run python scripts/export_server_text_keys.py``."""
    fixture = json.loads((ROOT / "ui" / "tests" / "fixtures" / "server-text-keys.json").read_text())

    assert fixture == registry_keys()


def test_the_registry_covers_every_issue_the_guidance_registry_knows():
    from sendspin_bridge.services.diagnostics.guidance_issue_registry import ISSUE_REGISTRY

    # Registry keys that only order issues (no card is ever written under them).
    sort_only = {
        "bluetooth",
        "audio",
        "device_disconnected",
        "playback_degraded",
        "daemon_disconnected",
        "device_released",
        "all_disabled",
    }
    for key in sorted(ISSUE_REGISTRY):
        assert has_code("issue", key) or key in sort_only, (
            f"{key}: add text codes in server_text or list it as sort-only"
        )


def test_every_area_code_has_at_least_one_leaf():
    for area, codes in SERVER_TEXT.items():
        for code, leaves in codes.items():
            assert leaves, (area, code)
