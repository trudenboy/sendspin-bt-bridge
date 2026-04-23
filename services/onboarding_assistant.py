"""Operator-facing onboarding and diagnostics guidance helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from services.bridge_state_model import BridgeStateModel, build_bridge_state_model

UTC = timezone.utc
_CHECKLIST_ORDER = ("runtime_access", "bluetooth", "audio", "bridge_control", "sink_verification", "ma_auth", "latency")
_CHECKLIST_PHASES = (
    ("foundation", "Foundation", ("runtime_access", "bluetooth", "audio")),
    ("first_speaker", "First speaker", ("bridge_control", "sink_verification")),
    ("music", "Music Assistant", ("ma_auth",)),
    ("tuning", "Fine-tuning", ("latency",)),
)
_CHECKLIST_TITLES = {
    "runtime_access": "Verify runtime host access",
    "bluetooth": "Check Bluetooth access",
    "audio": "Verify audio backend",
    "bridge_control": "Make a speaker available",
    "sink_verification": "Attach your first speaker",
    "ma_auth": "Connect Music Assistant",
    "latency": "Review latency tuning",
}


@dataclass
class OnboardingChecklistAction:
    key: str
    label: str
    device_names: list[str] = field(default_factory=list)
    check_key: str | None = None
    value: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"key": self.key, "label": self.label}
        if self.device_names:
            payload["device_names"] = [name for name in self.device_names if name]
        if self.check_key:
            payload["check_key"] = self.check_key
        if self.value is not None:
            payload["value"] = self.value
        return payload


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
class OnboardingChecklistPhase:
    key: str
    title: str
    status: str
    summary: str
    step_keys: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "status": self.status,
            "summary": self.summary,
            "step_keys": list(self.step_keys),
        }


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
    journey_key: str | None = None
    journey_title: str | None = None
    journey_summary: str | None = None
    steps: list[OnboardingChecklistStep] = field(default_factory=list)
    phases: list[OnboardingChecklistPhase] = field(default_factory=list)
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
            "journey_key": self.journey_key,
            "journey_title": self.journey_title,
            "journey_summary": self.journey_summary,
            "steps": [step.to_dict() for step in self.steps],
            "phases": [phase.to_dict() for phase in self.phases],
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


def _title_for_check(check: OnboardingCheck) -> str:
    if (
        check.key == "bluetooth"
        and check.status == "warning"
        and int(check.details.get("paired_devices") or 0) == 0
        and int(check.details.get("configured_devices") or 0) > 0
    ):
        return "Pair or rediscover a speaker"
    return _CHECKLIST_TITLES.get(check.key, check.key)


def _recommended_action_for_check(check: OnboardingCheck) -> OnboardingChecklistAction | None:
    if check.key == "runtime_access":
        return OnboardingChecklistAction(key="open_diagnostics", label="Open runtime diagnostics")
    if check.key == "bluetooth":
        if check.status == "error":
            return OnboardingChecklistAction(key="open_bluetooth_settings", label="Open adapter settings")
        if int(check.details.get("paired_devices") or 0) == 0:
            return OnboardingChecklistAction(key="scan_devices", label="Scan for speakers")
        return OnboardingChecklistAction(key="open_bluetooth_settings", label="Open Bluetooth settings")
    if check.key == "audio":
        return OnboardingChecklistAction(key="open_diagnostics", label="Open audio diagnostics")
    if check.key == "bridge_control":
        return OnboardingChecklistAction(key="open_devices_settings", label="Open device settings")
    if check.key == "sink_verification":
        if int(check.details.get("configured_devices") or 0) == 0:
            return OnboardingChecklistAction(key="scan_devices", label="Open device scan")
        if check.status == "error":
            return OnboardingChecklistAction(key="open_diagnostics", label="Open sink diagnostics")
        return OnboardingChecklistAction(key="open_devices_settings", label="Open device settings")
    if check.key == "ma_auth":
        if not str(check.details.get("configured_url") or "").strip():
            return OnboardingChecklistAction(key="retry_ma_discovery", label="Discover Music Assistant")
        return OnboardingChecklistAction(key="open_ma_settings", label="Open Music Assistant settings")
    if check.key == "latency":
        if check.status in {"warning", "error"} and int(check.details.get("configured_devices") or 0) >= 2:
            custom_delays = int(check.details.get("custom_device_delays") or 0)
            if custom_delays == 0:
                return OnboardingChecklistAction(key="open_devices_settings", label="Open device settings")
        recommended_latency = int(check.details.get("recommended_pulse_latency_msec") or 0)
        current_latency = int(check.details.get("pulse_latency_msec") or 0)
        if check.status in {"warning", "error"} and recommended_latency > 0 and recommended_latency != current_latency:
            return OnboardingChecklistAction(
                key="apply_latency_recommended",
                label=f"Apply {recommended_latency} ms latency",
                value=recommended_latency,
            )
        return OnboardingChecklistAction(key="open_latency_settings", label="Review Pulse latency")
    return None


def _build_checklist_phases(steps: list[OnboardingChecklistStep]) -> list[OnboardingChecklistPhase]:
    steps_by_key = {step.key: step for step in steps}
    phases: list[OnboardingChecklistPhase] = []
    for key, title, step_keys in _CHECKLIST_PHASES:
        phase_steps = [steps_by_key[step_key] for step_key in step_keys if step_key in steps_by_key]
        if not phase_steps:
            continue
        if any(step.stage == "current" for step in phase_steps):
            status = "current"
            current_step = next(step for step in phase_steps if step.stage == "current")
            summary = current_step.title
        elif all(step.stage == "complete" for step in phase_steps):
            status = "complete"
            summary = "All checks complete."
        else:
            status = "upcoming"
            pending_titles = [step.title for step in phase_steps if step.stage != "complete"]
            summary = pending_titles[0] if pending_titles else "Waiting for earlier setup steps."
        phases.append(
            OnboardingChecklistPhase(
                key=key,
                title=title,
                status=status,
                summary=summary,
                step_keys=list(step_keys),
            )
        )
    return phases


def _build_checkpoints(counts: dict[str, int], checks_by_key: dict[str, OnboardingCheck]) -> list[OnboardingCheckpoint]:
    configured_devices = int(counts.get("configured_devices") or 0)
    connected_devices = int(counts.get("connected_devices") or 0)
    standby_devices = int(counts.get("standby_devices") or 0)
    connected_or_standby = connected_devices + standby_devices
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
            reached=connected_or_standby > 0,
            summary=(
                f"{connected_devices} speaker{'s' if connected_devices != 1 else ''} connected"
                + (f" ({standby_devices} in standby)" if standby_devices else "")
                if connected_or_standby > 0
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
            f"Finish setup: {_title_for_check(current_check)}"
            if current_check.status == "error"
            else f"Next recommended step: {_title_for_check(current_check)}"
        )
        summary = current_check.summary
        current_step_key = current_check.key
        current_step_title = _title_for_check(current_check)
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
                title=_title_for_check(check),
                status=check.status,
                stage=stage,
                summary=check.summary,
                details=check.details,
                actions=check.actions,
                recommended_action=_recommended_action_for_check(check),
            )
        )
    progress_percent = int(round((completed_steps / total_steps) * 100)) if total_steps else 0
    phases = _build_checklist_phases(steps)
    configured_devices = int(counts.get("configured_devices") or 0)
    journey_key = "first_speaker" if configured_devices <= 1 else "multi_room"
    journey_title = "First speaker setup" if configured_devices <= 1 else "Expand the bridge room by room"
    journey_summary = (
        "Stabilize bridge access first, bring one speaker online, then link Music Assistant."
        if configured_devices <= 1
        else "Get the bridge foundation green, bring speakers online, then finish latency tuning."
    )

    return OnboardingChecklistSnapshot(
        overall_status=overall_status,
        headline=headline,
        summary=summary,
        progress_percent=progress_percent,
        completed_steps=completed_steps,
        total_steps=total_steps,
        current_step_key=current_step_key,
        current_step_title=current_step_title,
        journey_key=journey_key,
        journey_title=journey_title,
        journey_summary=journey_summary,
        steps=steps,
        phases=phases,
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
    bridge_state: BridgeStateModel | None = None,
) -> OnboardingAssistantSnapshot:
    """Build operator guidance from preflight, config, and runtime device state."""
    bridge_state = bridge_state or build_bridge_state_model(
        config=config,
        preflight=preflight,
        devices=devices,
        ma_connected=ma_connected,
        runtime_mode=runtime_mode,
    )
    normalized_devices = bridge_state.devices
    configured_count = int(bridge_state.configuration.configured_device_count)
    active_devices = [device for device in normalized_devices if device.management.get("bridge_managed", True)]
    released_devices = [device for device in normalized_devices if device.management.get("released")]
    standby_devices = sum(1 for device in active_devices if device.bluetooth.get("standby"))
    connected_devices = sum(1 for device in active_devices if device.bluetooth.get("connected"))
    # Devices in idle-standby were fully connected before the bridge
    # intentionally disconnected BT to save power.  Treat them as
    # "logically connected" so onboarding doesn't regress.
    connected_or_standby = connected_devices + standby_devices
    sink_ready_devices = sum(
        1
        for device in active_devices
        if (device.bluetooth.get("connected") and device.audio.get("has_sink")) or device.bluetooth.get("standby")
    )
    missing_sink_devices = [
        device.player_name or "Unknown"
        for device in active_devices
        if device.bluetooth.get("connected")
        and not device.audio.get("has_sink")
        and not device.bluetooth.get("standby")
    ]

    bluetooth = bridge_state.runtime_substrate.bluetooth
    audio = bridge_state.runtime_substrate.audio
    if "dbus" in preflight:
        dbus_available = bool(bridge_state.runtime_substrate.dbus_available)
    else:
        dbus_available = True
    audio_system = str(audio.get("system") or "unknown")
    audio_sinks = int(audio.get("sinks") or 0)
    paired_devices = int(bluetooth.get("paired_devices") or 0)
    controller_present = bool(bluetooth.get("controller", False))
    disabled_configured_count = int(bridge_state.configuration.disabled_device_count)
    user_released_devices = [
        device for device in released_devices if str(device.management.get("release_reason") or "") != "auto"
    ]
    auto_released_devices = [
        device for device in released_devices if str(device.management.get("release_reason") or "") == "auto"
    ]

    ma_url = str(config.get("MA_API_URL") or "").strip()
    ma_token = str(config.get("MA_API_TOKEN") or "").strip()
    ma_username = str(config.get("MA_USERNAME") or "").strip()
    pulse_latency = int(config.get("PULSE_LATENCY_MSEC") or 0)
    custom_delays = sum(
        1
        for device in devices
        if getattr(device, "bt_management_enabled", True)
        and getattr(device, "static_delay_ms", None) not in (None, 0, 0.0)
    )

    checks: list[OnboardingCheck] = []

    if not dbus_available:
        checks.append(
            OnboardingCheck(
                key="runtime_access",
                status="error",
                summary="The bridge runtime cannot reach the host D-Bus services required for Bluetooth control.",
                details={"dbus": False},
                actions=[
                    "Open diagnostics and confirm D-Bus is reachable from this runtime.",
                    "For Docker or LXC, verify the host D-Bus socket/mounts and required privileges are present.",
                ],
            )
        )
    else:
        checks.append(
            OnboardingCheck(
                key="runtime_access",
                status="ok",
                summary="The bridge runtime can reach the host services it needs for Bluetooth and audio control.",
                details={"dbus": True},
            )
        )

    if not controller_present:
        bluetooth_actions = [
            "Open Bluetooth settings and press Refresh adapters.",
            "If no adapter appears, verify Bluetooth passthrough or host access, then add the adapter manually.",
        ]
        audio_socket_mounted_but_unreachable = (
            bool(audio.get("socket_exists")) and audio.get("socket_reachable") is False
        )
        if audio_socket_mounted_but_unreachable:
            bluetooth_actions.append(
                "The audio socket is mounted but unreachable — a UID mismatch between the container "
                "and the host session owner often breaks both Bluetooth and audio access at once. "
                "Check that AUDIO_UID in .env matches `id -u` of the host audio user, and on rootless "
                'Docker consider adding `user: "<uid>:<gid>"` to docker-compose.yml.'
            )
        checks.append(
            OnboardingCheck(
                key="bluetooth",
                status="error",
                summary="No Bluetooth controller detected by preflight checks.",
                details={"paired_devices": paired_devices},
                actions=bluetooth_actions,
            )
        )
    elif configured_count > 0 and paired_devices == 0:
        bluetooth_summary = "No paired Bluetooth speakers are currently available to the bridge."
        bluetooth_actions = [
            "Put a speaker in pairing mode, then open Bluetooth scan to pair or rediscover it.",
        ]
        bluetooth_details = {"paired_devices": paired_devices, "configured_devices": configured_count}
        if disabled_configured_count >= configured_count:
            bluetooth_summary = (
                "No paired Bluetooth speakers are currently available, and the saved speaker is disabled."
            )
            bluetooth_details["disabled_devices"] = disabled_configured_count
            bluetooth_actions.append(
                "After the speaker appears again, re-enable it in Configuration → Devices and restart the bridge."
            )
        checks.append(
            OnboardingCheck(
                key="bluetooth",
                status="warning",
                summary=bluetooth_summary,
                details=bluetooth_details,
                actions=bluetooth_actions,
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

    if audio_system == "unreachable":
        socket_path = str(audio.get("socket") or "(unknown)")
        last_error = str(audio.get("last_error") or "")
        is_refused = "refused" in last_error.lower()
        details: dict[str, Any] = {
            "system": "unreachable",
            "socket": socket_path,
            "socket_exists": True,
            "socket_reachable": False,
            "last_error": last_error or "connection unreachable",
        }
        if is_refused:
            details["reason_code"] = "pa_socket_refused"
            summary = (
                f"Audio socket {socket_path} is mounted but the server refused the connection "
                f"({last_error or 'connection refused'})."
            )
            actions = [
                "On the Docker host, run `sudo loginctl enable-linger <user>` for the audio user so PipeWire/PulseAudio keeps running without an active SSH session.",
                "Reboot the host (or `systemctl --user start pipewire.socket pipewire.service wireplumber.service`) and restart the container.",
                "See docs: https://trudenboy.github.io/sendspin-bt-bridge/installation/docker/#headless-pipewire-bluetooth-sinks-not-appearing-after-reboot",
            ]
        else:
            summary = (
                f"Audio socket {socket_path} is mounted but the bridge cannot open it "
                f"({last_error or 'unknown error'})."
            )
            actions = [
                "Open diagnostics and inspect the audio socket error — permission issues usually mean the container UID does not match the audio user's UID.",
                "Verify the socket mount and `PULSE_SERVER` path, then restart the bridge.",
            ]
        checks.append(
            OnboardingCheck(
                key="audio",
                status="error",
                summary=summary,
                details=details,
                actions=actions,
            )
        )
    elif audio_system == "unknown":
        checks.append(
            OnboardingCheck(
                key="audio",
                status="error",
                summary="No PulseAudio or PipeWire server was detected.",
                details={"sinks": audio_sinks},
                actions=[
                    "Open diagnostics to confirm the runtime can reach PulseAudio or PipeWire.",
                    "Verify the audio socket mount and `PULSE_SERVER` configuration.",
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
                actions=[
                    "Power on a Bluetooth speaker and wait for its `bluez_*` sink to appear.",
                    "If no sink appears, open diagnostics and confirm `pactl list sinks short` shows it.",
                ],
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
                key="bridge_control",
                status="ok",
                summary="No Bluetooth speakers are configured yet, so the bridge is ready for your first device.",
                details={"configured_devices": 0, "active_devices": 0},
            )
        )
    elif disabled_configured_count >= configured_count:
        checks.append(
            OnboardingCheck(
                key="bridge_control",
                status="warning",
                summary="All configured Bluetooth speakers are globally disabled right now.",
                details={
                    "configured_devices": configured_count,
                    "disabled_devices": disabled_configured_count,
                    "active_devices": len(active_devices),
                },
                actions=[
                    "Open Configuration → Devices and re-enable at least one speaker.",
                    "Save and restart the bridge so it reloads the enabled device set.",
                ],
            )
        )
    elif not active_devices and released_devices:
        checks.append(
            OnboardingCheck(
                key="bridge_control",
                status="warning",
                summary=(
                    "All configured speakers were auto-released from bridge management after connection failures."
                    if auto_released_devices and len(auto_released_devices) >= configured_count
                    else "All configured speakers are currently released from bridge management."
                ),
                details={
                    "configured_devices": configured_count,
                    "released_devices": len(released_devices),
                    "user_released_devices": len(user_released_devices),
                    "auto_released_devices": len(auto_released_devices),
                },
                actions=[
                    "Open device settings and reclaim at least one speaker for bridge management.",
                    "If reclaim does not stick, open diagnostics and confirm Bluetooth access is healthy first.",
                ],
            )
        )
    else:
        checks.append(
            OnboardingCheck(
                key="bridge_control",
                status="ok",
                summary="At least one configured speaker is available to the bridge.",
                details={"configured_devices": configured_count, "active_devices": len(active_devices)},
            )
        )

    if configured_count == 0:
        checks.append(
            OnboardingCheck(
                key="sink_verification",
                status="warning",
                summary="No bridge devices are configured yet.",
                details={"configured_devices": configured_count},
                actions=[
                    "Put your speaker in pairing mode, then open device scan to discover it.",
                    "If scanning finds nothing, open device settings and add the speaker manually.",
                ],
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
    elif connected_or_standby == 0:
        checks.append(
            OnboardingCheck(
                key="sink_verification",
                status="warning",
                summary="Devices are configured, but none are currently connected over Bluetooth.",
                details={"configured_devices": configured_count},
                actions=[
                    "Power on a configured speaker and wait for the bridge to acquire its sink.",
                    "If the speaker stays offline, open device settings and run reconnect or re-pair.",
                ],
            )
        )
    else:
        standby_note = f" ({standby_devices} in standby)" if standby_devices and not connected_devices else ""
        checks.append(
            OnboardingCheck(
                key="sink_verification",
                status="ok",
                summary=f"Connected devices have resolved Bluetooth sinks.{standby_note}",
                details={
                    "connected_devices": connected_devices,
                    "sink_ready_devices": sink_ready_devices,
                    "standby_devices": standby_devices,
                },
            )
        )

    if not ma_url:
        checks.append(
            OnboardingCheck(
                key="ma_auth",
                status="warning",
                summary="Music Assistant API URL is not configured.",
                details={"configured_url": "", "auto_discovery_available": True},
                actions=[
                    "Run discovery first so the bridge can try to find Music Assistant automatically.",
                    "If discovery still finds nothing, open Music Assistant settings and set the server URL manually.",
                ],
            )
        )
    elif not ma_token and not ma_username:
        checks.append(
            OnboardingCheck(
                key="ma_auth",
                status="warning",
                summary="Music Assistant credentials are missing.",
                details={"configured_url": ma_url},
                actions=["Open Music Assistant settings and sign in or paste a long-lived token."],
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
                    "Open Music Assistant settings and verify the URL and credentials.",
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
                details={
                    "pulse_latency_msec": pulse_latency,
                    "custom_device_delays": custom_delays,
                    "recommended_pulse_latency_msec": pulse_latency or 300,
                },
            )
        )
    elif custom_delays == 0:
        recommended_latency = max(pulse_latency, 300)
        checks.append(
            OnboardingCheck(
                key="latency",
                status="warning",
                summary="Multi-device setup detected without per-device static delay tuning.",
                details={
                    "pulse_latency_msec": pulse_latency,
                    "configured_devices": configured_count,
                    "recommended_pulse_latency_msec": recommended_latency,
                },
                actions=["Open device settings and set `static_delay_ms` after both rooms stay connected reliably."],
            )
        )
    elif pulse_latency >= 800:
        recommended_latency = 600
        checks.append(
            OnboardingCheck(
                key="latency",
                status="warning",
                summary="Latency tuning is present, but the global PulseAudio latency is quite high.",
                details={
                    "pulse_latency_msec": pulse_latency,
                    "custom_device_delays": custom_delays,
                    "recommended_pulse_latency_msec": recommended_latency,
                },
                actions=[
                    "Review device settings after playback stabilizes and lower latency if reaction feels too sluggish."
                ],
            )
        )
    else:
        checks.append(
            OnboardingCheck(
                key="latency",
                status="ok",
                summary="Latency tuning is configured for a multi-device setup.",
                details={
                    "pulse_latency_msec": pulse_latency,
                    "custom_device_delays": custom_delays,
                    "recommended_pulse_latency_msec": pulse_latency,
                },
            )
        )

    next_steps: list[str] = []
    for check in sorted(checks, key=lambda item: _status_rank(item.status), reverse=True):
        for action in check.actions:
            if action not in next_steps:
                next_steps.append(action)

    counts = {
        "configured_devices": configured_count,
        "active_devices": len(active_devices),
        "connected_devices": connected_devices,
        "sink_ready_devices": sink_ready_devices,
        "standby_devices": standby_devices,
    }

    return OnboardingAssistantSnapshot(
        runtime_mode=runtime_mode,
        generated_at=datetime.now(tz=UTC).isoformat(),
        counts=counts,
        checks=checks,
        next_steps=next_steps,
        checklist=_build_onboarding_checklist(checks, counts),
    )
