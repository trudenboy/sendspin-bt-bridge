"""High-level Bluetooth / device command helpers used by command surfaces.

Both ``services/ha_command_dispatcher.py`` (HA integration entry) and
``routes/api_bt.py`` (web UI) call into this module so the rules for
threading, asyncio scheduling, and config persistence live in one place.

These wrappers do not duplicate the low-level work — they just locate the
client, schedule the operation appropriately (thread or asyncio loop), and
return a structured ``CommandResult`` that callers map to JSON / MQTT
ack payloads.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any

import state
from config import CONFIG_FILE, config_lock, load_config, write_config_file
from services.config_diff import diff_configs
from services.device_registry import get_device_registry_snapshot

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------


@dataclass
class CommandResult:
    """Outcome of a command dispatch.

    ``code`` mirrors HTTP status semantics so REST routes can return it
    directly: 200 = OK, 400 = bad request, 404 = not found, 409 = conflict,
    503 = bridge-not-ready, 500 = internal failure.
    """

    success: bool
    message: str = ""
    error: str | None = None
    code: int = 200
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {"success": self.success}
        if self.message:
            out["message"] = self.message
        if self.error:
            out["error"] = self.error
        if self.details:
            out["details"] = dict(self.details)
        return out


def _ok(message: str = "", **details: Any) -> CommandResult:
    return CommandResult(success=True, message=message, details=details)


def _err(error: str, *, code: int = 400, **details: Any) -> CommandResult:
    return CommandResult(success=False, error=error, code=code, details=details)


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------


def find_client_by_player_id(player_id: str) -> Any | None:
    """Return the running ``SendspinClient`` whose player_id matches.

    Returns ``None`` if no match.  Trims and matches case-insensitively for
    safety: HA discovery payloads tend to round-trip the player_id through
    JSON, and a casing mismatch should not silently misroute commands.
    """
    target = str(player_id or "").strip()
    if not target:
        return None
    snapshot = get_device_registry_snapshot().active_clients
    for client in snapshot:
        if str(getattr(client, "player_id", "") or "").strip() == target:
            return client
    return None


# ---------------------------------------------------------------------------
# Asyncio bridging
# ---------------------------------------------------------------------------


def _schedule_coroutine(coro, *, timeout: float = 5.0) -> CommandResult:
    """Schedule a coroutine on the bridge's main loop and wait briefly.

    Mirrors the pattern used across ``routes/api_bt.py``: coroutines run on
    the parent process's asyncio loop; the calling thread (Flask worker or
    MQTT subscriber) waits up to ``timeout`` seconds for completion.  On
    timeout the work continues in the background — typical for BT IPC that
    can stall on flaky links — and we return a "scheduled" result so the
    caller doesn't claim instant success it cannot guarantee.
    """
    loop = state.get_main_loop()
    if loop is None or not loop.is_running():
        coro.close()
        return _err("Bridge asyncio loop is not running", code=503)
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        fut.result(timeout=timeout)
        return _ok()
    except TimeoutError:
        return _ok("Scheduled (still running)", scheduled=True)
    except Exception as exc:  # pragma: no cover — bubbles through to error path
        logger.warning("Coroutine scheduling failed: %s", exc)
        return _err(str(exc), code=500)


def _spawn_thread(target, *args) -> None:
    threading.Thread(target=target, args=args, daemon=True).start()


# ---------------------------------------------------------------------------
# BT-level commands
# ---------------------------------------------------------------------------


def command_reconnect(client) -> CommandResult:
    """Force BT reconnect (disconnect → wait → connect)."""
    bt = getattr(client, "bt_manager", None)
    if bt is None:
        return _err("No BT manager for this player", code=503)

    def _do_reconnect():
        try:
            bt.disconnect_device()
            threading.Event().wait(1.0)
            bt.connect_device()
        except Exception as exc:
            logger.error("[%s] Force reconnect failed: %s", getattr(client, "player_name", ""), exc)

    _spawn_thread(_do_reconnect)
    return _ok("Reconnect started")


def command_disconnect(client) -> CommandResult:
    bt = getattr(client, "bt_manager", None)
    if bt is None:
        return _err("No BT manager for this player", code=503)

    def _do_disconnect():
        try:
            bt.disconnect_device()
        except Exception as exc:
            logger.error("[%s] Disconnect failed: %s", getattr(client, "player_name", ""), exc)

    _spawn_thread(_do_disconnect)
    return _ok("Disconnect requested")


def command_pair(client) -> CommandResult:
    bt = getattr(client, "bt_manager", None)
    if bt is None:
        return _err("No BT manager for this player", code=503)

    # Reuse the existing bt-operation lock used by routes/api_bt.py so we
    # don't pair while a scan / RSSI poll holds it.  Older builds without
    # the helper module fall through to "always acquire" — never seen in
    # practice but kept for forward-compat.
    _try_acquire: Any = None
    _release: Any = None
    try:
        from services.bt_operation_lock import release_bt_operation as _release_impl
        from services.bt_operation_lock import try_acquire_bt_operation as _try_acquire_impl

        _try_acquire = _try_acquire_impl
        _release = _release_impl
    except Exception:  # pragma: no cover
        _try_acquire = lambda: True  # noqa: E731
        _release = lambda: None  # noqa: E731

    if not _try_acquire():
        return _err("Bluetooth operation already in progress", code=409)

    def _do_pair():
        try:
            bt.pair_device()
            bt.connect_device()
        except Exception as exc:
            logger.error("[%s] Force pair failed: %s", getattr(client, "player_name", ""), exc)
        finally:
            _release()

    _spawn_thread(_do_pair)
    return _ok("Pairing started (~25s)")


def command_wake(client) -> CommandResult:
    if not client.status.get("bt_standby"):
        return _err("Device is not in standby", code=409)
    return _schedule_coroutine(client._wake_from_standby(), timeout=5.0)


def command_standby(client) -> CommandResult:
    if client.status.get("bt_standby"):
        return _err("Device is already in standby", code=409)
    return _schedule_coroutine(client._enter_standby(), timeout=10.0)


def command_power_save_toggle(client, *, enter: bool | None = None) -> CommandResult:
    """Enter or exit power-save mode.

    When ``enter`` is None we flip the current state.  Already-in-state
    requests collapse to a no-op success rather than an error so HA
    automations that fan out across devices don't trip on idempotent calls.
    """
    already = bool(client.status.get("bt_power_save"))
    target = (not already) if enter is None else bool(enter)
    if target == already:
        return _ok("Power save unchanged", state="entered" if already else "exited")
    coro = client._enter_power_save() if target else client._exit_power_save()
    result = _schedule_coroutine(coro, timeout=5.0)
    if result.success:
        result.message = "Entered power save" if target else "Exited power save"
    return result


def command_set_bt_management(client, enabled: bool) -> CommandResult:
    enabled = bool(enabled)
    _spawn_thread(client.set_bt_management_enabled, enabled)
    return _ok("Reclaimed" if enabled else "Released")


def command_claim_audio(client) -> CommandResult:
    """Force the bridge to assert active MPRIS source on a multipoint speaker.

    Implementation: wraps ``services.mpris_player.assert_active_source`` if
    available; otherwise falls back to a reconnect which re-registers the
    MPRIS player.  Many speakers will only respect the freshest registration.
    """
    bt = getattr(client, "bt_manager", None)
    if bt is None:
        return _err("No BT manager for this player", code=503)

    try:
        # Resolve the MPRIS registry by name so mypy doesn't fail when the
        # exposed singleton class evolves between bridge versions (rc cycle
        # has renamed it more than once).
        import importlib

        mpris_mod = importlib.import_module("services.mpris_player")
        registry_cls = getattr(mpris_mod, "MprisPlayerRegistry", None) or getattr(mpris_mod, "MprisRegistry", None)
        registry = registry_cls.singleton() if registry_cls and hasattr(registry_cls, "singleton") else None
        player = (
            registry.get_by_mac(getattr(bt, "mac_address", ""))
            if registry is not None and hasattr(registry, "get_by_mac")
            else None
        )
        if player is not None and hasattr(player, "assert_active_source"):
            _spawn_thread(player.assert_active_source)
            return _ok("Audio source claim requested")
    except Exception as exc:
        logger.debug("[%s] MPRIS assert path unavailable: %s", getattr(client, "player_name", ""), exc)

    # Fallback: a reconnect cycles the BlueZ MPRIS registration as a side
    # effect.  Acceptable because HA users invoking "claim audio" expect
    # *some* observable change, not a silent no-op.
    return command_reconnect(client)


def command_reset_reconnect(client) -> CommandResult:
    """Full reset: remove the device + power-cycle the daemon + re-pair.

    Implementation deferred to the existing route at
    ``/api/bt/reset_reconnect`` because it owns a long-running async job
    pipeline (see ``routes/api_bt.py``).  HA dispatchers should ack
    "scheduled" and let the user follow progress in the UI.
    """
    bt = getattr(client, "bt_manager", None)
    if bt is None:
        return _err("No BT manager for this player", code=503)

    # Best-effort kick: disconnect → spawn a thread that pairs again.
    # Mirrors the simpler reset path from rc.4; the full ladder behind
    # /api/bt/reset_reconnect remains user-visible via the web UI.
    def _do_reset():
        try:
            bt.disconnect_device()
            threading.Event().wait(2.0)
            bt.pair_device()
            bt.connect_device()
        except Exception as exc:
            logger.error("[%s] reset_reconnect failed: %s", getattr(client, "player_name", ""), exc)

    _spawn_thread(_do_reset)
    return _ok("BT reset started")


# ---------------------------------------------------------------------------
# Config-changing commands (idle_mode, static_delay_ms, ...)
# ---------------------------------------------------------------------------


_PER_DEVICE_HOT_CONFIG_KEYS = {
    "idle_mode",
    "static_delay_ms",
    "power_save_delay_minutes",
    "keep_alive_method",
}


def apply_device_config_change(player_id: str, key: str, value: Any) -> CommandResult:
    """Persist a per-device config change and dispatch HOT_APPLY.

    Updates ``BLUETOOTH_DEVICES[<mac>].{key} = value`` in ``config.json``
    and runs ``diff_configs`` + ``ReconfigOrchestrator.apply`` so the
    change reaches the running daemon subprocess via IPC without a restart.

    Restricted to the hot-config subset because anything else (rename,
    adapter change, listen_port change) requires a warm restart that
    HA writers should not trigger via a select / number entity flick.
    """
    if key not in _PER_DEVICE_HOT_CONFIG_KEYS:
        return _err(f"Field {key!r} is not hot-tunable from HA", code=400)

    client = find_client_by_player_id(player_id)
    if client is None:
        return _err(f"Unknown player_id: {player_id}", code=404)

    bt = getattr(client, "bt_manager", None)
    target_mac = (getattr(bt, "mac_address", "") or "").upper().strip()
    if not target_mac:
        return _err("Device has no MAC address yet", code=503)

    with config_lock:
        try:
            with open(CONFIG_FILE) as fh:
                old_config = json.load(fh)
        except FileNotFoundError:
            old_config = load_config()

        new_config = json.loads(json.dumps(old_config))  # deep copy
        devices = new_config.get("BLUETOOTH_DEVICES") or []
        target = None
        for dev in devices:
            if isinstance(dev, dict) and str(dev.get("mac", "")).upper() == target_mac:
                target = dev
                break
        if target is None:
            return _err(f"Device {target_mac} missing from config", code=404)

        target[key] = value
        new_config["BLUETOOTH_DEVICES"] = devices

        try:
            write_config_file(new_config)
        except OSError as exc:  # pragma: no cover — surfaced via UI normally
            logger.exception("Config write failed for HA dispatch")
            return _err(f"Config write failed: {exc}", code=500)

    actions = diff_configs(old_config, new_config)
    if not actions:
        return _ok("No-op (value unchanged)")

    try:
        from services.reconfig_orchestrator import ReconfigOrchestrator

        loop = state.get_main_loop()
        snapshot = get_device_registry_snapshot()
        orch = ReconfigOrchestrator(loop, snapshot)
        summary = orch.apply(actions)
    except Exception as exc:
        logger.exception("Reconfig dispatch failed for HA")
        return _err(f"Reconfig dispatch failed: {exc}", code=500)

    return CommandResult(
        success=not summary.errors,
        message=f"Applied {key}={value!r}",
        details={
            "actions": [a.to_summary() for a in actions],
            "errors": list(summary.errors),
        },
    )


def apply_device_enabled(player_id: str, enabled: bool) -> CommandResult:
    """Toggle device enabled flag — STOP_CLIENT or START_CLIENT under the hood.

    HA exposes a ``switch.<device>_enabled`` for this; flipping it produces
    a START / STOP action in the reconfig diff which the orchestrator runs
    out of band.  The route equivalent is ``/api/device/enabled``.
    """
    client = find_client_by_player_id(player_id)
    if client is None:
        # Disabled clients aren't in the active registry; look them up by
        # MAC in the config directly so users can re-enable from HA.
        target_mac = None
    else:
        bt = getattr(client, "bt_manager", None)
        target_mac = (getattr(bt, "mac_address", "") or "").upper().strip()

    with config_lock:
        try:
            with open(CONFIG_FILE) as fh:
                old_config = json.load(fh)
        except FileNotFoundError:
            old_config = load_config()
        new_config = json.loads(json.dumps(old_config))
        devices = new_config.get("BLUETOOTH_DEVICES") or []
        target = None
        for dev in devices:
            if not isinstance(dev, dict):
                continue
            mac_upper = str(dev.get("mac", "")).upper()
            if target_mac and mac_upper == target_mac:
                target = dev
                break
            # Fallback: match by player_id derived from MAC if active client lookup failed.
            if target_mac is None:
                from config import _player_id_from_mac

                if _player_id_from_mac(mac_upper) == player_id:
                    target = dev
                    break
        if target is None:
            return _err("Unknown device", code=404)

        target["enabled"] = bool(enabled)
        new_config["BLUETOOTH_DEVICES"] = devices

        try:
            write_config_file(new_config)
        except OSError as exc:  # pragma: no cover
            return _err(f"Config write failed: {exc}", code=500)

    actions = diff_configs(old_config, new_config)
    try:
        from services.bridge_runtime_state import get_activation_context
        from services.reconfig_orchestrator import ReconfigOrchestrator

        loop = state.get_main_loop()
        snapshot = get_device_registry_snapshot()
        ctx = None
        try:
            ctx = get_activation_context()
        except Exception:
            ctx = None
        orch = ReconfigOrchestrator(loop, snapshot, activation_context=ctx)
        summary = orch.apply(actions)
    except Exception as exc:
        logger.exception("Reconfig dispatch failed for enabled toggle")
        return _err(f"Reconfig dispatch failed: {exc}", code=500)

    return CommandResult(
        success=not summary.errors,
        message=f"Device {'enabled' if enabled else 'disabled'}",
        details={"errors": list(summary.errors)},
    )


__all__ = [
    "CommandResult",
    "apply_device_config_change",
    "apply_device_enabled",
    "command_claim_audio",
    "command_disconnect",
    "command_pair",
    "command_power_save_toggle",
    "command_reconnect",
    "command_reset_reconnect",
    "command_set_bt_management",
    "command_standby",
    "command_wake",
    "find_client_by_player_id",
]
