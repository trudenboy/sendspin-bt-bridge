"""Commands for one bridge speaker: reconnect, re-pair, release/reclaim, standby, power-save…"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
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


def require_action(device_id: str, action: str) -> None:
    """409 when the player's backend cannot do *action*; unknown ids fall through to the command's 404."""
    from sendspin_bridge.config.players import BLUETOOTH, find_player_by_id, supported_actions_for

    try:
        client = find_client(device_id)
        runtime_type = getattr(client, "backend_type", None)
        backend_type = runtime_type if isinstance(runtime_type, str) and runtime_type else BLUETOOTH
    except UseCaseError:
        from sendspin_bridge.config import load_config

        player = find_player_by_id(load_config(), device_id)
        backend = player.get("backend") if player else None
        backend_type = str(backend.get("type") or "") if isinstance(backend, dict) else ""
    if backend_type and action not in supported_actions_for(backend_type):
        raise UseCaseError(
            409, "not_supported_for_backend", f"A {backend_type} player does not support {action.replace('_', ' ')}"
        )


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
    player that is not running starts it the way a settings save does, and
    asks for a restart only when the bridge cannot start it here.
    """
    try:
        client = find_client(device_id)
    except UseCaseError:
        # A disabled speaker has no running client; find it in the config.
        from sendspin_bridge.config import load_config
        from sendspin_bridge.config.players import find_player_by_id

        configured = find_player_by_id(load_config(), device_id)
        if configured is None:
            raise
        started = _persist_enabled_and_apply(device_id, str(configured.get("name") or ""), enabled)
        _sync_ha_options_later()
        return {"enabled": enabled, "restart_required": not started}
    _persist_device_enabled(str(client.player_name), enabled)
    if not enabled:
        threading.Thread(target=client.set_bt_management_enabled, args=(False,), daemon=True).start()
    else:
        _clear_never_paired_state(client)
    _sync_ha_options_later()
    return {"enabled": enabled, "restart_required": not enabled}


def _persist_enabled_and_apply(device_id: str, player_name: str, enabled: bool) -> bool:
    """Persist the flag, then apply the config change online; whether *device_id* was started.

    The before/after snapshots and the diff are taken under ``config_lock`` so
    a concurrent save cannot slip in between; the orchestrator runs after the
    lock is released (it reads the config itself).
    """
    import sendspin_bridge.config as config_mod
    from sendspin_bridge.config.store import ConfigStore
    from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
    from sendspin_bridge.services.infrastructure.config_diff import ActionKind, diff_configs
    from sendspin_bridge.services.lifecycle import bridge_runtime_state
    from sendspin_bridge.services.lifecycle import reconfig_orchestrator as ro

    with config_mod.config_lock:
        store = ConfigStore(config_mod.CONFIG_FILE)
        before = store.load()
        _persist_device_enabled(player_name, enabled)
        actions = diff_configs(before, store.load())
    ours = [a.to_summary() for a in actions if a.kind is ActionKind.START_CLIENT and a.player_id == device_id]
    if not ours:
        return False
    summary = ro.ReconfigOrchestrator(
        bridge_runtime_state.get_main_loop(),
        get_device_registry_snapshot(),
        activation_context=bridge_runtime_state.get_activation_context(),
    ).apply(actions)
    started = any(entry in summary.started for entry in ours)
    if started:
        _drop_from_disabled_list(device_id)
    return started


def _change_player(device_id: str, change: Callable[[dict], None]) -> tuple[Any, dict]:
    """Change one player's config entry and apply the change live; (its running client or None, new config).

    *change* gets the player dict inside the config being written; a ``UseCaseError`` it raises
    leaves the file untouched. 404 when no player has *device_id* (a running speaker's id or its
    config id).
    """
    import sendspin_bridge.config as config_mod
    from sendspin_bridge.config.players import find_player_by_id, find_player_by_mac
    from sendspin_bridge.config.store import ConfigStore
    from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
    from sendspin_bridge.services.infrastructure.config_diff import diff_configs
    from sendspin_bridge.services.lifecycle import bridge_runtime_state
    from sendspin_bridge.services.lifecycle import reconfig_orchestrator as ro

    try:
        client = find_client(device_id)
    except UseCaseError:
        client = None
    config_id = getattr(client, "config_id", None)
    wanted = config_id if isinstance(config_id, str) and config_id else device_id

    def _find(cfg: dict) -> dict | None:
        player = find_player_by_id(cfg, wanted)
        mac = getattr(_bt_manager(client), "mac_address", None) if client is not None else None
        if player is None and isinstance(mac, str):
            player = find_player_by_mac(cfg, mac)
        return player

    if _find(config_mod.load_config()) is None:
        raise UseCaseError(404, "unknown_device", f"Unknown device: {device_id}")

    def _set(cfg: dict) -> None:
        player = _find(cfg)
        if player is not None:
            change(player)

    with config_mod.config_lock:
        before = ConfigStore(config_mod.CONFIG_FILE).load()
        config_mod.update_config(_set)
        after = ConfigStore(config_mod.CONFIG_FILE).load()
        actions = diff_configs(before, after)
    if actions:
        ro.ReconfigOrchestrator(
            bridge_runtime_state.get_main_loop(),
            get_device_registry_snapshot(),
            activation_context=bridge_runtime_state.get_activation_context(),
        ).apply(actions)
    _sync_ha_options_later()
    return client, after


def set_playout_sync(device_id: str, enabled: bool) -> dict[str, Any]:
    """Opt the speaker in or out of playout sync, and restart its player to apply it.

    ``False`` is stored; ``True`` clears the key, so the speaker follows
    ``PLAYOUT_SYNC`` again.  The warm restart is that one player's only.
    """
    from sendspin_bridge.application import status

    def _set(player: dict) -> None:
        if enabled:
            player.pop("playout_sync_enabled", None)
        else:
            player["playout_sync_enabled"] = False

    client, after = _change_player(device_id, _set)
    device = status._device(client, after) if client is not None else None
    return {"playout_sync_enabled": enabled, "device": device}


def forget_air_calibrations(
    device_id: str, codec: str | None = None, rate: int | None = None, adapter: str | None = None
) -> None:
    """Forget the speaker's air calibration for (codec, rate, adapter), or all of them without *codec*.

    Applied live: the running daemon gets the entry that still matches (or null). 404 when the
    speaker or (for one) the calibration is unknown.
    """
    from sendspin_bridge.config.players import forget_air_latency

    def _forget(player: dict) -> None:
        remaining, removed = forget_air_latency(player.get("air_to_sound_latency"), codec, rate, adapter)
        if codec is not None and not removed:
            raise UseCaseError(
                404, "unknown_calibration", f"No air calibration for {codec} {rate} Hz on adapter {adapter}"
            )
        if remaining:
            player["air_to_sound_latency"] = remaining
        else:
            player.pop("air_to_sound_latency", None)

    _change_player(device_id, _forget)


def _drop_from_disabled_list(device_id: str, also: Callable[[dict], bool] | None = None) -> None:
    """The disabled list is built at startup; a player started or removed since leaves it now.

    Entries match by player id; ``also`` matches more (a removed speaker by its MAC).
    """
    from sendspin_bridge.config import _player_id_from_mac
    from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot, set_disabled_devices

    def _entry_id(entry: dict) -> str:
        mac = entry.get("mac")
        return str(entry.get("id") or (_player_id_from_mac(str(mac)) if mac else ""))

    disabled = get_device_registry_snapshot().disabled_devices
    remaining = [d for d in disabled if _entry_id(d) != device_id and not (also and also(d))]
    if len(remaining) != len(disabled):
        set_disabled_devices(remaining)


def remove(device_id: str) -> dict[str, Any]:
    """Take the speaker off the bridge: its player stops and its Bluetooth bond is removed.

    Goes through the ordinary settings save, which already stops a removed
    speaker's player and unpairs it (an orphaned bond would keep the speaker
    from pairing with anything else). Works for disabled speakers too.
    """
    from sendspin_bridge.application import config as config_uc
    from sendspin_bridge.config import load_config
    from sendspin_bridge.config.players import BLUETOOTH, find_player_by_id, find_player_by_mac, normalize_mac

    config = load_config()
    target = find_player_by_id(config, device_id)
    if target is None:
        # A running speaker's id is the one its client reports; its MAC names the player.
        try:
            client_mac = str(getattr(getattr(find_client(device_id), "bt_manager", None), "mac_address", "") or "")
        except UseCaseError:
            client_mac = ""
        target = find_player_by_mac(config, client_mac) if client_mac else None
    if target is None:
        raise UseCaseError(404, "unknown_device", f"Unknown device: {device_id}")
    raw_backend = target.get("backend")
    backend: dict[str, Any] = raw_backend if isinstance(raw_backend, dict) else {}
    target_mac = normalize_mac(backend.get("mac")) if backend.get("type") == BLUETOOTH else ""

    config.pop("PLAYER_STATE", None)
    config["players"] = [p for p in config.get("players") or [] if p is not target]
    result = config_uc.save_config(config)
    # The disabled list is built at startup; a removed player leaves it now —
    # by its MAC, or by its id (a local output has no MAC).
    _drop_from_disabled_list(
        str(target.get("id") or device_id),
        also=lambda d: bool(target_mac) and normalize_mac(d.get("mac")) == target_mac,
    )
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
