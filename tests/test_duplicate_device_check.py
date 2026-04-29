"""Tests for cross-bridge duplicate device detection."""

from __future__ import annotations

from unittest.mock import patch

from sendspin_bridge.services.bluetooth.duplicate_device_check import (
    DuplicateDeviceWarning,
    _is_own_bridge_player,
    find_duplicate_devices,
    find_scan_device_conflicts,
)

# ---------------------------------------------------------------------------
# _is_own_bridge_player
# ---------------------------------------------------------------------------


def test_is_own_bridge_player_matching_suffix():
    assert _is_own_bridge_player("ENEBY20 @ HAOS", "HAOS") is True


def test_is_own_bridge_player_no_match():
    assert _is_own_bridge_player("ENEBY20 @ Other Bridge", "HAOS") is False


def test_is_own_bridge_player_empty_bridge_name():
    assert _is_own_bridge_player("ENEBY20 @ HAOS", "") is False


def test_is_own_bridge_player_trailing_space():
    assert _is_own_bridge_player("ENEBY20 @ HAOS ", "HAOS") is True


# ---------------------------------------------------------------------------
# find_duplicate_devices
# ---------------------------------------------------------------------------


def _make_config(
    devices: list[dict] | None = None,
    ma_url: str = "http://ma:8095",
    ma_token: str = "tok",
    bridge_name: str = "HAOS",
    check: bool = True,
) -> dict:
    return {
        "BLUETOOTH_DEVICES": devices or [],
        "MA_API_URL": ma_url,
        "MA_API_TOKEN": ma_token,
        "BRIDGE_NAME": bridge_name,
        "DUPLICATE_DEVICE_CHECK": check,
    }


@patch("sendspin_bridge.services.music_assistant.ma_client.fetch_all_players_snapshot")
def test_find_duplicate_devices_detects_conflict(mock_fetch):
    from config import _player_id_from_mac

    mac = "AA:BB:CC:DD:EE:FF"
    pid = _player_id_from_mac(mac)
    mock_fetch.return_value = [{"player_id": pid, "display_name": "ENEBY20 @ Other Bridge"}]

    cfg = _make_config(devices=[{"mac": mac, "name": "ENEBY20"}])
    warnings = find_duplicate_devices(cfg, "HAOS")

    assert len(warnings) == 1
    assert warnings[0].mac == mac
    assert warnings[0].other_bridge_name == "ENEBY20 @ Other Bridge"
    assert warnings[0].device_name == "ENEBY20"


@patch("sendspin_bridge.services.music_assistant.ma_client.fetch_all_players_snapshot")
def test_find_duplicate_devices_no_conflict_own_bridge(mock_fetch):
    from config import _player_id_from_mac

    mac = "AA:BB:CC:DD:EE:FF"
    pid = _player_id_from_mac(mac)
    mock_fetch.return_value = [{"player_id": pid, "display_name": "ENEBY20 @ HAOS"}]

    cfg = _make_config(devices=[{"mac": mac, "name": "ENEBY20"}])
    warnings = find_duplicate_devices(cfg, "HAOS")

    assert warnings == []


@patch("sendspin_bridge.services.music_assistant.ma_client.fetch_all_players_snapshot")
def test_find_duplicate_devices_no_conflict_unknown_player(mock_fetch):
    mock_fetch.return_value = [{"player_id": "unknown-id", "display_name": "Kitchen"}]

    cfg = _make_config(devices=[{"mac": "AA:BB:CC:DD:EE:FF", "name": "ENEBY20"}])
    warnings = find_duplicate_devices(cfg, "HAOS")

    assert warnings == []


def test_find_duplicate_devices_disabled_by_config():
    cfg = _make_config(devices=[{"mac": "AA:BB:CC:DD:EE:FF"}], check=False)
    warnings = find_duplicate_devices(cfg, "HAOS")
    assert warnings == []


def test_find_duplicate_devices_no_ma_credentials():
    cfg = _make_config(devices=[{"mac": "AA:BB:CC:DD:EE:FF"}], ma_url="", ma_token="")
    warnings = find_duplicate_devices(cfg, "HAOS")
    assert warnings == []


def test_find_duplicate_devices_no_devices():
    cfg = _make_config(devices=[])
    warnings = find_duplicate_devices(cfg, "HAOS")
    assert warnings == []


@patch(
    "sendspin_bridge.services.music_assistant.ma_client.fetch_all_players_snapshot",
    side_effect=ConnectionError("no MA"),
)
def test_find_duplicate_devices_api_failure(mock_fetch):
    cfg = _make_config(devices=[{"mac": "AA:BB:CC:DD:EE:FF", "name": "Speaker"}])
    warnings = find_duplicate_devices(cfg, "HAOS")
    assert warnings == []


# ---------------------------------------------------------------------------
# find_scan_device_conflicts
# ---------------------------------------------------------------------------


@patch("sendspin_bridge.services.music_assistant.ma_client.fetch_all_players_snapshot")
def test_find_scan_device_conflicts_detects_conflict(mock_fetch):
    from config import _player_id_from_mac

    mac = "AA:BB:CC:DD:EE:FF"
    pid = _player_id_from_mac(mac)
    mock_fetch.return_value = [{"player_id": pid, "display_name": "ENEBY20 @ Other Bridge"}]

    conflicts = find_scan_device_conflicts([mac], "http://ma:8095", "tok", "HAOS")

    assert mac in conflicts
    assert "Other Bridge" in conflicts[mac]


@patch("sendspin_bridge.services.music_assistant.ma_client.fetch_all_players_snapshot")
def test_find_scan_device_conflicts_no_conflict(mock_fetch):
    mock_fetch.return_value = []

    conflicts = find_scan_device_conflicts(["AA:BB:CC:DD:EE:FF"], "http://ma:8095", "tok", "HAOS")

    assert conflicts == {}


def test_find_scan_device_conflicts_no_credentials():
    conflicts = find_scan_device_conflicts(["AA:BB:CC:DD:EE:FF"], "", "", "HAOS")
    assert conflicts == {}


@patch("sendspin_bridge.services.music_assistant.ma_client.fetch_all_players_snapshot", side_effect=Exception("fail"))
def test_find_scan_device_conflicts_api_failure(mock_fetch):
    conflicts = find_scan_device_conflicts(["AA:BB:CC:DD:EE:FF"], "http://ma:8095", "tok", "HAOS")
    assert conflicts == {}


# ---------------------------------------------------------------------------
# State storage
# ---------------------------------------------------------------------------


def test_state_storage_roundtrip():
    from sendspin_bridge.services.music_assistant.ma_runtime_state import (
        get_duplicate_device_warnings,
        set_duplicate_device_warnings,
    )

    w = DuplicateDeviceWarning(
        mac="AA:BB:CC:DD:EE:FF", device_name="Speaker", other_bridge_name="Other", player_id="p1"
    )
    set_duplicate_device_warnings([w])
    result = get_duplicate_device_warnings()
    assert len(result) == 1
    assert result[0].mac == "AA:BB:CC:DD:EE:FF"

    set_duplicate_device_warnings([])
    assert get_duplicate_device_warnings() == []


# ---------------------------------------------------------------------------
# RecoveryIssue integration
# ---------------------------------------------------------------------------


@patch("sendspin_bridge.services.music_assistant.ma_runtime_state.get_duplicate_device_warnings")
def test_recovery_issue_for_duplicate_device(mock_get):
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_duplicate_device_issues

    mock_get.return_value = [
        DuplicateDeviceWarning(
            mac="AA:BB:CC:DD:EE:FF",
            device_name="ENEBY20",
            other_bridge_name="ENEBY20 @ RC Bridge",
            player_id="p1",
        )
    ]

    issues = _build_duplicate_device_issues()
    assert len(issues) == 1
    assert issues[0].key == "duplicate_device"
    assert issues[0].severity == "warning"
    assert "ENEBY20" in issues[0].title
    assert "RC Bridge" in issues[0].summary
    assert issues[0].primary_action is not None


@patch("sendspin_bridge.services.music_assistant.ma_runtime_state.get_duplicate_device_warnings")
def test_recovery_issue_empty_when_no_warnings(mock_get):
    from sendspin_bridge.services.diagnostics.recovery_assistant import _build_duplicate_device_issues

    mock_get.return_value = []
    issues = _build_duplicate_device_issues()
    assert issues == []


# ---------------------------------------------------------------------------
# Guidance issue registry
# ---------------------------------------------------------------------------


def test_guidance_registry_has_duplicate_device():
    from sendspin_bridge.services.diagnostics.guidance_issue_registry import ISSUE_REGISTRY

    defn = ISSUE_REGISTRY.get("duplicate_device")
    assert defn is not None
    assert defn.severity == "warning"
    assert defn.layer == "bridge_control"
    assert defn.priority == 35


# ---------------------------------------------------------------------------
# Scan result annotation
# ---------------------------------------------------------------------------


@patch("routes.api_bt.load_config")
@patch("sendspin_bridge.services.bluetooth.duplicate_device_check.find_scan_device_conflicts")
def test_annotate_scan_conflicts_adds_warning(mock_conflicts, mock_load):
    from routes.api_bt import _annotate_scan_conflicts

    mock_load.return_value = {
        "DUPLICATE_DEVICE_CHECK": True,
        "MA_API_URL": "http://ma",
        "MA_API_TOKEN": "tok",
        "BRIDGE_NAME": "HAOS",
    }
    mock_conflicts.return_value = {"AA:BB:CC:DD:EE:FF": "Already on another bridge"}

    devices = [{"mac": "AA:BB:CC:DD:EE:FF"}, {"mac": "11:22:33:44:55:66"}]
    _annotate_scan_conflicts(devices)

    assert devices[0].get("warning") == "Already on another bridge"
    assert "warning" not in devices[1]


@patch("routes.api_bt.load_config")
def test_annotate_scan_conflicts_disabled(mock_load):
    from routes.api_bt import _annotate_scan_conflicts

    mock_load.return_value = {"DUPLICATE_DEVICE_CHECK": False}

    devices = [{"mac": "AA:BB:CC:DD:EE:FF"}]
    _annotate_scan_conflicts(devices)

    assert "warning" not in devices[0]


@patch("routes.api_bt.load_config", side_effect=Exception("boom"))
def test_annotate_scan_conflicts_exception_safe(mock_load):
    from routes.api_bt import _annotate_scan_conflicts

    devices = [{"mac": "AA:BB:CC:DD:EE:FF"}]
    _annotate_scan_conflicts(devices)
    assert "warning" not in devices[0]
