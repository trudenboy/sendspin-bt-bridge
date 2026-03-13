"""
Status and diagnostics API Blueprint for sendspin-bt-bridge.

Routes for device status, groups, SSE stream, diagnostics, health,
bug reports, and preflight checks.
"""

import json
import logging
import os
import platform as _platform
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone

from flask import Blueprint, Response, jsonify

import state
from config import BUILD_DATE, VERSION, load_config
from services.pulse import get_server_name, list_sinks
from state import clients as _clients
from state import (
    clients_lock as _clients_lock,
)
from state import (
    get_adapter_name,
    get_ma_group_for_player_id,
    get_ma_now_playing_for_group,
)

UTC = timezone.utc

logger = logging.getLogger(__name__)

status_bp = Blueprint("api_status", __name__)

# ---------------------------------------------------------------------------
# SSE connection limiting — prevent resource exhaustion
# ---------------------------------------------------------------------------

_sse_count = 0
_sse_lock = threading.Lock()
_MAX_SSE = 4
_SSE_MAX_LIFETIME = 1800  # 30 minutes


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enrich_status_with_ma(status: dict, client) -> None:
    """Add MA syncgroup name and now-playing metadata to a client status dict."""
    player_id = getattr(client, "player_id", "")
    if not player_id:
        return
    ma_group = get_ma_group_for_player_id(player_id)
    if ma_group and ma_group.get("name"):
        status["group_name"] = ma_group["name"]
    # Per-device MA now-playing: prefer id-matched syncgroup, then Sendspin-reported
    # group_id (which IS the MA syncgroup id), then solo player_id queue
    if ma_group:
        status["ma_now_playing"] = get_ma_now_playing_for_group(ma_group["id"])
    else:
        dev_group_id: str = status.get("group_id", "")
        status["ma_now_playing"] = (
            get_ma_now_playing_for_group(dev_group_id) or get_ma_now_playing_for_group(player_id) or {}
        )


def get_client_status_for(client):
    """Get status dict for a specific client."""
    try:
        if client is None:
            return {
                "connected": False,
                "server_connected": False,
                "bluetooth_connected": False,
                "bluetooth_available": False,
                "playing": False,
                "error": "Client not running",
                "version": VERSION,
                "build_date": BUILD_DATE,
                "bluetooth_mac": None,
            }

        if not hasattr(client, "status"):
            return {
                "connected": False,
                "server_connected": False,
                "bluetooth_connected": False,
                "bluetooth_available": False,
                "playing": False,
                "error": "Client initializing",
                "version": VERSION,
                "build_date": BUILD_DATE,
                "bluetooth_mac": None,
            }

        with client._status_lock:
            status = client.status.copy()

        if "uptime_start" in status:
            uptime = datetime.now(tz=UTC) - status["uptime_start"]
            status["uptime"] = str(timedelta(seconds=int(uptime.total_seconds())))
            del status["uptime_start"]

        status["version"] = VERSION
        status["build_date"] = BUILD_DATE
        status["runtime"] = state._detect_runtime_type()
        status["connected"] = client.is_running()
        status["player_name"] = getattr(client, "player_name", None)
        status["listen_port"] = getattr(client, "listen_port", None)
        status["server_host"] = getattr(client, "server_host", None)
        status["server_port"] = getattr(client, "server_port", None)
        status["static_delay_ms"] = getattr(client, "static_delay_ms", None)
        status["connected_server_url"] = getattr(client, "connected_server_url", "") or (
            f"ws://{client.server_host}:{client.server_port}/sendspin"
            if getattr(client, "server_host", None) and client.server_host.lower() not in ("auto", "discover", "")
            else ""
        )

        bt_mgr = getattr(client, "bt_manager", None)
        status["bluetooth_mac"] = bt_mgr.mac_address if bt_mgr else None
        status["bluetooth_adapter"] = (bt_mgr.effective_adapter_mac or bt_mgr.adapter) if bt_mgr else None
        adapter_name = None
        if bt_mgr:
            lookup_mac = bt_mgr.effective_adapter_mac or bt_mgr.adapter
            if lookup_mac:
                adapter_name = get_adapter_name(lookup_mac.upper())
        status["bluetooth_adapter_name"] = adapter_name
        status["bluetooth_adapter_hci"] = getattr(bt_mgr, "adapter_hci_name", "") if bt_mgr else ""
        status["has_sink"] = bool(getattr(client, "bluetooth_sink_name", None))
        status["sink_name"] = getattr(client, "bluetooth_sink_name", None)
        status["bt_management_enabled"] = getattr(client, "bt_management_enabled", True)
        status["battery_level"] = getattr(bt_mgr, "battery_level", None) if bt_mgr else None

        _enrich_status_with_ma(status, client)

        logger.debug("Status retrieved: %s", status)
        return status

    except Exception as e:
        logger.exception("Error getting client status: %s", e)
        return {
            "connected": False,
            "server_connected": False,
            "bluetooth_connected": False,
            "bluetooth_available": False,
            "playing": False,
            "error": str(e),
            "version": VERSION,
            "build_date": BUILD_DATE,
            "bluetooth_mac": None,
        }


def _build_groups_summary(clients: list) -> list[dict]:
    """Build a list of group objects from the current client list.

    Players sharing the same non-None group_id are merged into one group entry.
    Solo players (group_id=None) each appear as their own single-member group.

    When MA API group data is available, entries that resolve to the same MA
    syncgroup are merged (Sendspin assigns unique UUIDs per session, so two
    local devices in the same MA syncgroup have different group_ids).  Each
    merged entry is then enriched with ``external_members`` (players from
    other bridges) and ``external_count``.
    """
    groups: dict[str | None, dict] = {}
    solo_counter = 0

    for client in clients:
        status = client.status
        gid = status.get("group_id")
        # Give each solo player a unique key so they don't merge
        key = gid if gid is not None else f"__solo_{solo_counter}"
        if gid is None:
            solo_counter += 1

        member = {
            "player_name": getattr(client, "player_name", None),
            "player_id": getattr(client, "player_id", ""),
            "volume": status.get("volume", 100),
            "playing": bool(status.get("playing")),
            "connected": bool(status.get("connected")),
            "server_connected": bool(status.get("server_connected")),
            "bluetooth_connected": bool(status.get("bluetooth_connected")),
        }

        if key not in groups:
            groups[key] = {
                "group_id": gid,
                "group_name": status.get("group_name"),
                "members": [],
            }

        groups[key]["members"].append(member)

    # Merge entries that resolve to the same MA syncgroup.
    # Sendspin assigns a unique UUID per session, so two local devices in
    # the same MA syncgroup appear as separate entries — merge them here.
    ma_groups = state.get_ma_groups()
    entry_syncgroup: dict[int, str] = {}  # index in merged → ma_syncgroup_id
    if ma_groups:
        syncgroup_map: dict[str, dict] = {}  # ma_syncgroup_id → merged entry
        merged: list[dict] = []
        for entry in groups.values():
            ma_syncgroup_id = None
            if entry["group_id"]:
                for m in entry["members"]:
                    pid = m.get("player_id", "")
                    if not pid:
                        continue
                    ma_info = get_ma_group_for_player_id(pid)
                    if ma_info:
                        ma_syncgroup_id = ma_info["id"]
                        if not entry["group_name"] and ma_info.get("name"):
                            entry["group_name"] = ma_info["name"]
                        break
                # Fallback: use group_id directly (it IS the MA syncgroup ID)
                if not ma_syncgroup_id:
                    ma_sg = state.get_ma_group_by_id(entry["group_id"])
                    if ma_sg:
                        ma_syncgroup_id = ma_sg["id"]
                        if not entry["group_name"] and ma_sg.get("name"):
                            entry["group_name"] = ma_sg["name"]
            if ma_syncgroup_id and ma_syncgroup_id in syncgroup_map:
                # Merge into existing entry
                target = syncgroup_map[ma_syncgroup_id]
                target["members"].extend(entry["members"])
                if not target["group_name"] and entry.get("group_name"):
                    target["group_name"] = entry["group_name"]
            else:
                if ma_syncgroup_id:
                    syncgroup_map[ma_syncgroup_id] = entry
                    entry_syncgroup[len(merged)] = ma_syncgroup_id
                merged.append(entry)
    else:
        merged = list(groups.values())

    result = []
    for idx, entry in enumerate(merged):
        members = entry["members"]
        volumes = [m["volume"] for m in members]
        entry["avg_volume"] = round(sum(volumes) / len(volumes)) if volumes else 100
        entry["playing"] = any(m["playing"] for m in members)
        entry["external_members"] = []
        entry["external_count"] = 0

        # Enrich with cross-bridge member info from MA API cache
        ma_sid = entry_syncgroup.get(idx)
        if ma_sid and ma_groups:
            ma_group = next((g for g in ma_groups if g["id"] == ma_sid), None)
            if ma_group:
                local_ids = {m.get("player_id", "") for m in members if m.get("player_id")}
                local_names = {(m.get("player_name") or "").lower() for m in members if m.get("player_name")}
                external = [
                    {"name": m["name"], "available": m.get("available", True)}
                    for m in ma_group.get("members", [])
                    if m.get("id", "") not in local_ids and (m.get("name") or "").lower() not in local_names
                ]
                entry["external_members"] = external
                entry["external_count"] = len(external)

        result.append(entry)

    return result


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@status_bp.route("/api/status")
def api_status():
    """Return status for all client instances."""
    with _clients_lock:
        snapshot = list(_clients)
    if not snapshot:
        result = state.get_bridge_system_info()
        result["error"] = "No clients"
        result["devices"] = []
        result["groups"] = []
        result["disabled_devices"] = state.get_disabled_devices()
        return jsonify(result)
    if len(snapshot) == 1:
        result = get_client_status_for(snapshot[0])
    else:
        first = get_client_status_for(snapshot[0])
        result = {**first, "devices": [get_client_status_for(c) for c in snapshot]}
    result["groups"] = _build_groups_summary(snapshot)
    result["ma_connected"] = state.is_ma_connected()
    result["disabled_devices"] = state.get_disabled_devices()
    _upd = state.get_update_available()
    if _upd:
        result["update_available"] = _upd
    return jsonify(result)


@status_bp.route("/api/groups")
def api_groups():
    """Return a list of MA player groups with their members.

    Players sharing the same group_id (assigned by MA when placed in a Sync Group)
    are returned as one entry. Solo players (not in any MA group) each appear as
    their own single-member entry with group_id=null.
    """
    with _clients_lock:
        snapshot = list(_clients)
    return jsonify(_build_groups_summary(snapshot))


@status_bp.route("/api/status/stream")
def api_status_stream():
    """Server-Sent Events endpoint — pushes status when it changes.

    Clients connect once and receive real-time updates instead of polling
    /api/status every 2 seconds.  A heartbeat comment is sent every 15 s to
    keep the connection alive through proxies (including HA ingress).

    Uses ``threading.Condition.wait_for()`` to avoid the race between reading
    ``_status_version`` and blocking: the Condition lock ensures that any
    ``notify_status_changed()`` call either happens before we start waiting
    (so ``wait_for`` returns immediately) or wakes us up cleanly.
    """
    global _sse_count
    with _sse_lock:
        if _sse_count >= _MAX_SSE:
            return 'data: {"error": "too many listeners"}\n\n', 503, {"Content-Type": "text/event-stream"}
        _sse_count += 1

    def _generate():
        global _sse_count
        try:

            def _build_snapshot():
                with _clients_lock:
                    snapshot = list(_clients)
                if not snapshot:
                    data = state.get_bridge_system_info()
                    data["devices"] = []
                    data["groups"] = []
                    return data
                if len(snapshot) == 1:
                    data = get_client_status_for(snapshot[0])
                else:
                    first = get_client_status_for(snapshot[0])
                    data = {**first, "devices": [get_client_status_for(c) for c in snapshot]}
                data["groups"] = _build_groups_summary(snapshot)
                _upd = state.get_update_available()
                if _upd:
                    data["update_available"] = _upd
                return data

            # Send current status immediately so the client doesn't have to wait
            # for the first change event (important through HA ingress proxy).
            #
            # Leading 2 KB padding flushes proxy buffers (Nginx, HA Ingress,
            # Cloudflare) so they start streaming instead of buffering the
            # entire response.
            yield ": " + " " * 2048 + "\n\n"

            initial = _build_snapshot()
            if initial:
                yield f"data: {json.dumps(initial)}\n\n"

            last_version = state.get_status_version()
            started = time.monotonic()
            while True:
                if time.monotonic() - started >= _SSE_MAX_LIFETIME:
                    yield 'data: {"error": "session expired"}\n\n'
                    break

                changed, last_version = state.wait_for_status_change(last_version, timeout=15)

                if changed:
                    data = _build_snapshot()
                    if data:
                        yield f"data: {json.dumps(data)}\n\n"
                else:
                    # 15 s timeout — send a keepalive comment so proxies don't close
                    yield ": heartbeat\n\n"
        finally:
            with _sse_lock:
                _sse_count -= 1

    return Response(
        _generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@status_bp.route("/api/diagnostics")
def api_diagnostics():
    """Return structured health diagnostics."""
    try:
        # Runtime detection
        runtime = "unknown"
        if os.path.exists("/data/options.json"):
            runtime = "ha_addon"
        elif os.path.exists("/.dockerenv"):
            runtime = "docker"
        elif os.path.exists("/etc/systemd/system/sendspin-client.service"):
            runtime = "systemd"

        uptime_str = str(datetime.now(tz=UTC) - state.bridge_start_time).split(".")[0]

        diag: dict = {
            "version": VERSION,
            "build_date": BUILD_DATE,
            "runtime": runtime,
            "uptime": uptime_str,
            "environment": _collect_environment(),
        }

        try:
            r = subprocess.run(["bluetoothctl", "list"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and "Controller" in r.stdout:
                diag["bluetooth_daemon"] = "active"
            else:
                r2 = subprocess.run(
                    ["systemctl", "is-active", "bluetooth"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                diag["bluetooth_daemon"] = r2.stdout.strip() or "inactive"
        except Exception:
            diag["bluetooth_daemon"] = "unknown"

        dbus_env = os.environ.get("DBUS_SYSTEM_BUS_ADDRESS", "")
        dbus_path = dbus_env.replace("unix:path=", "") if dbus_env else "/run/dbus/system_bus_socket"
        diag["dbus_available"] = os.path.exists(dbus_path)

        try:
            r = subprocess.run(["bluetoothctl", "list"], capture_output=True, text=True, timeout=5)
            adapters = []
            for i, line in enumerate(r.stdout.splitlines()):
                if "Controller" not in line:
                    continue
                parts = line.split()
                mac = next((p for p in parts if len(p) == 17 and p.count(":") == 5), "")
                adapters.append(
                    {
                        "id": f"hci{i}",
                        "mac": mac,
                        "default": "default" in line.lower(),
                    }
                )
            diag["adapters"] = adapters
        except Exception as e:
            diag["adapters"] = [{"error": str(e)}]

        try:
            diag["pulseaudio"] = get_server_name()
        except Exception:
            diag["pulseaudio"] = "not available"

        try:
            diag["sinks"] = [s["name"] for s in list_sinks() if "bluez" in s["name"].lower()]
        except Exception:
            diag["sinks"] = []

        device_diag = []
        with _clients_lock:
            snapshot = list(_clients)
        for client in snapshot:
            bt_mgr = getattr(client, "bt_manager", None)
            device_diag.append(
                {
                    "name": getattr(client, "player_name", "Unknown"),
                    "mac": bt_mgr.mac_address if bt_mgr else None,
                    "connected": client.status.get("bluetooth_connected", False),
                    "enabled": getattr(client, "bt_management_enabled", True),
                    "sink": getattr(client, "bluetooth_sink_name", None),
                    "last_error": client.status.get("last_error"),
                }
            )
        diag["devices"] = device_diag

        # MA API integration status
        ma_url, ma_token = state.get_ma_api_credentials()
        ma_groups = state.get_ma_groups()

        # Build a player_id→client lookup for matching MA members to bridge devices
        bridge_by_id = {getattr(c, "player_id", ""): c for c in snapshot if getattr(c, "player_id", "")}

        enriched_groups = []
        for g in ma_groups:
            members_detail = []
            for m in g.get("members", []):
                mid = m.get("id", "")
                bridge_client = bridge_by_id.get(mid)
                member_info: dict = {
                    "id": mid,
                    "name": m.get("name", m.get("id", "")),
                    "state": m.get("state"),
                    "volume": m.get("volume"),
                    "available": m.get("available", True),
                    "is_bridge": bridge_client is not None,
                }
                if bridge_client:
                    member_info["enabled"] = getattr(bridge_client, "bt_management_enabled", True)
                    member_info["bt_connected"] = bridge_client.status.get("bluetooth_connected", False)
                    member_info["server_connected"] = bridge_client.status.get("server_connected", False)
                    member_info["playing"] = bridge_client.status.get("playing", False)
                    member_info["sink"] = getattr(bridge_client, "bluetooth_sink_name", None)
                    member_info["bt_mac"] = (
                        getattr(bridge_client.bt_manager, "mac_address", None) if bridge_client.bt_manager else None
                    )
                members_detail.append(member_info)

            np = state.get_ma_now_playing_for_group(g["id"])
            group_info: dict = {
                "id": g["id"],
                "name": g.get("name", ""),
                "members": members_detail,
            }
            if np:
                group_info["now_playing"] = {
                    "title": np.get("title"),
                    "artist": np.get("artist"),
                    "state": np.get("state"),
                }
            enriched_groups.append(group_info)

        diag["ma_integration"] = {
            "configured": bool(ma_url and ma_token),
            "connected": state.is_ma_connected(),
            "version": state.get_ma_server_version(),
            "url": ma_url or "",
            "syncgroups": enriched_groups,
        }

        # PA sink-inputs with properties (for routing diagnostics)
        try:
            r = subprocess.run(
                ["pactl", "list", "sink-inputs"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            sink_inputs = []
            current: dict = {}
            for line in r.stdout.splitlines():
                line = line.strip()
                if line.startswith("Sink Input #"):
                    if current:
                        sink_inputs.append(current)
                    current = {"id": line.split("#")[1]}
                elif ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip().lower().replace(" ", "_")
                    if key in ("sink", "state") or "application" in key or "media" in key:
                        current[key] = val.strip()
            if current:
                sink_inputs.append(current)
            diag["sink_inputs"] = sink_inputs
        except Exception as e:
            diag["sink_inputs"] = [{"error": str(e)}]

        # PortAudio devices available inside the container
        try:
            from sendspin.audio import query_devices

            diag["portaudio_devices"] = [
                {"index": d.index, "name": d.name, "is_default": d.is_default}
                for d in query_devices()
                if d.output_channels > 0
            ]
        except Exception as e:
            diag["portaudio_devices"] = [{"error": str(e)}]

        diag["subprocesses"] = _collect_subprocess_info()

        return jsonify(diag)
    except Exception:
        logger.exception("Diagnostics collection failed")
        return jsonify({"error": "Internal error"}), 500


# ---------------------------------------------------------------------------
# (Logs endpoint lives in api_config.py — reads journalctl / supervisor / docker)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# /api/bugreport — assembled bug report with masked sensitive data
# ---------------------------------------------------------------------------

_ANSI_RE_STATUS = re.compile(r"\x1b\[[0-9;]*m")

_MAC_RE = re.compile(
    r"([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2}):([0-9A-Fa-f]{2})"
)
_IPV4_RE = re.compile(r"\b(\d{1,3})\.(\d{1,3})\.(\d{1,3})\.(\d{1,3})\b")


def _mask_mac(m: re.Match) -> str:
    """AA:BB:**:**:**:FF"""
    g = m.groups()
    return f"{g[0]}:{g[1]}:**:**:**:{g[5]}"


def _mask_ip(m: re.Match) -> str:
    """192.168.*.*"""
    return f"{m.group(1)}.{m.group(2)}.*.*"


def _mask_text(text: str) -> str:
    """Mask MAC and IPv4 addresses in arbitrary text."""
    text = _MAC_RE.sub(_mask_mac, text)
    return _IPV4_RE.sub(_mask_ip, text)


def _mask_obj(obj: object) -> object:
    """Recursively mask MAC/IP in dicts, lists, and strings."""
    if isinstance(obj, str):
        return _mask_text(obj)
    if isinstance(obj, dict):
        return {k: _mask_obj(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_mask_obj(item) for item in obj]
    return obj


def _collect_environment() -> dict:
    """Gather system environment info for bug reports."""
    env: dict = {
        "python": sys.version,
        "platform": _platform.platform(),
        "arch": _platform.machine(),
        "kernel": _platform.release(),
    }

    # BlueZ version
    try:
        r = subprocess.run(["bluetoothctl", "--version"], capture_output=True, text=True, timeout=3)
        env["bluez"] = r.stdout.strip()
    except Exception:
        env["bluez"] = "unknown"

    # PulseAudio / PipeWire version
    for cmd in [["pulseaudio", "--version"], ["pipewire", "--version"]]:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                env["audio_server"] = r.stdout.strip()
                break
        except FileNotFoundError:
            continue
    else:
        try:
            r = subprocess.run(["pactl", "info"], capture_output=True, text=True, timeout=3)
            for line in r.stdout.splitlines():
                if "Server Name" in line:
                    env["audio_server"] = line.split(":", 1)[1].strip()
                    break
        except Exception:
            env["audio_server"] = "unknown"

    # Process memory (RSS)
    try:
        import resource

        rss_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes, Linux reports KB
        if sys.platform == "darwin":
            rss_kb //= 1024
        env["process_rss_mb"] = round(rss_kb / 1024, 1)
    except Exception:
        pass

    return env


def _collect_subprocess_info() -> list[dict]:
    """Gather per-device subprocess info."""
    info = []
    with _clients_lock:
        snapshot = list(_clients)
    for client in snapshot:
        proc = getattr(client, "_daemon_proc", None)
        entry: dict = {
            "name": getattr(client, "player_name", "?"),
            "pid": proc.pid if proc else None,
            "alive": proc is not None and proc.returncode is None if proc else False,
            "running": getattr(client, "running", False),
            "restart_delay": getattr(client, "_restart_delay", 1.0),
            "zombie_restarts": getattr(client, "_zombie_restart_count", 0),
        }
        # Reconnect info from status
        status = getattr(client, "status", None)
        if status:
            entry["reconnecting"] = status.get("reconnecting", False)
            entry["reconnect_attempt"] = status.get("reconnect_attempt", 0)
            entry["last_error"] = status.get("last_error")
            entry["last_error_at"] = status.get("last_error_at")
        info.append(entry)
    return info


def _sanitized_config() -> dict:
    """Return config with secrets redacted."""
    try:
        cfg = load_config()
    except Exception:
        return {"error": "could not load config"}

    redacted_keys = {
        "AUTH_PASSWORD_HASH",
        "SECRET_KEY",
        "MA_API_TOKEN",
        "LAST_VOLUMES",
    }
    result: dict = {}
    for k, v in cfg.items():
        if k in redacted_keys:
            result[k] = "***"
        elif k == "MA_API_URL" and v:
            result[k] = _mask_text(str(v))
        elif k == "BLUETOOTH_DEVICES" and isinstance(v, list):
            masked_devs: list = [
                {dk: (_mask_text(str(dv)) if dk == "mac" else dv) for dk, dv in d.items()} if isinstance(d, dict) else d
                for d in v
            ]
            result[k] = masked_devs
        else:
            result[k] = v
    return result


def _collect_recent_logs(n: int = 100) -> list[str]:
    """Read recent log lines from journalctl, HA Supervisor, or docker logs."""
    try:
        if os.path.exists("/etc/systemd/system/sendspin-client.service"):
            r = subprocess.run(
                ["journalctl", "-u", "sendspin-client", "-n", str(n), "--no-pager", "--output=short-iso"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            return r.stdout.splitlines() or r.stderr.splitlines()
        if os.path.exists("/data/options.json"):
            import urllib.request as _ur

            token = os.environ.get("SUPERVISOR_TOKEN", "")
            if token:
                req = _ur.Request(
                    "http://supervisor/addons/self/logs",
                    headers={"Authorization": f"Bearer {token}", "Accept": "text/plain"},
                )
                with _ur.urlopen(req, timeout=10) as resp:
                    text = resp.read().decode("utf-8", errors="replace")
                return text.splitlines()[-n:]
            return []
        # Docker fallback
        r = subprocess.run(
            ["docker", "logs", "--tail", str(n), "sendspin-client"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (r.stdout + r.stderr).splitlines()
    except Exception:
        logger.debug("Could not collect logs for bug report", exc_info=True)
        return []


def _collect_bt_device_info() -> list[dict]:
    """Run ``bluetoothctl info`` for every configured BT device."""
    results: list[dict] = []
    try:
        cfg = load_config()
    except Exception:
        return results
    devices = cfg.get("BLUETOOTH_DEVICES", [])
    if not isinstance(devices, list):
        return results
    for dev in devices:
        mac = dev.get("mac", "") if isinstance(dev, dict) else ""
        if not mac:
            continue
        entry: dict = {"mac": mac, "name": dev.get("name", "?")}
        try:
            r = subprocess.run(
                ["bluetoothctl"],
                input=f"info {mac}\n",
                capture_output=True,
                text=True,
                timeout=5,
            )
            lines = [_ANSI_RE_STATUS.sub("", ln).strip() for ln in r.stdout.splitlines() if ln.strip()]
            for ln in lines:
                if ":" not in ln:
                    continue
                key, _, val = ln.partition(":")
                k = key.strip().lower().replace(" ", "_")
                if k in ("paired", "bonded", "trusted", "blocked", "connected", "class", "icon"):
                    entry[k] = val.strip()
        except Exception as exc:
            entry["error"] = str(exc)
        results.append(entry)
    return results


@status_bp.route("/api/bugreport")
def api_bugreport():
    """Assemble a bug report: short summary for URL + full file for download."""
    try:
        # Collect all diagnostic data
        diag_resp = api_diagnostics()
        diag = diag_resp.get_json() if hasattr(diag_resp, "get_json") else {}

        env = _collect_environment()
        subprocs = _collect_subprocess_info()
        config_info = _sanitized_config()

        log_lines = _collect_recent_logs(100)
        bt_device_info = _collect_bt_device_info()

        # Detect runtime
        runtime = "unknown"
        if os.path.exists("/data/options.json"):
            runtime = "ha_addon"
        elif os.path.exists("/.dockerenv"):
            runtime = "docker"
        elif os.path.exists("/etc/systemd/system/sendspin-client.service"):
            runtime = "systemd"

        uptime_str = str(datetime.now(tz=UTC) - state.bridge_start_time)

        # Build structured report
        report = {
            "version": VERSION,
            "build_date": BUILD_DATE,
            "runtime": runtime,
            "uptime": uptime_str,
            "environment": env,
            "diagnostics": diag,
            "subprocesses": subprocs,
            "bt_device_info": bt_device_info,
            "config": config_info,
            "logs": log_lines,
        }

        # Mask all MAC/IP in the report
        masked = _mask_obj(report)

        # --- Short markdown (for URL ?body=, fits ~4 KB) ---
        env = masked["environment"]
        diag = masked.get("diagnostics", {})
        devices = diag.get("devices", [])
        subprocs = masked["subprocesses"]

        bt_total = len(devices)
        bt_conn = sum(1 for d in devices if d.get("connected"))
        ma_info = diag.get("ma_integration", {})
        ma_status = "connected" if ma_info.get("connected") else "disconnected"
        sinks = diag.get("sinks", [])
        sink_inputs = diag.get("sink_inputs", [])
        alive_count = sum(1 for sp in subprocs if sp.get("alive"))

        # Last 3 WARNING/ERROR lines for short report
        warn_keywords = ("WARNING", "ERROR", "CRITICAL")
        recent_errors = [ln for ln in masked.get("logs", []) if any(k in ln for k in warn_keywords)][-3:]

        ma_ver = ma_info.get("version") or "?"
        ma_label = f"connected (v{ma_ver})" if ma_info.get("connected") and ma_ver != "?" else ma_status

        short = [
            "## Bug Report",
            "",
            f"**Version:** {masked['version']} (built {masked['build_date']})",
            f"**Runtime:** {masked['runtime']}  |  **Uptime:** {masked['uptime']}",
            f"**Platform:** {env.get('platform', '?')}  |  **Arch:** {env.get('arch', '?')}",
            f"**BlueZ:** {env.get('bluez', '?')}  |  **Audio:** {env.get('audio_server', '?')}",
            f"**Python:** {env.get('python', '?').split()[0]}  |  **RSS:** {env.get('process_rss_mb', '?')} MB",
            "",
            f"**BT:** {bt_conn}/{bt_total} connected  |  "
            f"**MA:** {ma_label}  |  "
            f"**Sinks:** {len(sinks)}  |  "
            f"**Streams:** {len(sink_inputs)}",
            f"**D-Bus:** {'✅' if diag.get('dbus_available') else '❌'}  |  "
            f"**bluetoothd:** {diag.get('bluetooth_daemon', '?')}  |  "
            f"**Subprocesses:** {alive_count}/{len(subprocs)} alive",
        ]
        if recent_errors:
            short.append("")
            short.append("**Recent warnings/errors:**")
            short.append("```")
            short.extend(recent_errors)
            short.append("```")
        short.append("")
        short.append("> 📎 **Full diagnostic report attached as file below**")

        markdown_short = "\n".join(short)

        # --- Full plain-text report (for downloadable file) ---
        text_full = _build_full_text_report(masked, title="BUG REPORT — FULL DIAGNOSTICS")

        return jsonify(
            {
                "markdown_short": markdown_short,
                "text_full": text_full,
                "report": masked,
            }
        )
    except Exception:
        logger.exception("Bug report assembly failed")
        return jsonify({"error": "Internal error"}), 500


def _build_full_text_report(
    masked: dict,
    *,
    title: str = "DIAGNOSTICS REPORT",
) -> str:
    """Build the full plain-text diagnostics report from masked data."""
    sep = "=" * 60
    full: list[str] = [
        sep,
        f"  {title}",
        sep,
        "",
        f"Version:  {masked.get('version', '?')} (built {masked.get('build_date', '?')})",
        f"Runtime:  {masked.get('runtime', '?')}  |  Uptime: {masked.get('uptime', '?')}",
        "",
    ]

    env = masked.get("environment", {})
    diag = masked.get("diagnostics", {})
    devices = diag.get("devices", [])
    subprocs = masked.get("subprocesses", [])
    ma_info = diag.get("ma_integration", {})
    sinks = diag.get("sinks", [])

    # Environment
    if env:
        full.append("--- ENVIRONMENT ---")
        for k, v in env.items():
            full.append(f"  {k + ':':<20s} {v}")
        full.append("")

    # Devices
    if devices:
        full.append("--- DEVICES ---")
        full.append(f"  {'Name':<24s} {'MAC':<20s} {'BT':<6s} {'Sink':<36s} {'Enabled'}")
        for d in devices:
            bt = "Yes" if d.get("connected") else "No"
            sink = d.get("sink") or "—"
            enabled = "Yes" if d.get("enabled") else "No"
            full.append(f"  {d.get('name', '?'):<24s} {d.get('mac', '?'):<20s} {bt:<6s} {sink:<36s} {enabled}")
        full.append("")

    # Subprocesses
    if subprocs:
        full.append("--- SUBPROCESSES ---")
        full.append(
            f"  {'Name':<24s} {'PID':<8s} {'Alive':<8s} {'Running':<10s} {'Recon':<8s} {'Zombie':<8s} Last Error"
        )
        for sp in subprocs:
            pid = str(sp.get("pid") or "—")
            alive = "Yes" if sp.get("alive") else "No"
            running = "Yes" if sp.get("running") else "No"
            recon = str(sp.get("reconnect_attempt", 0) or "—")
            zombie = str(sp.get("zombie_restarts", 0))
            err = sp.get("last_error") or "—"
            full.append(
                f"  {sp.get('name', '?'):<24s} {pid:<8s} {alive:<8s} {running:<10s} {recon:<8s} {zombie:<8s} {err}"
            )
        full.append("")

    # MA integration
    if ma_info.get("configured"):
        full.append("--- MUSIC ASSISTANT ---")
        full.append(f"  URL:        {ma_info.get('url', '?')}")
        full.append(f"  Version:    {ma_info.get('version') or '?'}")
        full.append(f"  Connected:  {'Yes' if ma_info.get('connected') else 'No'}")
        groups = ma_info.get("syncgroups", [])
        for g in groups:
            full.append(f"  Group: {g.get('name', '?')}")
            np = g.get("now_playing", {})
            if np:
                full.append(
                    f"    Now playing: {np.get('artist', '?')} — {np.get('title', '?')} ({np.get('state', '?')})"
                )
            for m in g.get("members", []):
                avail = "OK" if m.get("available") else "FAIL"
                vol = f" vol={m.get('volume')}" if m.get("volume") is not None else ""
                full.append(f"    {m.get('name', '?')}: {m.get('state', '?')} [{avail}]{vol}")
        full.append("")

    # Adapters
    adapters = diag.get("adapters", [])
    if adapters:
        full.append("--- BT ADAPTERS ---")
        for a in adapters:
            dflt = " (default)" if a.get("default") else ""
            full.append(f"  {a.get('id', '?')}  {a.get('mac', '?')}{dflt}")
        full.append("")

    # BT device info (bluetoothctl info per device)
    bt_devs = masked.get("bt_device_info", [])
    if bt_devs:
        full.append("--- BT DEVICE INFO (bluetoothctl) ---")
        for bd in bt_devs:
            full.append(f"  [{bd.get('name', '?')}]  MAC: {bd.get('mac', '?')}")
            for fld in ("paired", "trusted", "connected", "bonded", "blocked", "class", "icon"):
                if fld in bd:
                    full.append(f"    {fld:<12s}: {bd[fld]}")
            if bd.get("error"):
                full.append(f"    error: {bd['error']}")
        full.append("")

    # PA sinks
    if sinks:
        full.append("--- PA SINKS ---")
        for s in sinks:
            full.append(f"  {s}")
        full.append("")

    # Service status
    full.append("--- SERVICE STATUS ---")
    full.append(f"  D-Bus:       {'OK' if diag.get('dbus_available') else 'FAIL'}")
    full.append(f"  bluetoothd:  {diag.get('bluetooth_daemon', '?')}")
    full.append(f"  PulseAudio:  {diag.get('pulseaudio', '?')}")
    full.append("")

    # Raw diagnostics JSON
    full.append(sep)
    full.append("  RAW DIAGNOSTICS JSON")
    full.append(sep)
    full.append(json.dumps(diag, indent=2, default=str))
    full.append("")

    # Config
    config = masked.get("config")
    if config:
        full.append(sep)
        full.append("  CONFIG (sanitized)")
        full.append(sep)
        full.append(json.dumps(config, indent=2, default=str))
        full.append("")

    # Logs
    logs = masked.get("logs", [])
    if logs:
        full.append(sep)
        full.append(f"  RECENT LOGS (last {len(logs)} lines)")
        full.append(sep)
        for line in logs:
            full.append(str(line))

    return "\n".join(full)


@status_bp.route("/api/diagnostics/download")
def api_diagnostics_download():
    """Download full diagnostics as a plain-text file."""
    try:
        diag_resp = api_diagnostics()
        diag = diag_resp.get_json() if hasattr(diag_resp, "get_json") else {}

        config_info = _sanitized_config()
        log_lines = _collect_recent_logs(100)

        uptime_str = str(datetime.now(tz=UTC) - state.bridge_start_time)

        runtime = "unknown"
        if os.path.exists("/data/options.json"):
            runtime = "ha_addon"
        elif os.path.exists("/.dockerenv"):
            runtime = "docker"
        elif os.path.exists("/etc/systemd/system/sendspin-client.service"):
            runtime = "systemd"

        report = {
            "version": VERSION,
            "build_date": BUILD_DATE,
            "runtime": runtime,
            "uptime": uptime_str,
            "environment": diag.get("environment", {}),
            "diagnostics": diag,
            "subprocesses": diag.get("subprocesses", []),
            "config": config_info,
            "logs": log_lines,
        }

        masked = _mask_obj(report)
        text = _build_full_text_report(masked, title="DIAGNOSTICS REPORT")

        ts = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
        return Response(
            text,
            mimetype="text/plain",
            headers={"Content-Disposition": f'attachment; filename="diagnostics-{ts}.txt"'},
        )
    except Exception:
        logger.exception("Diagnostics download failed")
        return jsonify({"error": "Internal error"}), 500


@status_bp.route("/api/health")
def api_health():
    """Lightweight health check — no auth required, no sensitive data."""
    return jsonify({"ok": True})


@status_bp.route("/api/preflight")
def api_preflight():
    """Setup verification endpoint — no auth required, no sensitive data.

    Returns platform, audio, bluetooth, and D-Bus status for
    quick troubleshooting without exposing device details.
    """

    # Platform
    arch = _platform.machine()

    # Audio
    audio_info: dict = {"system": "unknown", "socket": None, "sinks": 0}
    try:
        srv = get_server_name()
        if srv and "pipewire" in srv.lower():
            audio_info["system"] = "pipewire"
        elif srv:
            audio_info["system"] = "pulseaudio"
        pulse_sock = os.environ.get("PULSE_SERVER", "")
        if pulse_sock:
            audio_info["socket"] = pulse_sock
        sinks = list_sinks()
        audio_info["sinks"] = len(sinks) if sinks else 0
    except Exception:
        pass

    # Bluetooth
    bt_info: dict = {"controller": False, "adapter": None, "paired_devices": 0}
    try:
        result = subprocess.run(
            ["bluetoothctl", "list"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if "Controller" in result.stdout:
            bt_info["controller"] = True
            bt_info["adapter"] = result.stdout.split()[1] if result.stdout.split() else None
        paired = subprocess.run(
            ["bluetoothctl", "devices", "Paired"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        bt_info["paired_devices"] = paired.stdout.strip().count("Device")
    except Exception:
        pass

    # D-Bus
    dbus_ok = os.path.exists("/var/run/dbus/system_bus_socket") or os.path.exists("/run/dbus/system_bus_socket")

    # Memory
    mem_mb = 0
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_mb = int(line.split()[1]) // 1024
                    break
    except Exception:
        pass

    return jsonify(
        {
            "ok": True,
            "platform": arch,
            "audio": audio_info,
            "bluetooth": bt_info,
            "dbus": dbus_ok,
            "memory_mb": mem_mb,
            "version": VERSION,
        }
    )
