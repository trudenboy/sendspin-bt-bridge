"""Recovery-oriented diagnostics helpers for Phase 2 operator flows."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sendspin_bridge.services.audio.sink_inventory import LocalSink
    from sendspin_bridge.services.ipc.bridge_state_model import BridgeStateModel

from sendspin_bridge.config import get_config_migration_error
from sendspin_bridge.config.players import device_entries
from sendspin_bridge.services.diagnostics.device_phrasing import reconnect_attempt_summary
from sendspin_bridge.services.diagnostics.preflight_status import collect_preflight_status
from sendspin_bridge.services.diagnostics.recovery_timeline import build_recovery_timeline
from sendspin_bridge.services.infrastructure._helpers import (
    _device_audio_streaming,
    _device_extra,
    _device_ma_reconnecting,
)
from sendspin_bridge.services.ipc.device_facts import DeviceFacts

UTC = timezone.utc


@dataclass
class NormalizedRecoveryDevice:
    """Lightweight projection of a NormalizedDeviceState used by the recovery assistant."""

    player_name: str
    state_model: dict[str, Any]
    health_summary: dict[str, Any]
    recent_events: list[dict[str, Any]]
    static_delay_ms: int | float | None = None
    # v2.70.0-rc.2 (#263) — surfaced from NormalizedDeviceState.enabled so the
    # `auto_disabled_never_paired` recovery card can detect devices that were
    # disabled by the auto-disable path. Default True keeps prior callers
    # (SimpleNamespace test fixtures, devices without bridge-state) working.
    enabled: bool = True


def _device_state_model(device: Any) -> dict[str, Any]:
    state_model = getattr(device, "state_model", None)
    return state_model if isinstance(state_model, dict) else {}


def _reconnect_attempt_summary(device: Any) -> str:
    """The shared sentence — see ``device_phrasing``."""
    return reconnect_attempt_summary(device)


@dataclass
class RecoveryAction:
    key: str
    label: str
    device_name: str | None = None
    device_names: list[str] = field(default_factory=list)
    check_key: str | None = None
    value: Any | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"key": self.key, "label": self.label}
        if self.device_names:
            payload["device_names"] = [name for name in self.device_names if name]
        elif self.device_name:
            payload["device_name"] = self.device_name
        if self.check_key:
            payload["check_key"] = self.check_key
        if self.value is not None:
            payload["value"] = self.value
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
    timeline: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "summary": dict(self.summary),
            "issues": [issue.to_dict() for issue in self.issues],
            "traces": [trace.to_dict() for trace in self.traces],
            "safe_actions": [action.to_dict() for action in self.safe_actions],
            "latency_assistant": dict(self.latency_assistant),
            "known_good_test_path": dict(self.known_good_test_path),
            "timeline": dict(self.timeline),
        }


def _recommended_action_from_onboarding(checklist: dict[str, Any]) -> RecoveryAction | None:
    action = checklist.get("primary_action") or {}
    key = str(action.get("key") or "").strip()
    label = str(action.get("label") or "").strip()
    if not key or not label:
        return None
    return RecoveryAction(
        key=key,
        label=label,
        check_key=str(action.get("check_key") or "").strip() or None,
        value=action.get("value"),
    )


def _normalize_device_names(device_names: list[str] | None) -> list[str]:
    return [str(name).strip() for name in (device_names or []) if str(name).strip()]


def _recovery_action(
    key: str,
    label: str,
    *,
    device_names: list[str] | None = None,
    check_key: str | None = None,
    value: Any | None = None,
) -> RecoveryAction:
    names = _normalize_device_names(device_names)
    return RecoveryAction(
        key=key,
        label=label,
        device_name=names[0] if len(names) == 1 else None,
        device_names=names if len(names) > 1 else [],
        check_key=check_key,
        value=value,
    )


def _merge_secondary_actions(
    primary_action: RecoveryAction | None,
    secondary_actions: list[RecoveryAction] | None = None,
) -> list[RecoveryAction]:
    actions = list(secondary_actions or [])
    seen = {
        (action.key, action.label, tuple(action.device_names), action.device_name, action.check_key, action.value)
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
                primary_action.check_key,
                primary_action.value,
            )
        )
    diagnostics = _recovery_action("open_diagnostics", "Open diagnostics")
    marker = (
        diagnostics.key,
        diagnostics.label,
        tuple(diagnostics.device_names),
        diagnostics.device_name,
        diagnostics.check_key,
        diagnostics.value,
    )
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
        secondary_actions.insert(
            0,
            _recovery_action(
                "rerun_safe_check",
                "Recheck sinks",
                device_names=names,
                check_key="sink_verification",
            ),
        )
        primary_action = _recovery_action(
            "reconnect_devices" if len(names) > 1 else "reconnect_device",
            f"Reconnect {len(names)} devices" if len(names) > 1 else "Reconnect speaker",
            device_names=names,
        )
    elif issue_key == "never_paired":
        # v2.70.0-rc.2 (#260) — device exists only in the bridge config, not in
        # BlueZ. The remedy is to scan/pair, not to re-pair or release. We
        # deliberately omit the `toggle_bt_management` secondary because
        # there is no live BT management to release yet.
        primary_action = _recovery_action(
            "open_devices_settings" if len(names) > 1 else "pair_device",
            "Open device settings" if len(names) > 1 else "Start pairing",
            device_names=names,
        )
    elif issue_key == "auto_disabled_never_paired":
        # v2.70.0-rc.2 (#263) — device auto-disabled after N failed reconnects
        # on a never-paired MAC. Re-enable flips `enabled=true` and resets
        # the never_paired counter so a fresh pairing attempt can run.
        primary_action = _recovery_action(
            "enable_devices" if len(names) > 1 else "enable_device",
            f"Re-enable {len(names)} devices" if len(names) > 1 else "Re-enable",
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
    elif issue_key == "sink_system_muted":
        primary_action = _recovery_action(
            "unmute_sink",
            "Unmute speaker",
            device_names=names,
        )
        secondary_actions.insert(
            0,
            _recovery_action("open_diagnostics", "Open diagnostics", device_names=names),
        )
    elif issue_key == "output_missing":
        primary_action = _recovery_action("open_devices_settings", "Open player settings", device_names=names)
    elif issue_key == "duplicate_device":
        primary_action = _recovery_action(
            "open_devices_settings",
            "Open device settings",
            device_names=names,
        )
    elif issue_key == "samsung_cod_filter":
        # Drives the user to the Bluetooth settings tab where they
        # set the adapter's ``device_class`` override.  Re-pair is
        # the natural follow-up so it goes in the secondary slot —
        # the operator confirms the fix worked by retrying the
        # connection that originally failed.
        primary_action = _recovery_action("open_bt_settings", "Open Bluetooth settings", device_names=names)
        secondary_actions.insert(
            0,
            _recovery_action("pair_device", "Re-pair speaker", device_names=names),
        )
    elif issue_key == "sendspin_port_unreachable":
        primary_action = _recovery_action("open_config", "Open config", device_names=names)
    elif issue_key == "sendspin_url_malformed":
        # User edited the config to something the URL builder can't parse.
        # The actionable fix is always "open Settings, edit SENDSPIN_SERVER".
        primary_action = _recovery_action("open_config", "Edit Music Assistant server", device_names=names)
        secondary_actions.insert(
            0,
            _recovery_action("open_diagnostics", "Open diagnostics", device_names=names),
        )
    elif issue_key == "sendspin_daemon_loop_timeout":
        # Daemon connects to a host that responds initially but drops/closes
        # the WebSocket on a fixed schedule.  Most useful next action is the
        # Test connection probe, which validates the URL+port reachability.
        primary_action = _recovery_action(
            "test_sendspin_connection", "Test Music Assistant connection", device_names=names
        )
        secondary_actions.insert(
            0,
            _recovery_action("open_config", "Open Music Assistant settings", device_names=names),
        )
        secondary_actions.insert(
            1,
            _recovery_action("open_diagnostics", "Open diagnostics", device_names=names),
        )
    elif issue_key == "setup_step":
        primary_action = None
    else:
        primary_action = _recovery_action("open_diagnostics", "Open diagnostics", device_names=names)
    return primary_action, _merge_secondary_actions(primary_action, secondary_actions)


_SINK_SUSPEND_DOCS = "https://trudenboy.github.io/sendspin-bt-bridge/bluetooth-speakers/#bluetooth-sink-suspend"
_SINK_SUSPEND_CAUSES = {
    "observed_suspend": "The host suspended this speaker's audio sink while the speaker was connected and idle.",
    "module_suspend_on_idle": "PulseAudio's module-suspend-on-idle is loaded and suspends idle sinks.",
    "wireplumber_suspend_timeout": "WirePlumber suspends idle Bluetooth nodes (session.suspend-timeout-seconds).",
}


def _host_sink_suspends(device: Any) -> dict[str, Any]:
    """The device's ``host_sink_suspends`` dict, from a status object or a normalized state model."""
    direct = getattr(device, "host_sink_suspends", None)
    if isinstance(direct, dict):
        return direct
    audio = _device_state_model(device).get("audio")
    nested = audio.get("host_sink_suspends") if isinstance(audio, dict) else None
    return nested if isinstance(nested, dict) else {}


def _build_sink_suspend_issues(devices: list[Any]) -> list[RecoveryIssue]:
    """One warning per connected speaker whose sink the host suspends in gaps (read-only check)."""
    issues: list[RecoveryIssue] = []
    for device in devices:
        facts = _host_sink_suspends(device)
        if not facts.get("warn"):
            continue
        # Only a connected, bridge-managed speaker: nothing to suspend otherwise.
        bluetooth = _device_state_model(device).get("bluetooth") or {}
        management = _device_state_model(device).get("management") or {}
        connected = (
            bool(bluetooth.get("connected")) if bluetooth else bool(getattr(device, "bluetooth_connected", False))
        )
        released = management.get("released") if management else getattr(device, "bt_management_enabled", True) is False
        if not connected or released:
            continue
        name = str(getattr(device, "player_name", None) or "Unknown")
        cause = _SINK_SUSPEND_CAUSES.get(str(facts.get("reason") or ""), "")
        primary_action, secondary_actions = build_recovery_issue_actions("bt_sink_suspend_in_gaps", [name])
        issues.append(
            RecoveryIssue(
                key="bt_sink_suspend_in_gaps",
                severity="warning",
                title=f"{name}: the host suspends the Bluetooth sink between songs",
                summary=(
                    f"{cause} After such a pause the speaker restarts its stream and may shift by tens of "
                    "milliseconds. The bridge never changes host audio settings; to stop it, disable the "
                    f"sink suspend on the host: {_SINK_SUSPEND_DOCS}"
                ).strip(),
                primary_action=primary_action,
                secondary_actions=secondary_actions,
                device_name=name,
            )
        )
    return issues


def _build_device_issues(devices: list[Any]) -> list[RecoveryIssue]:
    issues = _build_device_connection_issues(devices)
    issues.extend(_build_sink_suspend_issues(devices))
    return issues


def _build_device_connection_issues(devices: list[Any]) -> list[RecoveryIssue]:
    issues: list[RecoveryIssue] = []
    for device in devices:
        name = str(getattr(device, "player_name", None) or "Unknown")
        device_names = [name]
        health = getattr(device, "health_summary", None) or {}
        summary = str(health.get("summary") or "")
        state_model = _device_state_model(device)
        management = state_model.get("management") or {}
        bluetooth = state_model.get("bluetooth") or {}
        audio = state_model.get("audio") or {}
        transport = state_model.get("transport") or {}
        released = management.get("released") if management else getattr(device, "bt_management_enabled", True) is False
        release_reason = management.get("release_reason") if management else _device_extra(device).get("bt_released_by")
        bluetooth_connected = (
            bool(bluetooth.get("connected")) if bluetooth else bool(getattr(device, "bluetooth_connected", False))
        )
        has_sink = bool(audio.get("has_sink")) if audio else bool(getattr(device, "has_sink", False))
        daemon_connected = (
            bool(transport.get("daemon_connected")) if transport else bool(getattr(device, "server_connected", False))
        )
        audio_streaming = _device_audio_streaming(device)
        ma_reconnecting = _device_ma_reconnecting(device)
        # Devices in standby are intentionally disconnected — not a recovery issue
        in_standby = bool(bluetooth.get("standby") if bluetooth else _device_extra(device).get("bt_standby"))
        if in_standby:
            continue
        # v2.70.0-rc.2 (#263) — auto-disabled never-paired devices show their
        # own card with a Re-enable action. This branch runs BEFORE the
        # released path because an auto-disabled device may also report
        # released=true (BT management was implicitly dropped along with
        # the disable), but the operator action is to flip `enabled` back
        # on, not to reclaim BT management.
        never_paired_flag = bool(
            (bluetooth.get("never_paired") if bluetooth else None) or _device_extra(device).get("never_paired")
        )
        if never_paired_flag and getattr(device, "enabled", True) is False:
            primary_action, secondary_actions = build_recovery_issue_actions("auto_disabled_never_paired", device_names)
            issues.append(
                RecoveryIssue(
                    key="auto_disabled_never_paired",
                    severity="warning",
                    title=f"{name} was auto-disabled — never paired",
                    summary=summary
                    or (
                        "This speaker was disabled after repeated pairing failures. "
                        "Put it in pairing mode and click Re-enable to retry."
                    ),
                    primary_action=primary_action,
                    secondary_actions=secondary_actions,
                    device_name=name,
                )
            )
            continue
        if released:
            if release_reason != "auto":
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
        facts = DeviceFacts(device)
        local_output = facts.is_local_output
        if local_output and not facts.output_present:
            # No Bluetooth link to repair: the device is unplugged, renamed
            # away, or cannot be told apart from another one.
            issues.append(_output_missing_issue(name, facts))
            continue
        # Samsung Q-series Class-of-Device filter quirk
        # (bluez/bluez#1025).  Surfaced *before* the generic
        # bluetooth-disconnected / repair-required branches because
        # the operator action is different — they need to set the
        # adapter's ``device_class`` override, not press "Re-pair".
        # Detected at pair time by ``classify_pair_failure`` and
        # written to ``DeviceStatus.pair_failure_kind``.
        #
        # Defence-in-depth: ``bluetooth_manager.pair_device`` clears
        # this fingerprint at the start of every pair attempt (and on
        # success) so a stale match doesn't outlive the failure it
        # described.  We *also* gate the card on the device being
        # currently disconnected and unpaired here — if the speaker
        # is now connected and streaming, no diagnosis card should
        # cover it regardless of what a past pair-attempt fingerprint
        # said.
        pair_failure_kind = (
            bluetooth.get("pair_failure_kind") if bluetooth else _device_extra(device).get("pair_failure_kind")
        )
        if pair_failure_kind == "samsung_cod_filter" and not bluetooth_connected:
            adapter_mac = (
                bluetooth.get("pair_failure_adapter_mac")
                if bluetooth
                else _device_extra(device).get("pair_failure_adapter_mac")
            ) or ""
            adapter_label = adapter_mac or "the Bluetooth adapter"
            primary_action, secondary_actions = build_recovery_issue_actions("samsung_cod_filter", device_names)
            issues.append(
                RecoveryIssue(
                    key="samsung_cod_filter",
                    severity="error",
                    title=f"{name} pair rejected by Class of Device filter",
                    summary=(
                        f"{adapter_label} sent the connect attempt and the speaker rejected it with "
                        "'Limited Resources'. Samsung Q-series soundbars filter incoming pairings by the "
                        "initiator's Bluetooth Class of Device (bluez/bluez#1025). Set "
                        f"device_class to 0x00010c on {adapter_label} in Settings → Bluetooth, then re-pair."
                    ),
                    primary_action=primary_action,
                    secondary_actions=secondary_actions,
                    device_name=name,
                )
            )
            continue
        if bluetooth_connected and not has_sink:
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
        sink_muted = bool(audio.get("sink_muted")) if audio else bool(_device_extra(device).get("sink_muted"))
        app_muted = bool(audio.get("muted")) if audio else bool(_device_extra(device).get("muted"))
        if bluetooth_connected and has_sink and sink_muted and not app_muted:
            primary_action, secondary_actions = build_recovery_issue_actions("sink_system_muted", device_names)
            issues.append(
                RecoveryIssue(
                    key="sink_system_muted",
                    severity="warning",
                    title=f"{name} audio sink is muted at system level",
                    summary=summary
                    or "The PulseAudio sink is muted. Audio will not play until unmuted. This can happen after a crash or restart.",
                    primary_action=primary_action,
                    secondary_actions=secondary_actions,
                    device_name=name,
                )
            )
            continue
        if not local_output and not bluetooth_connected:
            attempt_summary = _reconnect_attempt_summary(device)
            bluetooth_paired = bluetooth.get("paired") if bluetooth else _device_extra(device).get("bluetooth_paired")
            # v2.70.0-rc.2 (#260) — three-way branch: never_paired beats
            # repair_required (device was never in BlueZ vs once paired,
            # now offline). Both fall back to `disconnected` when the
            # state is unknown but unflagged.
            never_paired_flag = bool(
                (bluetooth.get("never_paired") if bluetooth else None) or _device_extra(device).get("never_paired")
            )
            if never_paired_flag:
                issue_key = "never_paired"
                severity = "error"
                title = f"{name} has never been paired"
                default_summary = (
                    "This speaker was added to the bridge but has never appeared in BlueZ. "
                    "Put it in pairing mode and click Start pairing."
                )
            elif bluetooth_paired is False:
                issue_key = "repair_required"
                severity = "warning"
                title = f"{name} needs re-pairing"
                default_summary = (
                    "The speaker is no longer paired, so reconnect attempts will keep failing. "
                    "Put it in pairing mode and run re-pair."
                )
            else:
                issue_key = "disconnected"
                severity = "warning"
                title = f"{name} is disconnected"
                default_summary = "Power on the speaker or trigger a reconnect."
            primary_action, secondary_actions = build_recovery_issue_actions(issue_key, device_names)
            issues.append(
                RecoveryIssue(
                    key=issue_key,
                    severity=severity,
                    title=title,
                    summary=(summary or default_summary) + (f" {attempt_summary}" if attempt_summary else ""),
                    primary_action=primary_action,
                    secondary_actions=secondary_actions,
                    device_name=name,
                )
            )
            continue
        if not daemon_connected and not audio_streaming and not ma_reconnecting:
            last_error = str(_device_extra(device).get("last_error") or summary or "")
            is_connection_error = "Cannot connect" in last_error or "ClientConnectorError" in last_error
            if is_connection_error:
                primary_action, secondary_actions = build_recovery_issue_actions(
                    "sendspin_port_unreachable", device_names
                )
                port_summary = (
                    last_error
                    if "SENDSPIN_PORT" in last_error
                    else (
                        f"{last_error} Check that SENDSPIN_PORT in your bridge config matches "
                        "the Sendspin port configured in Music Assistant."
                    )
                )
                issues.append(
                    RecoveryIssue(
                        key="sendspin_port_unreachable",
                        severity="error",
                        title=f"{name} cannot reach Sendspin server",
                        summary=port_summary,
                        primary_action=primary_action,
                        secondary_actions=secondary_actions,
                        device_name=name,
                    )
                )
                continue
            if not local_output:
                # Its remedy is a Bluetooth reconnect; a local output's
                # daemon falls through to the generic attention card.
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
        if ma_reconnecting:
            continue
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


_OUTPUT_MISSING_CAUSES = {
    "sink_in_use": "Another player already plays to it.",
    "sink_ambiguous": "Several identical outputs match it and none can be told apart.",
}


def _output_missing_issue(name: str, facts: DeviceFacts) -> RecoveryIssue:
    """A ``pulse_sink`` player without its output: plug it in or pick another one."""
    sink = facts.audio_sink or "its audio output"
    cause = _OUTPUT_MISSING_CAUSES.get(str(facts.sink_match_status or ""), "It is unplugged or was renamed.")
    primary_action, secondary_actions = build_recovery_issue_actions("output_missing", [name])
    return RecoveryIssue(
        key="output_missing",
        severity="warning",
        title=f"{name}: the audio output is missing",
        summary=(
            f"The output {sink} is not available. {cause} Plug the device back in, or pick another "
            "output in the player's settings. Until then the player stays in its Music Assistant "
            "groups, silent."
        ),
        primary_action=primary_action,
        secondary_actions=secondary_actions,
        device_name=name,
    )


def _build_duplicate_device_issues() -> list[RecoveryIssue]:
    """Build recovery issues for devices detected on another bridge instance."""
    from sendspin_bridge.services.music_assistant.ma_runtime_state import get_duplicate_device_warnings

    warnings = get_duplicate_device_warnings()
    if not warnings:
        return []
    issues: list[RecoveryIssue] = []
    for w in warnings:
        device_names = [w.device_name]
        primary_action, secondary_actions = build_recovery_issue_actions("duplicate_device", device_names)
        issues.append(
            RecoveryIssue(
                key="duplicate_device",
                severity="warning",
                title=f"{w.device_name} may conflict with another bridge",
                summary=(
                    f"This device is also registered as '{w.other_bridge_name}' in Music Assistant. "
                    "Running on multiple bridges causes disconnect loops. "
                    "Disable it on one bridge or stop the other instance."
                ),
                primary_action=primary_action,
                secondary_actions=secondary_actions,
                device_name=w.device_name,
            )
        )
    return issues


def _build_config_writable_issue(preflight: dict[str, Any] | None = None) -> RecoveryIssue | None:
    """Surface ``/config not writable`` (issue #190) as a recovery card.

    Reuses an already-collected ``preflight`` payload when supplied so
    recovery snapshot builders don't rerun the bluetoothctl + audio
    probes a second time per request.  Falls back to
    ``collect_preflight_status()`` for legacy callers that don't
    plumb preflight through.  Returns ``None`` on the happy path so
    the recovery banner stays empty when nothing is wrong.
    """
    if preflight is None:
        try:
            preflight = collect_preflight_status()
        except Exception:
            # Never let this assistant builder crash the whole recovery
            # snapshot — preflight collection is best-effort.
            return None
    config_writable = preflight.get("config_writable") or {}
    if config_writable.get("status") == "ok":
        return None
    config_dir = config_writable.get("config_dir") or "/config"
    uid = config_writable.get("uid")
    remediation = config_writable.get("remediation") or ""
    primary_action, secondary_actions = build_recovery_issue_actions("config_dir_not_writable", [])
    return RecoveryIssue(
        key="config_dir_not_writable",
        severity="error",
        title=f"{config_dir} is not writable by the bridge",
        summary=(
            f"The bridge process (UID {uid}) cannot write to {config_dir}. "
            "Music Assistant token saves, password changes, and per-device "
            "config edits will all fail with a 500. "
            f"Fix: {remediation}"
        ),
        primary_action=primary_action,
        secondary_actions=secondary_actions,
    )


def device_output_sink(device: Any) -> str | None:
    """The local output a ``pulse_sink`` device plays to; None for any other device."""
    audio = _device_state_model(device).get("audio") or {}
    if audio:
        backend, sink = audio.get("backend_type"), audio.get("output_sink")
    else:
        extra = _device_extra(device)
        backend, sink = extra.get("backend_type"), extra.get("audio_sink")
    return str(sink) if backend == "pulse_sink" and sink else None


def _build_output_issues(devices: list[Any], local_sinks: list[LocalSink] | None) -> list[RecoveryIssue]:
    """Muted, silent or unplugged local outputs of ``pulse_sink`` players.

    Music Assistant's volume is the player's own stream volume; it cannot undo
    any of these.  ``availability unknown`` is not a warning: many cards never
    report jack detection.
    """
    by_name = {sink.name: sink for sink in local_sinks or []}
    issues: list[RecoveryIssue] = []
    for device in devices:
        if not getattr(device, "enabled", True):
            continue
        sink_name = device_output_sink(device)
        sink = by_name.get(sink_name) if sink_name else None
        if sink is None:
            continue
        name = str(getattr(device, "player_name", None) or "Unknown")
        found: list[tuple[str, str, str]] = []
        if sink.muted:
            found.append(
                (
                    "output_muted",
                    f"{name}: the audio output is muted",
                    f"The output {sink.name} is muted in the system mixer, so nothing is audible. "
                    "Music Assistant's volume cannot unmute it: unmute it in the system sound settings.",
                )
            )
        if sink.volume == 0:
            found.append(
                (
                    "output_volume_zero",
                    f"{name}: the audio output volume is 0 %",
                    f"The output {sink.name} is set to 0 % in the system mixer. "
                    "Music Assistant's volume cannot raise it: raise it in the system sound settings.",
                )
            )
        if sink.active_port_available == "not available":
            found.append(
                (
                    "output_port_unavailable",
                    f"{name}: nothing is plugged into the audio output",
                    f"The active port {sink.active_port or ''} of {sink.name} reports no connection. "
                    "Check the cable or the speakers.",
                )
            )
        for key, title, summary in found:
            primary_action, secondary_actions = build_recovery_issue_actions(key, [name])
            issues.append(
                RecoveryIssue(
                    key=key,
                    severity="warning",
                    title=title,
                    summary=summary,
                    primary_action=primary_action,
                    secondary_actions=secondary_actions,
                    device_name=name,
                )
            )
    return issues


def _build_config_migration_issue() -> RecoveryIssue | None:
    """The config file could not be moved to the current schema; speakers are not loaded."""
    error = get_config_migration_error()
    if not error:
        return None
    primary_action, secondary_actions = build_recovery_issue_actions("config_migration_failed", [])
    return RecoveryIssue(
        key="config_migration_failed",
        severity="error",
        title="The configuration could not be updated to this version",
        summary=(
            "The bridge started without speakers. config.json was left untouched "
            f"and is still the old file. Error: {error}. "
            "Please send a bug report."
        ),
        primary_action=primary_action,
        secondary_actions=secondary_actions,
    )


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
        RecoveryAction(key="rerun_safe_check", label="Rerun preflight", check_key="runtime_access"),
    ]
    issue_keys = {issue.key for issue in issues}
    if issue_keys.intersection({"missing_sink", "disconnected", "transport_down"}):
        actions.append(
            RecoveryAction(key="rerun_safe_check", label="Recheck Bluetooth sinks", check_key="sink_verification")
        )
    if (
        any(issue.recommended_action and issue.recommended_action.key == "open_ma_settings" for issue in issues)
        or str((onboarding_assistant.get("checklist") or {}).get("current_step_key") or "") == "ma_auth"
    ):
        actions.append(RecoveryAction(key="retry_ma_discovery", label="Retry MA discovery"))
        actions.append(RecoveryAction(key="rerun_safe_check", label="Revalidate MA link", check_key="ma_auth"))
    seen: set[tuple[str, str, str | None]] = set()
    deduped: list[RecoveryAction] = []
    for action in actions:
        marker = (action.key, action.label, action.check_key or action.device_name)
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
    configured_count = len(device_entries(config))
    custom_delays = sum(1 for device in devices if getattr(device, "static_delay_ms", None) not in (None, 0, 0.0))
    pulse_latency = int(config.get("PULSE_LATENCY_MSEC") or 0)
    presets = [
        {
            "key": "responsive",
            "label": "300 ms",
            "value": 300,
            "summary": "Balanced responsiveness for most native installs.",
        },
        {
            "key": "stable",
            "label": "600 ms",
            "value": 600,
            "summary": "Safer for multi-room setups or reconnect churn.",
        },
        {
            "key": "virtualized",
            "label": "800 ms",
            "value": 800,
            "summary": "Recommended when virtualization or HAOS needs extra buffer.",
        },
    ]

    def _runtime_value(device: Any, key: str, default: Any = None) -> Any:
        status = getattr(device, "status", None)
        if isinstance(status, dict):
            value = status.get(key)
            if value is not None:
                return value
        extra = getattr(device, "extra", None)
        if isinstance(extra, dict):
            value = extra.get(key)
            if value is not None:
                return value
        return getattr(device, key, default)

    actively_playing = [device for device in devices if bool(_runtime_value(device, "playing", False))]
    reanchor_count = sum(
        int(
            _runtime_value(device, "reanchor_count_5m", None)
            if _runtime_value(device, "reanchor_count_5m", None) is not None
            else _runtime_value(device, "reanchor_count", 0)
        )
        for device in actively_playing
    )
    if configured_count >= 2 and reanchor_count >= 3:
        current_min_buffer = max(
            [int(_runtime_value(device, "min_buffer_ms", 250) or 250) for device in actively_playing] or [250]
        )
        recommended_min_buffer = min(3000, max(400, current_min_buffer + 150))
        buffered_values = [
            float(value)
            for device in actively_playing
            if (value := _runtime_value(device, "buffered_audio_ms")) is not None
        ]
        return {
            "tone": "warning",
            "summary": "Repeated re-anchors were observed during active playback.",
            "current_pulse_latency_msec": pulse_latency,
            "recommended_pulse_latency_msec": pulse_latency,
            "recommended_min_buffer_ms": recommended_min_buffer,
            "recommended_summary": "Increase the Sendspin ongoing buffer before changing static delay.",
            "delta_from_recommendation_msec": 0,
            "evidence": {
                "reanchor_count": reanchor_count,
                "active_devices": len(actively_playing),
                "buffered_audio_ms": round(min(buffered_values), 1) if buffered_values else None,
            },
            "hints": [
                "Static delay corrects a stable offset; it does not repair an unstable stream.",
                f"Try min_buffer_ms={recommended_min_buffer} and observe another playback session.",
            ],
            "safe_actions": [RecoveryAction(key="open_devices_settings", label="Review buffer settings").to_dict()],
            "presets": presets,
        }
    if configured_count < 2:
        recommended = pulse_latency or 300
        return {
            "tone": "ok",
            "summary": "Single-device setups usually do not need extra latency tuning.",
            "current_pulse_latency_msec": pulse_latency,
            "recommended_pulse_latency_msec": recommended,
            "recommended_summary": "Keep the global latency conservative until you add more rooms.",
            "delta_from_recommendation_msec": recommended - pulse_latency,
            "hints": ["Add a second room before spending time on manual delay calibration."],
            "safe_actions": [RecoveryAction(key="open_devices_settings", label="Open device settings").to_dict()],
            "presets": presets,
        }
    if custom_delays == 0:
        recommended = max(pulse_latency, 300)
        safe_actions = [RecoveryAction(key="open_devices_settings", label="Tune device delays").to_dict()]
        if recommended != pulse_latency:
            safe_actions.insert(
                0,
                RecoveryAction(
                    key="apply_latency_recommended",
                    label=f"Apply {recommended} ms latency",
                    value=recommended,
                ).to_dict(),
            )
        return {
            "tone": "warning",
            "summary": "Multi-device setup detected without per-device static delays.",
            "current_pulse_latency_msec": pulse_latency,
            "recommended_pulse_latency_msec": recommended,
            "recommended_summary": "Verify one clean Bluetooth route first, then add per-device delays for room matching.",
            "delta_from_recommendation_msec": recommended - pulse_latency,
            "hints": [
                "Play the same short track in both rooms and listen for drift.",
                "Set `static_delay_ms` per device only after the Bluetooth sink is stable.",
            ],
            "safe_actions": safe_actions,
            "presets": presets,
        }
    if pulse_latency >= 800:
        recommended = 600
        safe_actions = [RecoveryAction(key="open_devices_settings", label="Review latency settings").to_dict()]
        if recommended != pulse_latency:
            safe_actions.insert(
                0,
                RecoveryAction(
                    key="apply_latency_recommended",
                    label=f"Apply {recommended} ms latency",
                    value=recommended,
                ).to_dict(),
            )
        return {
            "tone": "warning",
            "summary": "Per-device delay tuning exists, but the global PulseAudio latency is still high.",
            "current_pulse_latency_msec": pulse_latency,
            "recommended_pulse_latency_msec": recommended,
            "recommended_summary": "Lower the shared latency once routing is stable so transport controls feel snappier again.",
            "delta_from_recommendation_msec": recommended - pulse_latency,
            "hints": [
                "Keep the high latency if virtualization needs it, but lower it when playback reacts too slowly.",
                "Re-test one room at a time after every latency change.",
            ],
            "safe_actions": safe_actions,
            "presets": presets,
        }
    return {
        "tone": "ok",
        "summary": "Latency tuning is in a healthy range for a multi-device setup.",
        "current_pulse_latency_msec": pulse_latency,
        "recommended_pulse_latency_msec": pulse_latency,
        "recommended_summary": "Keep the current global latency and only revisit it after hardware or runtime changes.",
        "delta_from_recommendation_msec": 0,
        "hints": [
            "Use the known-good test path after Bluetooth reconnects to confirm the rooms still match.",
        ],
        "safe_actions": [RecoveryAction(key="refresh_diagnostics", label="Rerun checks").to_dict()],
        "presets": presets,
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
        "recommended_action": RecoveryAction(
            key="rerun_safe_check", label="Rerun preflight", check_key="runtime_access"
        ).to_dict(),
    }


def build_recovery_assistant_snapshot(
    *,
    config: dict[str, Any],
    devices: list[Any],
    onboarding_assistant: dict[str, Any],
    startup_progress: dict[str, Any],
    bridge_state: BridgeStateModel | None = None,
    preflight: dict[str, Any] | None = None,
    local_sinks: list[LocalSink] | None = None,
) -> RecoveryAssistantSnapshot:
    if bridge_state is not None:
        delay_by_name: dict[str, Any] = {}
        for dev_cfg in device_entries(config) or config.get("devices") or []:
            name = dev_cfg.get("player_name") or ""
            if name:
                delay_by_name[name] = dev_cfg.get("static_delay_ms")
        devices = [
            NormalizedRecoveryDevice(
                player_name=state.player_name,
                state_model=state.to_dict(),
                health_summary=state.health,
                recent_events=state.recent_events,
                static_delay_ms=delay_by_name.get(state.player_name),
                enabled=state.enabled,
            )
            for state in bridge_state.devices
        ]
    issues = _build_device_issues(devices)
    issues.extend(_build_output_issues(devices, local_sinks))
    issues.extend(_build_duplicate_device_issues())
    config_writable_issue = _build_config_writable_issue(preflight=preflight)
    if config_writable_issue:
        issues.append(config_writable_issue)
    config_migration_issue = _build_config_migration_issue()
    if config_migration_issue:
        issues.append(config_migration_issue)
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
        timeline=build_recovery_timeline(devices, startup_progress),
    )
