"""
Bluetooth API Blueprint for sendspin-bt-bridge.

All /api/bt/* routes and the helper functions they depend on.
"""

import concurrent.futures
import json
import logging
import re
import threading
import time
import uuid

from flask import Blueprint, jsonify, request

from sendspin_bridge.bluetooth.adapter_address import _dbus_get_adapter_address
from sendspin_bridge.bluetooth.adapter_map import hci_for
from sendspin_bridge.bluetooth.adapter_session import AdapterHandle
from sendspin_bridge.bluetooth.bluez import Adapter, Outcome, get_bluez
from sendspin_bridge.bluetooth.pairing import PairOptions, PairSession, PairTimings
from sendspin_bridge.config import CONFIG_FILE, config_lock, load_config
from sendspin_bridge.services import persist_device_enabled as _persist_device_enabled
from sendspin_bridge.services.bluetooth import (
    COMMON_BT_PAIR_PINS,
    build_hci_map,
    classify_audio_capability,
    get_adapter_alias,
    list_bt_adapters,
)
from sendspin_bridge.services.bluetooth import bt_remove_device as _bt_remove_device
from sendspin_bridge.services.bluetooth import persist_device_released as _persist_device_released
from sendspin_bridge.services.bluetooth.bt_class_of_device import read_device_class as _read_device_class
from sendspin_bridge.services.bluetooth.pairing_quiesce import quiesce_adapter_peers
from sendspin_bridge.services.lifecycle.async_job_state import (
    create_scan_job,
    finish_scan_job,
    get_scan_job,
    is_scan_running,
)
from sendspin_bridge.web.routes._helpers import get_client_or_error, validate_adapter, validate_mac
from sendspin_bridge.web.routes.api_status import invalidate_preflight_probe

logger = logging.getLogger(__name__)

bt_bp = Blueprint("api_bt", __name__)

_scan_lock = threading.Lock()


def _canonicalise_mac_input(mac: str) -> str | None:
    """Normalise a user-supplied MAC into the canonical XX:XX:XX:XX:XX:XX form.

    Accepts colon, dash, or no-separator forms (mirroring the
    ``MprisRegistry`` tolerance) and returns ``None`` if the input does
    not contain exactly 12 hex digits.  The caller still runs
    ``validate_mac`` on the result so any out-of-band malformed payload
    (non-string, oversized) is rejected with a clean 400.
    """
    if not isinstance(mac, str):
        return None
    hex_only = "".join(ch for ch in mac if ch.isalnum())
    if len(hex_only) != 12 or not all(c in "0123456789abcdefABCDEF" for c in hex_only):
        return None
    return ":".join(hex_only[i : i + 2] for i in range(0, 12, 2)).upper()


def _bt_operation_conflict_response():
    return jsonify({"success": False, "error": "Another Bluetooth operation is already in progress"}), 409


def _acquire_bt_lease(reason: str, *, bt_manager=None, adapter: str = ""):
    """Take the exclusive adapter lease for this request, or ``None``.

    The lease is taken on the request thread and released by the worker
    thread that carries out the operation — it is keyed by token, so a late
    release cannot free whatever operation is running by then.
    """
    handle = getattr(bt_manager, "adapter_handle", None) if bt_manager is not None else None
    if handle is None:
        handle = AdapterHandle(adapter=adapter)
    return handle.try_lease(reason)


def _start_bt_worker(target, *, lease, name: str | None = None) -> bool:
    """Start a worker that owns the adapter lease's release.

    Every caller takes the lease in the request thread and hands the release
    to the worker's ``finally``.  When the thread cannot start, that
    ``finally`` never runs and the adapter stays leased for the life of the
    process — every later Bluetooth operation then answers 409.  The release
    happens here instead, and the caller reports the failure.
    """
    try:
        threading.Thread(target=target, daemon=True, name=name).start()
    except Exception:
        logger.exception("Bluetooth worker thread failed to start")
        lease.release()
        return False
    return True


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@bt_bp.route("/api/bt/claim/<mac>", methods=["POST"])
def api_bt_claim(mac: str):
    """Claim AVRCP audio source on a multipoint speaker.

    For speakers paired to multiple hosts simultaneously (phone + bridge),
    this asserts the bridge as the active MPRIS source by pushing
    PlaybackStatus = "Playing" through the per-device MprisPlayer.  BlueZ
    forwards that to the speaker via AVRCP, which typically causes the
    speaker to switch its active source to us.

    Canonicalises the MAC before validating so dash-separated and
    compact (no-separator) forms work — matches the MprisRegistry's
    own normalisation and aligns with how operators tend to type or
    paste MAC addresses.
    """
    canonical_mac = _canonicalise_mac_input(mac)
    if canonical_mac is None or not validate_mac(canonical_mac):
        return jsonify({"success": False, "error": "Invalid MAC address"}), 400
    mac = canonical_mac

    from sendspin_bridge.services.audio.mpris_player import get_registry
    from sendspin_bridge.services.lifecycle.bridge_runtime_state import get_main_loop

    player = get_registry().get(mac)
    if player is None:
        return jsonify(
            {
                "success": False,
                "error": "No MPRIS player for this device — is the speaker connected?",
            }
        ), 404

    loop = get_main_loop()
    if loop is None:
        # No async loop → fall back to sync state mutation (registry-only).
        # Production always has the loop; this branch keeps the test path
        # deterministic when running endpoint tests without the bridge.
        player._state.status = "Playing"
        return jsonify({"success": True, "mac": mac})

    import asyncio as _asyncio

    try:
        fut = _asyncio.run_coroutine_threadsafe(player.set_playback_status("Playing"), loop)
        fut.result(timeout=2.0)
    except Exception as exc:
        logger.warning("Claim audio for %s failed: %s", mac, exc)
        return jsonify({"success": False, "error": str(exc)}), 500

    return jsonify({"success": True, "mac": mac})


@bt_bp.route("/api/bt/reconnect", methods=["POST"])
def api_bt_reconnect():
    """Force reconnect a BT device (connect without re-pairing)."""
    try:
        data = request.get_json() or {}
        player_name = data.get("player_name")
        client, err = get_client_or_error(player_name)
        if err:
            return err
        if not client or not client.bt_manager:
            return jsonify({"success": False, "error": "No BT manager for this player"}), 503

        bt = client.bt_manager
        lease = _acquire_bt_lease(f"reconnect {getattr(client, 'player_name', '')}", bt_manager=bt)
        if lease is None:
            return _bt_operation_conflict_response()

        def _do_reconnect():
            try:
                bt.disconnect_device()
                time.sleep(1)
                bt.connect_device()
            except Exception as e:
                logger.error("Force reconnect failed: %s", e)
            finally:
                lease.release()

        if not _start_bt_worker(_do_reconnect, lease=lease, name="bt-reconnect"):
            return jsonify({"success": False, "error": "Internal error"}), 500
        return jsonify({"success": True, "message": "Reconnect started"})
    except Exception:
        logger.exception("BT reconnect failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


@bt_bp.route("/api/bt/pair", methods=["POST"])
def api_bt_pair():
    """Force re-pair a BT device. Device must be in pairing mode."""
    try:
        data = request.get_json() or {}
        player_name = data.get("player_name")
        client, err = get_client_or_error(player_name)
        if err:
            return err
        if not client or not client.bt_manager:
            return jsonify({"success": False, "error": "No BT manager for this player"}), 503

        bt = client.bt_manager
        lease = _acquire_bt_lease(f"pair {getattr(client, 'player_name', '')}", bt_manager=bt)
        if lease is None:
            return _bt_operation_conflict_response()

        quiesce = bool(data.get("quiesce_adapter"))
        adapter_mac = getattr(bt, "effective_adapter_mac", "") or ""
        target_mac = getattr(bt, "mac_address", "") or None

        def _do_pair():
            try:
                if quiesce and adapter_mac:
                    with quiesce_adapter_peers(adapter_mac, exclude_mac=target_mac):
                        bt.pair_device()
                        bt.connect_device()
                else:
                    bt.pair_device()
                    bt.connect_device()
            except Exception as e:
                logger.error("Force pair failed: %s", e)
            finally:
                lease.release()

        if not _start_bt_worker(_do_pair, lease=lease, name="bt-pair"):
            return jsonify({"success": False, "error": "Internal error"}), 500
        return jsonify({"success": True, "message": "Pairing started (~25s)"})
    except Exception:
        logger.exception("BT pairing failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


@bt_bp.route("/api/bt/management", methods=["POST"])
def api_bt_management():
    """Release or reclaim the BT adapter for a player (hot toggle, BT-level only)."""
    data = request.get_json() or {}
    player_name = data.get("player_name")
    enabled = data.get("enabled")
    if enabled is None:
        return jsonify({"success": False, "error": 'Missing "enabled" field'}), 400
    client, err = get_client_or_error(player_name)
    if err:
        return err
    if not client:
        return jsonify({"success": False, "error": "No client found"}), 503
    enabled = bool(enabled)
    threading.Thread(target=client.set_bt_management_enabled, args=(enabled,), daemon=True).start()
    # An operator's release must never be auto-reclaimed (#349/#350) —
    # record who released so the distinction survives a bridge restart.
    _persist_device_released(str(player_name), not enabled, released_by=None if enabled else "user")
    # Sync enabled state to HA Supervisor so the Configuration page reflects it
    try:
        with config_lock, open(CONFIG_FILE) as _f:
            _cfg = json.load(_f)
        from sendspin_bridge.web.routes.api_config import _sync_ha_options  # late import to avoid circular dependency

        threading.Thread(target=_sync_ha_options, args=(_cfg,), daemon=True).start()
    except Exception as exc:
        logger.debug("sync HA options after toggle failed: %s", exc)
    action = "reclaimed" if enabled else "released"
    return jsonify({"success": True, "message": f"BT adapter {action}", "enabled": enabled})


@bt_bp.route("/api/bt/wake", methods=["POST"])
def api_bt_wake():
    """Wake a device from idle-timeout standby (reconnect BT + restart daemon)."""
    data = request.get_json() or {}
    player_name = data.get("player_name")
    client, err = get_client_or_error(player_name)
    if err:
        return err
    if not client:
        return jsonify({"success": False, "error": "No client found"}), 503
    if not client.status.get("bt_standby"):
        return jsonify({"success": False, "error": "Device is not in standby"}), 409
    import asyncio

    import sendspin_bridge.bridge.state as _state

    loop = _state.get_main_loop()
    if loop and loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(client._wake_from_standby(), loop)
        try:
            fut.result(timeout=5.0)
        except Exception as exc:
            logger.warning("[%s] wake_from_standby error: %s", player_name, exc)
    return jsonify({"success": True, "message": "Device waking from standby"})


@bt_bp.route("/api/bt/power_save", methods=["POST"])
def api_bt_power_save():
    """Toggle power-save mode (PA-sink suspend) for a single device.

    Power-save keeps the BT link up but suspends the PulseAudio sink so
    the speaker doesn't spin its codec on silence — much lighter than
    Standby (which fully disconnects BT).  Triggered automatically by
    the per-device idle timer when ``idle_mode='power_save'``; this
    endpoint exposes it as an on-demand action so the bulk-actions
    dropdown can fan it out.

    Body: ``{"player_name": str, "enter": bool}``.  ``enter=true``
    suspends the sink; ``enter=false`` resumes it.  Already-in-state
    requests collapse to a no-op success rather than 409 so a bulk
    fan-out doesn't error on devices that happen to be there already.
    """
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Request body must be a JSON object"}), 400
    player_name = data.get("player_name")
    # Strict bool validation for ``enter``: missing → True (default
    # behaviour, "enter power save"), but the moment the client sends
    # the field it must be an actual JSON boolean.  Plain
    # ``bool(data.get(...))`` would treat the string ``"false"`` as
    # truthy and silently flip behaviour.
    raw_enter = data.get("enter", True)
    if not isinstance(raw_enter, bool):
        return jsonify({"success": False, "error": "'enter' must be a boolean"}), 400
    enter = raw_enter
    client, err = get_client_or_error(player_name)
    if err:
        return err
    if not client:
        return jsonify({"success": False, "error": "No client found"}), 503

    already = bool(client.status.get("bt_power_save"))
    if enter and already:
        return jsonify({"success": True, "message": "Device already in power save"})
    if not enter and not already:
        return jsonify({"success": True, "message": "Device not in power save"})

    import asyncio

    import sendspin_bridge.bridge.state as _state

    loop = _state.get_main_loop()
    if loop is None or not loop.is_running():
        # Without a running asyncio loop we can't dispatch the
        # coroutine — return 503 instead of a false-positive 200 that
        # would mislead the bulk-actions dropdown into thinking every
        # selected device transitioned successfully.
        return (
            jsonify(
                {
                    "success": False,
                    "error": "Bridge asyncio loop is not running; cannot apply power-save",
                }
            ),
            503,
        )
    coro = client._enter_power_save() if enter else client._exit_power_save()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        fut.result(timeout=5.0)
    except Exception as exc:
        logger.warning("[%s] power_save error: %s", player_name, exc)
        return jsonify({"success": False, "error": str(exc)}), 500
    return jsonify(
        {
            "success": True,
            "message": "Device entering power save" if enter else "Device exiting power save",
        }
    )


@bt_bp.route("/api/bt/standby", methods=["POST"])
def api_bt_standby():
    """Put a device into standby (disconnect BT, park daemon on null sink)."""
    data = request.get_json() or {}
    player_name = data.get("player_name")
    client, err = get_client_or_error(player_name)
    if err:
        return err
    if not client:
        return jsonify({"success": False, "error": "No client found"}), 503
    if client.status.get("bt_standby"):
        return jsonify({"success": False, "error": "Device is already in standby"}), 409
    import asyncio

    import sendspin_bridge.bridge.state as _state

    loop = _state.get_main_loop()
    if loop and loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(client._enter_standby(), loop)
        try:
            fut.result(timeout=10.0)
        except Exception as exc:
            logger.warning("[%s] enter_standby error: %s", player_name, exc)
            return jsonify({"success": False, "error": str(exc)}), 500
    return jsonify({"success": True, "message": "Device entering standby"})


def _clear_never_paired_state_on_reenable(player_name: str) -> None:
    """v2.70.0-rc.2 (#263) — when an operator re-enables a previously
    auto-disabled never-paired device, clear the in-session state so the
    next reconnect cycle starts fresh:

    - reset DeviceStatus.never_paired / never_paired_since to defaults
    - reset DeviceStatus.reconnect_attempt to 0
    - flip BluetoothManager._has_ever_paired_since_start back to False so
      the auto-disable threshold resets for the next session
    - reclaim BT management (``management_enabled``) which was flipped to
      False by ``_auto_disable_never_paired`` to stop the polling loop;
      without this restore the device would stay silent after re-enable
      until a bridge restart (Copilot review on PR #290)

    The bridge config persistence happens in the caller; this helper only
    touches in-memory runtime state. If the SendspinClient was torn down
    (e.g. the user restarted the bridge between auto-disable and
    re-enable), this is a no-op and the next bridge start picks up the
    new enabled=true value cleanly.
    """
    client, _ = get_client_or_error(player_name)
    if client is None:
        return
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
        logger.debug("Could not clear never_paired status for %s: %s", player_name, exc)
    bt_mgr = getattr(client, "bt_manager", None)
    if bt_mgr is not None:
        try:
            bt_mgr._has_ever_paired_since_start = False
        except Exception as exc:
            logger.debug("Could not reset _has_ever_paired_since_start for %s: %s", player_name, exc)
        # Reclaim BT management — _auto_disable_never_paired flipped this off
        # to stop the polling loop. Re-enabling must restore the loop so the
        # next pair attempt actually runs.
        if getattr(bt_mgr, "management_enabled", True) is False:
            try:
                client.set_bt_management_enabled(True)
            except Exception as exc:
                logger.debug("Could not reclaim BT management for %s: %s", player_name, exc)


@bt_bp.route("/api/device/enabled", methods=["POST"])
def api_device_enabled():
    """Toggle global device enabled state (requires bridge restart to take effect)."""
    data = request.get_json() or {}
    player_name = data.get("player_name")
    enabled = data.get("enabled")
    if not player_name or enabled is None:
        return jsonify({"success": False, "error": "Missing player_name or enabled"}), 400
    enabled = bool(enabled)
    _persist_device_enabled(player_name, enabled)
    # When disabling, tear down the running client immediately so MA
    # unregisters the player (disconnect → ClientRemovedEvent).
    if not enabled:
        client, _ = get_client_or_error(player_name)
        if client:
            threading.Thread(target=client.set_bt_management_enabled, args=(False,), daemon=True).start()
    else:
        # When re-enabling a previously auto-disabled never-paired device
        # (#263), clear the in-session state so the next reconnect cycle
        # starts clean — never_paired flag, reconnect counter, and the
        # session-scoped _has_ever_paired_since_start gate.
        _clear_never_paired_state_on_reenable(player_name)
    # Sync to HA Supervisor
    try:
        with config_lock, open(CONFIG_FILE) as _f:
            _cfg = json.load(_f)
        from sendspin_bridge.web.routes.api_config import _sync_ha_options

        threading.Thread(target=_sync_ha_options, args=(_cfg,), daemon=True).start()
    except Exception as exc:
        logger.debug("sync HA options after device enabled toggle: %s", exc)
    action = "enabled" if enabled else "disabled"
    return jsonify(
        {
            "success": True,
            "enabled": enabled,
            "restart_required": not enabled,
            "message": f"Device {action}." + (" Restart bridge to re-enable." if not enabled else ""),
        }
    )


@bt_bp.route("/api/bt/adapters")
def api_bt_adapters():
    """List available Bluetooth adapters."""
    try:
        macs = list_bt_adapters()
        # Scan sysfs once per request — keeps the endpoint O(n) in the
        # number of adapters (vs. O(n²) when calling resolve_hci_for_mac
        # in the loop, which re-walks /sys/class/bluetooth on every call).
        hci_map = build_hci_map()
        adapters = []
        for i, mac in enumerate(macs):
            # Resolve the kernel hciN via sysfs so the UI label matches what
            # ``hciconfig`` and ``bluetoothctl info`` show (issue #193).  The
            # ``bluetoothctl list`` order is BlueZ's internal registration
            # order, which can differ from kernel hci numbering — especially
            # after a USB stick hotplug.  Falls back to the synthetic
            # ``hci{i}`` label only when /sys/class/bluetooth isn't mounted
            # (non-Linux dev box, container missing /sys).
            kernel_hci_sysfs = hci_for(hci_map, mac) or None
            kernel_hci = kernel_hci_sysfs or f"hci{i}"
            # Use ``show <MAC>`` instead of ``select <MAC>; show`` — the
            # latter is unreliable in piped-stdin mode and surfaced the wrong
            # adapter's alias when default and selected differed (issue #193).
            alias, powered = get_adapter_alias(mac)
            # Only read live CoD when we have a sysfs-confirmed hci index.
            # The synthetic fallback ``hci{i}`` uses the bluetoothctl list
            # order which may not match kernel numbering, so the index could
            # address the wrong controller.
            if kernel_hci_sysfs and kernel_hci_sysfs.startswith("hci") and kernel_hci_sysfs[3:].isdigit():
                live_cod = _read_device_class(int(kernel_hci_sysfs[3:]))
            else:
                live_cod = None
            adapters.append(
                {
                    "id": kernel_hci,
                    "mac": mac,
                    "name": alias or kernel_hci,
                    "powered": powered,
                    "live_class": f"0x{live_cod:06x}" if live_cod is not None else None,
                }
            )
        return jsonify({"adapters": adapters})
    except Exception:
        logger.exception("Failed to list adapters")
        return jsonify({"adapters": [], "error": "Failed to list adapters"}), 500


@bt_bp.route("/api/bt/paired")
def api_bt_paired():
    """Return already-paired Bluetooth devices across every known adapter."""
    named_only = request.args.get("filter", "1") != "0"
    try:
        adapter_macs = [str(mac).upper() for mac in list_bt_adapters() if mac]
        # ``mac -> {"name": str, "adapters": set[str]}`` — keep the first
        # non-empty name we encounter, but merge adapter memberships.
        merged: dict[str, dict] = {}

        def _ingest(pairs: "list[tuple[str, str]]", adapter_mac: str = "") -> None:
            for mac, name in pairs:
                entry = merged.setdefault(mac, {"name": "", "adapters": set()})
                if name and not entry["name"]:
                    entry["name"] = name
                if adapter_mac:
                    entry["adapters"].add(adapter_mac)

        bluez = get_bluez()
        if adapter_macs:
            for adapter in adapter_macs:
                _ingest(list(bluez.list_devices(Adapter.select(adapter))), adapter)
        else:
            # Legacy fallback contract: plain unfiltered ``devices``
            # against the default controller, no select line.
            _ingest(list(bluez.list_devices(filter="")))

        devices: list[dict] = []
        for mac, entry in merged.items():
            name = entry["name"]
            if named_only and not name:
                continue
            devices.append(
                {
                    "mac": mac,
                    "name": name or mac,
                    "adapters": sorted(entry["adapters"]),
                }
            )
        # Bridge devices first, then others; alphabetically within each group
        cfg = load_config()
        bridge_macs = {d.get("mac", "").upper() for d in cfg.get("BLUETOOTH_DEVICES", []) if d.get("mac")}
        devices.sort(key=lambda d: (0 if d["mac"] in bridge_macs else 1, d["name"].lower()))
        return jsonify({"devices": devices})
    except Exception:
        logger.exception("Failed to list paired devices")
        return jsonify({"devices": [], "error": "Failed to list paired devices"}), 500


@bt_bp.route("/api/bt/remove", methods=["POST"])
def api_bt_remove():
    """Remove (unpair) a device from the BlueZ stack.

    Optional ``adapter_mac`` targets a specific controller; when omitted we
    iterate every known adapter so bonds living on a non-default controller
    are cleaned up too.  Falls back to the default controller when no
    adapters are reported.
    """
    data = request.get_json(silent=True) or {}
    mac = (data.get("mac") or "").strip().upper()
    if not validate_mac(mac):
        return jsonify({"error": "Invalid MAC address"}), 400
    adapter_mac_raw = (data.get("adapter_mac") or "").strip().upper()
    if adapter_mac_raw and not validate_mac(adapter_mac_raw):
        return jsonify({"error": "Invalid adapter MAC"}), 400

    adapters = [str(a).upper() for a in list_bt_adapters() if a]
    if adapter_mac_raw:
        # Reject adapter MACs that aren't present on the host — otherwise
        # ``bluetoothctl select`` silently fails and ``remove`` runs against
        # the default controller, returning a misleading ``ok: true``.
        if adapters and adapter_mac_raw not in adapters:
            return (
                jsonify({"error": "Unknown adapter MAC", "adapter_mac": adapter_mac_raw}),
                400,
            )
        _bt_remove_device(mac, adapter_mac_raw)
    elif adapters:
        for adapter in adapters:
            _bt_remove_device(mac, adapter)
    else:
        _bt_remove_device(mac, "")
    return jsonify({"ok": True, "mac": mac})


def _device_info_payload(info, mac: str) -> dict:
    """The public ``/api/bt/info`` JSON shape — ``mac`` + ``raw`` stdout
    lines + the INFO_FIELDS keys, reproduced by ``DeviceInfo`` exactly
    (``static/app.js`` renders ``raw`` verbatim in the info modal)."""
    payload = {"mac": mac, "raw": list(info.raw)}
    payload.update(info.fields)
    return payload


def _get_bt_device_info(mac: str, adapter: str = "") -> dict:
    """Return ``bluetoothctl info`` for ``mac``, adapter-aware.

    With ``adapter`` explicit, the query is scoped by ``select`` (``hciN``
    is resolved to the controller MAC by the BluezControl chain — HAOS/LXC
    reject ``select hciN``). Without ``adapter``, each known controller is
    probed in turn and the first response that actually contains device
    fields (``Name:``/``Paired:``/…) wins; this is what lets the info
    modal work for bonds on the non-default radio when older UI call
    sites haven't been updated to pass the adapter yet.
    """
    bluez = get_bluez()
    if adapter:
        return _device_info_payload(bluez.device_info(mac, Adapter.select(adapter)), mac)

    try:
        adapter_macs = [m.upper() for m in list_bt_adapters() if m]
    except Exception:  # pragma: no cover - defensive
        adapter_macs = []

    last_info = None
    for adapter_mac in adapter_macs:
        info = bluez.device_info(mac, Adapter.select(adapter_mac))
        if info.fields:
            return _device_info_payload(info, mac)
        last_info = info

    if last_info is not None:
        return _device_info_payload(last_info, mac)
    return _device_info_payload(bluez.device_info(mac), mac)


@bt_bp.route("/api/bt/info", methods=["POST"])
def api_bt_info():
    """Return ``bluetoothctl info`` for a device."""
    data = request.get_json(silent=True) or {}
    mac = (data.get("mac") or "").strip().upper()
    if not validate_mac(mac):
        return jsonify({"success": False, "error": "Invalid MAC"}), 400
    try:
        adapter = validate_adapter(data.get("adapter"))
    except ValueError:
        return jsonify({"success": False, "error": "Invalid adapter identifier"}), 400
    try:
        return jsonify(_get_bt_device_info(mac, adapter))
    except Exception:
        logger.exception("Failed to get device info for %s", mac)
        return jsonify({"mac": mac, "error": "Failed to get device info"}), 500


@bt_bp.route("/api/bt/disconnect", methods=["POST"])
def api_bt_disconnect():
    """Disconnect a BT device without removing it."""
    data = request.get_json(silent=True) or {}
    mac = (data.get("mac") or "").strip().upper()
    if not validate_mac(mac):
        return jsonify({"success": False, "error": "Invalid MAC"}), 400
    try:
        bluez = get_bluez()
        # Target the adapter the device is bonded to: BlueZ bonds are
        # per-controller (/org/bluez/hciN/dev_…), so an unscoped disconnect
        # runs against the default controller — the wrong one on any host
        # where the bond lives elsewhere (rc.1 checklist item 8).
        owner = ""
        for ref in bluez.list_adapters():
            if any(entry.mac.upper() == mac for entry in bluez.list_devices(Adapter.select(ref.mac))):
                owner = ref.mac
                break
        result = bluez.disconnect(mac, Adapter.select(owner) if owner else Adapter.DEFAULT)
        if result.outcome is not Outcome.OK:
            # Any non-OK outcome is a failed disconnect.  A non-zero exit
            # often carries its error on stderr alone, where the
            # silence-means-success heuristic below would read it as done.
            logger.error("Failed to disconnect device %s: outcome=%s", mac, result.outcome.value)
            return jsonify({"ok": False, "error": "Bluetooth disconnect failed"}), 500
        # BlueZ ≥5.72 prints "Attempting to disconnect…" and stays silent on
        # success — only an explicit failure marker means the command failed.
        lowered = result.stdout.lower()
        ok = "successful" in lowered or not any(
            marker in lowered for marker in ("failed", "not connected", "not available", "error")
        )
        return jsonify({"ok": ok, "mac": mac})
    except Exception:
        logger.exception("Failed to disconnect device %s", mac)
        return jsonify({"ok": False, "error": "Bluetooth disconnect failed"}), 500


@bt_bp.route("/api/bt/adapter/power", methods=["POST"])
def api_bt_adapter_power():
    """Toggle adapter power. Accepts ``{adapter, power: true|false}``."""
    data = request.get_json(silent=True) or {}
    try:
        adapter = validate_adapter(data.get("adapter"))
    except ValueError:
        return jsonify({"error": "Invalid adapter identifier"}), 400
    power = data.get("power", True)
    try:
        result = get_bluez().power(bool(power), Adapter.of(adapter))
        if result.result.outcome in (Outcome.TIMEOUT, Outcome.UNAVAILABLE):
            logger.error("Failed to toggle adapter power: outcome=%s", result.result.outcome.value)
            return jsonify({"ok": False, "error": "Failed to toggle adapter power"}), 500
        # The host just changed under us; the next status build must measure
        # it rather than report the sample taken before the toggle.
        invalidate_preflight_probe()
        # ``changed`` reproduces the historical ok-heuristic (succeeded /
        # changing power / powered: marker) exactly.
        return jsonify({"ok": result.changed, "power": power})
    except Exception:
        logger.exception("Failed to toggle adapter power")
        return jsonify({"ok": False, "error": "Failed to toggle adapter power"}), 500


@bt_bp.route("/api/bt/reset_reconnect", methods=["POST"])
def api_bt_reset_reconnect():
    """Remove a device and re-pair from scratch. Returns a job_id.

    Sequence: remove → power cycle → scan → pair → trust → connect.
    """
    data = request.get_json(silent=True) or {}
    mac = (data.get("mac") or "").strip().upper()
    try:
        adapter = validate_adapter(data.get("adapter"))
    except ValueError:
        return jsonify({"success": False, "error": "Invalid adapter identifier"}), 400
    if not validate_mac(mac):
        return jsonify({"success": False, "error": "Invalid MAC"}), 400
    no_io_raw = data.get("no_input_no_output_agent")
    no_input_no_output_agent = no_io_raw if isinstance(no_io_raw, bool) else False
    allow_hfp_raw = data.get("allow_hfp_profile")
    allow_hfp_profile = allow_hfp_raw if isinstance(allow_hfp_raw, bool) else False
    lease = _acquire_bt_lease(f"reset-and-reconnect {mac}", adapter=adapter)
    if lease is None:
        return _bt_operation_conflict_response()
    job_id = str(uuid.uuid4())
    create_scan_job(job_id)

    def _run_job():
        try:
            _run_reset_reconnect(
                job_id,
                mac,
                adapter,
                no_input_no_output_agent=no_input_no_output_agent,
                allow_hfp_profile=allow_hfp_profile,
            )
        finally:
            lease.release()

    if not _start_bt_worker(_run_job, lease=lease, name=f"bt-reset-{job_id[:8]}"):
        return jsonify({"success": False, "error": "Internal error"}), 500
    return jsonify({"job_id": job_id})


@bt_bp.route("/api/bt/reset_reconnect/result/<job_id>", methods=["GET"])
def api_bt_reset_reconnect_result(job_id: str):
    """Poll for reset & reconnect result."""
    job = get_scan_job(job_id)
    if job is None:
        return jsonify({"error": "Unknown job_id"}), 404
    return jsonify(job)


def _resolve_adapter_to_mac(adapter: str) -> str:
    """Translate ``hciN`` → controller MAC for ``bluetoothctl select``.

    ``bluetoothctl select hci0`` fails with ``Controller hci0 not
    available`` on HAOS and LXC containers where the D-Bus objects are
    keyed by MAC, not by interface name.  When the bridge's fleet-row
    ``<select>`` emits ``hci0``/``hci1`` we must resolve it against
    ``bluetoothctl list`` (ordered) before issuing any ``select``. If
    resolution fails (adapters all down, etc.) the original ``hciN`` is
    returned so the caller can still attempt it — a failed ``select``
    at least surfaces as a visible paring failure, while silently
    dropping the prefix would run the flow against the default
    controller.  MAC inputs pass through unchanged.
    """
    if not adapter or not adapter.startswith("hci"):
        return adapter
    try:
        idx = int(adapter[3:])
    except ValueError:
        return adapter
    try:
        macs = [m.upper() for m in list_bt_adapters() if m]
    except Exception:  # pragma: no cover - defensive
        return adapter
    # Resolve through the sysfs-backed kernel map first: ``bluetoothctl
    # list`` order is BlueZ registration order, not kernel hciN numbering —
    # positional indexing paired/scanned the wrong physical adapter on
    # hosts where hci1 registers before hci0 (issue #340, hit live on the
    # two-adapter stand where a pair for hci1 silently ran against hci0).
    kernel_hci = adapter.lower()
    hci_map = build_hci_map()
    if hci_map:
        for mac in macs:
            if hci_for(hci_map, mac) == kernel_hci:
                return mac
        return adapter  # mapped nowhere — let the failed select surface loudly
    # Sysfs gave nothing (Docker without /sys, or kernels whose
    # /sys/class/bluetooth/hciN lacks the ``address`` file — seen live on the
    # rc.1 stand).  The D-Bus object path /org/bluez/hciN is keyed by the
    # kernel index unambiguously — prefer it over list position.
    dbus_addr = _dbus_get_adapter_address(kernel_hci)
    if dbus_addr:
        return dbus_addr.upper()
    # No sysfs/hciconfig/D-Bus visibility: the adapters endpoint fell back
    # to synthetic ``hci{i}`` labels in list order, so mirror that here.
    if 0 <= idx < len(macs):
        return macs[idx]
    return adapter


def _run_reset_reconnect(
    job_id: str,
    mac: str,
    adapter: str,
    *,
    no_input_no_output_agent: bool = False,
    allow_hfp_profile: bool = False,
) -> None:
    """Drop the bond, power-cycle the controller, then pair + trust + connect.

    The reset is this path's own contribution; the pairing that follows is
    the shared choreography, so this flow now gets the popular-PIN ladder
    and the failure fingerprint it never had.
    """
    adapter = _resolve_adapter_to_mac(adapter)
    bluez = get_bluez()
    scope = Adapter.of(adapter)
    try:
        logger.info("Reset & Reconnect %s: removing…", mac)
        bluez.remove(mac, scope)
        time.sleep(1)

        # A controller power cycle clears the kernel-side link state that
        # survives `remove` and keeps some speakers from bonding again.
        bluez.power(False, scope)
        time.sleep(2)

        logger.info("Reset & Reconnect %s: pairing…", mac)
        outcome = PairSession(
            bluez,
            adapter=scope,
            mac=mac,
            options=PairOptions(
                pins=COMMON_BT_PAIR_PINS,
                capability="NoInputNoOutput" if no_input_no_output_agent else "DisplayYesNo",
                allow_hfp=bool(allow_hfp_profile),
                connect_after_trust=True,
                timings=PairTimings(
                    scan_window_s=_PAIR_SCAN_DURATION,
                    pair_wait_s=_PAIR_WAIT_DURATION,
                    # The connect result and the closing ``info`` block land
                    # during this window; without it the job reports
                    # ``connected: false`` for a speaker that did connect.
                    post_trust_settle_s=5.0,
                ),
            ),
            label=mac,
        ).run()

        logger.info(
            "Reset & Reconnect %s: paired=%s connected=%s agent=%s",
            mac,
            outcome.success,
            outcome.connected,
            outcome.agent_telemetry,
        )
        finish_scan_job(
            job_id,
            {
                "success": outcome.success,
                "connected": outcome.connected,
                "mac": mac,
                "agent_telemetry": outcome.agent_telemetry,
            },
        )
    except Exception:
        logger.exception("Reset & Reconnect error for %s", mac)
        finish_scan_job(job_id, {"success": False, "mac": mac, "error": "Reset & reconnect failed"})


@bt_bp.route("/api/bt/scan", methods=["POST"])
def api_bt_scan():
    """Start an async BT device scan; returns a job_id immediately."""
    data = request.get_json(silent=True) or {}
    raw_adapter = (data.get("adapter") or "").strip()
    if not raw_adapter or raw_adapter.lower() == "all":
        return jsonify({"error": "A specific Bluetooth adapter is required"}), 400
    try:
        adapter = validate_adapter(raw_adapter)
        audio_only = _coerce_scan_audio_only(data.get("audio_only"))
        adapter_macs = _resolve_scan_adapter_macs(adapter)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    with _scan_lock:
        if is_scan_running():
            return jsonify({"error": "A scan is already in progress"}), 409
        if time.monotonic() - _last_scan_completed < _SCAN_COOLDOWN:
            remaining = int(_SCAN_COOLDOWN - (time.monotonic() - _last_scan_completed)) + 1
            return jsonify({"error": "Scan cooldown active", "retry_after": remaining}), 429
        lease = _acquire_bt_lease(f"scan {adapter}", adapter=adapter)
        if lease is None:
            return _bt_operation_conflict_response()
        job_id = str(uuid.uuid4())
        scan_options = _build_scan_options(adapter, audio_only, adapter_macs)
        expected_duration = _estimate_scan_duration(adapter_macs)
        create_scan_job(
            job_id,
            {
                "scan_options": scan_options,
                "expected_duration": expected_duration,
                "started_at": time.time(),
            },
        )

    def _run_job():
        try:
            _run_bt_scan(job_id, adapter, audio_only)
        finally:
            lease.release()

    if not _start_bt_worker(_run_job, lease=lease, name=f"bt-scan-{job_id[:8]}"):
        return jsonify({"success": False, "error": "Internal error"}), 500
    return jsonify({"job_id": job_id, "scan_options": scan_options, "expected_duration": expected_duration})


@bt_bp.route("/api/bt/scan/result/<job_id>", methods=["GET"])
def api_bt_scan_result(job_id: str):
    """Poll for BT scan result by job_id."""
    job = get_scan_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] == "running":
        return jsonify(
            {
                "status": "running",
                "scan_options": job.get("scan_options", {}),
                "expected_duration": job.get("expected_duration"),
                "started_at": job.get("started_at"),
            }
        )
    return jsonify(
        {
            "status": "done",
            "devices": job.get("devices", []),
            "error": job.get("error"),
            "scan_options": job.get("scan_options", {}),
            "expected_duration": job.get("expected_duration"),
            "started_at": job.get("started_at"),
            "stats": job.get("stats", {}),
        }
    )


# ---------------------------------------------------------------------------
# BT scan helpers (used only by routes above)
# ---------------------------------------------------------------------------

_MAX_SCAN_RESULTS = 50

_last_scan_completed: float = 0.0
_SCAN_COOLDOWN = 10.0  # seconds between scans
_SCAN_BASE_DURATION = 15
_SCAN_ADAPTER_OVERHEAD = 2


def _coerce_scan_audio_only(value) -> bool:
    """Return a normalized audio-only flag from request JSON."""
    if value is None:
        return True
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError("Invalid audio_only flag")


def _resolve_scan_adapter_macs(adapter: str) -> "list[str]":
    """Resolve a selected adapter identifier into bluetoothctl adapter MACs."""
    adapter_macs = list_bt_adapters()
    normalized = adapter.strip()
    if not normalized or normalized.lower() == "all":
        raise ValueError("A specific Bluetooth adapter is required")
    if normalized.lower().startswith("hci"):
        kernel_hci = normalized.lower()
        try:
            idx = int(kernel_hci[3:])
        except ValueError as exc:
            raise ValueError("Invalid adapter identifier") from exc
        # The UI's adapter ids are kernel hciN labels resolved via sysfs
        # (/api/bt/adapters, issue #193).  Resolve them back through the
        # same map — ``bluetoothctl list`` order is BlueZ registration
        # order, not kernel numbering, so indexing the list positionally
        # scanned the wrong physical adapter (issue #340).
        hci_map = build_hci_map()
        if hci_map:
            for mac in adapter_macs:
                if hci_for(hci_map, mac) == kernel_hci:
                    return [mac.upper()]
            raise ValueError("Selected adapter is not available")
        # No sysfs/hciconfig visibility: the adapters endpoint fell back
        # to synthetic ``hci{i}`` labels in list order, so mirror that.
        if idx < 0 or idx >= len(adapter_macs):
            raise ValueError("Selected adapter is not available")
        return [adapter_macs[idx].upper()]
    normalized = normalized.upper()
    if normalized not in {mac.upper() for mac in adapter_macs}:
        raise ValueError("Selected adapter is not available")
    return [normalized]


def _build_scan_options(adapter: str, audio_only: bool, adapter_macs: "list[str]") -> dict:
    """Build the public scan-options payload returned to the UI."""
    return {
        "adapter": adapter,
        "audio_only": audio_only,
        "adapter_scope": "selected",
        "adapter_count": len(adapter_macs),
    }


def _estimate_scan_duration(adapter_macs: "list[str]") -> int:
    """Return a client-facing timed-scan duration hint in seconds."""
    return _SCAN_BASE_DURATION + max(len(adapter_macs) - 1, 0) * _SCAN_ADAPTER_OVERHEAD


def _describe_discovery_refusal(errors: "tuple[str, ...]") -> str:
    """Turn ``Failed to start discovery: …`` into operator-facing guidance.

    A controller whose firmware has wedged answers every discovery request
    with an error and then goes quiet, which used to be reported as "no
    devices found" — the one outcome that hides the actual fault.
    """
    reason = errors[0]
    lowered = reason.lower()
    if "inprogress" in lowered:
        detail = "the adapter reports a discovery already in progress"
    elif "notready" in lowered:
        detail = "the adapter is not ready — it may be powered off or blocked by rfkill"
    else:
        detail = f"the adapter refused it ({reason})"
    return (
        f"Bluetooth discovery could not be started: {detail}. "
        "Power-cycle the adapter (Reboot adapter), or unplug and replug the USB dongle, then scan again."
    )


def _resolve_unnamed_devices(all_macs: "set[str]", names: "dict[str, str]") -> None:
    """Look up names for unnamed devices from the bluetoothctl device cache."""
    unnamed = {mac for mac in all_macs if mac not in names}
    if not unnamed:
        return
    for entry in get_bluez().list_devices(filter=""):
        if entry.mac in unnamed and entry.name:
            names[entry.mac] = entry.name


def _enrich_scan_device(mac: str, names: "dict[str, str]", audio_only: bool = True) -> "tuple[dict | None, str | None]":
    """Return ``(device_info_or_None, drop_reason_or_None)``.

    ``device_info`` is ``None`` when the device was filtered out by
    ``audio_only``; ``drop_reason`` is populated in that case so the caller
    can aggregate scan reject stats for support diagnostics.
    """
    if not validate_mac(mac):
        return {"mac": mac, "name": mac, "audio_capable": True}, None
    info = get_bluez().device_info(mac, timeout=4.0)
    if info.outcome is not Outcome.OK:
        # Legacy contract: never drop a scannable speaker on the strength of
        # an info block the command itself reported as failed — a non-zero
        # exit can still leave a partial block behind.
        return {"mac": mac, "name": names.get(mac, mac), "audio_capable": True}, None
    if mac not in names and info.name and not re.match(r"^[0-9A-Fa-f]{2}[-:]", info.name):
        names[mac] = info.name
    audio_capable, reason = classify_audio_capability(info)
    if audio_only and not audio_capable:
        logger.info(
            "BT scan filter dropped %s (name=%s, reason=%s)",
            mac,
            names.get(mac, ""),
            reason,
        )
        return None, reason
    info_rssi = info.rssi
    device_info: dict = {"mac": mac, "name": names.get(mac, mac), "audio_capable": audio_capable}
    if info_rssi is not None:
        device_info["rssi_dbm"] = info_rssi
    return device_info, None


def _annotate_scan_conflicts(devices: list[dict]) -> None:
    """Add ``warning`` field to devices that are already registered on another bridge."""
    try:
        cfg = load_config()
        if not cfg.get("DUPLICATE_DEVICE_CHECK", True):
            return
        ma_url = str(cfg.get("MA_API_URL") or "").strip()
        ma_token = str(cfg.get("MA_API_TOKEN") or "").strip()
        bridge_name = str(cfg.get("BRIDGE_NAME") or "").strip()
        if not ma_url or not ma_token:
            return
        macs = [d["mac"] for d in devices if d.get("mac")]
        if not macs:
            return
        from sendspin_bridge.services.bluetooth.duplicate_device_check import find_scan_device_conflicts

        conflicts = find_scan_device_conflicts(macs, ma_url, ma_token, bridge_name)
        for d in devices:
            warning = conflicts.get(str(d.get("mac") or "").strip().upper())
            if warning:
                d["warning"] = warning
    except Exception:
        logger.debug("Scan conflict annotation failed", exc_info=True)


def _run_bt_scan(job_id: str, adapter: str = "", audio_only: bool = True) -> None:
    """Perform BT scan in a background thread and store result in state."""
    global _last_scan_completed
    try:
        adapter_macs = _resolve_scan_adapter_macs(adapter)

        # The discovery window is the same number the UI is told to expect
        # (``_estimate_scan_duration``), so the progress bar can't drift.
        transcript = get_bluez().scan(adapter_macs, window_s=float(_SCAN_BASE_DURATION))
        names = dict(transcript.names)
        device_adapter = transcript.device_adapter
        rssi_by_mac = transcript.rssi_by_mac
        discovery_errors = transcript.discovery_errors
        if discovery_errors:
            logger.warning("BT scan: adapter refused to start discovery (%s)", "; ".join(discovery_errors))
        all_macs = set(transcript.seen_macs) | set(transcript.active_macs)

        if len(all_macs) > _MAX_SCAN_RESULTS:
            logger.warning("BT scan found %d devices, capping to %d", len(all_macs), _MAX_SCAN_RESULTS)
            all_macs = set(list(all_macs)[:_MAX_SCAN_RESULTS])

        _resolve_unnamed_devices(all_macs, names)

        devices = []
        dropped_reasons: dict[str, int] = {}
        if all_macs:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(_enrich_scan_device, mac, names, audio_only): mac for mac in all_macs}
                for fut in concurrent.futures.as_completed(futures):
                    device, drop_reason = fut.result()
                    if device is not None:
                        devices.append(device)
                    elif drop_reason:
                        dropped_reasons[drop_reason] = dropped_reasons.get(drop_reason, 0) + 1

        for d in devices:
            d["adapter"] = device_adapter.get(d["mac"], "")
            d["supports_import"] = bool(d.get("audio_capable", True))
            d["kind"] = "audio" if d.get("audio_capable", True) else "other"
            # v2.63.0-rc.2 — surface RSSI captured during the scan window
            # (and merged with whatever ``_enrich_scan_device`` pulled from
            # ``bluetoothctl info`` for already-connected peers).
            scan_rssi = rssi_by_mac.get(d["mac"])
            if scan_rssi is not None:
                d["rssi_dbm"] = scan_rssi
            elif d.get("rssi_dbm") is None:
                d["rssi_dbm"] = None

        # Annotate with cross-bridge conflict warnings
        _annotate_scan_conflicts(devices)

        devices.sort(key=lambda d: (d["name"] == d["mac"], d["name"]))
        if discovery_errors and not devices:
            # Nothing found *and* discovery never started: report the refusal
            # instead of an empty result the operator can't act on.
            finish_scan_job(job_id, {"devices": [], "error": _describe_discovery_refusal(discovery_errors)})
            return
        stats: dict = {
            "total_candidates": len(all_macs),
            "returned_candidates": len(devices),
            "audio_candidates": sum(1 for d in devices if d.get("audio_capable", True)),
            "audio_only": audio_only,
            "dropped_reasons": dropped_reasons,
        }
        if discovery_errors:
            # Results survive a partial refusal (e.g. one of several
            # controllers wedged); the refusal rides along as a stat.
            stats["discovery_error"] = discovery_errors[0]
        finish_scan_job(job_id, {"devices": devices, "stats": stats})
    except Exception:
        logger.exception("BT scan failed")
        finish_scan_job(job_id, {"devices": [], "error": "Bluetooth scan failed"})
    finally:
        # The cooldown is the adapter's rest period, so it runs from the end
        # of the scan window.  Stamping it on entry made it expire during the
        # 15 s discovery window itself — the 10 s gate never held anyone back.
        # The ``finally`` keeps the original intent that every attempt counts,
        # even one that failed during enrichment.
        _last_scan_completed = time.monotonic()


# ---------------------------------------------------------------------------
# Standalone pair (for new devices discovered via scan)
# ---------------------------------------------------------------------------

_PAIR_SCAN_DURATION = 12  # seconds to scan before pairing
_PAIR_WAIT_DURATION = 15  # seconds to wait for pairing to complete


@bt_bp.route("/api/bt/pair_new", methods=["POST"])
def api_bt_pair_new():
    """Pair a new BT device by MAC address (no existing client required).

    Accepts ``{mac, adapter?}`` and returns a ``job_id``.
    Poll ``/api/bt/pair_new/result/<job_id>`` for the outcome.
    """
    data = request.get_json() or {}
    mac = (data.get("mac") or "").strip().upper()
    try:
        adapter = validate_adapter(data.get("adapter"))
    except ValueError:
        return jsonify({"success": False, "error": "Invalid adapter identifier"}), 400
    if not validate_mac(mac):
        return jsonify({"success": False, "error": "Invalid MAC"}), 400
    lease = _acquire_bt_lease(f"pair {mac}", adapter=adapter)
    if lease is None:
        return _bt_operation_conflict_response()
    quiesce = bool(data.get("quiesce_adapter"))
    # Pairing compatibility options are explicit, one-shot request values.
    # Non-bool coercion (``bool("false") -> True``) would silently weaken
    # pairing or authorize HFP, so only JSON booleans are accepted.
    no_io_agent_raw = data.get("no_input_no_output_agent")
    no_input_no_output_agent = no_io_agent_raw if isinstance(no_io_agent_raw, bool) else False
    allow_hfp_raw = data.get("allow_hfp_profile")
    allow_hfp_profile = allow_hfp_raw if isinstance(allow_hfp_raw, bool) else False
    job_id = str(uuid.uuid4())
    create_scan_job(job_id)

    def _run_job():
        try:
            _run_standalone_pair(
                job_id,
                mac,
                adapter,
                quiesce=quiesce,
                no_input_no_output_agent=no_input_no_output_agent,
                allow_hfp_profile=allow_hfp_profile,
            )
        finally:
            lease.release()

    if not _start_bt_worker(_run_job, lease=lease, name=f"bt-pair-{job_id[:8]}"):
        return jsonify({"success": False, "error": "Internal error"}), 500
    return jsonify({"job_id": job_id})


@bt_bp.route("/api/bt/pair_new/result/<job_id>", methods=["GET"])
def api_bt_pair_new_result(job_id: str):
    """Poll for standalone pair result."""
    job = get_scan_job(job_id)
    if job is None:
        return jsonify({"error": "Unknown job_id"}), 404
    return jsonify(job)


def _run_standalone_pair(
    job_id: str,
    mac: str,
    adapter: str,
    *,
    quiesce: bool = False,
    no_input_no_output_agent: bool = False,
    allow_hfp_profile: bool = False,
) -> None:
    """Pair + trust a device that is not yet in the config.

    Compatibility options apply to this pairing job only and are never
    sourced from persisted global configuration.  The pairing choreography
    itself — early pair on discovery, SSP auto-confirm, the popular-PIN
    ladder — lives in :class:`PairSession`, shared with the monitor loop's
    re-pair and with reset-and-reconnect.
    """
    adapter = _resolve_adapter_to_mac(adapter)

    attempt_context = None
    if quiesce and adapter:

        def attempt_context():
            # Single-adapter hosts can't pair while another A2DP ACL is up;
            # park the peers for the duration of each attempt.
            return quiesce_adapter_peers(adapter, exclude_mac=mac)

    options = PairOptions(
        pins=COMMON_BT_PAIR_PINS,
        # NoInputNoOutput forces Just-Works SSP for speakers that cancel a
        # passkey exchange; opt-in per request (issue #168).
        capability="NoInputNoOutput" if no_input_no_output_agent else "DisplayYesNo",
        allow_hfp=bool(allow_hfp_profile),
        timings=PairTimings(
            scan_window_s=_PAIR_SCAN_DURATION,
            pair_wait_s=_PAIR_WAIT_DURATION,
        ),
    )

    try:
        outcome = PairSession(
            get_bluez(),
            adapter=Adapter.of(adapter),
            mac=mac,
            options=options,
            attempt_context=attempt_context,
            label=mac,
        ).run()
    except Exception:
        logger.exception("Standalone pair error for %s", mac)
        finish_scan_job(job_id, {"success": False, "mac": mac})
        return

    finish_scan_job(job_id, {"success": outcome.success, "mac": mac})
