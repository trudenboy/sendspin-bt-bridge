"""The bridge's current state as API v1 models — one build serves REST and events."""

from __future__ import annotations

import os
import socket
from dataclasses import asdict

from sendspin_bridge.application import diagnostics as _diag
from sendspin_bridge.application.models import (
    Bridge,
    BridgeStatus,
    BridgeVerdict,
    Device,
    DisabledDevice,
    Group,
    StartupProgress,
    UpdateInfo,
)
from sendspin_bridge.application.models.devices import LinkStatus
from sendspin_bridge.config import load_config
from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
from sendspin_bridge.services.diagnostics.status_model import ma_account_state, verdict_from_issues
from sendspin_bridge.services.ipc.bridge_state_model import build_bridge_state_model
from sendspin_bridge.services.lifecycle.status_snapshot import build_bridge_snapshot


def bridge_name(config: dict) -> str:
    """BRIDGE_NAME as the bridge uses it: ``auto``/``hostname``/empty mean the host name."""
    raw = str(config.get("BRIDGE_NAME") or os.getenv("BRIDGE_NAME") or "").strip()
    return socket.gethostname() if raw.lower() in ("", "auto", "hostname") else raw


def _disabled(entry: dict, config: dict) -> DisabledDevice:
    """A disabled player, with its output and room from its config entry when the startup list lacks them."""
    from sendspin_bridge.config import _player_id_from_mac
    from sendspin_bridge.config.players import PULSE_SINK, find_player_by_id

    mac = entry.get("mac")
    player_id = entry.get("id") or (_player_id_from_mac(str(mac)) if mac else None)
    player = find_player_by_id(config, str(player_id)) if player_id else None
    from_config: dict = {}
    if player:
        raw_backend = player.get("backend")
        backend: dict = raw_backend if isinstance(raw_backend, dict) else {}
        if not entry.get("sink") and backend.get("type") == PULSE_SINK and backend.get("sink"):
            from_config["sink"] = backend["sink"]
        if not entry.get("room_name") and player.get("room_name"):
            from_config["room_name"] = player["room_name"]
    return DisabledDevice.model_validate({**entry, **from_config, "id": player_id})


def _disabled_running(device) -> DisabledDevice:
    """A disabled speaker whose client still runs: its commands take the client's id."""
    return DisabledDevice.model_validate(
        {
            "id": device.player_id,
            "mac": device.bluetooth_mac,
            "player_name": device.player_name,
            "adapter": device.bluetooth_adapter,
            "room_name": device.room_name,
            "enabled": False,
        }
    )


def _device(running_device, config: dict) -> Device:
    """The /devices entry for a running speaker, tied to its configuration entry.

    A Bluetooth speaker is found by its MAC; a pulse_sink player's runtime id
    is its config id.
    """
    from sendspin_bridge.config.players import (
        PULSE_SINK,
        find_player_by_id,
        find_player_by_mac,
        valid_air_latency_entries,
        valid_air_learned_entries,
    )

    status = running_device.to_dict()
    mac = status.get("bluetooth_mac")
    if status.get("backend_type") == PULSE_SINK:
        player = find_player_by_id(config, str(status.get("player_id") or ""))
    else:
        player = find_player_by_mac(config, str(mac)) if mac else None
    return Device.from_status(
        status,
        config_id=str(player["id"]) if player and player.get("id") else None,
        playout_sync_enabled=not (player and player.get("playout_sync_enabled") is False),
        air_to_sound_latency=valid_air_latency_entries(player.get("air_to_sound_latency")) if player else [],
        air_learned_latency=valid_air_learned_entries(player.get("air_learned_latency")) if player else [],
    )


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
    # A speaker disabled since the bridge started keeps its client until a
    # restart; it belongs with the disabled speakers all the same.
    running = [d for d in snapshot.devices if d.enabled]
    off = [d for d in snapshot.devices if not d.enabled]
    info = state.get_bridge_system_info()
    verdict = verdict_from_issues(guidance.get("issue_groups") or [])
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
        music_assistant_account=LinkStatus(
            **asdict(
                ma_account_state(
                    configured=bool(config.get("MA_API_URL") and config.get("MA_API_TOKEN")),
                    connected=bool(snapshot.ma_connected),
                )
            )
        ),
        verdict=BridgeVerdict(level=verdict.level, count=verdict.count),
        ma_web_url=snapshot.ma_web_url,
        device_count=len(running),
        disabled_devices=[_disabled(d, config) for d in snapshot.disabled_devices]
        + [_disabled_running(d) for d in off],
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
        devices=[_device(device, config) for device in running],
        groups=[Group.from_snapshot(group.to_dict()) for group in snapshot.groups],
    )


def find_client(device_id: str):
    """The running client for *device_id*, or raise 404."""
    from sendspin_bridge.application.errors import UseCaseError

    for client in get_device_registry_snapshot().active_clients:
        if str(getattr(client, "player_id", "") or "") == device_id:
            return client
    raise UseCaseError(404, "unknown_device", f"Unknown device: {device_id}")
