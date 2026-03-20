"""Unified operator guidance built from onboarding, capability, and recovery data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from services.recovery_assistant import RecoveryAction, build_recovery_issue_actions

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
        return f"Reconnect attempt {attempt}/{threshold}; {remaining} attempts remain before auto-release."
    return f"Reconnect attempt {attempt} is in progress."


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


def _guidance_action_from_recovery(action: RecoveryAction | None) -> GuidanceAction | None:
    if action is None:
        return None
    payload = action.to_dict()
    if payload.get("device_name") and not payload.get("device_names"):
        payload["device_names"] = [payload["device_name"]]
    return _guidance_action_from_dict(payload)


def _guidance_actions_from_recovery(
    issue_key: str,
    device_names: list[str],
    *,
    extra_secondary_actions: list[GuidanceAction] | None = None,
) -> tuple[GuidanceAction | None, list[GuidanceAction]]:
    recovery_secondary = []
    for action in extra_secondary_actions or []:
        recovery_secondary.append(
            RecoveryAction(
                key=action.key,
                label=action.label,
                device_name=action.device_names[0] if len(action.device_names) == 1 else None,
                device_names=list(action.device_names) if len(action.device_names) > 1 else [],
            )
        )
    primary_action, secondary_actions = build_recovery_issue_actions(
        issue_key,
        device_names,
        extra_secondary_actions=recovery_secondary,
    )
    return _guidance_action_from_recovery(primary_action), [
        action for action in (_guidance_action_from_recovery(item) for item in secondary_actions) if action is not None
    ]


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
    repair_needed = [
        device
        for device in devices
        if getattr(device, "bt_management_enabled", True)
        and not getattr(device, "bluetooth_connected", False)
        and _device_extra(device).get("bluetooth_paired") is False
    ]
    disconnected = [
        str(getattr(device, "player_name", None) or "Unknown")
        for device in devices
        if getattr(device, "bt_management_enabled", True)
        and not getattr(device, "bluetooth_connected", False)
        and _device_extra(device).get("bluetooth_paired") is not False
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
    auto_released = [
        str(getattr(device, "player_name", None) or "Unknown")
        for device in devices
        if getattr(device, "bt_management_enabled", True) is False
        and _device_extra(device).get("bt_released_by") == "auto"
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
        primary_action, secondary_actions = _guidance_actions_from_recovery("missing_sink", missing_sink)
        _append_group(
            groups,
            key="missing_sink",
            severity="error",
            title=title,
            summary=summary,
            device_names=missing_sink,
            primary_action=primary_action,
            secondary_actions=secondary_actions,
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
        primary_action, secondary_actions = _guidance_actions_from_recovery("transport_down", transport_down)
        _append_group(
            groups,
            key="transport_down",
            severity="error",
            title=title,
            summary=summary,
            device_names=transport_down,
            primary_action=primary_action,
            secondary_actions=secondary_actions,
        )
    if repair_needed:
        repair_names = [str(getattr(device, "player_name", None) or "Unknown") for device in repair_needed]
        title = (
            f"{repair_names[0]} needs re-pairing"
            if len(repair_names) == 1
            else f"{len(repair_names)} devices need re-pairing"
        )
        attempt_summary = _reconnect_attempt_summary(repair_needed[0]) if len(repair_needed) == 1 else ""
        summary = (
            "The speaker is no longer paired, so reconnect attempts will keep failing. Put it in pairing mode and run re-pair."
            if len(repair_names) == 1
            else f"These speakers are no longer paired. Re-pair {_format_device_label(repair_names)} from pairing mode."
        )
        if attempt_summary:
            summary = f"{summary} {attempt_summary}"
        primary_action, secondary_actions = _guidance_actions_from_recovery("repair_required", repair_names)
        _append_group(
            groups,
            key="repair_required",
            severity="error",
            title=title,
            summary=summary,
            device_names=repair_names,
            primary_action=primary_action,
            secondary_actions=secondary_actions,
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
        if len(disconnected) == 1:
            attempt_summary = next(
                (
                    _reconnect_attempt_summary(device)
                    for device in devices
                    if str(getattr(device, "player_name", None) or "Unknown") == disconnected[0]
                ),
                "",
            )
            if attempt_summary:
                summary = f"{summary} {attempt_summary}"
        primary_action, secondary_actions = _guidance_actions_from_recovery("disconnected", disconnected)
        _append_group(
            groups,
            key="disconnected",
            severity="warning",
            title=title,
            summary=summary,
            device_names=disconnected,
            primary_action=primary_action,
            secondary_actions=secondary_actions,
        )
    if auto_released:
        title = (
            f"{auto_released[0]} was auto-released"
            if len(auto_released) == 1
            else f"{len(auto_released)} devices were auto-released"
        )
        summary = (
            "Bluetooth management was auto-released after repeated connection problems."
            if len(auto_released) == 1
            else f"Reclaim {_format_device_label(auto_released)} to restore bridge management."
        )
        primary_action, secondary_actions = _guidance_actions_from_recovery("auto_released", auto_released)
        _append_group(
            groups,
            key="auto_released",
            severity="warning",
            title=title,
            summary=summary,
            device_names=auto_released,
            primary_action=primary_action,
            secondary_actions=secondary_actions,
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
    devices: list[Any],
) -> GuidanceHeaderStatus:
    configured_adapters = _count_config_entries(config, "BLUETOOTH_ADAPTERS")
    configured_devices = _count_config_entries(config, "BLUETOOTH_DEVICES")
    checklist = onboarding_assistant.get("checklist") or {}
    active_devices = [device for device in devices if getattr(device, "bt_management_enabled", True)]
    released_devices = [device for device in devices if getattr(device, "bt_management_enabled", True) is False]
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
            label="First run" if configured_adapters == 0 else "Add first speaker",
            summary=(
                "Add your first Bluetooth adapter and speaker to start guided setup."
                if configured_adapters == 0
                else "Scan for and attach your first Bluetooth speaker to continue guided setup."
            ),
        )
    if issue_groups:
        lead = issue_groups[0]
        label = lead.title if len(issue_groups) == 1 else f"{len(issue_groups)} issues need attention"
        return GuidanceHeaderStatus(tone=lead.severity, label=label, summary=lead.summary)
    if not active_devices and released_devices:
        return GuidanceHeaderStatus(
            tone="neutral",
            label="Bluetooth released",
            summary="All configured devices are intentionally released from bridge management.",
        )
    if active_devices and configured_devices > 0 and str(checklist.get("overall_status") or "") != "ok":
        return GuidanceHeaderStatus(
            tone="warning",
            label=f"Setup {int(checklist.get('progress_percent') or 0)}%",
            summary=str(checklist.get("summary") or "Bridge setup still has pending steps."),
        )

    ready_devices = sum(1 for device in active_devices if getattr(device, "has_sink", False))
    connected_devices = sum(1 for device in active_devices if getattr(device, "bluetooth_connected", False))
    recovery_summary = recovery_assistant.get("summary") or {}
    if active_devices:
        return GuidanceHeaderStatus(
            tone="success",
            label=f"{ready_devices}/{len(active_devices)} active devices ready",
            summary=(
                "All active devices have sinks and are ready for playback."
                if ready_devices == len(active_devices) and active_devices
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
    configured_devices = _count_config_entries(config, "BLUETOOTH_DEVICES")
    checklist = onboarding_assistant.get("checklist") or {}
    issue_groups = _build_issue_groups(devices, onboarding_assistant, recovery_assistant)
    startup_status = str(startup_progress.get("status") or "idle")
    active_devices = [device for device in devices if getattr(device, "bt_management_enabled", True)]
    released_devices = [device for device in devices if getattr(device, "bt_management_enabled", True) is False]

    empty_state = configured_devices == 0
    if empty_state:
        mode = "empty_state"
    elif issue_groups:
        mode = "attention"
    elif not active_devices and released_devices:
        mode = "healthy"
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
            devices=devices,
        ),
        banner=_build_banner(mode=mode, issue_groups=issue_groups),
        onboarding_card=_build_onboarding_card(empty_state=empty_state, onboarding_assistant=onboarding_assistant),
        issue_groups=issue_groups,
    )
