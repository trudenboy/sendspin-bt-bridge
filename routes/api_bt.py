"""
Bluetooth API Blueprint for sendspin-bt-bridge.

All /api/bt/* routes and the helper functions they depend on.
"""

import concurrent.futures
import json
import logging
import re
import subprocess
import threading
import time
import uuid
from typing import Any

from flask import Blueprint, jsonify, request

from config import CONFIG_FILE, config_lock, load_config
from routes._helpers import get_client_or_error, validate_adapter, validate_mac
from services import persist_device_enabled as _persist_device_enabled
from services.async_job_state import create_scan_job, finish_scan_job, get_scan_job, is_scan_running
from services.bluetooth import (
    _AUDIO_UUIDS,
    COMMON_BT_PAIR_PINS,
    describe_pair_failure,
    is_pin_rejection,
    list_bt_adapters,
)
from services.bluetooth import bt_remove_device as _bt_remove_device
from services.bluetooth import persist_device_released as _persist_device_released
from services.pairing_agent import PairingAgent
from services.pairing_quiesce import quiesce_adapter_peers

logger = logging.getLogger(__name__)

bt_bp = Blueprint("api_bt", __name__)

# ---------------------------------------------------------------------------
# Pre-compiled regex patterns for BT scan output parsing
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_DEV_PAT = re.compile(r"Device\s+([0-9A-Fa-f:]{17})\s+(.*)")
_NEW_DEV_PAT = re.compile(r"\[NEW\]\s+Device\s+([0-9A-Fa-f:]{17})\s+(.*)")
_CHG_NAME_PAT = re.compile(r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+Name:\s+(.*)")
_CHG_RSSI_PAT = re.compile(r"\[CHG\]\s+Device\s+([0-9A-Fa-f:]{17})\s+RSSI:")
_SHOW_CTRL_PAT = re.compile(r"^Controller\s+([0-9A-Fa-f:]{17})")
_SHOW_DEV_PAT = re.compile(r"^Device\s+([0-9A-Fa-f:]{17})")
_bt_operation_lock = threading.Lock()
_scan_lock = threading.Lock()


def _bt_operation_conflict_response():
    return jsonify({"success": False, "error": "Another Bluetooth operation is already in progress"}), 409


def _try_acquire_bt_operation() -> bool:
    return _bt_operation_lock.acquire(blocking=False)


def _release_bt_operation() -> None:
    try:
        _bt_operation_lock.release()
    except RuntimeError:
        logger.debug("BT operation lock release skipped; lock was not held")


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


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

        def _do_reconnect():
            try:
                bt.disconnect_device()
                time.sleep(1)
                bt.connect_device()
            except Exception as e:
                logger.error("Force reconnect failed: %s", e)

        threading.Thread(target=_do_reconnect, daemon=True).start()
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
        if not _try_acquire_bt_operation():
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
                _release_bt_operation()

        threading.Thread(target=_do_pair, daemon=True).start()
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
    _persist_device_released(str(player_name), not enabled)
    # Sync enabled state to HA Supervisor so the Configuration page reflects it
    try:
        with config_lock, open(CONFIG_FILE) as _f:
            _cfg = json.load(_f)
        from routes.api_config import _sync_ha_options  # late import to avoid circular dependency

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

    import state as _state

    loop = _state.get_main_loop()
    if loop and loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(client._wake_from_standby(), loop)
        try:
            fut.result(timeout=5.0)
        except Exception as exc:
            logger.warning("[%s] wake_from_standby error: %s", player_name, exc)
    return jsonify({"success": True, "message": "Device waking from standby"})


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

    import state as _state

    loop = _state.get_main_loop()
    if loop and loop.is_running():
        fut = asyncio.run_coroutine_threadsafe(client._enter_standby(), loop)
        try:
            fut.result(timeout=10.0)
        except Exception as exc:
            logger.warning("[%s] enter_standby error: %s", player_name, exc)
            return jsonify({"success": False, "error": str(exc)}), 500
    return jsonify({"success": True, "message": "Device entering standby"})


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
    # Sync to HA Supervisor
    try:
        with config_lock, open(CONFIG_FILE) as _f:
            _cfg = json.load(_f)
        from routes.api_config import _sync_ha_options

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
        adapters = []
        for i, mac in enumerate(macs):
            show_out = subprocess.run(
                ["bluetoothctl"],
                input=f"select {mac}\nshow\n",
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
            powered = "Powered: yes" in show_out
            alias = next(
                (ln.split("Alias:")[1].strip() for ln in show_out.splitlines() if "Alias:" in ln),
                f"hci{i}",
            )
            adapters.append({"id": f"hci{i}", "mac": mac, "name": alias, "powered": powered})
        return jsonify({"adapters": adapters})
    except Exception:
        logger.exception("Failed to list adapters")
        return jsonify({"adapters": [], "error": "Failed to list adapters"}), 500


def _parse_paired_stdout(stdout: str) -> "list[tuple[str, str]]":
    """Extract ``(mac, name)`` pairs from bluetoothctl ``devices [Paired]`` output.

    Interactive ``bluetoothctl`` interleaves async discovery notifications
    (``[CHG] Device <mac> RSSI: …``, ``[NEW]/[DEL] Device …``, ``[CHG]
    Device <mac> ManufacturerData.*``) on the same stdout we are parsing.
    Only lines that begin with a bare ``Device <mac> <rest>`` token — after
    stripping ANSI colour codes and any leading prompt echo — are genuine
    responses to ``devices Paired``; everything else is noise.  Without
    this discrimination the Already-Paired list contained ghost rows
    whose ``bluetoothctl info`` actually reported ``Paired: no``.

    Names that look like MAC-as-name (``AA:BB:…`` / ``AA-BB-…``) are normalized
    to an empty string so downstream filters can treat them as unnamed.
    """
    results: list[tuple[str, str]] = []
    for line in stdout.splitlines():
        clean = _ANSI_RE.sub("", line)
        # Strip any leading prompt echo like ``[ENEBY20]> ``. Anchored so
        # bracket-prefixed async notifications (``[CHG] ``/``[NEW] ``/
        # ``[DEL] ``) survive and fail the ``startswith("Device ")`` check
        # below.
        stripped = re.sub(r"^\[[^\]]+\]>\s*", "", clean).lstrip()
        if not stripped.startswith("Device "):
            continue
        m = _DEV_PAT.match(stripped)
        if not m:
            continue
        mac = m.group(1).upper()
        name = m.group(2).strip()
        if re.match(r"^[0-9A-Fa-f]{2}[-:]", name):
            name = ""
        results.append((mac, name))
    return results


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

        if adapter_macs:
            for adapter in adapter_macs:
                res = subprocess.run(
                    ["bluetoothctl"],
                    input=f"select {adapter}\ndevices Paired\n",
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                _ingest(_parse_paired_stdout(res.stdout), adapter)
        else:
            res = subprocess.run(
                ["bluetoothctl"],
                input="devices\n",
                capture_output=True,
                text=True,
                timeout=5,
            )
            _ingest(_parse_paired_stdout(res.stdout))

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


_INFO_FIELDS = frozenset({"name", "alias", "paired", "bonded", "trusted", "blocked", "connected", "class", "icon"})


def _parse_bluetoothctl_info(stdout: str, mac: str) -> dict:
    lines = [_ANSI_RE.sub("", ln).strip() for ln in stdout.splitlines() if ln.strip()]
    info: dict = {"mac": mac, "raw": lines}
    for ln in lines:
        if ":" not in ln:
            continue
        key, _, val = ln.partition(":")
        k = key.strip().lower().replace(" ", "_")
        if k in _INFO_FIELDS:
            info[k] = val.strip()
    return info


def _run_bluetoothctl_info(mac: str, adapter: str) -> dict:
    cmds: list[str] = []
    if adapter:
        cmds.append(f"select {adapter}")
    cmds.append(f"info {mac}")
    r = subprocess.run(
        ["bluetoothctl"],
        input="\n".join(cmds) + "\n",
        capture_output=True,
        text=True,
        timeout=5,
    )
    return _parse_bluetoothctl_info(r.stdout, mac)


def _get_bt_device_info(mac: str, adapter: str = "") -> dict:
    """Return ``bluetoothctl info`` for ``mac``, adapter-aware.

    With ``adapter`` explicit, ``select <adapter>`` is prefixed (``hciN``
    is resolved to the controller MAC first — HAOS/LXC reject
    ``select hciN``). Without ``adapter``, each known controller is
    probed in turn and the first response that actually contains device
    fields (``Name:``/``Paired:``/…) wins; this is what lets the info
    modal work for bonds on the non-default radio when older UI call
    sites haven't been updated to pass the adapter yet.
    """
    if adapter:
        return _run_bluetoothctl_info(mac, _resolve_adapter_to_mac(adapter))

    try:
        adapter_macs = [m.upper() for m in list_bt_adapters() if m]
    except Exception:  # pragma: no cover - defensive
        adapter_macs = []

    last_result: dict | None = None
    for adapter_mac in adapter_macs:
        result = _run_bluetoothctl_info(mac, adapter_mac)
        if any(field in result for field in _INFO_FIELDS):
            return result
        last_result = result

    if last_result is not None:
        return last_result
    return _run_bluetoothctl_info(mac, "")


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
        r = subprocess.run(
            ["bluetoothctl"],
            input=f"disconnect {mac}\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        ok = "successful" in r.stdout.lower()
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
    cmd = "power on" if power else "power off"
    cmds = f"select {adapter}\n{cmd}\n" if adapter else f"{cmd}\n"
    try:
        r = subprocess.run(
            ["bluetoothctl"],
            input=cmds,
            capture_output=True,
            text=True,
            timeout=5,
        )
        clean = _ANSI_RE.sub("", r.stdout).lower()
        ok = (
            "succeeded" in clean
            or "changing power" in clean
            or (("powered: yes" in clean) if power else ("powered: no" in clean))
        )
        return jsonify({"ok": ok, "power": power})
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
    if not _try_acquire_bt_operation():
        return _bt_operation_conflict_response()
    job_id = str(uuid.uuid4())
    create_scan_job(job_id)

    def _run_job():
        try:
            _run_reset_reconnect(job_id, mac, adapter)
        finally:
            _release_bt_operation()

    t = threading.Thread(
        target=_run_job,
        daemon=True,
        name=f"bt-reset-{job_id[:8]}",
    )
    t.start()
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
    if 0 <= idx < len(macs):
        return macs[idx]
    return adapter


def _run_reset_reconnect(job_id: str, mac: str, adapter: str) -> None:
    """Remove device, then pair + trust + connect from scratch."""
    adapter = _resolve_adapter_to_mac(adapter)
    try:
        # Step 1: Remove existing pairing
        logger.info("Reset & Reconnect %s: removing…", mac)
        remove_cmds: list[str] = []
        if adapter:
            remove_cmds.append(f"select {adapter}")
        remove_cmds.append(f"remove {mac}")
        subprocess.run(
            ["bluetoothctl"],
            input="\n".join(remove_cmds) + "\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        time.sleep(1)

        # Step 2: Power cycle adapter
        power_cmds: list[str] = []
        if adapter:
            power_cmds.append(f"select {adapter}")
        power_cmds.extend(["power off"])
        subprocess.run(
            ["bluetoothctl"],
            input="\n".join(power_cmds) + "\n",
            capture_output=True,
            text=True,
            timeout=5,
        )
        time.sleep(2)

        # Step 3: Pair from scratch (power on + scan + pair, trust only after success)
        logger.info("Reset & Reconnect %s: pairing…", mac)

        # Native BlueZ agent first — same contract as _run_standalone_pair_inner.
        # DisplayYesNo is the default because it reached Bonded: yes on Synergy
        # 65 S in the #168 reproduction where the stdin-yes race kept losing.
        native_agent: PairingAgent | None = None
        try:
            native_agent = PairingAgent(capability="DisplayYesNo", pin="0000").__enter__()
            logger.info("Reset & Reconnect %s: native agent active", mac)
        except Exception as exc:
            native_agent = None
            logger.warning(
                "Reset & Reconnect %s: native agent unavailable (%s) — falling back to bluetoothctl agent",
                mac,
                exc,
            )

        # Outer try/finally guarantees native_agent cleanup even when
        # ``subprocess.Popen(["bluetoothctl"])`` below raises before the
        # inner proc-scoped finally has a chance to run.  Otherwise the
        # agent thread / SystemBus connection leaks per failed attempt.
        try:
            initial_cmds = []
            if adapter:
                initial_cmds.append(f"select {adapter}")
            initial_cmds.append("power on")
            if native_agent is None:
                initial_cmds.extend(["agent on", "default-agent"])
            initial_cmds.append("scan bredr")
            pair_cmds = [f"pair {mac}"]

            proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                if proc.stdin is None:
                    raise RuntimeError("bluetoothctl stdin unavailable")

                proc.stdin.write("\n".join(initial_cmds) + "\n")
                proc.stdin.flush()
                time.sleep(_PAIR_SCAN_DURATION)

                proc.stdin.write("\n".join(pair_cmds) + "\n")
                proc.stdin.flush()

                import selectors

                collected: list[str] = []
                paired_ok = False
                deadline = time.monotonic() + _PAIR_WAIT_DURATION
                sel = selectors.DefaultSelector()
                sel.register(proc.stdout, selectors.EVENT_READ)  # type: ignore[arg-type]
                try:
                    while time.monotonic() < deadline and proc.poll() is None:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        events = sel.select(timeout=min(remaining, 0.5))
                        if not events:
                            continue
                        line = proc.stdout.readline()  # type: ignore[union-attr]
                        if not line:
                            break
                        collected.append(line)
                        stripped = line.strip().lower()
                        if "confirm passkey" in stripped or "request confirmation" in stripped:
                            logger.info("SSP passkey prompt — auto-confirming")
                            proc.stdin.write("yes\n")
                            proc.stdin.flush()
                        if "pairing successful" in stripped or "already paired" in stripped:
                            paired_ok = True
                            time.sleep(1)
                            break
                finally:
                    sel.close()

                if paired_ok:
                    proc.stdin.write(f"trust {mac}\nconnect {mac}\n")
                proc.stdin.write(f"info {mac}\nscan off\nquit\n")
                proc.stdin.flush()
                if paired_ok:
                    time.sleep(5)

                try:
                    tail, _ = proc.communicate(timeout=3)
                    collected.append(tail)
                except subprocess.TimeoutExpired:
                    pass

                out = "".join(collected)
                ok = paired_ok or any(s in out.lower() for s in ("pairing successful", "already paired", "paired: yes"))
                connected = "connection successful" in out.lower() or "connected: yes" in out.lower()
                agent_telemetry: dict[str, Any] | None = None
                if native_agent is not None:
                    try:
                        agent_telemetry = native_agent.telemetry
                    except Exception as exc:
                        logger.debug("Reset & Reconnect %s: telemetry read failed: %s", mac, exc)
                logger.info(
                    "Reset & Reconnect %s: paired=%s connected=%s agent=%s (last 400: %s)",
                    mac,
                    ok,
                    connected,
                    agent_telemetry,
                    out[-400:],
                )
                finish_scan_job(
                    job_id,
                    {
                        "success": ok,
                        "connected": connected,
                        "mac": mac,
                        "agent_telemetry": agent_telemetry,
                    },
                )
            finally:
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except Exception:
                    pass
        finally:
            if native_agent is not None:
                try:
                    native_agent.__exit__(None, None, None)
                except Exception as exc:
                    logger.debug("Reset & Reconnect %s: agent cleanup error: %s", mac, exc)
    except Exception:
        logger.exception("Reset & Reconnect error for %s", mac)
        finish_scan_job(job_id, {"success": False, "mac": mac, "error": "Reset & reconnect failed"})


@bt_bp.route("/api/bt/scan", methods=["POST"])
def api_bt_scan():
    """Start an async BT device scan; returns a job_id immediately."""
    data = request.get_json(silent=True) or {}
    raw_adapter = (data.get("adapter") or "").strip()
    adapter_value = "" if raw_adapter.lower() == "all" else raw_adapter
    try:
        adapter = validate_adapter(adapter_value)
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
        if not _try_acquire_bt_operation():
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
            _release_bt_operation()

    t = threading.Thread(
        target=_run_job,
        daemon=True,
        name=f"bt-scan-{job_id[:8]}",
    )
    t.start()
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
    if not adapter:
        return adapter_macs
    normalized = adapter.strip()
    if not normalized:
        return adapter_macs
    if normalized.lower() == "all":
        return adapter_macs
    if normalized.lower().startswith("hci"):
        try:
            idx = int(normalized[3:])
        except ValueError as exc:
            raise ValueError("Invalid adapter identifier") from exc
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
        "adapter_scope": "all" if not adapter else "selected",
        "adapter_count": max(len(adapter_macs), 1 if adapter else 0),
    }


def _estimate_scan_duration(adapter_macs: "list[str]") -> int:
    """Return a client-facing timed-scan duration hint in seconds."""
    return _SCAN_BASE_DURATION + max(len(adapter_macs) - 1, 0) * _SCAN_ADAPTER_OVERHEAD


def _classify_audio_capability(out: str) -> "tuple[bool, str]":
    """Return ``(is_audio, reason)`` from bluetoothctl info output.

    Reason is a short machine-readable label used for scan-filter diagnostics:
    ``audio_class_of_device`` / ``non_audio_class_of_device`` / ``audio_uuid``
    / ``no_audio_class_no_uuid`` / ``no_class_info_defaults_audio``.
    """
    out_lower = out.lower()
    class_m = re.search(r"\bClass:\s+(0x[0-9A-Fa-f]+)", out)
    if class_m:
        cls = int(class_m.group(1), 16)
        major = (cls >> 8) & 0x1F
        if major == 4:
            return True, "audio_class_of_device"
        return False, "non_audio_class_of_device"
    if any(u in out_lower for u in _AUDIO_UUIDS):
        return True, "audio_uuid"
    if "UUID:" in out:
        return False, "no_audio_class_no_uuid"
    return True, "no_class_info_defaults_audio"


def _run_bluetoothctl_scan(adapter_macs: "list[str]") -> str:
    """Run a bluetoothctl scan session and return combined stdout."""
    post_scan_cmds: list[str] = []
    for m in adapter_macs:
        post_scan_cmds.extend([f"select {m}", "show", "devices"])
    bt_timeout = 12 + len(adapter_macs) * 2

    proc = subprocess.Popen(
        ["bluetoothctl"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        if adapter_macs:
            init_cmds: list[str] = ["agent on", "default-agent"]
            for m in adapter_macs:
                init_cmds.extend([f"select {m}", "power on", "scan bredr"])
        else:
            init_cmds = ["power on", "agent on", "default-agent", "scan bredr"]
        if proc.stdin is None:
            raise RuntimeError("bluetoothctl subprocess stdin unavailable")
        proc.stdin.write("\n".join(init_cmds) + "\n")
        proc.stdin.flush()
        time.sleep(15)
        proc.stdin.write("scan off\n" + "\n".join(post_scan_cmds) + "\n")
        proc.stdin.flush()
        time.sleep(1)
        result_stdout, _ = proc.communicate(timeout=bt_timeout + 4)
    except Exception:
        proc.kill()
        proc.wait()
        raise
    return result_stdout


def _parse_scan_output(stdout: str) -> "tuple[set[str], dict[str, str], dict[str, str], set[str]]":
    """Parse bluetoothctl scan output into (seen_macs, names, device_adapter, active_macs)."""
    seen: set[str] = set()
    names: dict[str, str] = {}
    device_adapter: dict[str, str] = {}
    active_macs: set[str] = set()
    current_show_adapter: str = ""
    for line in stdout.splitlines():
        clean = _ANSI_RE.sub("", line).strip()
        if not clean.startswith("["):
            ctrl_m = _SHOW_CTRL_PAT.match(clean)
            if ctrl_m:
                current_show_adapter = ctrl_m.group(1).upper()
                continue
            if current_show_adapter:
                dev_m = _SHOW_DEV_PAT.match(clean)
                if dev_m:
                    dmac = dev_m.group(1).upper()
                    if dmac not in device_adapter:
                        device_adapter[dmac] = current_show_adapter
                    continue
        scan_m = _NEW_DEV_PAT.search(clean)
        if scan_m:
            mac = scan_m.group(1).upper()
            name = scan_m.group(2).strip()
            seen.add(mac)
            if name and not re.match(r"^[0-9A-Fa-f]{2}[-:]", name):
                names[mac] = name
            continue
        chg_n = _CHG_NAME_PAT.search(clean)
        if chg_n:
            mac = chg_n.group(1).upper()
            names[mac] = chg_n.group(2).strip()
            continue
        chg_r = _CHG_RSSI_PAT.search(clean)
        if chg_r:
            active_macs.add(chg_r.group(1).upper())
    return seen, names, device_adapter, active_macs


def _resolve_unnamed_devices(all_macs: "set[str]", names: "dict[str, str]") -> None:
    """Look up names for unnamed devices from the bluetoothctl device cache."""
    unnamed = {mac for mac in all_macs if mac not in names}
    if not unnamed:
        return
    db_result = subprocess.run(
        ["bluetoothctl"],
        input="devices\n",
        capture_output=True,
        text=True,
        timeout=5,
    )
    for line in db_result.stdout.splitlines():
        clean = _ANSI_RE.sub("", line)
        db_m = _DEV_PAT.search(clean)
        if db_m:
            mac = db_m.group(1).upper()
            name = db_m.group(2).strip()
            if mac in unnamed and name and not re.match(r"^[0-9A-Fa-f]{2}[-:]", name):
                names[mac] = name


def _enrich_scan_device(mac: str, names: "dict[str, str]", audio_only: bool = True) -> "tuple[dict | None, str | None]":
    """Return ``(device_info_or_None, drop_reason_or_None)``.

    ``device_info`` is ``None`` when the device was filtered out by
    ``audio_only``; ``drop_reason`` is populated in that case so the caller
    can aggregate scan reject stats for support diagnostics.
    """
    if not validate_mac(mac):
        return {"mac": mac, "name": mac, "audio_capable": True}, None
    try:
        r = subprocess.run(
            ["bluetoothctl", "info", mac],
            capture_output=True,
            text=True,
            timeout=4,
        )
        out = r.stdout
    except Exception:
        return {"mac": mac, "name": names.get(mac, mac), "audio_capable": True}, None
    if mac not in names:
        nm = re.search(r"\bName:\s+(.*)", out)
        if nm:
            n = nm.group(1).strip()
            if n and not re.match(r"^[0-9A-Fa-f]{2}[-:]", n):
                names[mac] = n
    audio_capable, reason = _classify_audio_capability(out)
    if audio_only and not audio_capable:
        logger.info(
            "BT scan filter dropped %s (name=%s, reason=%s)",
            mac,
            names.get(mac, ""),
            reason,
        )
        return None, reason
    return {"mac": mac, "name": names.get(mac, mac), "audio_capable": audio_capable}, None


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
        from services.duplicate_device_check import find_scan_device_conflicts

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
    # Apply cooldown to every scan attempt, even if later enrichment fails.
    _last_scan_completed = time.monotonic()
    try:
        adapter_macs = _resolve_scan_adapter_macs(adapter)

        result_stdout = _run_bluetoothctl_scan(adapter_macs)
        seen, names, device_adapter, active_macs = _parse_scan_output(result_stdout)
        all_macs = seen | active_macs

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

        # Annotate with cross-bridge conflict warnings
        _annotate_scan_conflicts(devices)

        devices.sort(key=lambda d: (d["name"] == d["mac"], d["name"]))
        finish_scan_job(
            job_id,
            {
                "devices": devices,
                "stats": {
                    "total_candidates": len(all_macs),
                    "returned_candidates": len(devices),
                    "audio_candidates": sum(1 for d in devices if d.get("audio_capable", True)),
                    "audio_only": audio_only,
                    "dropped_reasons": dropped_reasons,
                },
            },
        )
    except Exception:
        logger.exception("BT scan failed")
        finish_scan_job(job_id, {"devices": [], "error": "Bluetooth scan failed"})


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
    if not _try_acquire_bt_operation():
        return _bt_operation_conflict_response()
    quiesce = bool(data.get("quiesce_adapter"))
    # Per-pair override for the NoInputNoOutput pairing agent. The scan
    # modal's experimental toggle sends this explicitly; when absent
    # (legacy clients, hand-crafted curl) or when the payload supplies a
    # non-bool value, the pair runner falls back to the persisted config
    # key. Non-bool coercion (``bool("false") -> True``) would silently
    # force NoInputNoOutput, so we accept only JSON booleans here.
    no_io_agent_raw = data.get("no_input_no_output_agent")
    no_input_no_output_agent: bool | None = no_io_agent_raw if isinstance(no_io_agent_raw, bool) else None
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
            )
        finally:
            _release_bt_operation()

    t = threading.Thread(
        target=_run_job,
        daemon=True,
        name=f"bt-pair-{job_id[:8]}",
    )
    t.start()
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
    no_input_no_output_agent: bool | None = None,
) -> None:
    """Run pair + trust via bluetoothctl for a device not yet in config.

    ``no_input_no_output_agent`` is a per-request override for the
    NoInputNoOutput pairing agent (Just-Works SSP). ``None`` (the
    default) means "use whatever config.EXPERIMENTAL_PAIR_JUST_WORKS
    says". A bool explicitly wins over config — the scan-modal toggle is
    the authoritative intent for this pair attempt.

    When the device asks for a legacy PIN and rejects our first guess,
    the orchestrator retries the whole pair flow with the next PIN from
    ``COMMON_BT_PAIR_PINS``. Non-PIN failures (connection errors,
    timeouts) stop the loop — they aren't PIN-related and retrying
    wastes ~20 s per attempt.
    """
    adapter = _resolve_adapter_to_mac(adapter)

    def _attempt(pin: str):
        if quiesce and adapter:
            with quiesce_adapter_peers(adapter, exclude_mac=mac):
                return _run_standalone_pair_inner(
                    job_id, mac, adapter, pin=pin, no_input_no_output_agent=no_input_no_output_agent
                )
        return _run_standalone_pair_inner(
            job_id, mac, adapter, pin=pin, no_input_no_output_agent=no_input_no_output_agent
        )

    tried_pins: list[str] = []
    last_reason = ""
    for pin in COMMON_BT_PAIR_PINS:
        tried_pins.append(pin)
        result = _attempt(pin)
        last_reason = result.get("reason", "") or last_reason
        if result.get("success"):
            logger.info("Standalone pair %s: OK", mac)
            finish_scan_job(job_id, {"success": True, "mac": mac})
            return
        if not result.get("pin_rejected"):
            logger.warning(
                "Standalone pair %s: FAIL (%s)",
                mac,
                result.get("reason") or "no explicit bluetoothctl reason captured",
            )
            finish_scan_job(job_id, {"success": False, "mac": mac})
            return
        logger.warning(
            "Standalone pair %s: PIN %s rejected — retrying with next candidate",
            mac,
            pin,
        )

    # All popular PINs exhausted. The device almost certainly requires a
    # custom PIN that the bridge can't auto-enter — surface that loud so
    # the operator doesn't have to grep per-attempt warnings.
    logger.warning(
        "Standalone pair %s: FAIL — device rejected all popular PINs (%s). "
        "Likely requires a custom PIN; the bridge cannot auto-enter non-popular PINs. "
        "Last bluetoothctl reason: %s",
        mac,
        ", ".join(tried_pins),
        last_reason or "no explicit bluetoothctl reason captured",
    )
    finish_scan_job(job_id, {"success": False, "mac": mac})


def _run_standalone_pair_inner(
    job_id: str,
    mac: str,
    adapter: str,
    *,
    pin: str = "0000",
    no_input_no_output_agent: bool | None = None,
) -> dict:
    """Actual bluetoothctl pair flow — split out so quiesce wraps the whole op.

    Returns a dict ``{success, pin_attempted, pin_rejected, reason, output}``
    so the outer orchestrator can decide whether to retry with a different
    PIN or surface the failure to the UI.
    """
    try:
        cleanup_cmds: list[str] = []
        if adapter:
            cleanup_cmds.append(f"select {adapter}")
        # `agent off` tears down any agent object lingering on the system bus
        # from a previous bluetoothctl session. Without it, `agent on` below
        # can return `Failed to register agent object`, leaving the pair
        # without an authentication agent and producing
        # `org.bluez.Error.ConnectionAttemptFailed` (issue #162).
        cleanup_cmds.append("agent off")
        cleanup_cmds.append(f"remove {mac}")
        subprocess.run(
            ["bluetoothctl"],
            input="\n".join(cleanup_cmds) + "\n",
            capture_output=True,
            text=True,
            timeout=10,
        )
        time.sleep(1)

        # `agent NoInputNoOutput` forces Just-Works SSP (both sides auto-accept
        # without a passkey exchange). Many consumer BT audio sinks cancel
        # authentication when the default `KeyboardDisplay` agent negotiates
        # a passkey; opt-in toggle lets affected users work around it (issue #168).
        # Precedence: per-request override (scan modal toggle) > config key.
        if no_input_no_output_agent is not None:
            use_no_io_agent = no_input_no_output_agent
        else:
            try:
                cfg = load_config()
            except Exception:
                cfg = {}
            use_no_io_agent = bool(cfg.get("EXPERIMENTAL_PAIR_JUST_WORKS"))
        agent_cmd = "agent NoInputNoOutput" if use_no_io_agent else "agent on"

        # Native D-Bus agent: exports org.bluez.Agent1 directly so BlueZ calls
        # RequestConfirmation / RequestPinCode on us without the bluetoothctl
        # stdin-``yes`` race that loses to SSP agent timeouts on some speakers
        # (issue #168, Synergy 65 S). Default capability is DisplayYesNo — the
        # same one manual ``bluetoothctl`` uses, which reached ``Bonded: yes``
        # in the issue reproduction. Falls back to bluetoothctl's built-in
        # agent if dbus-fast / SystemBus are unavailable.
        native_capability = "NoInputNoOutput" if use_no_io_agent else "DisplayYesNo"
        native_agent: PairingAgent | None = None
        try:
            native_agent = PairingAgent(capability=native_capability, pin=pin).__enter__()
            logger.info(
                "Standalone pair %s: native agent active (cap=%s)",
                mac,
                native_capability,
            )
        except Exception as exc:
            native_agent = None
            logger.warning(
                "Standalone pair %s: native pairing agent unavailable (%s) — falling back to bluetoothctl stdin agent",
                mac,
                exc,
            )

        # Outer try/finally guarantees native_agent cleanup even if the
        # bluetoothctl subprocess fails to launch before the inner
        # proc-scoped finally has a chance to run.
        try:
            initial_cmds: list[str] = []
            if adapter:
                initial_cmds.append(f"select {adapter}")
            initial_cmds.append("power on")
            if native_agent is None:
                # No native agent → rely on bluetoothctl's built-in agent as before.
                initial_cmds.extend([agent_cmd, "default-agent"])
            initial_cmds.append("scan bredr")

            pair_cmds = [f"pair {mac}"]

            proc = subprocess.Popen(
                ["bluetoothctl"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                if proc.stdin is None:
                    raise RuntimeError("bluetoothctl stdin unavailable")

                proc.stdin.write("\n".join(initial_cmds) + "\n")
                proc.stdin.flush()

                # Read stdout to auto-confirm SSP passkey
                import selectors

                collected: list[str] = []
                paired_ok = False
                pair_sent = False
                pin_attempted = False
                # Single loop that handles both scan-window observation and pair
                # outcome parsing. `pair <mac>` fires as soon as `[NEW] Device <mac>`
                # appears, rather than waiting the full `_PAIR_SCAN_DURATION` fixed
                # sleep — shaves typical pair-mode window from ~12s to ~1-3s so the
                # speaker is still accepting when `pair` arrives (issue #168).
                mac_lower = mac.lower()
                start = time.monotonic()
                scan_deadline = start + _PAIR_SCAN_DURATION
                full_deadline = scan_deadline + _PAIR_WAIT_DURATION
                sel = selectors.DefaultSelector()
                sel.register(proc.stdout, selectors.EVENT_READ)  # type: ignore[arg-type]
                try:
                    while proc.poll() is None:
                        now = time.monotonic()
                        # Fire `pair` at scan deadline if device never advertised.
                        if not pair_sent and now >= scan_deadline:
                            proc.stdin.write("\n".join(pair_cmds) + "\n")
                            proc.stdin.flush()
                            pair_sent = True
                        if now >= full_deadline:
                            break
                        end = full_deadline if pair_sent else scan_deadline
                        remaining = end - now
                        if remaining <= 0:
                            continue
                        events = sel.select(timeout=min(remaining, 0.5))
                        if not events:
                            continue
                        line = proc.stdout.readline()  # type: ignore[union-attr]
                        if not line:
                            break
                        collected.append(line)
                        low = line.lower()
                        stripped = line.strip().lower()

                        if not pair_sent and "[new] device" in low and mac_lower in low:
                            logger.debug("Device %s visible via scan, firing pair early", mac)
                            proc.stdin.write("\n".join(pair_cmds) + "\n")
                            proc.stdin.flush()
                            pair_sent = True
                            continue

                        if "confirm passkey" in stripped or "request confirmation" in stripped:
                            logger.info("SSP passkey prompt — auto-confirming: %s", line.strip())
                            proc.stdin.write("yes\n")
                            proc.stdin.flush()
                        elif "enter pin code" in stripped or "enter passkey" in stripped:
                            # Legacy BT 2.x devices (e.g. HMDX JAM, `LegacyPairing: yes`)
                            # ask for a numeric PIN. `0000` is the BlueZ-default fallback
                            # and works for most consumer audio sinks (issue #162). If
                            # this attempt is a retry, the outer orchestrator supplies
                            # the next popular PIN from ``COMMON_BT_PAIR_PINS``.
                            logger.info("Legacy PIN prompt — auto-entering %s: %s", pin, line.strip())
                            proc.stdin.write(f"{pin}\n")
                            proc.stdin.flush()
                            pin_attempted = True
                        if "pairing successful" in stripped or "already paired" in stripped:
                            paired_ok = True
                            break
                finally:
                    sel.close()

                # Safety net: ensure `pair` was sent at least once even if the loop
                # exited via proc.poll() before the scan deadline.
                if not pair_sent and proc.poll() is None:
                    proc.stdin.write("\n".join(pair_cmds) + "\n")
                    proc.stdin.flush()

                if paired_ok:
                    proc.stdin.write(f"trust {mac}\n")
                proc.stdin.write(f"info {mac}\nscan off\nquit\n")
                proc.stdin.flush()

                try:
                    tail, _ = proc.communicate(timeout=3)
                    collected.append(tail)
                except subprocess.TimeoutExpired:
                    pass

                out = "".join(collected)
                ok = paired_ok or any(s in out.lower() for s in ("pairing successful", "already paired", "paired: yes"))
                reason = ""
                pin_rejected = False
                if not ok:
                    reason = (
                        describe_pair_failure(out, pin_attempted=pin_attempted, pin_used=pin)
                        or "no explicit bluetoothctl reason captured"
                    )
                    # A PIN rejection means the device asked for a PIN AND the
                    # attempt failed with AuthenticationFailed — derived from
                    # the raw bluetoothctl output so the check is independent
                    # of the human-readable `reason` wording (otherwise a reword
                    # of `describe_pair_failure` would silently break retry).
                    pin_rejected = pin_attempted and is_pin_rejection(out)
                    # Log full captured output (not just a tail) so passkey/agent
                    # prompts near the start of the session are visible in bug
                    # reports. bluetoothctl's SSP dialog is typically <4 KB per
                    # pair attempt (issue #168 diagnostic lost with 800-byte tail).
                    logger.debug("Standalone pair %s output (pin=%s): %s", mac, pin, out)
                # Structured pair-agent telemetry: what BlueZ asked us, passkey
                # shown, authorized/rejected services.  Emitted regardless of
                # outcome so success telemetry (e.g. "which capability worked")
                # stays visible alongside failure triage.
                agent_telemetry: dict[str, Any] | None = None
                if native_agent is not None:
                    try:
                        agent_telemetry = native_agent.telemetry
                        logger.info(
                            "Standalone pair %s agent telemetry: outcome=%s capability=%s "
                            "methods=%s passkey=%s cancelled=%s authorized=%s rejected=%s",
                            mac,
                            "success" if ok else "fail",
                            agent_telemetry.get("capability"),
                            agent_telemetry.get("method_calls"),
                            agent_telemetry.get("last_passkey"),
                            agent_telemetry.get("peer_cancelled"),
                            agent_telemetry.get("authorized_services"),
                            agent_telemetry.get("rejected_services"),
                        )
                    except Exception as exc:
                        logger.debug("Standalone pair %s: telemetry read failed: %s", mac, exc)
                return {
                    "success": ok,
                    "pin_attempted": pin_attempted,
                    "pin_rejected": pin_rejected,
                    "reason": reason,
                    "output": out,
                    "agent_telemetry": agent_telemetry,
                }
            finally:
                try:
                    proc.kill()
                    proc.wait(timeout=3)
                except Exception:
                    pass
        finally:
            if native_agent is not None:
                try:
                    native_agent.__exit__(None, None, None)
                except Exception as exc:
                    logger.debug(
                        "Standalone pair %s: native agent cleanup error (non-fatal): %s",
                        mac,
                        exc,
                    )
    except Exception:
        logger.exception("Standalone pair error for %s", mac)
        return {
            "success": False,
            "pin_attempted": False,
            "pin_rejected": False,
            "reason": "Pairing failed",
            "output": "",
        }
