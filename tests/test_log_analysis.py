from services.log_analysis import is_issue_worthy_log_line, summarize_issue_logs


def test_temporary_bt_connect_failure_warning_is_not_issue_worthy():
    line = "2026-03-17 23:40:44,856 - bluetooth_manager - WARNING - Failed to connect (not connected after 5 status checks)"

    assert is_issue_worthy_log_line(line) is False


def test_auto_disable_reconnect_warning_remains_issue_worthy():
    line = (
        "2026-03-17 23:42:00,000 - bluetooth_manager - WARNING - "
        "[Lenco LS-500 @ HAOS] 10 consecutive failed reconnects (threshold=10) — auto-disabling BT management"
    )

    summary = summarize_issue_logs([line])

    assert summary["has_issues"] is True
    assert summary["issue_count"] == 1
    assert summary["highest_level"] == "warning"
