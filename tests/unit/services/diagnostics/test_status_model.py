"""The three named states, derived once, and the verdict that can never contradict the issues."""

from __future__ import annotations

import pytest

from sendspin_bridge.application.models.devices import Device
from sendspin_bridge.services.diagnostics.status_model import (
    audio_link_state,
    bluetooth_connection_state,
    ma_account_state,
    verdict_from_issues,
)

_BT = {
    "player_id": "11111111-2222-3333-4444-555555555555",
    "player_name": "Kitchen",
    "bluetooth_mac": "AA:BB:CC:DD:EE:FF",
    "bluetooth_adapter_hci": "hci0",
    "bluetooth_connected": True,
    "has_sink": True,
}


def _links(**facts):
    return Device.from_status({**_BT, **facts}).links


@pytest.mark.parametrize(
    ("facts", "state", "reason"),
    [
        ({}, "connected", None),
        ({"bluetooth_connected": False}, "disconnected", None),
        ({"bluetooth_connected": False, "reconnecting": True}, "connecting", "reconnecting"),
        ({"bluetooth_connected": False, "bt_standby": True}, "standby", None),
        ({"bt_management_enabled": False, "bt_released_by": "user"}, "released", "user"),
        ({"bt_management_enabled": False, "bt_released_by": "auto"}, "released", "auto"),
        ({"enabled": False}, "disabled", None),
        ({"bluetooth_connected": False, "never_paired": True}, "not_paired", None),
    ],
)
def test_bluetooth_connection_states(facts, state, reason) -> None:
    bt = _links(**facts).bluetooth
    assert bt is not None
    assert (bt.state, bt.reason) == (state, reason)


def test_a_local_audio_output_has_no_bluetooth_connection() -> None:
    links = Device.from_status({"player_id": "x", "backend_type": "pulse_sink", "has_sink": True}).links
    assert links.bluetooth is None


@pytest.mark.parametrize(
    ("facts", "state", "reason"),
    [
        ({"server_connected": True}, "connected", None),
        ({"server_connected": True, "playing": True, "audio_streaming": True}, "streaming", None),
        ({"server_connected": True, "connected_server_own": False}, "foreign", "foreign_server"),
        ({"server_connected": False}, "disconnected", None),
        ({"server_connected": False, "bluetooth_connected": False}, "unavailable", "bluetooth_down"),
        ({"server_connected": False, "bt_standby": True, "bluetooth_connected": False}, "unavailable", "standby"),
        ({"bt_management_enabled": False}, "unavailable", "released"),
        ({"enabled": False}, "unavailable", "disabled"),
    ],
)
def test_audio_link_states(facts, state, reason) -> None:
    link = _links(**facts).audio_link
    assert (link.state, link.reason) == (state, reason)


def test_the_two_per_speaker_states_never_share_a_state_code_meaning() -> None:
    """A connected speaker with no link to Music Assistant: Bluetooth fine, audio link down."""
    links = _links(server_connected=False)
    assert links.bluetooth is not None
    assert links.bluetooth.state == "connected"
    assert links.audio_link.state == "disconnected"


@pytest.mark.parametrize(
    ("configured", "connected", "state"),
    [
        (False, False, "not_configured"),
        (True, False, "signed_out"),
        (True, True, "signed_in"),
        (False, True, "signed_in"),
    ],
)
def test_music_assistant_account_states(configured, connected, state) -> None:
    assert ma_account_state(configured=configured, connected=connected).state == state


def test_state_vocabularies_do_not_overlap_between_account_and_audio_link() -> None:
    """The X1 contradiction: 'Connected' for the account and for the link meant two things."""
    account = {ma_account_state(configured=c, connected=k).state for c in (True, False) for k in (True, False)}
    link = {
        audio_link_state(
            enabled=True, bluetooth_state=b, server_connected=s, streaming=p, foreign=f, bluetooth_reason=None
        ).state
        for b in ("connected", "disconnected")
        for s in (True, False)
        for p in (True, False)
        for f in (True, False)
    }
    assert not account & link


def test_bluetooth_state_function_directly() -> None:
    assert (
        bluetooth_connection_state(
            enabled=True,
            managed=True,
            released_by=None,
            standby=False,
            reconnecting=False,
            connected=True,
            never_paired=False,
        ).state
        == "connected"
    )


class _Issue:
    def __init__(self, severity: str) -> None:
        self.severity = severity


@pytest.mark.parametrize("severity", ["warning", "error"])
def test_the_verdict_is_never_healthy_when_a_warning_or_worse_exists(severity) -> None:
    verdict = verdict_from_issues([_Issue("info"), _Issue(severity)])
    assert verdict.level != "ok"
    assert verdict.level == ("error" if severity == "error" else "warning")
    assert verdict.count == 1


def test_the_verdict_is_ok_without_issues_and_for_info_only() -> None:
    assert verdict_from_issues([]).level == "ok"
    assert verdict_from_issues([_Issue("info")]).level == "ok"


def test_a_failed_collection_alone_degrades_the_verdict() -> None:
    assert verdict_from_issues([], failed_collections=["telemetry"]).level == "warning"


def test_diagnostics_status_follows_the_guidance_issue_list(monkeypatch) -> None:
    from sendspin_bridge.application import diagnostics as diag_mod

    payload = {"issue_groups": [{"severity": "warning", "key": "disconnected"}]}
    assert diag_mod._status_from_guidance(payload, []) == "degraded"
    assert diag_mod._status_from_guidance({"issue_groups": [{"severity": "error"}]}, []) == "error"
    assert diag_mod._status_from_guidance({"issue_groups": []}, []) == "ok"
    assert diag_mod._status_from_guidance({"issue_groups": []}, ["x"]) == "degraded"
    assert diag_mod._status_from_guidance({"error": "boom"}, []) == "ok"


def test_diagnostics_verdict_and_header_pill_share_one_issue_list() -> None:
    """Real guidance with disconnected speakers: pill is not healthy, so neither is the verdict."""
    from types import SimpleNamespace

    from sendspin_bridge.application import diagnostics as diag_mod
    from sendspin_bridge.services.diagnostics.operator_guidance import build_operator_guidance_snapshot
    from tests.support.config_v6 import v6_players

    device = SimpleNamespace(
        player_name="Kitchen",
        bt_management_enabled=True,
        bluetooth_connected=False,
        has_sink=False,
        server_connected=False,
    )
    data = build_operator_guidance_snapshot(
        config={"BLUETOOTH_ADAPTERS": [{"id": "hci0"}], **v6_players([{"mac": "AA"}])},
        onboarding_assistant={
            "checklist": {"overall_status": "ok", "progress_percent": 100},
            "counts": {"configured_devices": 1, "connected_devices": 0, "sink_ready_devices": 0},
        },
        recovery_assistant={"summary": {"summary": "x"}},
        startup_progress={"status": "complete"},
        devices=[device],
    ).to_dict()

    assert data["header_status"]["tone"] in {"warning", "error"}
    assert diag_mod._status_from_guidance(data, []) != "ok"
    assert verdict_from_issues(data["issue_groups"]).level == data["header_status"]["tone"]
