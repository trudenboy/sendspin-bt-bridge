"""Helpers for aligning log severity with operational impact."""

from __future__ import annotations

import re
from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

_LOG_LEVEL_RE = re.compile(r"\s-\s(DEBUG|INFO|WARNING|ERROR|CRITICAL)\s-\s", re.IGNORECASE)
_ERROR_WORD_RE = re.compile(r"\b(?:\w+error|\w+exception)\b", re.IGNORECASE)

_STDERR_CRITICAL_MARKERS = (
    "critical",
    "fatal",
    "panic",
    "unrecoverable",
)

_STDERR_ERROR_MARKERS = (
    "traceback",
    "failed",
    "unexpected",
    "crash",
    "aborted",
)

_ACTIONABLE_WARNING_MARKERS = (
    "daemon stderr:",
    "failed",
    "could not",
    "timed out",
    "timeout",
    "not available",
    "unavailable",
    "rejected",
    "invalid",
)

_NON_ISSUE_WARNING_MARKERS = ("failed to connect (not connected after",)

_ISSUE_LEVEL_RANK = {
    "warning": 1,
    "error": 2,
    "critical": 3,
}


def extract_log_level(line: str) -> str | None:
    """Return the structured Python logging level embedded in a service log line."""
    match = _LOG_LEVEL_RE.search(line or "")
    if not match:
        return None
    return match.group(1).upper()


def classify_subprocess_stderr_level(line: str) -> str:
    """Infer severity for raw daemon stderr text that lacks an explicit log level."""
    text = (line or "").strip()
    if not text:
        return "warning"
    lower = text.lower()
    if any(marker in lower for marker in _STDERR_CRITICAL_MARKERS):
        return "critical"
    if _ERROR_WORD_RE.search(text) or any(marker in lower for marker in _STDERR_ERROR_MARKERS):
        return "error"
    return "warning"


def is_actionable_warning_log_line(line: str) -> bool:
    """Return True for warnings that are worth surfacing as report-worthy issues."""
    if extract_log_level(line) != "WARNING":
        return False
    lower = (line or "").lower()
    if any(marker in lower for marker in _NON_ISSUE_WARNING_MARKERS):
        return False
    return any(marker in lower for marker in _ACTIONABLE_WARNING_MARKERS)


def issue_level_for_log_line(line: str) -> str | None:
    """Return warning/error/critical for issue-worthy lines, or None otherwise."""
    level = extract_log_level(line)
    if level == "CRITICAL":
        return "critical"
    if level == "ERROR":
        return "error"
    if level == "WARNING":
        return "warning" if is_actionable_warning_log_line(line) else None
    if level is not None:
        return None
    inferred = classify_subprocess_stderr_level(line)
    return inferred if inferred in _ISSUE_LEVEL_RANK else None


def is_issue_worthy_log_line(line: str) -> bool:
    """Return True for lines that should influence bugreport/report indicators."""
    return issue_level_for_log_line(line) is not None


def summarize_issue_logs(
    lines: Iterable[str],
    *,
    tail_lines: int | None = None,
    max_lines: int | None = None,
) -> dict[str, object]:
    """Summarize issue-worthy lines from a log sequence."""
    source = deque(lines, maxlen=tail_lines) if tail_lines else list(lines)
    issue_lines = [line for line in source if is_issue_worthy_log_line(line)]
    highest_level = None
    for line in issue_lines:
        level = issue_level_for_log_line(line)
        if level is None:
            continue
        if highest_level is None or _ISSUE_LEVEL_RANK[level] > _ISSUE_LEVEL_RANK[highest_level]:
            highest_level = level
    display_lines = issue_lines[-max_lines:] if max_lines is not None else issue_lines
    return {
        "has_issues": bool(issue_lines),
        "issue_count": len(issue_lines),
        "highest_level": highest_level,
        "issue_lines": display_lines,
    }
