"""The bridge's current state as API v1 models — one build serves REST and events."""

from __future__ import annotations

import os
import socket

from sendspin_bridge.application import diagnostics as _diag
from sendspin_bridge.application.models import (
    Bridge,
    BridgeStatus,
    Device,
    DisabledDevice,
    Group,
    StartupProgress,
    UpdateInfo,
)
from sendspin_bridge.config import load_config


def bridge_name(config: dict) -> str:
    """BRIDGE_NAME as the bridge uses it: ``auto``/``hostname``/empty mean the host name."""
    raw = str(config.get("BRIDGE_NAME") or os.getenv("BRIDGE_NAME") or "").strip()
    return socket.gethostname() if raw.lower() in ("", "auto", "hostname") else raw
from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
from sendspin_bridge.services.ipc.bridge_state_model import build_bridge_state_model
from sendspin_bridge.services.lifecycle.status_snapshot import build_bridge_snapshot


def build_status(*, auth_enabled: bool) -> BridgeStatus:
    """Bridge, devices and groups, with guidance — what the dashboard renders."""
    from sendspin_bridge.bridge import state

    registry = get_device_registry_snapshot()
    snapshot = build_bridge_snapshot(registry.active_clients)
    config = load_config()
    preflight = _diag._sampled_preflight_status()
    startup = snapshot.startup_progress.to_dict() if snapshot.startup_progress else {}
    bridge_state = build_bridge_state_model(
        config=config,
        preflight=preflight,
        devices=snapshot.devices,
        ma_connected=snapshot.ma_connected,
        runtime_mode=snapshot.runtime_mode,
        startup_progress=startup,
        update_available=bool(snapshot.update_available),
        disabled_devices=snapshot.disabled_devices,
    )
    onboarding = _diag._build_onboarding_assistant_payload(
        preflight=preflight,
        config=config,
        devices=snapshot.devices,
        runtime_mode=snapshot.runtime_mode,
        ma_connected=snapshot.ma_connected,
        bridge_state=bridge_state,
    )
    recovery = _diag._build_recovery_assistant_payload(
        preflight=preflight,
        config=config,
        devices=snapshot.devices,
        onboarding_assistant=onboarding,
        startup_progress=startup,
        bridge_state=bridge_state,
    )
    guidance = _diag._build_operator_guidance_payload(
        config=config,
        devices=snapshot.devices,
        disabled_devices=snapshot.disabled_devices,
        onboarding_assistant=onboarding,
        recovery_assistant=recovery,
        startup_progress=startup,
        preflight=preflight,
        runtime_mode=snapshot.runtime_mode,
        ma_connected=snapshot.ma_connected,
        bridge_state=bridge_state,
    )
    info = state.get_bridge_system_info()
    bridge = Bridge(
        name=bridge_name(config),
        version=info.get("version", ""),
        build_date=info.get("build_date", ""),
        hostname=info.get("hostname", ""),
        ip_address=info.get("ip_address", ""),
        uptime=info.get("uptime", ""),
        runtime=info.get("runtime", "unknown"),
        runtime_mode=snapshot.runtime_mode,
        config_schema_version=info.get("config_schema_version"),
        ipc_protocol_version=info.get("ipc_protocol_version"),
        auth_enabled=auth_enabled,
        ma_connected=snapshot.ma_connected,
        ma_web_url=snapshot.ma_web_url,
        device_count=len(snapshot.devices),
        disabled_devices=[DisabledDevice.model_validate(d) for d in snapshot.disabled_devices],
        startup=StartupProgress(**startup) if startup else None,
        update_available=UpdateInfo(**snapshot.update_available) if snapshot.update_available else None,
        mock_runtime=snapshot.mock_runtime.to_dict() if snapshot.mock_runtime else None,
        preflight=preflight,
        state_model=bridge_state.to_dict(),
        guidance=guidance,
        onboarding=onboarding,
        recovery=recovery,
    )
    return BridgeStatus(
        bridge=bridge,
        devices=[Device.from_status(device.to_dict()) for device in snapshot.devices],
        groups=[Group.from_snapshot(group.to_dict()) for group in snapshot.groups],
    )


def find_client(device_id: str):
    """The running client for *device_id*, or raise 404."""
    from sendspin_bridge.application.errors import UseCaseError

    for client in get_device_registry_snapshot().active_clients:
        if str(getattr(client, "player_id", "") or "") == device_id:
            return client
    raise UseCaseError(404, "unknown_device", f"Unknown device: {device_id}")
