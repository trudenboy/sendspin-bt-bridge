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

from flask import Blueprint, jsonify, request

from config import CONFIG_FILE, config_lock, load_config
from routes._helpers import get_client_or_error, validate_mac
from services import persist_device_enabled as _persist_device_enabled
from services.bluetooth import _AUDIO_UUIDS, list_bt_adapters
from state import create_scan_job, finish_scan_job, get_scan_job, is_scan_running

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

        def _do_pair():
            try:
                bt.pair_device()
                bt.connect_device()
            except Exception as e:
                logger.error("Force pair failed: %s", e)

        threading.Thread(target=_do_pair, daemon=True).start()
        return jsonify({"success": True, "message": "Pairing started (~25s)"})
    except Exception:
        logger.exception("BT pairing failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


@bt_bp.route("/api/bt/management", methods=["POST"])
def api_bt_management():
    """Release or reclaim the BT adapter for a player."""
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
    _persist_device_enabled(player_name, enabled)
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
    except Exception as e:
        return jsonify({"adapters": [], "error": str(e)})


@bt_bp.route("/api/bt/paired")
def api_bt_paired():
    """Return already-paired Bluetooth devices."""
    named_only = request.args.get("filter", "1") != "0"
    try:
        result = subprocess.run(
            ["bluetoothctl"],
            input="devices\n",
            capture_output=True,
            text=True,
            timeout=5,
        )
        devices = []
        seen = set()
        for line in result.stdout.splitlines():
            clean = _ANSI_RE.sub("", line)
            m = _DEV_PAT.search(clean)
            if m:
                mac = m.group(1).upper()
                name = m.group(2).strip()
                if mac not in seen:
                    seen.add(mac)
                    if re.match(r"^[0-9A-Fa-f]{2}[-:]", name):
                        name = ""
                    if named_only and not name:
                        continue
                    devices.append({"mac": mac, "name": name or mac})
        # Bridge devices first, then others; alphabetically within each group
        cfg = load_config()
        bridge_macs = {d.get("mac", "").upper() for d in cfg.get("BLUETOOTH_DEVICES", []) if d.get("mac")}
        devices.sort(key=lambda d: (0 if d["mac"] in bridge_macs else 1, d["name"].lower()))
        return jsonify({"devices": devices})
    except Exception as e:
        return jsonify({"devices": [], "error": str(e)})


@bt_bp.route("/api/bt/scan", methods=["POST"])
def api_bt_scan():
    """Start an async BT device scan; returns a job_id immediately."""
    if is_scan_running():
        return jsonify({"error": "A scan is already in progress"}), 409
    if time.monotonic() - _last_scan_completed < _SCAN_COOLDOWN:
        return jsonify({"error": "Scan cooldown active, try again later"}), 429
    job_id = str(uuid.uuid4())
    create_scan_job(job_id)
    t = threading.Thread(target=_run_bt_scan, args=(job_id,), daemon=True, name=f"bt-scan-{job_id[:8]}")
    t.start()
    return jsonify({"job_id": job_id})


@bt_bp.route("/api/bt/scan/result/<job_id>", methods=["GET"])
def api_bt_scan_result(job_id: str):
    """Poll for BT scan result by job_id."""
    job = get_scan_job(job_id)
    if job is None:
        return jsonify({"error": "Job not found"}), 404
    if job["status"] == "running":
        return jsonify({"status": "running"})
    return jsonify({"status": "done", "devices": job.get("devices", []), "error": job.get("error")})


# ---------------------------------------------------------------------------
# BT scan helpers (used only by routes above)
# ---------------------------------------------------------------------------

_MAX_SCAN_RESULTS = 50

_last_scan_completed: float = 0.0
_SCAN_COOLDOWN = 30.0  # seconds between scans


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
            init_cmds: list[str] = []
            for m in adapter_macs:
                init_cmds.extend([f"select {m}", "power on", "scan on"])
        else:
            init_cmds = ["power on", "agent on", "scan on"]
        if proc.stdin is None:
            raise RuntimeError("bluetoothctl subprocess stdin unavailable")
        proc.stdin.write("\n".join(init_cmds) + "\n")
        proc.stdin.flush()
        time.sleep(10)
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


def _enrich_audio_device(mac: str, names: "dict[str, str]") -> "dict | None":
    """Return device info dict if the device is audio-capable, else None."""
    if not validate_mac(mac):
        return {"mac": mac, "name": mac}
    try:
        r = subprocess.run(
            ["bluetoothctl", "info", mac],
            capture_output=True,
            text=True,
            timeout=4,
        )
        out = r.stdout
        out_lower = out.lower()
    except Exception:
        return {"mac": mac, "name": names.get(mac, mac)}
    if mac not in names:
        nm = re.search(r"\bName:\s+(.*)", out)
        if nm:
            n = nm.group(1).strip()
            if n and not re.match(r"^[0-9A-Fa-f]{2}[-:]", n):
                names[mac] = n
    class_m = re.search(r"Class:\s+(0x[0-9A-Fa-f]+)", out)
    if class_m:
        cls = int(class_m.group(1), 16)
        if (cls >> 8) & 0x1F != 4:
            return None
    elif any(u in out_lower for u in _AUDIO_UUIDS):
        pass
    elif "UUID:" in out:
        return None
    return {"mac": mac, "name": names.get(mac, mac)}


def _run_bt_scan(job_id: str) -> None:
    """Perform BT scan in a background thread and store result in state."""
    try:
        adapter_macs = list_bt_adapters()

        result_stdout = _run_bluetoothctl_scan(adapter_macs)
        seen, names, device_adapter, active_macs = _parse_scan_output(result_stdout)
        all_macs = seen | active_macs

        if len(all_macs) > _MAX_SCAN_RESULTS:
            logger.warning("BT scan found %d devices, capping to %d", len(all_macs), _MAX_SCAN_RESULTS)
            all_macs = set(list(all_macs)[:_MAX_SCAN_RESULTS])

        _resolve_unnamed_devices(all_macs, names)

        devices = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(_enrich_audio_device, mac, names): mac for mac in all_macs}
            for fut in concurrent.futures.as_completed(futures):
                result = fut.result()
                if result is not None:
                    devices.append(result)

        for d in devices:
            d["adapter"] = device_adapter.get(d["mac"], "")

        devices.sort(key=lambda d: (d["name"] == d["mac"], d["name"]))
        finish_scan_job(job_id, {"devices": devices})
        global _last_scan_completed
        _last_scan_completed = time.monotonic()
    except Exception as e:
        logger.error("BT scan failed: %s", e)
        finish_scan_job(job_id, {"devices": [], "error": str(e)})
