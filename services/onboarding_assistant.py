"""Operator-facing onboarding and diagnostics guidance helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

UTC = timezone.utc


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "runtime_mode": self.runtime_mode,
            "generated_at": self.generated_at,
            "counts": dict(self.counts),
            "checks": [check.to_dict() for check in self.checks],
            "next_steps": list(self.next_steps),
        }


def _status_rank(status: str) -> int:
    return {"ok": 0, "warning": 1, "error": 2}.get(status, 1)


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

    return OnboardingAssistantSnapshot(
        runtime_mode=runtime_mode,
        generated_at=datetime.now(tz=UTC).isoformat(),
        counts={
            "configured_devices": configured_count,
            "active_devices": len(devices),
            "connected_devices": connected_devices,
            "sink_ready_devices": sink_ready_devices,
        },
        checks=checks,
        next_steps=next_steps,
    )
