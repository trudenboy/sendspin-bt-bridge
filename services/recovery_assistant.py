"""Recovery-oriented diagnostics helpers for Phase 2 operator flows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc


def _device_extra(device: Any) -> dict[str, Any]:
    extra = getattr(device, "extra", None)
    return extra if isinstance(extra, dict) else {}


def _reconnect_attempt_summary(device: Any) -> str:
    extra = _device_extra(device)
    attempt = int(extra.get("reconnect_attempt") or 0)
    if attempt <= 0:
        return ""
    threshold = int(extra.get("max_reconnect_fails") or 0)
    if threshold > 0:
        remaining = max(threshold - attempt, 0)
        return f"Reconnect attempt {attempt}/{threshold}. {remaining} attempts remain before auto-release."
    return f"Reconnect attempt {attempt} is in progress."


@dataclass
class RecoveryAction:
    key: str
    label: str
    device_name: str | None = None
    device_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"key": self.key, "label": self.label}
        if self.device_names:
            payload["device_names"] = [name for name in self.device_names if name]
        elif self.device_name:
            payload["device_name"] = self.device_name
        return payload


@dataclass
class RecoveryIssue:
    key: str
    severity: str
    title: str
    summary: str
    primary_action: RecoveryAction | None = None
    secondary_actions: list[RecoveryAction] = field(default_factory=list)
    device_name: str | None = None

    @property
    def recommended_action(self) -> RecoveryAction | None:
        return self.primary_action

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "key": self.key,
            "severity": self.severity,
            "title": self.title,
            "summary": self.summary,
            "secondary_actions": [action.to_dict() for action in self.secondary_actions],
        }
        if self.device_name:
            payload["device_name"] = self.device_name
        if self.primary_action:
            payload["primary_action"] = self.primary_action.to_dict()
            payload["recommended_action"] = self.primary_action.to_dict()
        return payload


@dataclass
class RecoveryTrace:
    label: str
    tone: str
    summary: str
    entries: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "tone": self.tone,
            "summary": self.summary,
            "entries": list(self.entries),
        }


@dataclass
class RecoveryAssistantSnapshot:
    generated_at: str
    summary: dict[str, Any]
    issues: list[RecoveryIssue] = field(default_factory=list)
    traces: list[RecoveryTrace] = field(default_factory=list)
    safe_actions: list[RecoveryAction] = field(default_factory=list)
    latency_assistant: dict[str, Any] = field(default_factory=dict)
    known_good_test_path: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "summary": dict(self.summary),
            "issues": [issue.to_dict() for issue in self.issues],
            "traces": [trace.to_dict() for trace in self.traces],
            "safe_actions": [action.to_dict() for action in self.safe_actions],
            "latency_assistant": dict(self.latency_assistant),
            "known_good_test_path": dict(self.known_good_test_path),
        }


def _recommended_action_from_onboarding(checklist: dict[str, Any]) -> RecoveryAction | None:
    action = checklist.get("primary_action") or {}
    key = str(action.get("key") or "").strip()
    label = str(action.get("label") or "").strip()
    if not key or not label:
        return None
    return RecoveryAction(key=key, label=label)


def _normalize_device_names(device_names: list[str] | None) -> list[str]:
    return [str(name).strip() for name in (device_names or []) if str(name).strip()]


def _recovery_action(
    key: str,
    label: str,
    *,
    device_names: list[str] | None = None,
) -> RecoveryAction:
    names = _normalize_device_names(device_names)
    return RecoveryAction(
        key=key,
        label=label,
        device_name=names[0] if len(names) == 1 else None,
        device_names=names if len(names) > 1 else [],
    )


def _merge_secondary_actions(
    primary_action: RecoveryAction | None,
    secondary_actions: list[RecoveryAction] | None = None,
) -> list[RecoveryAction]:
    actions = list(secondary_actions or [])
    seen = {
        (action.key, action.label, tuple(action.device_names), action.device_name)
        for action in actions
        if action and action.key
    }
    if primary_action:
        seen.add(
            (
                primary_action.key,
                primary_action.label,
                tuple(primary_action.device_names),
                primary_action.device_name,
            )
        )
    diagnostics = _recovery_action("open_diagnostics", "Open diagnostics")
    marker = (diagnostics.key, diagnostics.label, tuple(diagnostics.device_names), diagnostics.device_name)
    if marker not in seen:
        actions.append(diagnostics)
    return actions


def build_recovery_issue_actions(
    issue_key: str,
    device_names: list[str] | None = None,
    *,
    extra_secondary_actions: list[RecoveryAction] | None = None,
) -> tuple[RecoveryAction | None, list[RecoveryAction]]:
    names = _normalize_device_names(device_names)
    primary_action: RecoveryAction | None
    secondary_actions = list(extra_secondary_actions or [])
    if issue_key in {"missing_sink", "disconnected", "transport_down"}:
        primary_action = _recovery_action(
            "reconnect_devices" if len(names) > 1 else "reconnect_device",
            f"Reconnect {len(names)} devices" if len(names) > 1 else "Reconnect speaker",
            device_names=names,
        )
    elif issue_key == "repair_required":
        primary_action = _recovery_action(
            "open_devices_settings" if len(names) > 1 else "pair_device",
            "Open device settings" if len(names) > 1 else "Re-pair speaker",
            device_names=names,
        )
        if len(names) == 1:
            secondary_actions.insert(
                0,
                _recovery_action("toggle_bt_management", "Release Bluetooth", device_names=names),
            )
    elif issue_key == "auto_released":
        primary_action = _recovery_action(
            "toggle_bt_management_devices" if len(names) > 1 else "toggle_bt_management",
            f"Reclaim {len(names)} devices" if len(names) > 1 else "Reclaim Bluetooth",
            device_names=names,
        )
    elif issue_key == "setup_step":
        primary_action = None
    else:
        primary_action = _recovery_action("open_diagnostics", "Open diagnostics", device_names=names)
    return primary_action, _merge_secondary_actions(primary_action, secondary_actions)


def _build_device_issues(devices: list[Any]) -> list[RecoveryIssue]:
    issues: list[RecoveryIssue] = []
    for device in devices:
        name = str(getattr(device, "player_name", None) or "Unknown")
        device_names = [name]
        health = getattr(device, "health_summary", None) or {}
        summary = str(health.get("summary") or "")
        if getattr(device, "bt_management_enabled", True) is False:
            if _device_extra(device).get("bt_released_by") != "auto":
                continue
            primary_action, secondary_actions = build_recovery_issue_actions("auto_released", device_names)
            issues.append(
                RecoveryIssue(
                    key="auto_released",
                    severity="warning",
                    title=f"{name} was auto-released",
                    summary=summary
                    or "Bluetooth management was auto-released for this speaker after connection problems.",
                    primary_action=primary_action,
                    secondary_actions=secondary_actions,
                    device_name=name,
                )
            )
            continue
        if getattr(device, "bluetooth_connected", False) and not getattr(device, "has_sink", False):
            primary_action, secondary_actions = build_recovery_issue_actions("missing_sink", device_names)
            issues.append(
                RecoveryIssue(
                    key="missing_sink",
                    severity="error",
                    title=f"{name} is missing a sink",
                    summary=summary or "The speaker is connected, but no Bluetooth sink is resolved yet.",
                    primary_action=primary_action,
                    secondary_actions=secondary_actions,
                    device_name=name,
                )
            )
            continue
        if not getattr(device, "bluetooth_connected", False):
            attempt_summary = _reconnect_attempt_summary(device)
            bluetooth_paired = _device_extra(device).get("bluetooth_paired")
            issue_key = "repair_required" if bluetooth_paired is False else "disconnected"
            primary_action, secondary_actions = build_recovery_issue_actions(issue_key, device_names)
            issues.append(
                RecoveryIssue(
                    key=issue_key,
                    severity="warning",
                    title=f"{name} needs re-pairing" if bluetooth_paired is False else f"{name} is disconnected",
                    summary=(
                        summary
                        or (
                            "The speaker is no longer paired, so reconnect attempts will keep failing. Put it in pairing mode and run re-pair."
                            if bluetooth_paired is False
                            else "Power on the speaker or trigger a reconnect."
                        )
                    )
                    + (f" {attempt_summary}" if attempt_summary else ""),
                    primary_action=primary_action,
                    secondary_actions=secondary_actions,
                    device_name=name,
                )
            )
            continue
        if not getattr(device, "server_connected", False):
            primary_action, secondary_actions = build_recovery_issue_actions("transport_down", device_names)
            issues.append(
                RecoveryIssue(
                    key="transport_down",
                    severity="error",
                    title=f"{name} lost bridge transport",
                    summary=summary or "The Sendspin daemon is not connected for this device.",
                    primary_action=primary_action,
                    secondary_actions=secondary_actions,
                    device_name=name,
                )
            )
            continue
        health_state = str(health.get("state") or "")
        if health_state in {"degraded", "recovering", "transitioning"}:
            issues.append(
                RecoveryIssue(
                    key="needs_attention",
                    severity="error" if health_state == "degraded" else "warning",
                    title=f"{name} needs recovery attention",
                    summary=summary or "Review the recent recovery timeline and diagnostics.",
                    primary_action=_recovery_action("open_diagnostics", "Open diagnostics", device_names=device_names),
                    device_name=name,
                )
            )
    return issues


def _build_onboarding_issue(onboarding_assistant: dict[str, Any]) -> RecoveryIssue | None:
    checklist = onboarding_assistant.get("checklist") or {}
    current_title = str(checklist.get("current_step_title") or "").strip()
    current_summary = str(checklist.get("summary") or "").strip()
    overall_status = str(checklist.get("overall_status") or "")
    if not current_title or overall_status == "ok":
        return None
    primary_action = _recommended_action_from_onboarding(checklist)
    secondary_actions = _merge_secondary_actions(primary_action)
    return RecoveryIssue(
        key=str(checklist.get("current_step_key") or "setup_step"),
        severity="error" if overall_status == "error" else "warning",
        title=current_title,
        summary=current_summary or "The setup checklist still has an unresolved step.",
        primary_action=primary_action,
        secondary_actions=secondary_actions,
    )


def _build_safe_actions(issues: list[RecoveryIssue], onboarding_assistant: dict[str, Any]) -> list[RecoveryAction]:
    actions = [
        RecoveryAction(key="refresh_diagnostics", label="Rerun checks"),
        RecoveryAction(key="open_diagnostics", label="Open diagnostics"),
    ]
    if (
        any(issue.recommended_action and issue.recommended_action.key == "open_ma_settings" for issue in issues)
        or str((onboarding_assistant.get("checklist") or {}).get("current_step_key") or "") == "ma_auth"
    ):
        actions.append(RecoveryAction(key="retry_ma_discovery", label="Retry MA discovery"))
    seen: set[tuple[str, str, str | None]] = set()
    deduped: list[RecoveryAction] = []
    for action in actions:
        marker = (action.key, action.label, action.device_name)
        if marker in seen:
            continue
        seen.add(marker)
        deduped.append(action)
    return deduped


def _build_traces(devices: list[Any], startup_progress: dict[str, Any]) -> list[RecoveryTrace]:
    traces: list[RecoveryTrace] = []
    startup_status = str(startup_progress.get("status") or "idle")
    startup_tone = "ok" if startup_status == "complete" else "error" if startup_status == "error" else "warn"
    startup_entries = []
    if startup_progress:
        startup_entries.append(
            {
                "at": startup_progress.get("updated_at")
                or startup_progress.get("completed_at")
                or startup_progress.get("started_at"),
                "level": "error" if startup_status == "error" else "info",
                "label": startup_progress.get("phase") or "startup",
                "summary": startup_progress.get("message") or "Startup progress available",
            }
        )
    traces.append(
        RecoveryTrace(
            label="Bridge startup",
            tone=startup_tone,
            summary=str(startup_progress.get("message") or "No startup progress recorded."),
            entries=startup_entries,
        )
    )

    for device in devices:
        recent_events = list(getattr(device, "recent_events", []) or [])
        health = getattr(device, "health_summary", None) or {}
        if not recent_events and str(health.get("state") or "") in {"ready", "streaming"}:
            continue
        tone = "error" if str(health.get("severity") or "") == "error" else "warn"
        traces.append(
            RecoveryTrace(
                label=str(getattr(device, "player_name", None) or "Device"),
                tone=tone,
                summary=str(health.get("summary") or "Recent recovery activity available."),
                entries=[
                    {
                        "at": event.get("at"),
                        "level": event.get("level") or "info",
                        "label": event.get("event_type") or "event",
                        "summary": event.get("message") or "Recovery event",
                    }
                    for event in recent_events[:5]
                ],
            )
        )
    return traces


def _build_latency_assistant(config: dict[str, Any], devices: list[Any]) -> dict[str, Any]:
    configured_devices = config.get("BLUETOOTH_DEVICES", [])
    configured_count = len(configured_devices) if isinstance(configured_devices, list) else 0
    custom_delays = sum(1 for device in devices if getattr(device, "static_delay_ms", None) not in (None, 0, 0.0))
    pulse_latency = int(config.get("PULSE_LATENCY_MSEC") or 0)
    if configured_count < 2:
        return {
            "tone": "ok",
            "summary": "Single-device setups usually do not need extra latency tuning.",
            "recommended_pulse_latency_msec": pulse_latency or 300,
            "hints": ["Add a second room before spending time on manual delay calibration."],
            "safe_actions": [RecoveryAction(key="open_devices_settings", label="Open device settings").to_dict()],
        }
    if custom_delays == 0:
        return {
            "tone": "warning",
            "summary": "Multi-device setup detected without per-device static delays.",
            "recommended_pulse_latency_msec": max(pulse_latency, 300),
            "hints": [
                "Play the same short track in both rooms and listen for drift.",
                "Set `static_delay_ms` per device only after the Bluetooth sink is stable.",
            ],
            "safe_actions": [RecoveryAction(key="open_devices_settings", label="Tune device delays").to_dict()],
        }
    if pulse_latency >= 800:
        return {
            "tone": "warning",
            "summary": "Per-device delay tuning exists, but the global PulseAudio latency is still high.",
            "recommended_pulse_latency_msec": 600,
            "hints": [
                "Keep the high latency if virtualization needs it, but lower it when playback reacts too slowly.",
                "Re-test one room at a time after every latency change.",
            ],
            "safe_actions": [RecoveryAction(key="open_devices_settings", label="Review latency settings").to_dict()],
        }
    return {
        "tone": "ok",
        "summary": "Latency tuning is in a healthy range for a multi-device setup.",
        "recommended_pulse_latency_msec": pulse_latency,
        "hints": [
            "Use the known-good test path after Bluetooth reconnects to confirm the rooms still match.",
        ],
        "safe_actions": [RecoveryAction(key="refresh_diagnostics", label="Rerun checks").to_dict()],
    }


def _build_known_good_test_path(devices: list[Any], onboarding_assistant: dict[str, Any]) -> dict[str, Any]:
    checklist = onboarding_assistant.get("checklist") or {}
    checkpoints = {item.get("key"): item for item in checklist.get("checkpoints") or []}
    any_transport = any(bool(getattr(device, "server_connected", False)) for device in devices)
    return {
        "summary": "Use this path to separate Bluetooth/routing issues from Music Assistant visibility issues.",
        "steps": [
            {
                "label": "Confirm a speaker is connected",
                "reached": bool(checkpoints.get("bluetooth_connected", {}).get("reached")),
                "summary": checkpoints.get("bluetooth_connected", {}).get("summary") or "No speaker is connected yet.",
            },
            {
                "label": "Confirm a Bluetooth sink is attached",
                "reached": bool(checkpoints.get("sink_ready", {}).get("reached")),
                "summary": checkpoints.get("sink_ready", {}).get("summary") or "No audio sink is ready yet.",
            },
            {
                "label": "Confirm the bridge transport is up",
                "reached": any_transport,
                "summary": "At least one device has an active Sendspin transport."
                if any_transport
                else "No device has an active Sendspin transport.",
            },
            {
                "label": "Confirm Music Assistant visibility",
                "reached": bool(checkpoints.get("ma_visible", {}).get("reached")),
                "summary": checkpoints.get("ma_visible", {}).get("summary") or "Music Assistant is still not linked.",
            },
        ],
        "recommended_action": RecoveryAction(key="refresh_diagnostics", label="Rerun checks").to_dict(),
    }


def build_recovery_assistant_snapshot(
    *,
    config: dict[str, Any],
    devices: list[Any],
    onboarding_assistant: dict[str, Any],
    startup_progress: dict[str, Any],
) -> RecoveryAssistantSnapshot:
    issues = _build_device_issues(devices)
    onboarding_issue = _build_onboarding_issue(onboarding_assistant)
    if onboarding_issue:
        issues.append(onboarding_issue)

    issues.sort(key=lambda issue: 0 if issue.severity == "error" else 1)
    highest_severity = "ok"
    if any(issue.severity == "error" for issue in issues):
        highest_severity = "error"
    elif issues:
        highest_severity = "warning"

    headline = "No active recovery issues"
    summary = "The bridge looks healthy right now."
    if issues:
        headline = issues[0].title
        summary = issues[0].summary

    return RecoveryAssistantSnapshot(
        generated_at=datetime.now(tz=UTC).isoformat(),
        summary={
            "open_issue_count": len(issues),
            "highest_severity": highest_severity,
            "headline": headline,
            "summary": summary,
        },
        issues=issues,
        traces=_build_traces(devices, startup_progress),
        safe_actions=_build_safe_actions(issues, onboarding_assistant),
        latency_assistant=_build_latency_assistant(config, devices),
        known_good_test_path=_build_known_good_test_path(devices, onboarding_assistant),
    )
