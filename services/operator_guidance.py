"""Unified operator guidance built from onboarding, capability, and recovery data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc

_ONBOARDING_VISIBILITY_KEY = "sendspin-ui:show-onboarding-guidance"
_RECOVERY_VISIBILITY_KEY = "sendspin-ui:show-recovery-guidance"


@dataclass
class GuidanceAction:
    key: str
    label: str
    device_names: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"key": self.key, "label": self.label}
        if self.device_names:
            payload["device_names"] = list(self.device_names)
        return payload


@dataclass
class GuidanceIssueGroup:
    key: str
    severity: str
    title: str
    summary: str
    count: int
    device_names: list[str] = field(default_factory=list)
    primary_action: GuidanceAction | None = None
    secondary_actions: list[GuidanceAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "key": self.key,
            "severity": self.severity,
            "title": self.title,
            "summary": self.summary,
            "count": self.count,
            "device_names": list(self.device_names),
            "secondary_actions": [action.to_dict() for action in self.secondary_actions],
        }
        if self.primary_action:
            payload["primary_action"] = self.primary_action.to_dict()
        return payload


@dataclass
class GuidanceHeaderStatus:
    tone: str
    label: str
    summary: str

    def to_dict(self) -> dict[str, str]:
        return {"tone": self.tone, "label": self.label, "summary": self.summary}


@dataclass
class GuidanceBanner:
    kind: str
    tone: str
    headline: str
    summary: str
    dismissible: bool
    preference_key: str
    primary_action: GuidanceAction | None = None
    secondary_actions: list[GuidanceAction] = field(default_factory=list)
    issue_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "kind": self.kind,
            "tone": self.tone,
            "headline": self.headline,
            "summary": self.summary,
            "dismissible": self.dismissible,
            "preference_key": self.preference_key,
            "secondary_actions": [action.to_dict() for action in self.secondary_actions],
            "issue_count": self.issue_count,
        }
        if self.primary_action:
            payload["primary_action"] = self.primary_action.to_dict()
        return payload


@dataclass
class GuidanceOnboardingCard:
    headline: str
    summary: str
    checklist: dict[str, Any]
    dismissible: bool
    preference_key: str
    primary_action: GuidanceAction | None = None
    secondary_actions: list[GuidanceAction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "headline": self.headline,
            "summary": self.summary,
            "checklist": dict(self.checklist),
            "dismissible": self.dismissible,
            "preference_key": self.preference_key,
            "secondary_actions": [action.to_dict() for action in self.secondary_actions],
        }
        if self.primary_action:
            payload["primary_action"] = self.primary_action.to_dict()
        return payload


@dataclass
class OperatorGuidanceSnapshot:
    mode: str
    generated_at: str
    visibility_keys: dict[str, str]
    header_status: GuidanceHeaderStatus | None = None
    banner: GuidanceBanner | None = None
    onboarding_card: GuidanceOnboardingCard | None = None
    issue_groups: list[GuidanceIssueGroup] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "mode": self.mode,
            "generated_at": self.generated_at,
            "visibility_keys": dict(self.visibility_keys),
            "issue_groups": [group.to_dict() for group in self.issue_groups],
        }
        if self.header_status:
            payload["header_status"] = self.header_status.to_dict()
        if self.banner:
            payload["banner"] = self.banner.to_dict()
        if self.onboarding_card:
            payload["onboarding_card"] = self.onboarding_card.to_dict()
        return payload


def _count_config_entries(config: dict[str, Any], key: str) -> int:
    value = config.get(key, [])
    return len(value) if isinstance(value, list) else 0


def _guidance_action_from_dict(payload: dict[str, Any] | None) -> GuidanceAction | None:
    if not isinstance(payload, dict):
        return None
    key = str(payload.get("key") or "").strip()
    label = str(payload.get("label") or "").strip()
    if not key or not label:
        return None
    device_names = payload.get("device_names") or []
    return GuidanceAction(
        key=key,
        label=label,
        device_names=[str(name) for name in device_names if str(name).strip()],
    )


def _format_device_label(device_names: list[str]) -> str:
    names = [name for name in device_names if name]
    if not names:
        return "devices"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{names[0]}, {names[1]}, and {len(names) - 2} more"


def _always_open_diagnostics(
    action: GuidanceAction | None, secondary_actions: list[GuidanceAction]
) -> list[GuidanceAction]:
    actions = list(secondary_actions)
    diagnostics = GuidanceAction(key="open_diagnostics", label="Open diagnostics")
    known = {(item.key, tuple(item.device_names)) for item in actions}
    if action:
        known.add((action.key, tuple(action.device_names)))
    if (diagnostics.key, tuple()) not in known:
        actions.append(diagnostics)
    return actions


def _append_group(
    groups: list[GuidanceIssueGroup],
    *,
    key: str,
    severity: str,
    title: str,
    summary: str,
    device_names: list[str] | None = None,
    primary_action: GuidanceAction | None = None,
    secondary_actions: list[GuidanceAction] | None = None,
) -> None:
    names = [name for name in (device_names or []) if name]
    groups.append(
        GuidanceIssueGroup(
            key=key,
            severity=severity,
            title=title,
            summary=summary,
            count=max(1, len(names)),
            device_names=names,
            primary_action=primary_action,
            secondary_actions=_always_open_diagnostics(primary_action, list(secondary_actions or [])),
        )
    )


def _build_issue_groups(
    devices: list[Any],
    onboarding_assistant: dict[str, Any],
    recovery_assistant: dict[str, Any],
) -> list[GuidanceIssueGroup]:
    groups: list[GuidanceIssueGroup] = []
    disconnected = [
        str(getattr(device, "player_name", None) or "Unknown")
        for device in devices
        if getattr(device, "bt_management_enabled", True) and not getattr(device, "bluetooth_connected", False)
    ]
    missing_sink = [
        str(getattr(device, "player_name", None) or "Unknown")
        for device in devices
        if getattr(device, "bt_management_enabled", True)
        and getattr(device, "bluetooth_connected", False)
        and not getattr(device, "has_sink", False)
    ]
    transport_down = [
        str(getattr(device, "player_name", None) or "Unknown")
        for device in devices
        if getattr(device, "bt_management_enabled", True)
        and getattr(device, "bluetooth_connected", False)
        and getattr(device, "has_sink", False)
        and not getattr(device, "server_connected", False)
    ]
    released = [
        str(getattr(device, "player_name", None) or "Unknown")
        for device in devices
        if getattr(device, "bt_management_enabled", True) is False
    ]

    if missing_sink:
        title = (
            f"{missing_sink[0]} is missing a sink"
            if len(missing_sink) == 1
            else f"{len(missing_sink)} devices are missing sinks"
        )
        summary = (
            "The speaker is connected, but its Bluetooth sink is still not resolved."
            if len(missing_sink) == 1
            else f"Reconnect or inspect routing for {_format_device_label(missing_sink)}."
        )
        _append_group(
            groups,
            key="missing_sink",
            severity="error",
            title=title,
            summary=summary,
            device_names=missing_sink,
            primary_action=GuidanceAction(
                key="reconnect_devices" if len(missing_sink) > 1 else "reconnect_device",
                label=f"Reconnect {len(missing_sink)} devices" if len(missing_sink) > 1 else "Reconnect speaker",
                device_names=missing_sink,
            ),
        )
    if transport_down:
        title = (
            f"{transport_down[0]} lost bridge transport"
            if len(transport_down) == 1
            else f"{len(transport_down)} devices lost bridge transport"
        )
        summary = (
            "The speaker is connected, but the Sendspin daemon is not active."
            if len(transport_down) == 1
            else f"Reconnect {_format_device_label(transport_down)} to restore bridge transport."
        )
        _append_group(
            groups,
            key="transport_down",
            severity="error",
            title=title,
            summary=summary,
            device_names=transport_down,
            primary_action=GuidanceAction(
                key="reconnect_devices" if len(transport_down) > 1 else "reconnect_device",
                label=f"Reconnect {len(transport_down)} devices" if len(transport_down) > 1 else "Reconnect speaker",
                device_names=transport_down,
            ),
        )
    if disconnected:
        title = (
            f"{disconnected[0]} is disconnected"
            if len(disconnected) == 1
            else f"{len(disconnected)} devices are disconnected"
        )
        summary = (
            "Power on the speaker or trigger a reconnect."
            if len(disconnected) == 1
            else f"Power on or reconnect {_format_device_label(disconnected)}."
        )
        _append_group(
            groups,
            key="disconnected",
            severity="warning",
            title=title,
            summary=summary,
            device_names=disconnected,
            primary_action=GuidanceAction(
                key="reconnect_devices" if len(disconnected) > 1 else "reconnect_device",
                label=f"Reconnect {len(disconnected)} devices" if len(disconnected) > 1 else "Reconnect speaker",
                device_names=disconnected,
            ),
        )
    if released:
        title = f"{released[0]} is released" if len(released) == 1 else f"{len(released)} devices are released"
        summary = (
            "Bluetooth management is disabled for this speaker."
            if len(released) == 1
            else f"Reclaim {_format_device_label(released)} to restore bridge management."
        )
        _append_group(
            groups,
            key="released",
            severity="warning",
            title=title,
            summary=summary,
            device_names=released,
            primary_action=GuidanceAction(
                key="toggle_bt_management_devices" if len(released) > 1 else "toggle_bt_management",
                label=f"Reclaim {len(released)} devices" if len(released) > 1 else "Reclaim Bluetooth",
                device_names=released,
            ),
        )

    checklist = onboarding_assistant.get("checklist") or {}
    current_step_key = str(checklist.get("current_step_key") or "")
    overall_status = str(checklist.get("overall_status") or "")
    if current_step_key == "ma_auth" and overall_status in {"warning", "error"}:
        recovery_actions = [_guidance_action_from_dict(item) for item in recovery_assistant.get("safe_actions") or []]
        valid_recovery_actions = [item for item in recovery_actions if item is not None]
        primary_action = _guidance_action_from_dict(checklist.get("primary_action")) or (
            valid_recovery_actions[0]
            if valid_recovery_actions
            else GuidanceAction(key="open_ma_settings", label="Open Music Assistant settings")
        )
        secondary = [
            action
            for action in valid_recovery_actions
            if action.key != primary_action.key or action.device_names != primary_action.device_names
        ]
        _append_group(
            groups,
            key="ma_auth",
            severity="error" if overall_status == "error" else "warning",
            title="Music Assistant needs attention",
            summary=str(checklist.get("summary") or "Music Assistant integration is not ready yet."),
            primary_action=primary_action,
            secondary_actions=secondary,
        )

    groups.sort(key=lambda item: (0 if item.severity == "error" else 1, -item.count, item.title))
    return groups


def _build_onboarding_card(
    *,
    empty_state: bool,
    onboarding_assistant: dict[str, Any],
) -> GuidanceOnboardingCard | None:
    if not empty_state:
        return None
    checklist = onboarding_assistant.get("checklist") or {}
    if not checklist:
        return None
    primary_action = _guidance_action_from_dict(checklist.get("primary_action"))
    secondary_actions = [GuidanceAction(key="open_diagnostics", label="Open diagnostics")]
    return GuidanceOnboardingCard(
        headline=str(checklist.get("headline") or "Get started"),
        summary=str(checklist.get("summary") or "Finish the first-run checklist."),
        checklist=checklist,
        dismissible=True,
        preference_key=_ONBOARDING_VISIBILITY_KEY,
        primary_action=primary_action,
        secondary_actions=secondary_actions,
    )


def _build_header_status(
    *,
    mode: str,
    config: dict[str, Any],
    onboarding_assistant: dict[str, Any],
    issue_groups: list[GuidanceIssueGroup],
    startup_progress: dict[str, Any],
    recovery_assistant: dict[str, Any],
) -> GuidanceHeaderStatus:
    configured_devices = _count_config_entries(config, "BLUETOOTH_DEVICES")
    checklist = onboarding_assistant.get("checklist") or {}
    counts = onboarding_assistant.get("counts") or {}
    startup_status = str(startup_progress.get("status") or "idle")
    if startup_status in {"running", "starting"}:
        percent = int(startup_progress.get("percent") or 0)
        return GuidanceHeaderStatus(
            tone="info",
            label=f"Startup {percent}%",
            summary=str(startup_progress.get("message") or "Bridge startup checks are still running."),
        )
    if mode == "empty_state":
        return GuidanceHeaderStatus(
            tone="info",
            label="First run",
            summary="Add your first Bluetooth adapter and speaker to start guided setup.",
        )
    if issue_groups:
        lead = issue_groups[0]
        label = lead.title if len(issue_groups) == 1 else f"{len(issue_groups)} issues need attention"
        return GuidanceHeaderStatus(tone=lead.severity, label=label, summary=lead.summary)
    if configured_devices > 0 and str(checklist.get("overall_status") or "") != "ok":
        return GuidanceHeaderStatus(
            tone="warning",
            label=f"Setup {int(checklist.get('progress_percent') or 0)}%",
            summary=str(checklist.get("summary") or "Bridge setup still has pending steps."),
        )

    ready_devices = int(counts.get("sink_ready_devices") or 0)
    connected_devices = int(counts.get("connected_devices") or 0)
    recovery_summary = recovery_assistant.get("summary") or {}
    if configured_devices > 0:
        return GuidanceHeaderStatus(
            tone="success",
            label=f"{ready_devices}/{configured_devices} devices ready",
            summary=(
                "All configured devices have sinks and are ready for playback."
                if ready_devices == configured_devices and configured_devices > 0
                else f"{connected_devices} connected · {ready_devices} with sinks ready."
            ),
        )
    return GuidanceHeaderStatus(
        tone="neutral",
        label="Waiting for setup",
        summary=str(recovery_summary.get("summary") or "Configure your first speaker to start playback."),
    )


def _build_banner(
    *,
    mode: str,
    issue_groups: list[GuidanceIssueGroup],
) -> GuidanceBanner | None:
    if mode != "attention" or not issue_groups:
        return None
    lead = issue_groups[0]
    return GuidanceBanner(
        kind="attention",
        tone="error" if lead.severity == "error" else "warning",
        headline=lead.title,
        summary=lead.summary,
        dismissible=True,
        preference_key=_RECOVERY_VISIBILITY_KEY,
        primary_action=lead.primary_action,
        secondary_actions=lead.secondary_actions,
        issue_count=len(issue_groups),
    )


def build_operator_guidance_snapshot(
    *,
    config: dict[str, Any],
    onboarding_assistant: dict[str, Any],
    recovery_assistant: dict[str, Any],
    startup_progress: dict[str, Any],
    devices: list[Any],
) -> OperatorGuidanceSnapshot:
    configured_adapters = _count_config_entries(config, "BLUETOOTH_ADAPTERS")
    configured_devices = _count_config_entries(config, "BLUETOOTH_DEVICES")
    checklist = onboarding_assistant.get("checklist") or {}
    issue_groups = _build_issue_groups(devices, onboarding_assistant, recovery_assistant)
    startup_status = str(startup_progress.get("status") or "idle")

    empty_state = configured_adapters == 0 and configured_devices == 0
    if empty_state:
        mode = "empty_state"
    elif issue_groups:
        mode = "attention"
    elif startup_status in {"running", "starting"} or str(checklist.get("overall_status") or "") != "ok":
        mode = "progress"
    else:
        mode = "healthy"

    return OperatorGuidanceSnapshot(
        mode=mode,
        generated_at=datetime.now(tz=UTC).isoformat(),
        visibility_keys={
            "onboarding": _ONBOARDING_VISIBILITY_KEY,
            "recovery": _RECOVERY_VISIBILITY_KEY,
        },
        header_status=_build_header_status(
            mode=mode,
            config=config,
            onboarding_assistant=onboarding_assistant,
            issue_groups=issue_groups,
            startup_progress=startup_progress,
            recovery_assistant=recovery_assistant,
        ),
        banner=_build_banner(mode=mode, issue_groups=issue_groups),
        onboarding_card=_build_onboarding_card(empty_state=empty_state, onboarding_assistant=onboarding_assistant),
        issue_groups=issue_groups,
    )
