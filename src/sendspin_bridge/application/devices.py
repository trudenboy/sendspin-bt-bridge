"""Commands for one bridge speaker: reconnect, re-pair, release/reclaim, standby, power-save…"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from sendspin_bridge.application.bluetooth import _lease, start_leased_job
from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.jobs import Job, JobContext
from sendspin_bridge.application.runtime import run_on_loop
from sendspin_bridge.application.status import find_client
from sendspin_bridge.services import persist_device_enabled as _persist_device_enabled
from sendspin_bridge.services.bluetooth import persist_device_released as _persist_device_released
from sendspin_bridge.services.bluetooth.pairing_quiesce import quiesce_adapter_peers

logger = logging.getLogger(__name__)


def _bt_manager(client):
    bt = getattr(client, "bt_manager", None)
    if bt is None:
        raise UseCaseError(409, "no_bluetooth", "This speaker has no Bluetooth manager")
    return bt


def _sync_ha_options_later() -> None:
    """Mirror the change into the HA add-on options so the Configuration page agrees."""
    try:
        from sendspin_bridge.application.config import sync_ha_options
        from sendspin_bridge.config import load_config

        cfg = load_config()
        threading.Thread(target=sync_ha_options, args=(cfg,), daemon=True).start()
    except Exception as exc:
        logger.debug("sync HA options failed: %s", exc)


def reconnect(device_id: str) -> Job:
    """Disconnect and connect again without re-pairing."""
    client = find_client(device_id)
    bt = _bt_manager(client)
    lease = _lease(f"reconnect {client.player_name}", bt_manager=bt)

    def _work(_ctx: JobContext):
        bt.disconnect_device()
        time.sleep(1)
        return {"connected": bool(bt.connect_device())}

    return start_leased_job("device.reconnect", _work, lease=lease, subject=device_id)


def repair(device_id: str, *, quiesce_adapter: bool = False) -> Job:
    """Pair this speaker again (it must be in pairing mode), then connect."""
    client = find_client(device_id)
    bt = _bt_manager(client)
    lease = _lease(f"pair {client.player_name}", bt_manager=bt)
    adapter_mac = getattr(bt, "effective_adapter_mac", "") or ""
    target_mac = getattr(bt, "mac_address", "") or None

    def _work(_ctx: JobContext):
        if quiesce_adapter and adapter_mac:
            with quiesce_adapter_peers(adapter_mac, exclude_mac=target_mac):
                paired = bt.pair_device()
                connected = bt.connect_device()
        else:
            paired = bt.pair_device()
            connected = bt.connect_device()
        if not paired:
            raise UseCaseError(422, "pairing_failed", "Pairing failed — is the speaker in pairing mode?")
        return {"paired": True, "connected": bool(connected)}

    return start_leased_job("device.repair", _work, lease=lease, subject=device_id)


def set_management(device_id: str, enabled: bool) -> dict[str, Any]:
    """Release the speaker to the host (other devices may use it) or reclaim it."""
    client = find_client(device_id)
    threading.Thread(target=client.set_bt_management_enabled, args=(enabled,), daemon=True).start()
    # An operator's release must never be auto-reclaimed (#349/#350): record who released it.
    _persist_device_released(str(client.player_name), not enabled, released_by=None if enabled else "user")
    _sync_ha_options_later()
    return {"management_enabled": enabled}


def wake(device_id: str) -> None:
    """Wake from idle standby: reconnect Bluetooth and restart the player."""
    client = find_client(device_id)
    if not client.status.get("bt_standby"):
        raise UseCaseError(409, "not_in_standby", "Device is not in standby")
    run_on_loop(client._wake_from_standby(), timeout=5.0, description="wake from standby")


def standby(device_id: str) -> None:
    """Disconnect Bluetooth and park the player on a null sink."""
    client = find_client(device_id)
    if client.status.get("bt_standby"):
        raise UseCaseError(409, "already_in_standby", "Device is already in standby")
    run_on_loop(client._enter_standby(), timeout=10.0, description="enter standby")


def set_power_save(device_id: str, enabled: bool) -> dict[str, Any]:
    """Suspend (or resume) the PulseAudio sink while keeping the Bluetooth link up."""
    client = find_client(device_id)
    already = bool(client.status.get("bt_power_save"))
    if enabled != already:
        coro = client._enter_power_save() if enabled else client._exit_power_save()
        run_on_loop(coro, timeout=5.0, description="power save")
    return {"power_save": enabled}


def _clear_never_paired_state(client) -> None:
    """Re-enabling an auto-disabled never-paired speaker starts its session clean (#263)."""
    try:
        client.update_status(
            {
                "never_paired": False,
                "never_paired_since": None,
                "reconnect_attempt": 0,
                "last_error": None,
                "last_error_at": None,
            }
        )
    except Exception as exc:
        logger.debug("Could not clear never_paired status for %s: %s", client.player_name, exc)
    bt = getattr(client, "bt_manager", None)
    if bt is not None:
        bt._has_ever_paired_since_start = False
        if getattr(bt, "management_enabled", True) is False:
            client.set_bt_management_enabled(True)


def set_enabled(device_id: str, enabled: bool) -> dict[str, Any]:
    """Enable or disable the speaker in the configuration.

    Disabling stops the player at once (Music Assistant drops it); enabling a
    speaker that is not running takes a bridge restart.
    """
    try:
        client = find_client(device_id)
    except UseCaseError:
        # A disabled speaker has no running client; find it in the config.
        from sendspin_bridge.config import _player_id_from_mac, load_config

        configured = next(
            (
                d
                for d in load_config().get("BLUETOOTH_DEVICES", [])
                if d.get("mac") and _player_id_from_mac(str(d["mac"])) == device_id
            ),
            None,
        )
        if configured is None:
            raise
        _persist_device_enabled(str(configured.get("player_name") or ""), enabled)
        _sync_ha_options_later()
        return {"enabled": enabled, "restart_required": True}
    _persist_device_enabled(str(client.player_name), enabled)
    if not enabled:
        threading.Thread(target=client.set_bt_management_enabled, args=(False,), daemon=True).start()
    else:
        _clear_never_paired_state(client)
    _sync_ha_options_later()
    return {"enabled": enabled, "restart_required": not enabled}


def remove(device_id: str) -> dict[str, Any]:
    """Take the speaker off the bridge: its player stops and its Bluetooth bond is removed.

    Goes through the ordinary settings save, which already stops a removed
    speaker's player and unpairs it (an orphaned bond would keep the speaker
    from pairing with anything else). Works for disabled speakers too.
    """
    from sendspin_bridge.application import config as config_uc
    from sendspin_bridge.config import _player_id_from_mac, load_config

    config = load_config()
    entries = list(config.get("BLUETOOTH_DEVICES") or [])
    kept = [d for d in entries if not (d.get("mac") and _player_id_from_mac(str(d["mac"])) == device_id)]
    if len(kept) == len(entries):
        raise UseCaseError(404, "unknown_device", f"Unknown device: {device_id}")
    config["BLUETOOTH_DEVICES"] = kept
    result = config_uc.save_config(config)
    # The disabled list is built at startup; a removed speaker leaves it now.
    from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot, set_disabled_devices

    disabled = get_device_registry_snapshot().disabled_devices
    remaining = [d for d in disabled if not (d.get("mac") and _player_id_from_mac(str(d["mac"])) == device_id)]
    if len(remaining) != len(disabled):
        set_disabled_devices(remaining)
    return {"removed": True, "reconfig": result.get("reconfig") or {}}


def claim_audio(device_id: str) -> None:
    """On a multipoint speaker, make the bridge the active AVRCP source (MPRIS 'Playing')."""
    client = find_client(device_id)
    mac = getattr(getattr(client, "bt_manager", None), "mac_address", "") or ""
    from sendspin_bridge.services.audio.mpris_player import get_registry

    player = get_registry().get(mac.upper())
    if player is None:
        raise UseCaseError(409, "not_connected", "No MPRIS player for this device — is the speaker connected?")
    run_on_loop(player.set_playback_status("Playing"), timeout=2.0, description="claim audio")
