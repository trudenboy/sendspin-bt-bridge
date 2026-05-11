"""Pre-submit classifier for the bug-report flow (issue #262).

When an operator opens the in-UI bug-report form, the bridge already has
enough diagnostic context to recognise a handful of "fix this first"
patterns. Instead of letting the user submit a ticket with an empty
"What I was doing / expected / happened" body, we surface a short list
of likely causes with one-click action hints. The classifier is purely
advisory — the UI always offers a "Report anyway" fallback so genuine
bug paths are never blocked.

Returned shape per cause:

    {
        "code": "<stable_machine_id>",
        "title": "<short human title>",
        "hint": "<one-line remediation>",
        "action_key": "<stable action id consumed by app.js>",
        "confidence": "high|medium|low",
    }

``action_key`` is a stable identifier; the frontend maps it to the
appropriate ``_openConfigPanel(...)`` invocation. Earlier versions used
``action_url`` with hash fragments like ``#bluetooth-devices``, but the
project does not implement hash routing and the fragments did not match
any real anchor IDs, so the "Try this first" links were silent no-ops
(Copilot review on PR #290).

Each rule is a small pure function so it can be unit-tested in isolation.
The orchestrator (``classify_likely_causes``) runs them all and
de-duplicates by ``code`` — first match wins.
"""

from __future__ import annotations

from typing import Any


def _rule_never_paired(recovery_snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Fire when at least one recovery issue is a never-paired or
    auto-disabled-never-paired card. The remedy is unambiguous: put the
    speaker in pairing mode and run Start pairing from the device card.
    """
    issues = recovery_snapshot.get("issues") if isinstance(recovery_snapshot, dict) else None
    if not isinstance(issues, list):
        return None
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if issue.get("key") in ("never_paired", "auto_disabled_never_paired"):
            return {
                "code": "never_paired_device",
                "title": "A configured speaker has never been paired",
                "hint": (
                    "Put the speaker in pairing mode, then click Start pairing on its "
                    "device card. This usually resolves 'no audio' and 'reconnect loop' "
                    "reports without a ticket."
                ),
                "action_key": "open_bluetooth_devices",
                "confidence": "high",
            }
    return None


def _rule_ma_not_connected(diagnostics: dict[str, Any]) -> dict[str, Any] | None:
    """Fire when Music Assistant is configured but the bridge is not
    currently connected. Either the MA server is offline or the token
    expired — both are operator-fixable before opening a ticket.

    The diagnostics payload uses ``ma_integration`` as the canonical block
    name in ``api_diagnostics()``; we accept ``ma`` as a fallback for
    test fixtures that use the shorter alias.
    """
    if not isinstance(diagnostics, dict):
        return None
    ma_block = diagnostics.get("ma_integration") or diagnostics.get("ma")
    if not isinstance(ma_block, dict):
        return None
    configured = bool(ma_block.get("configured"))
    connected = bool(ma_block.get("connected"))
    if configured and not connected:
        return {
            "code": "ma_not_connected",
            "title": "Music Assistant is not connected",
            "hint": (
                "The bridge can't reach Music Assistant. Check that MA is running and "
                "reachable from this host, then re-authenticate from the Music Assistant tab."
            ),
            "action_key": "open_ma_settings",
            "confidence": "high",
        }
    return None


def _rule_no_bluetooth_adapter(diagnostics: dict[str, Any]) -> dict[str, Any] | None:
    """Fire when the diagnostics report no Bluetooth adapter is visible
    to the bridge — either no controllers configured or all of them
    failed to enumerate. This is mid-confidence because a transient
    D-Bus hiccup can show the same shape briefly.

    The diagnostics payload uses ``adapters`` as the canonical key in
    ``api_diagnostics()``; we accept ``bluetooth_adapters`` as a
    fallback for the bridge config shape and test fixtures.
    """
    if not isinstance(diagnostics, dict):
        return None
    adapters = diagnostics.get("adapters")
    if adapters is None:
        adapters = diagnostics.get("bluetooth_adapters")
    if isinstance(adapters, list) and not adapters:
        return {
            "code": "no_bluetooth_adapter",
            "title": "No Bluetooth adapters detected",
            "hint": (
                "The bridge can't see any Bluetooth controllers. Confirm the adapter is "
                "plugged in and powered (rfkill list), check that bluetoothd is running, "
                "and re-scan from the Bluetooth tab. On HA addons, verify USB passthrough."
            ),
            "action_key": "open_bluetooth_adapters",
            "confidence": "medium",
        }
    return None


def _rule_audio_sink_missing(recovery_snapshot: dict[str, Any]) -> dict[str, Any] | None:
    """Fire when at least one recovery card flags a missing audio sink —
    the BT link is up but PulseAudio/PipeWire didn't create the
    ``bluez_output.*`` / ``bluez_sink.*`` node.
    """
    issues = recovery_snapshot.get("issues") if isinstance(recovery_snapshot, dict) else None
    if not isinstance(issues, list):
        return None
    for issue in issues:
        if not isinstance(issue, dict):
            continue
        if issue.get("key") == "missing_sink":
            return {
                "code": "audio_sink_missing",
                "title": "Bluetooth sink is not exposed by the audio backend",
                "hint": (
                    "The speaker is connected to BlueZ but no bluez_output/bluez_sink "
                    "node appeared. Restart WirePlumber (or pipewire-pulse) on the host, "
                    "then trigger Reconnect on the device card."
                ),
                "action_key": "open_bluetooth_devices",
                "confidence": "high",
            }
    return None


def classify_likely_causes(
    *,
    recovery_snapshot: dict[str, Any] | None,
    log_summary: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    """Run every rule against the diagnostic context and return matches.

    Returns a list of likely-cause dicts in deterministic order, de-duped
    by ``code``. Empty list when nothing matches — the UI then renders
    the unguarded bug-report form.
    """
    recovery_snapshot = recovery_snapshot or {}
    diagnostics = diagnostics or {}
    causes: list[dict[str, Any]] = []
    seen: set[str] = set()
    for rule, ctx in (
        (_rule_never_paired, recovery_snapshot),
        (_rule_audio_sink_missing, recovery_snapshot),
        (_rule_ma_not_connected, diagnostics),
        (_rule_no_bluetooth_adapter, diagnostics),
    ):
        cause = rule(ctx)
        if cause is None:
            continue
        code = str(cause.get("code") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        causes.append(cause)
    return causes
