"""check_sendspin_connection operator check (#291 follow-up)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from sendspin_bridge.services.diagnostics.operator_check_runner import run_safe_check


def test_malformed_server_returns_error_without_probe():
    """Pre-flight validation runs first — no point in probing a malformed value."""
    with patch(
        "sendspin_bridge.services.diagnostics.sendspin_port_probe.probe_sendspin_port",
        new_callable=AsyncMock,
    ) as probe_mock:
        result = run_safe_check(
            "sendspin_connection",
            config={"SENDSPIN_SERVER": "http://192.168.1.11:8095", "SENDSPIN_PORT": 8927},
        )
    assert result["status"] == "error"
    assert result["check_key"] == "sendspin_connection"
    assert result.get("reason_code") == "config_invalid"
    probe_mock.assert_not_called()


def test_auto_mode_reports_ok_without_probe():
    """Auto-discovery delegates target resolution to the sendspin library."""
    with patch(
        "sendspin_bridge.services.diagnostics.sendspin_port_probe.probe_sendspin_port",
        new_callable=AsyncMock,
    ) as probe_mock:
        result = run_safe_check(
            "sendspin_connection",
            config={"SENDSPIN_SERVER": "auto", "SENDSPIN_PORT": 8927},
        )
    assert result["status"] == "ok"
    assert result.get("auto_discovery") is True
    probe_mock.assert_not_called()


def test_reachable_port_reports_ok():
    with patch(
        "sendspin_bridge.services.diagnostics.sendspin_port_probe.probe_sendspin_port",
        new_callable=AsyncMock,
        return_value=8927,
    ):
        result = run_safe_check(
            "sendspin_connection",
            config={"SENDSPIN_SERVER": "192.168.1.11", "SENDSPIN_PORT": 8927},
        )
    assert result["status"] == "ok"
    assert result["resolved_port"] == 8927
    assert result["reachable"] is True


def test_port_shift_reports_warning_with_actual_port():
    """When the configured port is closed but 8927 responds, surface the mismatch as a warning."""
    with patch(
        "sendspin_bridge.services.diagnostics.sendspin_port_probe.probe_sendspin_port",
        new_callable=AsyncMock,
        return_value=8927,
    ):
        result = run_safe_check(
            "sendspin_connection",
            config={"SENDSPIN_SERVER": "192.168.1.11", "SENDSPIN_PORT": 9000},
        )
    assert result["status"] == "warning"
    assert result["resolved_port"] == 8927
    assert result["configured_port"] == 9000


def test_unreachable_port_reports_error():
    with patch(
        "sendspin_bridge.services.diagnostics.sendspin_port_probe.probe_sendspin_port",
        new_callable=AsyncMock,
        return_value=None,
    ):
        result = run_safe_check(
            "sendspin_connection",
            config={"SENDSPIN_SERVER": "192.168.1.11", "SENDSPIN_PORT": 8927},
        )
    assert result["status"] == "error"
    assert result["reachable"] is False


def test_unknown_check_key_reports_error():
    """The dispatcher must reject unknown keys, including near-matches."""
    result = run_safe_check("sendspin", config={})
    assert result["status"] == "error"
    assert "Unknown" in result["summary"]
