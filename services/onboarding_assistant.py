"""Operator-facing onboarding and diagnostics guidance helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc
_CHECKLIST_ORDER = ("bluetooth", "audio", "sink_verification", "ma_auth", "latency")
_CHECKLIST_TITLES = {
    "bluetooth": "Check Bluetooth access",
    "audio": "Verify audio backend",
    "sink_verification": "Attach your first speaker",
    "ma_auth": "Connect Music Assistant",
    "latency": "Review latency tuning",
}


@dataclass
class OnboardingChecklistAction:
    key: str
    label: str

    def to_dict(self) -> dict[str, str]:
        return {"key": self.key, "label": self.label}


@dataclass
class OnboardingCheckpoint:
    key: str
    label: str
    reached: bool
    summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "reached": self.reached,
            "summary": self.summary,
        }


@dataclass
class OnboardingChecklistStep:
    key: str
    title: str
    status: str
    stage: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)
    recommended_action: OnboardingChecklistAction | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "key": self.key,
            "title": self.title,
            "status": self.status,
            "stage": self.stage,
            "summary": self.summary,
            "details": dict(self.details),
            "actions": list(self.actions),
        }
        if self.recommended_action:
            payload["recommended_action"] = self.recommended_action.to_dict()
        return payload


@dataclass
class OnboardingChecklistSnapshot:
    overall_status: str
    headline: str
    summary: str
    progress_percent: int
    completed_steps: int
    total_steps: int
    current_step_key: str | None
    current_step_title: str | None
    steps: list[OnboardingChecklistStep] = field(default_factory=list)
    checkpoints: list[OnboardingCheckpoint] = field(default_factory=list)
    primary_action: OnboardingChecklistAction | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "overall_status": self.overall_status,
            "headline": self.headline,
            "summary": self.summary,
            "progress_percent": self.progress_percent,
            "completed_steps": self.completed_steps,
            "total_steps": self.total_steps,
            "current_step_key": self.current_step_key,
            "current_step_title": self.current_step_title,
            "steps": [step.to_dict() for step in self.steps],
            "checkpoints": [checkpoint.to_dict() for checkpoint in self.checkpoints],
        }
        if self.primary_action:
            payload["primary_action"] = self.primary_action.to_dict()
        return payload


@dataclass
class OnboardingCheck:
    key: str
    status: str
    summary: str
    details: dict[str, Any] = field(default_factory=dict)
    actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "status": self.status,
            "summary": self.summary,
            "details": dict(self.details),
            "actions": list(self.actions),
        }


@dataclass
class OnboardingAssistantSnapshot:
    runtime_mode: str
    generated_at: str
    counts: dict[str, int]
    checks: list[OnboardingCheck]
    next_steps: list[str]
    checklist: OnboardingChecklistSnapshot | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "runtime_mode": self.runtime_mode,
            "generated_at": self.generated_at,
            "counts": dict(self.counts),
            "checks": [check.to_dict() for check in self.checks],
            "next_steps": list(self.next_steps),
        }
        if self.checklist:
            payload["checklist"] = self.checklist.to_dict()
        return payload


def _status_rank(status: str) -> int:
    return {"ok": 0, "warning": 1, "error": 2}.get(status, 1)


def _recommended_action_for_check(check: OnboardingCheck) -> OnboardingChecklistAction | None:
    if check.key == "bluetooth":
        return OnboardingChecklistAction(key="open_bluetooth_settings", label="Open Bluetooth settings")
    if check.key == "audio":
        return OnboardingChecklistAction(key="open_diagnostics", label="Open diagnostics")
    if check.key == "sink_verification":
        if int(check.details.get("configured_devices") or 0) == 0:
            return OnboardingChecklistAction(key="scan_devices", label="Scan for devices")
        if check.status == "error":
            return OnboardingChecklistAction(key="open_diagnostics", label="Open diagnostics")
        return OnboardingChecklistAction(key="open_devices_settings", label="Open device settings")
    if check.key == "ma_auth":
        return OnboardingChecklistAction(key="open_ma_settings", label="Open Music Assistant settings")
    if check.key == "latency":
        return OnboardingChecklistAction(key="open_devices_settings", label="Open device settings")
    return None


def _build_checkpoints(counts: dict[str, int], checks_by_key: dict[str, OnboardingCheck]) -> list[OnboardingCheckpoint]:
    configured_devices = int(counts.get("configured_devices") or 0)
    connected_devices = int(counts.get("connected_devices") or 0)
    sink_ready_devices = int(counts.get("sink_ready_devices") or 0)
    ma_ready = checks_by_key.get("ma_auth", OnboardingCheck(key="ma_auth", status="warning", summary="")).status == "ok"
    return [
        OnboardingCheckpoint(
            key="devices_configured",
            label="Bridge device created",
            reached=configured_devices > 0,
            summary=(
                f"{configured_devices} device{'s' if configured_devices != 1 else ''} configured"
                if configured_devices > 0
                else "Add your first Bluetooth device"
            ),
        ),
        OnboardingCheckpoint(
            key="bluetooth_connected",
            label="Bluetooth connected",
            reached=connected_devices > 0,
            summary=(
                f"{connected_devices} speaker{'s' if connected_devices != 1 else ''} connected"
                if connected_devices > 0
                else "Waiting for a configured speaker to connect"
            ),
        ),
        OnboardingCheckpoint(
            key="sink_ready",
            label="Audio sink ready",
            reached=sink_ready_devices > 0,
            summary=(
                f"{sink_ready_devices} sink{'s' if sink_ready_devices != 1 else ''} resolved"
                if sink_ready_devices > 0
                else "No connected speaker has a resolved sink yet"
            ),
        ),
        OnboardingCheckpoint(
            key="ma_visible",
            label="Music Assistant linked",
            reached=ma_ready,
            summary="Music Assistant is connected" if ma_ready else "Music Assistant still needs attention",
        ),
    ]


def _build_onboarding_checklist(checks: list[OnboardingCheck], counts: dict[str, int]) -> OnboardingChecklistSnapshot:
    checks_by_key = {check.key: check for check in checks}
    ordered_checks = [checks_by_key[key] for key in _CHECKLIST_ORDER if key in checks_by_key]
    ordered_checks.extend(check for check in checks if check.key not in _CHECKLIST_ORDER)

    current_check = next((check for check in ordered_checks if check.status != "ok"), None)
    total_steps = len(ordered_checks)

    if any(check.status == "error" for check in ordered_checks):
        overall_status = "error"
    elif any(check.status == "warning" for check in ordered_checks):
        overall_status = "warning"
    else:
        overall_status = "ok"

    if current_check:
        headline = (
            f"Finish setup: {_CHECKLIST_TITLES.get(current_check.key, current_check.key)}"
            if current_check.status == "error"
            else f"Next recommended step: {_CHECKLIST_TITLES.get(current_check.key, current_check.key)}"
        )
        summary = current_check.summary
        current_step_key = current_check.key
        current_step_title = _CHECKLIST_TITLES.get(current_check.key, current_check.key)
        primary_action = _recommended_action_for_check(current_check)
    else:
        headline = "Bridge setup looks ready"
        summary = "Core setup checks are green and the bridge is ready for first playback."
        current_step_key = None
        current_step_title = None
        primary_action = OnboardingChecklistAction(key="open_diagnostics", label="Review diagnostics")

    steps: list[OnboardingChecklistStep] = []
    completed_steps = 0
    current_step_reached = current_check is None
    for check in ordered_checks:
        if current_check is None:
            stage = "complete"
        elif not current_step_reached and check.key == current_check.key:
            stage = "current"
            current_step_reached = True
        elif not current_step_reached and check.status == "ok":
            stage = "complete"
        else:
            stage = "upcoming"
        if stage == "complete":
            completed_steps += 1
        steps.append(
            OnboardingChecklistStep(
                key=check.key,
                title=_CHECKLIST_TITLES.get(check.key, check.key),
                status=check.status,
                stage=stage,
                summary=check.summary,
                details=check.details,
                actions=check.actions,
                recommended_action=_recommended_action_for_check(check),
            )
        )
    progress_percent = int(round((completed_steps / total_steps) * 100)) if total_steps else 0

    return OnboardingChecklistSnapshot(
        overall_status=overall_status,
        headline=headline,
        summary=summary,
        progress_percent=progress_percent,
        completed_steps=completed_steps,
        total_steps=total_steps,
        current_step_key=current_step_key,
        current_step_title=current_step_title,
        steps=steps,
        checkpoints=_build_checkpoints(counts, checks_by_key),
        primary_action=primary_action,
    )


def build_onboarding_assistant_snapshot(
    *,
    config: dict[str, Any],
    preflight: dict[str, Any],
    devices: list[Any],
    ma_connected: bool,
    runtime_mode: str,
) -> OnboardingAssistantSnapshot:
    """Build operator guidance from preflight, config, and runtime device state."""
    configured_devices = config.get("BLUETOOTH_DEVICES", [])
    configured_count = len(configured_devices) if isinstance(configured_devices, list) else 0
    connected_devices = sum(1 for device in devices if getattr(device, "bluetooth_connected", False))
    sink_ready_devices = sum(
        1 for device in devices if getattr(device, "bluetooth_connected", False) and getattr(device, "has_sink", False)
    )
    missing_sink_devices = [
        getattr(device, "player_name", "Unknown")
        for device in devices
        if getattr(device, "bluetooth_connected", False) and not getattr(device, "has_sink", False)
    ]

    bluetooth = preflight.get("bluetooth", {})
    audio = preflight.get("audio", {})
    audio_system = str(audio.get("system") or "unknown")
    audio_sinks = int(audio.get("sinks") or 0)
    paired_devices = int(bluetooth.get("paired_devices") or 0)
    controller_present = bool(bluetooth.get("controller", False))

    ma_url = str(config.get("MA_API_URL") or "").strip()
    ma_token = str(config.get("MA_API_TOKEN") or "").strip()
    ma_username = str(config.get("MA_USERNAME") or "").strip()
    pulse_latency = int(config.get("PULSE_LATENCY_MSEC") or 0)
    custom_delays = sum(1 for device in devices if getattr(device, "static_delay_ms", None) not in (None, 0, 0.0))

    checks: list[OnboardingCheck] = []

    if not controller_present:
        checks.append(
            OnboardingCheck(
                key="bluetooth",
                status="error",
                summary="No Bluetooth controller detected by preflight checks.",
                details={"paired_devices": paired_devices},
                actions=[
                    "Verify Bluetooth adapter passthrough or host access.",
                    "Check that `bluetoothctl list` shows a controller inside the runtime.",
                ],
            )
        )
    elif configured_count > 0 and paired_devices == 0:
        checks.append(
            OnboardingCheck(
                key="bluetooth",
                status="warning",
                summary="Bluetooth controller is available, but no paired devices were found.",
                details={"paired_devices": paired_devices, "configured_devices": configured_count},
                actions=["Pair at least one speaker before assigning it in the bridge UI."],
            )
        )
    else:
        checks.append(
            OnboardingCheck(
                key="bluetooth",
                status="ok",
                summary="Bluetooth controller is visible to the bridge.",
                details={"paired_devices": paired_devices},
            )
        )

    if audio_system == "unknown":
        checks.append(
            OnboardingCheck(
                key="audio",
                status="error",
                summary="No PulseAudio or PipeWire server was detected.",
                details={"sinks": audio_sinks},
                actions=[
                    "Verify the audio socket mount and `PULSE_SERVER` configuration.",
                    "Confirm the runtime can access the host PulseAudio or PipeWire service.",
                ],
            )
        )
    elif audio_sinks == 0:
        checks.append(
            OnboardingCheck(
                key="audio",
                status="warning",
                summary="The audio server is reachable, but it currently exposes no sinks.",
                details={"system": audio_system, "sinks": audio_sinks},
                actions=["Connect a Bluetooth output and verify that `pactl list sinks short` shows it."],
            )
        )
    else:
        checks.append(
            OnboardingCheck(
                key="audio",
                status="ok",
                summary="The audio server is reachable and exposes sinks.",
                details={"system": audio_system, "sinks": audio_sinks},
            )
        )

    if configured_count == 0:
        checks.append(
            OnboardingCheck(
                key="sink_verification",
                status="warning",
                summary="No bridge devices are configured yet.",
                details={"configured_devices": configured_count},
                actions=["Add at least one Bluetooth device in the config UI before verifying sinks."],
            )
        )
    elif missing_sink_devices:
        checks.append(
            OnboardingCheck(
                key="sink_verification",
                status="error",
                summary="Some connected devices are missing a resolved Bluetooth sink.",
                details={
                    "connected_devices": connected_devices,
                    "sink_ready_devices": sink_ready_devices,
                    "missing_sink_devices": missing_sink_devices,
                },
                actions=[
                    "Reconnect the affected speakers and inspect diagnostics for sink recovery events.",
                    "Verify the audio system exposes a `bluez_*` sink for each connected speaker.",
                ],
            )
        )
    elif connected_devices == 0:
        checks.append(
            OnboardingCheck(
                key="sink_verification",
                status="warning",
                summary="Devices are configured, but none are currently connected over Bluetooth.",
                details={"configured_devices": configured_count},
                actions=["Power on a configured speaker and wait for the bridge to acquire its sink."],
            )
        )
    else:
        checks.append(
            OnboardingCheck(
                key="sink_verification",
                status="ok",
                summary="Connected devices have resolved Bluetooth sinks.",
                details={
                    "connected_devices": connected_devices,
                    "sink_ready_devices": sink_ready_devices,
                },
            )
        )

    if not ma_url:
        checks.append(
            OnboardingCheck(
                key="ma_auth",
                status="warning",
                summary="Music Assistant API URL is not configured.",
                actions=["Set `MA_API_URL` if you want group control and now-playing integration."],
            )
        )
    elif not ma_token and not ma_username:
        checks.append(
            OnboardingCheck(
                key="ma_auth",
                status="warning",
                summary="Music Assistant credentials are missing.",
                details={"configured_url": ma_url},
                actions=["Provide an API token or complete Music Assistant sign-in from the web UI."],
            )
        )
    elif ma_connected:
        checks.append(
            OnboardingCheck(
                key="ma_auth",
                status="ok",
                summary="Music Assistant integration is configured and connected.",
                details={"configured_url": ma_url},
            )
        )
    else:
        checks.append(
            OnboardingCheck(
                key="ma_auth",
                status="error",
                summary="Music Assistant credentials are configured, but the bridge is not connected.",
                details={"configured_url": ma_url, "has_token": bool(ma_token), "has_username": bool(ma_username)},
                actions=[
                    "Verify the Music Assistant URL, token, and network reachability.",
                    "Use diagnostics to confirm the MA server is reachable from the bridge runtime.",
                ],
            )
        )

    if configured_count < 2:
        checks.append(
            OnboardingCheck(
                key="latency",
                status="ok",
                summary="Single-device setups usually do not require extra latency calibration.",
                details={"pulse_latency_msec": pulse_latency, "custom_device_delays": custom_delays},
            )
        )
    elif custom_delays == 0:
        checks.append(
            OnboardingCheck(
                key="latency",
                status="warning",
                summary="Multi-device setup detected without per-device static delay tuning.",
                details={"pulse_latency_msec": pulse_latency, "configured_devices": configured_count},
                actions=[
                    "Measure drift between rooms and set `static_delay_ms` per device if playback feels out of sync."
                ],
            )
        )
    elif pulse_latency >= 800:
        checks.append(
            OnboardingCheck(
                key="latency",
                status="warning",
                summary="Latency tuning is present, but the global PulseAudio latency is quite high.",
                details={"pulse_latency_msec": pulse_latency, "custom_device_delays": custom_delays},
                actions=[
                    "Keep the high latency for VMs if needed, but lower it if playback reaction feels too sluggish."
                ],
            )
        )
    else:
        checks.append(
            OnboardingCheck(
                key="latency",
                status="ok",
                summary="Latency tuning is configured for a multi-device setup.",
                details={"pulse_latency_msec": pulse_latency, "custom_device_delays": custom_delays},
            )
        )

    next_steps: list[str] = []
    for check in sorted(checks, key=lambda item: _status_rank(item.status), reverse=True):
        for action in check.actions:
            if action not in next_steps:
                next_steps.append(action)

    counts = {
        "configured_devices": configured_count,
        "active_devices": len(devices),
        "connected_devices": connected_devices,
        "sink_ready_devices": sink_ready_devices,
    }

    return OnboardingAssistantSnapshot(
        runtime_mode=runtime_mode,
        generated_at=datetime.now(tz=UTC).isoformat(),
        counts=counts,
        checks=checks,
        next_steps=next_steps,
        checklist=_build_onboarding_checklist(checks, counts),
    )
