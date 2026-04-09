"""Registry for machine-readable operator guidance issues."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GuidanceIssueDefinition:
    key: str
    layer: str
    priority: int
    severity: str
    default_reason_codes: tuple[str, ...] = ()


ISSUE_REGISTRY: dict[str, GuidanceIssueDefinition] = {
    "runtime_access": GuidanceIssueDefinition(
        key="runtime_access",
        layer="runtime_access",
        priority=10,
        severity="error",
        default_reason_codes=("runtime_access_unavailable",),
    ),
    "runtime-access": GuidanceIssueDefinition(
        key="runtime-access",
        layer="runtime_access",
        priority=10,
        severity="error",
        default_reason_codes=("runtime_access_unavailable",),
    ),
    "bluetooth_unavailable": GuidanceIssueDefinition(
        key="bluetooth_unavailable",
        layer="bluetooth",
        priority=20,
        severity="error",
        default_reason_codes=("bluetooth_adapter_unavailable",),
    ),
    "bluetooth": GuidanceIssueDefinition(
        key="bluetooth",
        layer="bluetooth",
        priority=20,
        severity="error",
        default_reason_codes=("bluetooth_adapter_unavailable",),
    ),
    "audio_unavailable": GuidanceIssueDefinition(
        key="audio_unavailable",
        layer="audio",
        priority=30,
        severity="warning",
        default_reason_codes=("audio_backend_unavailable",),
    ),
    "audio": GuidanceIssueDefinition(
        key="audio",
        layer="audio",
        priority=30,
        severity="warning",
        default_reason_codes=("audio_backend_unavailable",),
    ),
    "missing_sink": GuidanceIssueDefinition(
        key="missing_sink",
        layer="sink_verification",
        priority=40,
        severity="error",
        default_reason_codes=("no_sink",),
    ),
    "sink_system_muted": GuidanceIssueDefinition(
        key="sink_system_muted",
        layer="sink_verification",
        priority=42,
        severity="warning",
        default_reason_codes=("sink_muted_at_system_level",),
    ),
    "device-disconnected": GuidanceIssueDefinition(
        key="device-disconnected",
        layer="sink_verification",
        priority=40,
        severity="warning",
        default_reason_codes=("bluetooth_disconnected",),
    ),
    "playback-degraded": GuidanceIssueDefinition(
        key="playback-degraded",
        layer="sink_verification",
        priority=45,
        severity="warning",
        default_reason_codes=("playback_without_audio", "recent_audio_stall"),
    ),
    "daemon-disconnected": GuidanceIssueDefinition(
        key="daemon-disconnected",
        layer="sink_verification",
        priority=50,
        severity="warning",
        default_reason_codes=("daemon_disconnected",),
    ),
    "transport_down": GuidanceIssueDefinition(
        key="transport_down",
        layer="sink_verification",
        priority=50,
        severity="error",
        default_reason_codes=("daemon_disconnected",),
    ),
    "sendspin_port_unreachable": GuidanceIssueDefinition(
        key="sendspin_port_unreachable",
        layer="transport",
        priority=48,
        severity="error",
        default_reason_codes=("server_connection_refused",),
    ),
    "repair_required": GuidanceIssueDefinition(
        key="repair_required",
        layer="sink_verification",
        priority=55,
        severity="warning",
        default_reason_codes=("not_paired",),
    ),
    "disconnected": GuidanceIssueDefinition(
        key="disconnected",
        layer="sink_verification",
        priority=56,
        severity="warning",
        default_reason_codes=("bluetooth_disconnected",),
    ),
    "auto_released": GuidanceIssueDefinition(
        key="auto_released",
        layer="bridge_control",
        priority=60,
        severity="warning",
        default_reason_codes=("bt_management_disabled", "management_auto_disabled"),
    ),
    "device-released": GuidanceIssueDefinition(
        key="device-released",
        layer="bridge_control",
        priority=60,
        severity="info",
        default_reason_codes=("bt_management_disabled",),
    ),
    "needs_attention": GuidanceIssueDefinition(
        key="needs_attention",
        layer="sink_verification",
        priority=65,
        severity="warning",
        default_reason_codes=("device_needs_attention",),
    ),
    "duplicate_device": GuidanceIssueDefinition(
        key="duplicate_device",
        layer="bridge_control",
        priority=35,
        severity="warning",
        default_reason_codes=("duplicate_player_id",),
    ),
    "all-disabled": GuidanceIssueDefinition(
        key="all-disabled",
        layer="bridge_control",
        priority=70,
        severity="info",
        default_reason_codes=("all_devices_disabled",),
    ),
    "ma_auth": GuidanceIssueDefinition(
        key="ma_auth",
        layer="ma_auth",
        priority=80,
        severity="warning",
        default_reason_codes=("ma_disconnected",),
    ),
    "ma-auth": GuidanceIssueDefinition(
        key="ma-auth",
        layer="ma_auth",
        priority=80,
        severity="warning",
        default_reason_codes=("ma_disconnected",),
    ),
}


def issue_sort_priority(key: str) -> int:
    definition = ISSUE_REGISTRY.get(key)
    return definition.priority if definition else 999


def build_issue_context(
    key: str,
    *,
    severity: str | None = None,
    device_names: list[str] | None = None,
    reason_codes: list[str] | None = None,
    all_devices_affected: bool | None = None,
) -> dict[str, Any]:
    definition = ISSUE_REGISTRY.get(key)
    return {
        "layer": definition.layer if definition else "unclassified",
        "priority": issue_sort_priority(key),
        "severity": severity or (definition.severity if definition else "warning"),
        "reason_codes": list(reason_codes or (definition.default_reason_codes if definition else ())),
        "device_names": list(device_names or []),
        "affected_count": len(device_names or []),
        "all_devices_affected": bool(all_devices_affected),
    }
