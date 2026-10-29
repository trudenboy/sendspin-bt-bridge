"""The three states an operator is asked to tell apart, derived in one place.

* **Audio link** (per speaker): the Sendspin stream between the player and Music Assistant.
* **Music Assistant account** (bridge-wide): the bridge's sign-in to the Music Assistant API.
* **Bluetooth connection** (per speaker): the Bluetooth link between the bridge and the speaker.

They used to be shown as one word, "Connected", and meant three things.  Each
has its own state vocabulary here, so no state code is shared between the
account and the audio link, and every surface reads the same answer.  The
``reason`` of a state is a code, never prose; the web UI translates both.
Decision record: UI/UX overhaul Q1; names in ``docs/ux-glossary.md``.

The verdict (the "Healthy" the header and Diagnostics show) is derived from
the same issue list as everything else, so it cannot sit above a warning.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "LinkState",
    "Verdict",
    "audio_link_state",
    "bluetooth_connection_state",
    "ma_account_state",
    "verdict_from_issues",
]


@dataclass(frozen=True)
class LinkState:
    state: str
    tone: str
    reason: str | None = None


def bluetooth_connection_state(
    *,
    enabled: bool,
    managed: bool,
    released_by: str | None,
    standby: bool,
    reconnecting: bool,
    connected: bool,
    never_paired: bool,
) -> LinkState:
    """connected, connecting, disconnected, standby, released, disabled or not_paired."""
    if not enabled:
        return LinkState("disabled", "neutral")
    if not managed:
        return LinkState(
            "released", "warning" if released_by == "auto" else "neutral", "auto" if released_by == "auto" else "user"
        )
    if standby:
        return LinkState("standby", "neutral")
    if reconnecting:
        return LinkState("connecting", "warning", "reconnecting")
    if connected:
        return LinkState("connected", "success")
    if never_paired:
        return LinkState("not_paired", "error")
    return LinkState("disconnected", "error")


def audio_link_state(
    *,
    enabled: bool,
    bluetooth_state: str | None,
    bluetooth_reason: str | None,
    server_connected: bool,
    streaming: bool,
    foreign: bool,
) -> LinkState:
    """streaming, connected, foreign, disconnected or unavailable.

    ``bluetooth_state`` is None for a local audio output, which has no Bluetooth
    link to wait for.
    """
    if not enabled:
        return LinkState("unavailable", "neutral", "disabled")
    if bluetooth_state == "released":
        return LinkState("unavailable", "neutral", "released")
    if bluetooth_state == "standby":
        return LinkState("unavailable", "neutral", "standby")
    if server_connected:
        if foreign:
            return LinkState("foreign", "warning", "foreign_server")
        return LinkState("streaming" if streaming else "connected", "success")
    if bluetooth_state is not None and bluetooth_state != "connected":
        return LinkState("unavailable", "neutral", "bluetooth_down")
    return LinkState("disconnected", "warning")


def ma_account_state(*, configured: bool, connected: bool) -> LinkState:
    """signed_in, signed_out (set up, not signed in right now) or not_configured."""
    if connected:
        return LinkState("signed_in", "success")
    if configured:
        return LinkState("signed_out", "warning", "unreachable_or_rejected")
    return LinkState("not_configured", "neutral")


@dataclass(frozen=True)
class Verdict:
    """ok, warning or error, and how many issues stand behind it."""

    level: str
    count: int = 0
    failed_collections: int = 0


def verdict_from_issues(issues: Iterable[Any], *, failed_collections: Iterable[str] = ()) -> Verdict:
    """The worst severity in the issue list; informational issues do not count.

    The one function the header pill's tone and the Diagnostics verdict share:
    nothing healthy can be reported while a warning or an error is listed.
    """
    worst = "ok"
    count = 0
    for issue in issues:
        severity = str(issue.get("severity") if isinstance(issue, dict) else getattr(issue, "severity", "") or "")
        if severity == "error":
            worst = "error"
            count += 1
        elif severity == "warning":
            if worst == "ok":
                worst = "warning"
            count += 1
    failed = len(list(failed_collections))
    if failed and worst == "ok":
        worst = "warning"
    return Verdict(level=worst, count=count, failed_collections=failed)
