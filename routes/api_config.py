"""
Configuration and settings API Blueprint for sendspin-bt-bridge.

Routes for reading/writing config, password management, log level,
service logs, and version info.
"""

import asyncio
import functools
import json
import logging
import os
import subprocess

from flask import Blueprint, jsonify, request

import state
from config import (
    BUILD_DATE,
    CONFIG_DIR,
    CONFIG_FILE,
    DEFAULT_CONFIG,
    VERSION,
    config_lock,
    load_config,
    update_config,
)
from services import (
    bt_remove_device as _bt_remove_device,
)
from services.bluetooth import _MAC_RE
from state import (
    _adapter_cache_lock,
    load_adapter_name_cache,
)
from state import clients as _clients
from state import (
    clients_lock as _clients_lock,
)

logger = logging.getLogger(__name__)

config_bp = Blueprint("api_config", __name__)

# Cached config flag — avoid reading config.json on every volume/mute request.
# Reloaded in api_config() after config save; also valid on process restart
# since config.py is re-read.  Does NOT auto-reload on manual file edit.
_volume_via_ma: bool = True
_mute_via_ma: bool = False


def _reload_volume_via_ma() -> None:
    global _volume_via_ma, _mute_via_ma
    cfg = load_config()
    _volume_via_ma = cfg.get("VOLUME_VIA_MA", True)
    _mute_via_ma = cfg.get("MUTE_VIA_MA", False)


_reload_volume_via_ma()


def get_volume_via_ma() -> bool:
    """Return the cached VOLUME_VIA_MA flag for use by other modules."""
    return _volume_via_ma


def get_mute_via_ma() -> bool:
    """Return the cached MUTE_VIA_MA flag for use by other modules."""
    return _mute_via_ma


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _detect_runtime() -> str:
    """Detect whether running under systemd, HA addon, or Docker. Result is cached."""
    if os.path.exists("/etc/systemd/system/sendspin-client.service") or os.path.exists(
        "/run/systemd/system/sendspin-client.service"
    ):
        return "systemd"
    elif os.path.exists("/data/options.json"):
        return "ha_addon"
    else:
        return "docker"


def _sync_ha_options(config: dict) -> None:
    """Push current config to HA Supervisor options (no-op outside HA addon)."""
    if _detect_runtime() != "ha_addon":
        return
    try:
        import urllib.request as _ur

        token = os.environ.get("SUPERVISOR_TOKEN", "")
        if not token:
            return
        sup_devices = []
        for d in config.get("BLUETOOTH_DEVICES", []):
            entry = {"mac": d.get("mac", ""), "player_name": d.get("player_name", "")}
            if d.get("adapter"):
                entry["adapter"] = d["adapter"]
            if d.get("static_delay_ms"):
                entry["static_delay_ms"] = int(d["static_delay_ms"])
            if d.get("listen_host"):
                entry["listen_host"] = d["listen_host"]
            if d.get("listen_port"):
                entry["listen_port"] = int(d["listen_port"])
            if "enabled" in d:
                entry["enabled"] = bool(d["enabled"])
            if d.get("preferred_format"):
                entry["preferred_format"] = d["preferred_format"]
            sup_devices.append(entry)
        sup_adapters = [
            dict(
                {"id": a["id"], "mac": a.get("mac", "")},
                **({"name": a["name"]} if a.get("name") else {}),
            )
            for a in config.get("BLUETOOTH_ADAPTERS", [])
            if a.get("id")
        ]
        sup_opts = {
            "options": {
                "sendspin_server": config.get("SENDSPIN_SERVER", "auto"),
                "sendspin_port": int(config.get("SENDSPIN_PORT") or 9000),
                "bridge_name": config.get("BRIDGE_NAME", ""),
                "tz": config.get("TZ", ""),
                "pulse_latency_msec": int(config.get("PULSE_LATENCY_MSEC") or 200),
                "prefer_sbc_codec": bool(config.get("PREFER_SBC_CODEC", False)),
                "bt_check_interval": int(config.get("BT_CHECK_INTERVAL") or 10),
                "bt_max_reconnect_fails": int(config.get("BT_MAX_RECONNECT_FAILS") or 0),
                "auth_enabled": bool(config.get("AUTH_ENABLED", False)),
                "bluetooth_devices": sup_devices,
                "bluetooth_adapters": sup_adapters,
            }
        }
        body = json.dumps(sup_opts).encode()
        req = _ur.Request(
            "http://supervisor/addons/self/options",
            data=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        _ur.urlopen(req, timeout=10)
    except Exception as e:
        logger.warning("Failed to sync Supervisor options: %s", e)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@config_bp.route("/api/config", methods=["GET", "POST"])
def api_config():
    """Read or write the service configuration."""
    if request.method == "GET":
        if CONFIG_FILE.exists():
            with config_lock, open(CONFIG_FILE) as f:
                config = json.load(f)
        else:
            config = DEFAULT_CONFIG.copy()

        # Never expose secrets to the browser
        config.pop("AUTH_PASSWORD_HASH", None)
        config.pop("SECRET_KEY", None)

        # Enrich BLUETOOTH_DEVICES with resolved listen_port / listen_host from running clients
        with _clients_lock:
            snapshot = list(_clients)
        client_map = {getattr(c, "player_name", None): c for c in snapshot}
        mac_map = {getattr(getattr(c, "bt_manager", None), "mac_address", None): c for c in snapshot}
        for dev in config.get("BLUETOOTH_DEVICES", []):
            client = client_map.get(dev.get("player_name")) or mac_map.get(dev.get("mac"))
            if client:
                if "listen_port" not in dev or not dev["listen_port"]:
                    dev["listen_port"] = getattr(client, "listen_port", None)
                if "listen_host" not in dev or not dev["listen_host"]:
                    dev["listen_host"] = getattr(client, "listen_host", None) or client.status.get("ip_address")

        return jsonify(config)

    # POST
    config = request.get_json()
    if not isinstance(config, dict):
        return jsonify({"error": "Invalid JSON body"}), 400

    # Validate top-level string fields
    for str_key in ("SENDSPIN_SERVER", "BRIDGE_NAME", "TZ", "LOG_LEVEL"):
        val = config.get(str_key)
        if val is not None and not isinstance(val, str):
            return jsonify({"error": f"{str_key} must be a string"}), 400

    # Validate BLUETOOTH_DEVICES entries
    bt_devices = config.get("BLUETOOTH_DEVICES", [])
    if not isinstance(bt_devices, list):
        return jsonify({"error": "BLUETOOTH_DEVICES must be an array"}), 400
    for dev in bt_devices:
        if not isinstance(dev, dict):
            return jsonify({"error": "Each device must be an object"}), 400
        mac = str(dev.get("mac", ""))
        if mac and not _MAC_RE.match(mac):
            return jsonify({"error": f"Invalid MAC address: {mac}"}), 400
        lp = dev.get("listen_port")
        if lp is not None:
            try:
                lp = int(lp)
                if not (1024 <= lp <= 65535):
                    raise ValueError
            except (ValueError, TypeError):
                return jsonify({"error": f"Invalid listen_port: {dev.get('listen_port')}"}), 400
        ki = dev.get("keepalive_interval")
        if ki is not None:
            try:
                ki = int(ki)
                if ki != 0 and not (30 <= ki <= 3600):
                    raise ValueError
            except (ValueError, TypeError):
                return jsonify(
                    {"error": f"Invalid keepalive_interval: {dev.get('keepalive_interval')} (must be 0 or 30-3600)"}
                ), 400

    # Validate BLUETOOTH_ADAPTERS entries
    bt_adapters = config.get("BLUETOOTH_ADAPTERS", [])
    if not isinstance(bt_adapters, list):
        return jsonify({"error": "BLUETOOTH_ADAPTERS must be an array"}), 400
    for adp in bt_adapters:
        if not isinstance(adp, dict):
            return jsonify({"error": "Each adapter must be an object"}), 400
        amac = str(adp.get("mac", ""))
        if amac and not _MAC_RE.match(amac):
            return jsonify({"error": f"Invalid adapter MAC address: {amac}"}), 400

    # Validate top-level port (empty string treated as unset)
    sp = config.get("SENDSPIN_PORT")
    if sp is not None and sp != "":
        try:
            sp = int(sp)
            if not (1 <= sp <= 65535):
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({"error": f"Invalid SENDSPIN_PORT: {config.get('SENDSPIN_PORT')}"}), 400

    # Strip unknown top-level keys (whitelist)
    _ALLOWED_POST_KEYS = {
        "SENDSPIN_SERVER",
        "SENDSPIN_PORT",
        "BRIDGE_NAME",
        "BLUETOOTH_DEVICES",
        "BLUETOOTH_ADAPTERS",
        "TZ",
        "PULSE_LATENCY_MSEC",
        "PREFER_SBC_CODEC",
        "BT_CHECK_INTERVAL",
        "BT_MAX_RECONNECT_FAILS",
        "AUTH_ENABLED",
        "LAST_VOLUMES",
        "LOG_LEVEL",
        "MA_API_URL",
        "MA_API_TOKEN",
        "MA_USERNAME",
        "VOLUME_VIA_MA",
        "MUTE_VIA_MA",
        "_new_device_default_volume",
    }
    config = {k: v for k, v in config.items() if k in _ALLOWED_POST_KEYS}

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with config_lock:
        existing = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    existing = json.load(f)
                # Preserve keys that are never submitted via the form
                for key in (
                    "LAST_VOLUMES",
                    "AUTH_PASSWORD_HASH",
                    "SECRET_KEY",
                ):
                    if key in existing and key not in config:
                        config[key] = existing[key]
                # The form pre-fills MA_API_TOKEN with the stored value.
                # Empty string = user explicitly cleared it → do NOT restore.
                # (No implicit preserve needed — the field is always submitted.)
                # Preserve MA_USERNAME if not submitted
                if not config.get("MA_USERNAME") and existing.get("MA_USERNAME"):
                    config["MA_USERNAME"] = existing["MA_USERNAME"]
            except Exception as _exc:
                logger.debug("Could not read existing config for merge: %s", _exc)

        # Normalize MA_API_URL: add http:// scheme if missing
        ma_url = config.get("MA_API_URL", "").strip()
        if ma_url and "://" not in ma_url:
            config["MA_API_URL"] = f"http://{ma_url}"

        old_devices = {d["mac"]: d for d in existing.get("BLUETOOTH_DEVICES", []) if d.get("mac")}
        new_devices = {d["mac"]: d for d in config.get("BLUETOOTH_DEVICES", []) if d.get("mac")}

        with _clients_lock:
            client_adapter = {
                getattr(getattr(c, "bt_manager", None), "mac_address", None): getattr(
                    getattr(c, "bt_manager", None), "_adapter_select", ""
                )
                for c in _clients
            }

        for mac, old_dev in old_devices.items():
            new_dev = new_devices.get(mac)
            adapter_changed = new_dev and new_dev.get("adapter") != old_dev.get("adapter")
            deleted = new_dev is None
            if deleted or adapter_changed:
                adapter_mac = client_adapter.get(mac) or ""
                _bt_remove_device(mac, adapter_mac)

        default_vol = config.pop("_new_device_default_volume", None)
        if default_vol is not None:
            last_volumes = config.setdefault("LAST_VOLUMES", existing.get("LAST_VOLUMES", {}))
            for mac in new_devices:
                if mac and mac not in last_volumes:
                    last_volumes[mac] = default_vol

        tmp = str(CONFIG_FILE) + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(config, f, indent=2)
            os.replace(tmp, str(CONFIG_FILE))
        except Exception:
            # Remove partial temp file on failure
            try:
                os.unlink(tmp)
            except OSError as exc:
                logger.debug("cleanup temp config file failed: %s", exc)
            raise

    # Invalidate adapter name cache so next status poll picks up changes
    with _adapter_cache_lock:
        load_adapter_name_cache()

    _reload_volume_via_ma()
    _sync_ha_options(config)

    return jsonify({"success": True})


@config_bp.route("/api/set-password", methods=["POST"])
def api_set_password():
    """Set (or change) the standalone web UI password.

    Only available in non-HA-addon mode.  Requires the request body to contain
    a JSON object with a 'password' key (string, ≥8 characters).
    """
    if os.environ.get("SUPERVISOR_TOKEN"):
        return jsonify({"error": "Use HA user management in HA addon mode"}), 400

    data = request.get_json(silent=True) or {}
    password = data.get("password", "")
    if not password:
        return jsonify({"error": "password is required"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400

    from config import hash_password as _hash_pw

    pw_hash = _hash_pw(password)

    try:
        update_config(lambda cfg: cfg.__setitem__("AUTH_PASSWORD_HASH", pw_hash))
    except Exception as exc:
        logger.debug("read config for auth update failed: %s", exc)

    return jsonify({"success": True})


@config_bp.route("/api/settings/log_level", methods=["POST"])
def api_set_log_level():
    """Apply log level immediately (INFO or DEBUG) and persist to config.json."""
    data = request.get_json(force=True, silent=True) or {}
    level = str(data.get("level", "")).upper()
    if level not in ("INFO", "DEBUG"):
        return jsonify({"error": "level must be 'info' or 'debug'"}), 400

    # Apply to main process root logger immediately
    logging.getLogger().setLevel(getattr(logging, level))
    os.environ["LOG_LEVEL"] = level

    # Persist to config.json
    try:
        update_config(lambda cfg: cfg.__setitem__("LOG_LEVEL", level))
    except Exception as exc:
        logger.debug("read config for log level update failed: %s", exc)

    # Propagate to all running subprocesses via stdin IPC
    loop = state.get_main_loop()
    if loop is not None:
        cmd = {"cmd": "set_log_level", "level": level}
        with _clients_lock:
            snapshot = list(_clients)
        for client in snapshot:
            if client.is_running():
                try:
                    asyncio.run_coroutine_threadsafe(client._send_subprocess_command(cmd), loop).result(timeout=2.0)
                except Exception as exc:
                    logger.debug("Could not send set_log_level to %s: %s", client.player_name, exc)

    return jsonify({"success": True, "level": level})


def _read_log_lines(runtime: str, lines: int) -> list[str]:
    """Read service log lines for the given runtime."""
    if runtime == "systemd":
        result = subprocess.run(
            [
                "journalctl",
                "-u",
                "sendspin-client",
                "-n",
                str(lines),
                "--no-pager",
                "--output=short-iso",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        log_lines = result.stdout.splitlines()
        if not log_lines and result.stderr:
            log_lines = result.stderr.splitlines()
    elif runtime == "ha_addon":
        import urllib.request as _ur

        token = os.environ.get("SUPERVISOR_TOKEN", "")
        if token:
            req = _ur.Request(
                "http://supervisor/addons/self/logs",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "text/plain",
                },
            )
            with _ur.urlopen(req, timeout=10) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            log_lines = text.splitlines()[-lines:]
        else:
            log_lines = ["(SUPERVISOR_TOKEN not available — check addon permissions)"]
    else:
        result = subprocess.run(
            ["docker", "logs", "--tail", str(lines), "sendspin-client"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        log_lines = (result.stdout + result.stderr).splitlines()

    return log_lines or ["(No logs available)"]


@config_bp.route("/api/logs")
def api_logs():
    """Return real service logs (journalctl, Supervisor, or docker logs)."""
    lines = min(request.args.get("lines", 150, type=int), 500)
    try:
        runtime = _detect_runtime()
        log_lines = _read_log_lines(runtime, lines)
        return jsonify({"logs": log_lines, "runtime": runtime})
    except Exception as e:
        logger.error("Error reading logs: %s", e)
        return jsonify({"logs": [f"Error reading logs: {e}"]}), 500


@config_bp.route("/api/logs/download")
def api_logs_download():
    """Download full service logs as a text file."""
    from datetime import datetime, timezone

    from flask import Response

    try:
        lines = 500
        runtime = _detect_runtime()
        log_lines = _read_log_lines(runtime, lines)

        text = "\n".join(log_lines)
        ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
        return Response(
            text,
            mimetype="text/plain",
            headers={"Content-Disposition": f'attachment; filename="sendspin-logs-{ts}.txt"'},
        )
    except Exception as e:
        logger.error("Error downloading logs: %s", e)
        return Response(f"Error: {e}", mimetype="text/plain", status=500)


@config_bp.route("/api/version")
def api_version():
    """Return git version information."""
    cwd = os.path.dirname(os.path.abspath(__file__))
    try:
        git_sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=cwd,
        ).stdout.strip()
        git_desc = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=cwd,
        ).stdout.strip()
        git_date = subprocess.run(
            ["git", "log", "-1", "--format=%ci"],
            capture_output=True,
            text=True,
            timeout=3,
            cwd=cwd,
        ).stdout.strip()
        return jsonify(
            {
                "version": git_desc or VERSION,
                "git_sha": git_sha or "unknown",
                "built_at": (git_date.split(" ")[0] if git_date else BUILD_DATE),
            }
        )
    except Exception:
        return jsonify({"version": VERSION, "git_sha": "unknown", "built_at": BUILD_DATE})


# ---------------------------------------------------------------------------
# Update check & apply
# ---------------------------------------------------------------------------


@config_bp.route("/api/update/check", methods=["POST"])
def api_update_check():
    """Force an immediate version check against GitHub releases."""
    loop = state.get_main_loop()
    if not loop:
        return jsonify({"success": False, "error": "Event loop not available"}), 503
    try:
        from services.update_checker import _parse_version, check_latest_version

        fut = asyncio.run_coroutine_threadsafe(check_latest_version(), loop)
        latest = fut.result(timeout=20)
        if not latest:
            return jsonify({"success": False, "error": "Could not reach GitHub API"}), 502

        current = _parse_version(VERSION)
        remote = _parse_version(latest["version"])
        if remote > current:
            latest["current_version"] = VERSION
            state.set_update_available(latest)
            return jsonify({"success": True, "update_available": True, **latest})
        state.set_update_available(None)
        return jsonify({"success": True, "update_available": False, "latest": latest["version"]})
    except Exception:
        logger.exception("Update check failed")
        return jsonify({"success": False, "error": "Internal error"}), 500


@config_bp.route("/api/update/info")
def api_update_info():
    """Return cached update availability information."""
    info = state.get_update_available()
    runtime = _detect_runtime()
    cfg = load_config()
    result: dict = {
        "update_available": info is not None,
        "runtime": runtime,
        "auto_update": cfg.get("AUTO_UPDATE", False),
    }
    if info:
        result.update(info)
    if runtime == "systemd":
        result["update_method"] = "one_click"
        result["instructions"] = "Click 'Update Now' to upgrade automatically."
    elif runtime == "ha_addon":
        result["update_method"] = "ha_store"
        result["instructions"] = "Update via Home Assistant → Add-ons → Sendspin BT Bridge → Update."
    else:
        result["update_method"] = "manual"
        result["instructions"] = "Run: docker compose pull && docker compose up -d"
    return jsonify(result)


@config_bp.route("/api/update/apply", methods=["POST"])
def api_update_apply():
    """Run upgrade.sh (LXC/systemd only). Returns progress lines."""
    runtime = _detect_runtime()
    if runtime != "systemd":
        methods = {
            "ha_addon": "Update via Home Assistant → Add-ons → Sendspin BT Bridge → Update.",
            "docker": "Run: docker compose pull && docker compose up -d",
        }
        return jsonify({"success": False, "error": methods.get(runtime, "Unsupported runtime")}), 400

    upgrade_script = "/opt/sendspin-client/lxc/upgrade.sh"
    if not os.path.isfile(upgrade_script):
        # Try relative path (dev mode)
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        upgrade_script = os.path.join(base, "lxc", "upgrade.sh")
    if not os.path.isfile(upgrade_script):
        return jsonify({"success": False, "error": "upgrade.sh not found"}), 404

    try:
        result = subprocess.run(
            ["bash", upgrade_script],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return jsonify(
            {
                "success": result.returncode == 0,
                "returncode": result.returncode,
                "stdout": result.stdout[-4000:] if result.stdout else "",
                "stderr": result.stderr[-2000:] if result.stderr else "",
            }
        )
    except subprocess.TimeoutExpired:
        return jsonify({"success": False, "error": "Upgrade timed out (120s)"}), 504
    except Exception:
        logger.exception("Upgrade failed")
        return jsonify({"success": False, "error": "Internal error"}), 500
