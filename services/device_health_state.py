"""Shared device health and capability derivation."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from services._helpers import _device_audio_streaming, _device_extra, _device_ma_reconnecting


def _append_reason(reasons: list[str], reason: str) -> None:
    if reason and reason not in reasons:
        reasons.append(reason)


def derive_event_reasons(events: list[dict[str, Any]]) -> list[str]:
    reasons: list[str] = []
    if not events:
        return reasons
    event_types = [str(event.get("event_type") or "") for event in events]
    if "runtime-error" in event_types:
        _append_reason(reasons, "recent_runtime_error")
    if "bluetooth-reconnect-failed" in event_types:
        _append_reason(reasons, "recent_reconnect_failure")
    if event_types.count("bluetooth-reconnect-failed") >= 2:
        _append_reason(reasons, "repeated_reconnect_failures")
    if "bluetooth-reconnected" in event_types or "reconnecting" in event_types:
        _append_reason(reasons, "recent_reconnect_activity")
    if "audio-stream-stalled" in event_types:
        _append_reason(reasons, "recent_audio_stall")
    if "reanchoring" in event_types:
        _append_reason(reasons, "recent_reanchor")
    if "bt-management-disabled" in event_types:
        _append_reason(reasons, "management_auto_disabled")
    if "ma-monitor-stale" in event_types:
        _append_reason(reasons, "ma_monitor_stale")
    return reasons


@dataclass
class BlockReason:
    code: str
    message: str
    remediation: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    recommended_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DeviceHealthState:
    state: str
    severity: str
    summary: str
    reasons: list[str] = field(default_factory=list)
    last_event_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_device_health_state(device: Any) -> DeviceHealthState:
    reasons: list[str] = []
    extra = _device_extra(device)
    audio_streaming = _device_audio_streaming(device)
    ma_reconnecting = _device_ma_reconnecting(device)
    recent_events = list(getattr(device, "recent_events", []) or [])
    event_reasons = derive_event_reasons(recent_events)
    last_event_at = recent_events[0]["at"] if recent_events else None

    if getattr(device, "bt_management_enabled", True) is False:
        return DeviceHealthState(
            state="disabled",
            severity="info",
            summary="Bluetooth management released",
            reasons=["bt_management_disabled", *event_reasons],
            last_event_at=last_event_at,
        )

    if extra.get("last_error"):
        _append_reason(reasons, "last_error")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthState(
            state="degraded",
            severity="error",
            summary=str(extra["last_error"]),
            reasons=reasons,
            last_event_at=extra.get("last_error_at") or last_event_at,
        )

    if extra.get("stopping"):
        _append_reason(reasons, "stopping")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthState(
            state="transitioning",
            severity="info",
            summary="Stopping playback service",
            reasons=reasons,
            last_event_at=last_event_at,
        )

    if extra.get("reconnecting"):
        _append_reason(reasons, "reconnecting")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthState(
            state="recovering",
            severity="warning",
            summary="Reconnect in progress",
            reasons=reasons,
            last_event_at=last_event_at,
        )

    if extra.get("reanchoring"):
        _append_reason(reasons, "reanchoring")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthState(
            state="recovering",
            severity="warning",
            summary="Audio sync re-anchor in progress",
            reasons=reasons,
            last_event_at=last_event_at,
        )

    if not getattr(device, "bluetooth_connected", False):
        _append_reason(reasons, "bluetooth_disconnected")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthState(
            state="offline",
            severity="warning",
            summary="Bluetooth speaker disconnected",
            reasons=reasons,
            last_event_at=last_event_at,
        )

    if audio_streaming:
        return DeviceHealthState(
            state="streaming",
            severity="info",
            summary="Streaming audio",
            reasons=event_reasons,
            last_event_at=last_event_at,
        )

    if ma_reconnecting:
        _append_reason(reasons, "ma_reconnecting")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthState(
            state="recovering",
            severity="warning",
            summary="Refreshing Music Assistant connection",
            reasons=reasons,
            last_event_at=last_event_at,
        )

    if not getattr(device, "server_connected", False):
        _append_reason(reasons, "daemon_disconnected")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthState(
            state="degraded",
            severity="warning",
            summary="Sendspin daemon disconnected",
            reasons=reasons,
            last_event_at=last_event_at,
        )

    if getattr(device, "playing", False) and not audio_streaming:
        _append_reason(reasons, "playback_without_audio")
        for reason in event_reasons:
            _append_reason(reasons, reason)
        return DeviceHealthState(
            state="degraded",
            severity="warning",
            summary="Playback active without audio stream",
            reasons=reasons,
            last_event_at=last_event_at,
        )

    if getattr(device, "playing", False):
        return DeviceHealthState(
            state="streaming",
            severity="info",
            summary="Streaming audio",
            reasons=event_reasons,
            last_event_at=last_event_at,
        )

    return DeviceHealthState(
        state="ready" if getattr(device, "connected", False) else "idle",
        severity="info",
        summary="Connected and ready" if getattr(device, "connected", False) else "Idle",
        reasons=event_reasons,
        last_event_at=last_event_at,
    )


def _block_reason_payload(
    *,
    code: str,
    message: str,
    remediation: list[str] | None = None,
    depends_on: list[str] | None = None,
    recommended_action: str | None = None,
) -> BlockReason:
    return BlockReason(
        code=code,
        message=message,
        remediation=list(remediation or []),
        depends_on=list(depends_on or []),
        recommended_action=str(recommended_action or "").strip() or None,
    )


def _capability_payload(
    *,
    supported: bool,
    currently_available: bool,
    blocked_reason: BlockReason | None = None,
    safe_actions: list[str] | None = None,
) -> dict[str, Any]:
    actions = list(safe_actions or [])
    recommended_action = (
        blocked_reason.recommended_action
        if blocked_reason and blocked_reason.recommended_action
        else (actions[0] if actions else None)
    )
    return {
        "supported": supported,
        "currently_available": currently_available,
        "blocked_reason": blocked_reason.message if blocked_reason else None,
        "blocked_reason_detail": blocked_reason.to_dict() if blocked_reason else None,
        "safe_actions": actions,
        "recommended_action": recommended_action,
        "depends_on": list(blocked_reason.depends_on) if blocked_reason else [],
    }


def _capability_domain_payload(*capabilities: dict[str, Any]) -> dict[str, Any]:
    supported = any(bool(item.get("supported")) for item in capabilities)
    currently_available = any(bool(item.get("currently_available")) for item in capabilities)
    blocked_reason = None
    blocked_reason_detail = None
    if supported and not currently_available:
        blocked_reason = next((item.get("blocked_reason") for item in capabilities if item.get("blocked_reason")), None)
        blocked_reason_detail = next(
            (item.get("blocked_reason_detail") for item in capabilities if item.get("blocked_reason_detail")),
            None,
        )
    safe_actions: list[str] = []
    for item in capabilities:
        for action in item.get("safe_actions") or []:
            if action not in safe_actions:
                safe_actions.append(action)
    payload = _capability_payload(
        supported=supported,
        currently_available=currently_available,
        blocked_reason=None
        if blocked_reason is None
        else BlockReason(
            code=str((blocked_reason_detail or {}).get("code") or "blocked"),
            message=str(blocked_reason),
            remediation=list((blocked_reason_detail or {}).get("remediation") or []),
            depends_on=list((blocked_reason_detail or {}).get("depends_on") or []),
            recommended_action=str((blocked_reason_detail or {}).get("recommended_action") or "").strip() or None,
        ),
        safe_actions=safe_actions,
    )
    payload["blocked_reason"] = blocked_reason
    payload["blocked_reason_detail"] = blocked_reason_detail
    return payload


def build_device_capabilities(device: Any) -> dict[str, Any]:
    ma_connected = bool((getattr(device, "ma_now_playing", None) or {}).get("connected"))
    extra = _device_extra(device)
    reconnecting = bool(extra.get("reconnecting"))
    ma_reconnecting = _device_ma_reconnecting(device)
    stopping = bool(extra.get("stopping"))
    released = getattr(device, "bt_management_enabled", True) is False
    has_sink = bool(getattr(device, "has_sink", False))
    bluetooth_paired = extra.get("bluetooth_paired")

    reconnect_blocked_reason: BlockReason | None = None
    if released:
        reconnect_blocked_reason = _block_reason_payload(
            code="released",
            message="Bluetooth management is released; reclaim it before reconnecting.",
            remediation=["toggle_bt_management", "open_diagnostics"],
            depends_on=["bt_management_enabled"],
            recommended_action="toggle_bt_management",
        )
    elif bluetooth_paired is False:
        reconnect_blocked_reason = _block_reason_payload(
            code="not_paired",
            message="Device is no longer paired; put it in pairing mode and run re-pair.",
            remediation=["pair_device", "open_diagnostics"],
            depends_on=["bluetooth_paired"],
            recommended_action="pair_device",
        )
    elif reconnecting:
        reconnect_blocked_reason = _block_reason_payload(
            code="reconnecting",
            message="Reconnect is already in progress.",
            remediation=["toggle_bt_management", "open_diagnostics"],
            depends_on=["reconnect_idle"],
            recommended_action="toggle_bt_management",
        )
    elif ma_reconnecting:
        reconnect_blocked_reason = _block_reason_payload(
            code="ma_reconnecting",
            message="Music Assistant reconnect is already in progress.",
            remediation=["open_diagnostics"],
            depends_on=["sendspin_connected"],
            recommended_action="open_diagnostics",
        )
    elif stopping:
        reconnect_blocked_reason = _block_reason_payload(
            code="stopping",
            message="Device is stopping.",
            remediation=["open_diagnostics"],
            depends_on=["device_not_stopping"],
            recommended_action="open_diagnostics",
        )

    reconnect = _capability_payload(
        supported=bool(getattr(device, "bluetooth_mac", None)),
        currently_available=reconnect_blocked_reason is None and bool(getattr(device, "bluetooth_mac", None)),
        blocked_reason=reconnect_blocked_reason,
        safe_actions=(
            ["pair_device", "open_diagnostics"]
            if bluetooth_paired is False
            else ["toggle_bt_management", "open_diagnostics"]
            if reconnect_blocked_reason
            else ["reconnect"]
        ),
    )

    toggle_management_blocked_reason = (
        _block_reason_payload(
            code="stopping",
            message="Device is stopping.",
            remediation=["open_diagnostics"],
            depends_on=["device_not_stopping"],
            recommended_action="open_diagnostics",
        )
        if stopping
        else None
    )
    toggle_bt_management = _capability_payload(
        supported=True,
        currently_available=toggle_management_blocked_reason is None,
        blocked_reason=toggle_management_blocked_reason,
        safe_actions=(["open_diagnostics"] if toggle_management_blocked_reason else ["toggle_bt_management"]),
    )

    if getattr(device, "server_connected", False):
        play_pause_blocked_reason = None
    elif released:
        play_pause_blocked_reason = _block_reason_payload(
            code="released",
            message="Bluetooth management is released; reclaim it first.",
            remediation=["toggle_bt_management", "open_diagnostics"],
            depends_on=["bt_management_enabled"],
            recommended_action="toggle_bt_management",
        )
    elif ma_reconnecting:
        play_pause_blocked_reason = _block_reason_payload(
            code="ma_reconnecting",
            message="Music Assistant reconnect is in progress.",
            remediation=["open_diagnostics"],
            depends_on=["sendspin_connected"],
            recommended_action="open_diagnostics",
        )
    else:
        play_pause_blocked_reason = _block_reason_payload(
            code="daemon_disconnected",
            message="Sendspin is not connected.",
            remediation=["reconnect", "open_diagnostics"],
            depends_on=["sendspin_connected"],
            recommended_action="reconnect",
        )
    play_pause = _capability_payload(
        supported=True,
        currently_available=bool(getattr(device, "server_connected", False)),
        blocked_reason=play_pause_blocked_reason,
        safe_actions=(
            ["toggle_bt_management", "open_diagnostics"]
            if released and not getattr(device, "server_connected", False)
            else ["reconnect", "open_diagnostics"]
            if not getattr(device, "server_connected", False)
            else ["play_pause"]
        ),
    )

    volume_blocked_reason = None
    if not has_sink:
        volume_blocked_reason = _block_reason_payload(
            code="released" if released else "no_sink",
            message="Bluetooth management is released." if released else "Audio sink is not configured.",
            remediation=["toggle_bt_management", "open_diagnostics"] if released else ["reconnect", "open_diagnostics"],
            depends_on=["bt_management_enabled", "audio_sink"] if released else ["audio_sink"],
            recommended_action="toggle_bt_management" if released else "reconnect",
        )
    volume = _capability_payload(
        supported=True,
        currently_available=has_sink,
        blocked_reason=volume_blocked_reason,
        safe_actions=(
            ["toggle_bt_management", "open_diagnostics"]
            if released and not has_sink
            else ["reconnect", "open_diagnostics"]
            if not has_sink
            else ["volume"]
        ),
    )
    mute = _capability_payload(
        supported=True,
        currently_available=has_sink,
        blocked_reason=volume_blocked_reason,
        safe_actions=(
            ["toggle_bt_management", "open_diagnostics"]
            if released and not has_sink
            else ["reconnect", "open_diagnostics"]
            if not has_sink
            else ["mute"]
        ),
    )

    queue_blocked_reason = None
    if not getattr(device, "server_connected", False):
        if released:
            queue_blocked_reason = _block_reason_payload(
                code="released",
                message="Bluetooth management is released; reclaim it first.",
                remediation=["toggle_bt_management", "open_diagnostics"],
                depends_on=["bt_management_enabled"],
                recommended_action="toggle_bt_management",
            )
        elif ma_reconnecting:
            queue_blocked_reason = _block_reason_payload(
                code="ma_reconnecting",
                message="Music Assistant reconnect is in progress.",
                remediation=["open_diagnostics"],
                depends_on=["sendspin_connected"],
                recommended_action="open_diagnostics",
            )
        else:
            queue_blocked_reason = _block_reason_payload(
                code="daemon_disconnected",
                message="Sendspin is not connected.",
                remediation=["reconnect", "open_diagnostics"],
                depends_on=["sendspin_connected"],
                recommended_action="reconnect",
            )
    elif not ma_connected:
        queue_blocked_reason = _block_reason_payload(
            code="ma_disconnected",
            message="Music Assistant API is not connected.",
            remediation=["open_ma_settings", "open_diagnostics"],
            depends_on=["ma_connected"],
            recommended_action="open_ma_settings",
        )
    queue_control = _capability_payload(
        supported=bool(getattr(device, "server_connected", False)),
        currently_available=bool(getattr(device, "server_connected", False) and ma_connected),
        blocked_reason=queue_blocked_reason,
        safe_actions=["open_ma_settings", "open_diagnostics"] if not ma_connected else ["queue_control"],
    )

    diagnostics = _capability_payload(
        supported=True,
        currently_available=True,
        blocked_reason=None,
        safe_actions=["open_diagnostics", "download_diagnostics"],
    )
    disable_device = _capability_payload(
        supported=True,
        currently_available=not stopping,
        blocked_reason=toggle_management_blocked_reason,
        safe_actions=["disable_device"] if not stopping else ["open_diagnostics"],
    )

    actions = {
        "reconnect": reconnect,
        "toggle_bt_management": toggle_bt_management,
        "play_pause": play_pause,
        "volume": volume,
        "mute": mute,
        "queue_control": queue_control,
        "diagnostics": diagnostics,
        "disable_device": disable_device,
    }
    domains = {
        "connectivity": _capability_domain_payload(reconnect, toggle_bt_management),
        "playback": _capability_domain_payload(play_pause, volume, mute),
        "music_assistant": _capability_domain_payload(queue_control),
        "recovery": _capability_domain_payload(reconnect, toggle_bt_management, diagnostics),
        "diagnostics": _capability_domain_payload(diagnostics),
    }
    health_summary = getattr(device, "health_summary", None) or compute_device_health_state(device).to_dict()
    return {
        "health_state": str(health_summary.get("state") or "unknown"),
        "domains": domains,
        "actions": actions,
    }
