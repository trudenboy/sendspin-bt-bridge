"""Unified operator guidance built from onboarding, capability, and recovery data."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from services._helpers import _device_extra, _parse_timestamp
from services.guidance_issue_registry import build_issue_context, issue_sort_priority
from services.recovery_assistant import RecoveryAction, build_recovery_issue_actions

UTC = timezone.utc

_ONBOARDING_VISIBILITY_KEY = "sendspin-ui:show-onboarding-guidance"
_RECOVERY_VISIBILITY_KEY = "sendspin-ui:show-recovery-guidance"
_DEFAULT_STARTUP_BANNER_GRACE_SECONDS = 10


@dataclass
class GuidanceAction:
    key: str
    label: str
    device_names: list[str] = field(default_factory=list)
    check_key: str | None = None
    value: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"key": self.key, "label": self.label}
        if self.device_names:
            payload["device_names"] = list(self.device_names)
        if self.check_key:
            payload["check_key"] = self.check_key
        if self.value is not None:
            payload["value"] = self.value
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
    context: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "key": self.key,
            "severity": self.severity,
            "title": self.title,
            "summary": self.summary,
            "count": self.count,
            "device_names": list(self.device_names),
            "secondary_actions": [action.to_dict() for action in self.secondary_actions],
            "context": dict(self.context),
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
    show_by_default: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "headline": self.headline,
            "summary": self.summary,
            "checklist": dict(self.checklist),
            "dismissible": self.dismissible,
            "preference_key": self.preference_key,
            "secondary_actions": [action.to_dict() for action in self.secondary_actions],
            "show_by_default": self.show_by_default,
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


def _startup_banner_grace_seconds(config: dict[str, Any] | None) -> int:
    if not isinstance(config, dict):
        return _DEFAULT_STARTUP_BANNER_GRACE_SECONDS
    raw_value = config.get("STARTUP_BANNER_GRACE_SECONDS", _DEFAULT_STARTUP_BANNER_GRACE_SECONDS)
    try:
        grace_seconds = int(raw_value)
    except (TypeError, ValueError):
        return _DEFAULT_STARTUP_BANNER_GRACE_SECONDS
    return max(0, min(grace_seconds, 300))


def _startup_banner_cooldown_active(startup_progress: dict[str, Any], config: dict[str, Any] | None = None) -> bool:
    startup_status = str(startup_progress.get("status") or "idle")
    if startup_status in {"running", "starting"}:
        return True
    if startup_status not in {"ready", "complete", "error"}:
        return False
    grace_seconds = _startup_banner_grace_seconds(config)
    if grace_seconds <= 0:
        return False
    completed_at = _parse_timestamp(
        startup_progress.get("completed_at") or startup_progress.get("updated_at") or startup_progress.get("started_at")
    )
    if completed_at is None:
        return False
    return (datetime.now(tz=UTC) - completed_at) < timedelta(seconds=grace_seconds)


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


def _has_active_reconnect_attempt(device: Any) -> bool:
    extra = _device_extra(device)
    return int(extra.get("reconnect_attempt") or 0) > 0


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
        check_key=str(payload.get("check_key") or "").strip() or None,
        value=payload.get("value"),
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
                check_key=action.check_key,
                value=action.value,
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
    reason_codes: list[str] | None = None,
    all_devices_affected: bool | None = None,
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
            context=build_issue_context(
                key,
                severity=severity,
                device_names=names,
                reason_codes=reason_codes,
                all_devices_affected=all_devices_affected,
            ),
        )
    )


def _onboarding_check_by_key(onboarding_assistant: dict[str, Any], key: str) -> dict[str, Any] | None:
    checks = onboarding_assistant.get("checks") or []
    return next(
        (check for check in checks if isinstance(check, dict) and str(check.get("key") or "") == key),
        None,
    )


def _runtime_access_issue(
    onboarding_assistant: dict[str, Any],
    *,
    all_devices_globally_disabled: bool,
) -> dict[str, Any] | None:
    runtime_check = _onboarding_check_by_key(onboarding_assistant, "runtime_access")
    if not isinstance(runtime_check, dict) or str(runtime_check.get("status") or "") != "error":
        return None

    summary = str(
        runtime_check.get("summary")
        or "The bridge runtime cannot reach the host services required for Bluetooth control."
    )
    if all_devices_globally_disabled:
        summary = (
            "The bridge runtime cannot reach the host services it needs right now. "
            "Restore runtime host access before re-enabling speakers or playback will stay unavailable."
        )

    actions = [str(item) for item in (runtime_check.get("actions") or []) if str(item).strip()]
    if all_devices_globally_disabled:
        actions.append("After runtime access is restored, re-enable at least one speaker and restart the bridge.")

    secondary_actions = []
    if all_devices_globally_disabled:
        secondary_actions.append(GuidanceAction(key="open_devices_settings", label="Open device settings"))

    return {
        "title": "Host service access unavailable",
        "summary": summary,
        "details": dict(runtime_check.get("details") or {}),
        "actions": actions,
        "primary_action": GuidanceAction(key="open_diagnostics", label="Open diagnostics"),
        "secondary_actions": secondary_actions,
    }


def _bluetooth_access_issue(
    onboarding_assistant: dict[str, Any],
    *,
    all_devices_globally_disabled: bool,
    disabled_devices: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    bluetooth_check = _onboarding_check_by_key(onboarding_assistant, "bluetooth")
    if not isinstance(bluetooth_check, dict) or str(bluetooth_check.get("status") or "") != "error":
        return None

    disabled_count = len(disabled_devices or [])
    details = bluetooth_check.get("details") or {}
    paired_devices = int(details.get("paired_devices") or 0)
    summary = str(bluetooth_check.get("summary") or "No Bluetooth controller detected by preflight checks.")
    if all_devices_globally_disabled:
        summary = (
            "The bridge cannot access a Bluetooth adapter right now. "
            "Restore adapter access before re-enabling speakers or playback will stay unavailable."
        )

    actions = [str(item) for item in (bluetooth_check.get("actions") or []) if str(item).strip()]
    if all_devices_globally_disabled:
        actions.append("After the adapter is visible again, re-enable at least one speaker and restart the bridge.")

    secondary_actions = []
    if all_devices_globally_disabled:
        secondary_actions.append(GuidanceAction(key="open_devices_settings", label="Open device settings"))

    return {
        "title": "Bluetooth adapter unavailable",
        "summary": summary,
        "details": {"paired_devices": paired_devices, "disabled_devices": disabled_count},
        "actions": actions,
        "primary_action": GuidanceAction(key="open_bluetooth_settings", label="Open adapter settings"),
        "secondary_actions": secondary_actions,
    }


def _audio_backend_issue(
    onboarding_assistant: dict[str, Any],
    *,
    all_devices_globally_disabled: bool,
) -> dict[str, Any] | None:
    audio_check = _onboarding_check_by_key(onboarding_assistant, "audio")
    if not isinstance(audio_check, dict) or str(audio_check.get("status") or "") != "error":
        return None

    summary = str(audio_check.get("summary") or "The bridge cannot reach its audio backend right now.")
    if all_devices_globally_disabled:
        summary = (
            "The bridge cannot reach its audio backend right now. "
            "Restore audio access before re-enabling speakers or playback will stay unavailable."
        )

    actions = [str(item) for item in (audio_check.get("actions") or []) if str(item).strip()]
    if all_devices_globally_disabled:
        actions.append("After audio access is restored, re-enable at least one speaker and restart the bridge.")

    secondary_actions = []
    if all_devices_globally_disabled:
        secondary_actions.append(GuidanceAction(key="open_devices_settings", label="Open device settings"))

    return {
        "title": "Audio backend unavailable",
        "summary": summary,
        "details": dict(audio_check.get("details") or {}),
        "actions": actions,
        "primary_action": GuidanceAction(key="open_diagnostics", label="Open diagnostics"),
        "secondary_actions": secondary_actions,
    }


def _disabled_unpaired_bluetooth_guidance(
    onboarding_assistant: dict[str, Any],
    *,
    all_devices_globally_disabled: bool,
) -> dict[str, Any] | None:
    if not all_devices_globally_disabled:
        return None
    checklist = onboarding_assistant.get("checklist") or {}
    if str(checklist.get("current_step_key") or "") != "bluetooth":
        return None
    bluetooth_check = _onboarding_check_by_key(onboarding_assistant, "bluetooth")
    if not isinstance(bluetooth_check, dict) or str(bluetooth_check.get("status") or "") != "warning":
        return None
    details = bluetooth_check.get("details") or {}
    if int(details.get("paired_devices") or 0) != 0:
        return None

    configured_devices = int(details.get("configured_devices") or 0)
    device_label = "speaker" if configured_devices == 1 else "speakers"
    summary = (
        f"The saved {device_label} is disabled, and no paired Bluetooth {device_label} "
        "is currently available to the bridge. Pair or rediscover it first, then re-enable it in Configuration → Devices."
    )
    return {
        "title": "No playable speaker available",
        "summary": summary,
        "actions": [
            "Open Bluetooth scan and pair or rediscover the speaker first.",
            "After it appears again, re-enable it in Configuration → Devices and restart the bridge.",
        ],
        "primary_action": GuidanceAction(key="scan_devices", label="Scan for speakers"),
        "secondary_actions": [
            GuidanceAction(key="open_devices_settings", label="Open device settings"),
            GuidanceAction(key="open_diagnostics", label="Open diagnostics"),
        ],
    }


def _issue_group_sort_key(item: GuidanceIssueGroup) -> tuple[int, int, int, str]:
    severity_rank = 0 if item.severity == "error" else 1
    priority = issue_sort_priority(item.key)
    return (severity_rank, priority, -item.count, item.title)


def _build_issue_groups(
    devices: list[Any],
    onboarding_assistant: dict[str, Any],
    recovery_assistant: dict[str, Any],
    *,
    all_devices_globally_disabled: bool = False,
    disabled_devices: list[dict[str, Any]] | None = None,
    runtime_access_issue: dict[str, Any] | None = None,
    bluetooth_access_issue: dict[str, Any] | None = None,
    audio_backend_issue: dict[str, Any] | None = None,
) -> list[GuidanceIssueGroup]:
    groups: list[GuidanceIssueGroup] = []
    checklist = onboarding_assistant.get("checklist") or {}
    current_step_key = str(checklist.get("current_step_key") or "")
    overall_status = str(checklist.get("overall_status") or "")
    if runtime_access_issue and current_step_key == "runtime_access":
        _append_group(
            groups,
            key="runtime_access",
            severity="error",
            title=runtime_access_issue["title"],
            summary=runtime_access_issue["summary"],
            primary_action=runtime_access_issue["primary_action"],
            secondary_actions=runtime_access_issue["secondary_actions"],
        )
    elif bluetooth_access_issue and current_step_key == "bluetooth":
        _append_group(
            groups,
            key="bluetooth_unavailable",
            severity="error",
            title=bluetooth_access_issue["title"],
            summary=bluetooth_access_issue["summary"],
            primary_action=bluetooth_access_issue["primary_action"],
            secondary_actions=bluetooth_access_issue["secondary_actions"],
        )
    elif audio_backend_issue and current_step_key == "audio":
        _append_group(
            groups,
            key="audio_unavailable",
            severity="error",
            title=audio_backend_issue["title"],
            summary=audio_backend_issue["summary"],
            primary_action=audio_backend_issue["primary_action"],
            secondary_actions=audio_backend_issue["secondary_actions"],
        )
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
        disconnected_device = None
        if len(disconnected) == 1:
            disconnected_device = next(
                (
                    device
                    for device in devices
                    if str(getattr(device, "player_name", None) or "Unknown") == disconnected[0]
                ),
                None,
            )
            attempt_summary = _reconnect_attempt_summary(disconnected_device) if disconnected_device is not None else ""
            if attempt_summary:
                summary = f"{summary} {attempt_summary}"
        primary_action, secondary_actions = _guidance_actions_from_recovery("disconnected", disconnected)
        if (
            len(disconnected) == 1
            and disconnected_device is not None
            and _has_active_reconnect_attempt(disconnected_device)
        ):
            reconnect_action = primary_action
            primary_action = GuidanceAction(
                key="toggle_bt_management",
                label="Release Bluetooth",
                device_names=disconnected,
            )
            if reconnect_action is not None:
                secondary_actions = [reconnect_action, *secondary_actions]
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

    if current_step_key == "latency" and overall_status in {"warning", "error"}:
        latency_assistant = recovery_assistant.get("latency_assistant") or {}
        latency_actions = [_guidance_action_from_dict(item) for item in latency_assistant.get("safe_actions") or []]
        valid_latency_actions = [item for item in latency_actions if item is not None]
        primary_action = _guidance_action_from_dict(checklist.get("primary_action")) or (
            valid_latency_actions[0]
            if valid_latency_actions
            else GuidanceAction(key="open_latency_settings", label="Review latency settings")
        )
        secondary = [
            action
            for action in valid_latency_actions
            if action.key != primary_action.key
            or action.device_names != primary_action.device_names
            or action.check_key != primary_action.check_key
            or action.value != primary_action.value
        ]
        _append_group(
            groups,
            key="latency",
            severity="warning",
            title="Latency tuning needs attention",
            summary=str(
                latency_assistant.get("summary")
                or checklist.get("summary")
                or "Latency guidance recommends another tuning pass."
            ),
            primary_action=primary_action,
            secondary_actions=secondary,
        )

    groups.sort(key=_issue_group_sort_key)
    return groups


def _build_onboarding_card(
    *,
    empty_state: bool,
    all_devices_globally_disabled: bool,
    all_devices_user_released: bool,
    disabled_devices: list[dict[str, Any]] | None,
    released_devices: list[Any] | None,
    onboarding_assistant: dict[str, Any],
    runtime_access_issue: dict[str, Any] | None = None,
    bluetooth_access_issue: dict[str, Any] | None = None,
    audio_backend_issue: dict[str, Any] | None = None,
    mixed_bluetooth_guidance: dict[str, Any] | None = None,
) -> GuidanceOnboardingCard | None:
    checklist = onboarding_assistant.get("checklist") or {}
    if not checklist:
        return None
    card_checklist = checklist
    secondary_actions = [GuidanceAction(key="open_diagnostics", label="Open diagnostics")]
    current_step_key = str(checklist.get("current_step_key") or "")
    if runtime_access_issue and current_step_key == "runtime_access":
        card_checklist = dict(checklist)
        card_checklist["headline"] = "Restore host service access first"
        card_checklist["summary"] = runtime_access_issue["summary"]
        card_checklist["primary_action"] = runtime_access_issue["primary_action"].to_dict()
        card_checklist["current_step_key"] = "runtime_access"
        card_checklist["current_step_title"] = "Verify runtime host access"
        steps = card_checklist.get("steps")
        if isinstance(steps, list):
            adapted_steps = []
            for step in steps:
                if not isinstance(step, dict):
                    adapted_steps.append(step)
                    continue
                adapted_step = dict(step)
                if adapted_step.get("key") == "runtime_access":
                    adapted_step.update(
                        {
                            "title": "Verify runtime host access",
                            "status": "error",
                            "stage": "current",
                            "summary": runtime_access_issue["summary"],
                            "details": runtime_access_issue["details"],
                            "actions": list(runtime_access_issue["actions"]),
                            "recommended_action": runtime_access_issue["primary_action"].to_dict(),
                        }
                    )
                elif adapted_step.get("stage") == "current":
                    adapted_step["stage"] = "upcoming"
                adapted_steps.append(adapted_step)
            card_checklist["steps"] = adapted_steps
        secondary_actions = list(runtime_access_issue["secondary_actions"]) + secondary_actions
    elif bluetooth_access_issue and current_step_key == "bluetooth":
        card_checklist = dict(checklist)
        card_checklist["headline"] = "Restore Bluetooth adapter access first"
        card_checklist["summary"] = bluetooth_access_issue["summary"]
        card_checklist["primary_action"] = bluetooth_access_issue["primary_action"].to_dict()
        card_checklist["current_step_key"] = "bluetooth"
        card_checklist["current_step_title"] = "Check Bluetooth access"
        steps = card_checklist.get("steps")
        if isinstance(steps, list):
            adapted_steps = []
            for step in steps:
                if not isinstance(step, dict):
                    adapted_steps.append(step)
                    continue
                adapted_step = dict(step)
                if adapted_step.get("key") == "bluetooth":
                    adapted_step.update(
                        {
                            "title": "Check Bluetooth access",
                            "status": "error",
                            "stage": "current",
                            "summary": bluetooth_access_issue["summary"],
                            "details": bluetooth_access_issue["details"],
                            "actions": list(bluetooth_access_issue["actions"]),
                            "recommended_action": bluetooth_access_issue["primary_action"].to_dict(),
                        }
                    )
                elif adapted_step.get("stage") == "current":
                    adapted_step["stage"] = "upcoming"
                adapted_steps.append(adapted_step)
            card_checklist["steps"] = adapted_steps
        secondary_actions = list(bluetooth_access_issue["secondary_actions"]) + secondary_actions
    elif mixed_bluetooth_guidance:
        card_checklist = dict(checklist)
        card_checklist["headline"] = "Pair or rediscover a speaker first"
        card_checklist["summary"] = mixed_bluetooth_guidance["summary"]
        card_checklist["primary_action"] = mixed_bluetooth_guidance["primary_action"].to_dict()
        card_checklist["current_step_key"] = "bluetooth"
        card_checklist["current_step_title"] = "Pair or rediscover a speaker"
        steps = card_checklist.get("steps")
        if isinstance(steps, list):
            adapted_steps = []
            for step in steps:
                if not isinstance(step, dict):
                    adapted_steps.append(step)
                    continue
                adapted_step = dict(step)
                if adapted_step.get("key") == "bluetooth":
                    adapted_step.update(
                        {
                            "title": "Pair or rediscover a speaker",
                            "status": "warning",
                            "stage": "current",
                            "summary": mixed_bluetooth_guidance["summary"],
                            "actions": list(mixed_bluetooth_guidance["actions"]),
                            "recommended_action": mixed_bluetooth_guidance["primary_action"].to_dict(),
                        }
                    )
                elif adapted_step.get("stage") == "current":
                    adapted_step["stage"] = "upcoming"
                adapted_steps.append(adapted_step)
            card_checklist["steps"] = adapted_steps
        secondary_actions = list(mixed_bluetooth_guidance["secondary_actions"])
    elif all_devices_globally_disabled and current_step_key == "bridge_control":
        disabled_count = len(disabled_devices or [])
        card_checklist = dict(checklist)
        card_checklist["headline"] = "Re-enable a speaker to resume playback"
        card_checklist["summary"] = (
            "All configured Bluetooth devices are currently disabled. "
            "Re-enable at least one device in Configuration → Devices, then save and restart the bridge."
        )
        card_checklist["primary_action"] = {"key": "open_devices_settings", "label": "Open device settings"}
        card_checklist["current_step_key"] = "bridge_control"
        card_checklist["current_step_title"] = "Re-enable a speaker"
        steps = card_checklist.get("steps")
        if isinstance(steps, list):
            adapted_steps = []
            for step in steps:
                if not isinstance(step, dict):
                    adapted_steps.append(step)
                    continue
                adapted_step = dict(step)
                if adapted_step.get("key") == "bridge_control":
                    adapted_step.update(
                        {
                            "title": "Re-enable a speaker",
                            "status": "warning",
                            "stage": "current",
                            "summary": "All configured speakers are globally disabled right now.",
                            "details": {"configured_devices": disabled_count},
                            "actions": [
                                "Open Configuration → Devices and turn at least one speaker back on.",
                                "Click Save and restart so the bridge reloads the enabled devices.",
                            ],
                            "recommended_action": {
                                "key": "open_devices_settings",
                                "label": "Open device settings",
                            },
                        }
                    )
                elif adapted_step.get("stage") == "current":
                    adapted_step["stage"] = "upcoming"
                adapted_steps.append(adapted_step)
            card_checklist["steps"] = adapted_steps
    elif all_devices_user_released and current_step_key == "bridge_control":
        released_names = [str(getattr(device, "player_name", None) or "Unknown") for device in (released_devices or [])]
        released_count = len(released_names)
        action_key = "toggle_bt_management" if released_count == 1 else "toggle_bt_management_devices"
        action_label = "Reclaim speaker" if released_count == 1 else f"Reclaim {released_count} devices"
        card_checklist = dict(checklist)
        card_checklist["headline"] = "Reclaim a speaker to resume playback"
        card_checklist["summary"] = (
            "All configured Bluetooth devices are currently released from bridge management. "
            "Reclaim at least one speaker so the bridge can resume playback."
        )
        card_checklist["primary_action"] = {
            "key": action_key,
            "label": action_label,
            "device_names": released_names,
        }
        card_checklist["current_step_key"] = "bridge_control"
        card_checklist["current_step_title"] = "Reclaim a speaker"
        steps = card_checklist.get("steps")
        if isinstance(steps, list):
            adapted_steps = []
            for step in steps:
                if not isinstance(step, dict):
                    adapted_steps.append(step)
                    continue
                adapted_step = dict(step)
                if adapted_step.get("key") == "bridge_control":
                    adapted_step.update(
                        {
                            "title": "Reclaim a speaker",
                            "status": "warning",
                            "stage": "current",
                            "summary": "All configured speakers are currently released from bridge management.",
                            "details": {"configured_devices": released_count},
                            "actions": [
                                "Use Reclaim to hand at least one speaker back to the bridge.",
                                "If reclaim does not restore playback, open diagnostics or Configuration → Devices to inspect the speaker state.",
                            ],
                            "recommended_action": {
                                "key": action_key,
                                "label": action_label,
                                "device_names": released_names,
                            },
                        }
                    )
                elif adapted_step.get("stage") == "current":
                    adapted_step["stage"] = "upcoming"
                adapted_steps.append(adapted_step)
            card_checklist["steps"] = adapted_steps
        secondary_actions = [
            GuidanceAction(key="open_devices_settings", label="Open device settings"),
            GuidanceAction(key="open_diagnostics", label="Open diagnostics"),
        ]
    primary_action = _guidance_action_from_dict(card_checklist.get("primary_action"))
    return GuidanceOnboardingCard(
        headline=str(card_checklist.get("headline") or "Get started"),
        summary=str(card_checklist.get("summary") or "Finish the first-run checklist."),
        checklist=card_checklist,
        dismissible=True,
        preference_key=_ONBOARDING_VISIBILITY_KEY,
        primary_action=primary_action,
        secondary_actions=secondary_actions,
        show_by_default=(
            empty_state
            or mixed_bluetooth_guidance is not None
            or (all_devices_globally_disabled and current_step_key == "bridge_control")
            or (all_devices_user_released and current_step_key == "bridge_control")
            or (runtime_access_issue is not None and current_step_key == "runtime_access")
            or (bluetooth_access_issue is not None and current_step_key == "bluetooth")
            or (audio_backend_issue is not None and current_step_key == "audio")
        ),
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
    disabled_devices: list[dict[str, Any]] | None = None,
    mixed_bluetooth_guidance: dict[str, Any] | None = None,
) -> GuidanceHeaderStatus:
    configured_adapters = _count_config_entries(config, "BLUETOOTH_ADAPTERS")
    configured_devices = _count_config_entries(config, "BLUETOOTH_DEVICES")
    checklist = onboarding_assistant.get("checklist") or {}
    active_devices = [device for device in devices if getattr(device, "bt_management_enabled", True)]
    released_devices = [device for device in devices if getattr(device, "bt_management_enabled", True) is False]
    disabled_count = len(disabled_devices or [])
    all_devices_globally_disabled = configured_devices > 0 and not devices and disabled_count >= configured_devices
    lead_issue = issue_groups[0] if issue_groups else None
    startup_status = str(startup_progress.get("status") or "idle")
    if startup_status in {"running", "starting"}:
        percent = int(startup_progress.get("percent") or 0)
        return GuidanceHeaderStatus(
            tone="info",
            label=f"Startup {percent}%",
            summary=str(startup_progress.get("message") or "Bridge startup checks are still running."),
        )
    if _startup_banner_cooldown_active(startup_progress, config):
        return GuidanceHeaderStatus(
            tone="info",
            label="Startup 90%",
            summary="Finalizing Startup",
        )
    if lead_issue and lead_issue.key in {"runtime_access", "bluetooth_unavailable", "audio_unavailable"}:
        return GuidanceHeaderStatus(
            tone=lead_issue.severity,
            label=lead_issue.title,
            summary=lead_issue.summary,
        )
    if mixed_bluetooth_guidance:
        return GuidanceHeaderStatus(
            tone="warning",
            label=mixed_bluetooth_guidance["title"],
            summary=mixed_bluetooth_guidance["summary"],
        )
    if all_devices_globally_disabled:
        return GuidanceHeaderStatus(
            tone="neutral",
            label="All devices disabled",
            summary="All configured Bluetooth devices are globally disabled. Re-enable a device in Configuration → Devices.",
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
    disabled_devices: list[dict[str, Any]] | None = None,
) -> OperatorGuidanceSnapshot:
    configured_devices = _count_config_entries(config, "BLUETOOTH_DEVICES")
    checklist = onboarding_assistant.get("checklist") or {}
    disabled_count = len(disabled_devices or [])
    all_devices_globally_disabled = configured_devices > 0 and not devices and disabled_count >= configured_devices

    # Pre-compute expensive guidance checks once; pass results to sub-builders.
    runtime_access_issue = _runtime_access_issue(
        onboarding_assistant,
        all_devices_globally_disabled=all_devices_globally_disabled,
    )
    bluetooth_access_issue = _bluetooth_access_issue(
        onboarding_assistant,
        all_devices_globally_disabled=all_devices_globally_disabled,
        disabled_devices=disabled_devices,
    )
    audio_backend_issue = _audio_backend_issue(
        onboarding_assistant,
        all_devices_globally_disabled=all_devices_globally_disabled,
    )
    mixed_bluetooth_guidance = _disabled_unpaired_bluetooth_guidance(
        onboarding_assistant,
        all_devices_globally_disabled=all_devices_globally_disabled,
    )

    issue_groups = _build_issue_groups(
        devices,
        onboarding_assistant,
        recovery_assistant,
        all_devices_globally_disabled=all_devices_globally_disabled,
        disabled_devices=disabled_devices,
        runtime_access_issue=runtime_access_issue,
        bluetooth_access_issue=bluetooth_access_issue,
        audio_backend_issue=audio_backend_issue,
    )
    startup_status = str(startup_progress.get("status") or "idle")
    startup_banner_cooldown_active = _startup_banner_cooldown_active(startup_progress, config)
    active_devices = [device for device in devices if getattr(device, "bt_management_enabled", True)]
    released_devices = [device for device in devices if getattr(device, "bt_management_enabled", True) is False]
    user_released_devices = [
        device for device in released_devices if _device_extra(device).get("bt_released_by") != "auto"
    ]
    all_devices_user_released = (
        configured_devices > 0 and not active_devices and len(user_released_devices) >= configured_devices
    )

    empty_state = configured_devices == 0
    if empty_state:
        mode = "empty_state"
    elif startup_banner_cooldown_active:
        mode = "progress"
    elif issue_groups:
        mode = "attention"
    elif all_devices_globally_disabled or (not active_devices and released_devices):
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
            disabled_devices=disabled_devices,
            mixed_bluetooth_guidance=mixed_bluetooth_guidance,
        ),
        banner=_build_banner(mode=mode, issue_groups=issue_groups),
        onboarding_card=_build_onboarding_card(
            empty_state=empty_state,
            all_devices_globally_disabled=all_devices_globally_disabled,
            all_devices_user_released=all_devices_user_released,
            disabled_devices=disabled_devices,
            released_devices=user_released_devices,
            onboarding_assistant=onboarding_assistant,
            runtime_access_issue=runtime_access_issue,
            bluetooth_access_issue=bluetooth_access_issue,
            audio_backend_issue=audio_backend_issue,
            mixed_bluetooth_guidance=mixed_bluetooth_guidance,
        ),
        issue_groups=issue_groups,
    )
