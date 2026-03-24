"""
Configuration and settings API Blueprint for sendspin-bt-bridge.

Routes for reading/writing config, password management, log level,
service logs, and version info.
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import subprocess
import threading
import uuid
from datetime import datetime, timezone

from flask import Blueprint, Response, jsonify, request

from config import (
    BUILD_DATE,
    CONFIG_ALLOWED_KEYS,
    CONFIG_FILE,
    CONFIG_SCHEMA_VERSION,
    DEFAULT_UPDATE_CHANNEL,
    SENSITIVE_CONFIG_KEYS,
    _player_id_from_mac,
    config_lock,
    detect_ha_addon_channel,
    get_runtime_version,
    load_config,
    normalize_update_channel,
    resolve_base_listen_port,
    resolve_web_port,
    update_config,
    write_config_file,
)
from services import (
    bt_remove_device as _bt_remove_device,
)
from services.adapter_names import refresh_adapter_name_cache
from services.async_job_state import (
    create_async_job,
    finish_async_job,
    get_async_job,
    get_update_available,
    set_update_available,
)
from services.bluetooth import _MAC_RE
from services.bridge_runtime_state import get_main_loop
from services.config_validation import validate_uploaded_config
from services.device_registry import get_device_registry_snapshot
from services.ha_addon import detect_delivery_channel_from_slug, get_self_addon_info, get_self_delivery_channel
from services.ha_core_api import HaCoreApiError, fetch_ha_area_catalog
from services.ipc_protocol import IPC_PROTOCOL_VERSION
from services.log_analysis import summarize_issue_logs
from services.ma_client import fetch_all_players_snapshot
from services.sendspin_compat import get_runtime_dependency_versions
from services.status_snapshot import build_device_snapshot
from services.update_checker import _is_newer_version, _start_upgrade_job, channel_image_tag, check_latest_version

logger = logging.getLogger(__name__)

config_bp = Blueprint("api_config", __name__)

# Cached config flag — avoid reading config.json on every volume/mute request.
# Reloaded in api_config() after config save; also valid on process restart
# since config.py is re-read.  Does NOT auto-reload on manual file edit.
_volume_via_ma: bool = True
_mute_via_ma: bool = False


def _submit_loop_coroutine(loop, coro, *, description: str) -> bool:
    """Schedule work on the main loop without blocking the current request."""
    try:
        future = asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception as exc:
        if asyncio.iscoroutine(coro):
            coro.close()
        logger.debug("Could not schedule %s: %s", description, exc)
        return False

    add_done_callback = getattr(future, "add_done_callback", None)
    if callable(add_done_callback):

        def _log_completion(done_future) -> None:
            result_getter = getattr(done_future, "result", None)
            if not callable(result_getter):
                return
            try:
                result_getter()
            except Exception as exc:
                logger.debug("%s failed asynchronously: %s", description, exc)

        add_done_callback(_log_completion)
    return True


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


_DOWNLOAD_REDACTED_KEYS = (
    "AUTH_PASSWORD_HASH",
    "SECRET_KEY",
    "MA_API_TOKEN",
    "MA_ACCESS_TOKEN",
    "MA_REFRESH_TOKEN",
    "MA_TOKEN_INSTANCE_HOSTNAME",
    "MA_TOKEN_LABEL",
)
_HA_ADDON_BASE_SLUG = "sendspin_bt_bridge"


def _sanitize_download_config(config: dict) -> dict:
    """Return a copy of config safe for export/download sharing."""
    sanitized = dict(config)
    for key in _DOWNLOAD_REDACTED_KEYS:
        sanitized.pop(key, None)
    return sanitized


def _error_response(message: str, status: int = 400):
    """Return a consistent JSON error payload."""
    return jsonify({"error": message}), status


def _validation_error_response(
    errors: list[dict[str, str]], warnings: list[dict[str, str]] | None = None, status: int = 400
):
    """Return a structured validation error payload."""
    payload = {
        "error": errors[0]["message"] if errors else "Validation failed",
        "errors": errors,
    }
    if warnings:
        payload["warnings"] = warnings
    return jsonify(payload), status


def _parse_optional_int(
    raw, field_name: str, *, min_value: int | None = None, max_value: int | None = None
) -> int | None:
    """Parse an optional integer field and validate inclusive bounds."""
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name}: {raw}") from exc
    if min_value is not None and value < min_value:
        raise ValueError(f"Invalid {field_name}: {raw}")
    if max_value is not None and value > max_value:
        raise ValueError(f"Invalid {field_name}: {raw}")
    return value


def _build_config_get_response():
    """Return the current config payload for the web UI."""
    config = load_config()
    runtime = _detect_runtime()

    # Never expose secrets to the browser — but indicate whether password is set
    has_password = bool(config.get("AUTH_PASSWORD_HASH"))
    config.pop("AUTH_PASSWORD_HASH", None)
    config.pop("SECRET_KEY", None)
    config.pop("MA_ACCESS_TOKEN", None)
    config.pop("MA_REFRESH_TOKEN", None)
    config.pop("MA_TOKEN_INSTANCE_HOSTNAME", None)
    config.pop("MA_TOKEN_LABEL", None)
    config["_password_set"] = has_password
    if runtime == "ha_addon":
        config["WEB_PORT"] = None
        config["_effective_web_port"] = resolve_web_port()
        config["_delivery_channel"] = detect_ha_addon_channel()
    config["_effective_base_listen_port"] = resolve_base_listen_port()

    # Enrich BLUETOOTH_DEVICES with resolved listen_port / listen_host from running clients
    registry = get_device_registry_snapshot()
    client_map = registry.client_map_by_player_name()
    mac_map = registry.client_map_by_mac()
    for dev in config.get("BLUETOOTH_DEVICES", []):
        client = client_map.get(dev.get("player_name")) or mac_map.get(dev.get("mac"))
        if client:
            device = build_device_snapshot(client)
            if "listen_port" not in dev or not dev["listen_port"]:
                dev["listen_port"] = getattr(client, "listen_port", None)
            if "listen_host" not in dev or not dev["listen_host"]:
                dev["listen_host"] = getattr(client, "listen_host", None) or device.extra.get("ip_address")

    return jsonify(config)


def _normalize_device_mac(raw_mac) -> str:
    """Return a canonical MAC string for config payload validation."""
    return str(raw_mac or "").strip().upper()


def _sanitize_last_volumes(last_volumes, valid_macs: set[str]) -> dict[str, int]:
    """Keep saved per-device volumes only for currently configured devices."""
    if not isinstance(last_volumes, dict):
        return {}
    return {
        mac: volume
        for mac, volume in last_volumes.items()
        if mac in valid_macs and isinstance(volume, int) and 0 <= volume <= 100
    }


def _normalize_ha_adapter_area_map(raw_mapping) -> dict[str, dict[str, str]]:
    if raw_mapping in (None, ""):
        return {}
    if not isinstance(raw_mapping, dict):
        raise ValueError("HA_ADAPTER_AREA_MAP must be an object")

    normalized: dict[str, dict[str, str]] = {}
    for raw_mac, raw_entry in raw_mapping.items():
        mac = _normalize_device_mac(raw_mac)
        if not mac or not _MAC_RE.match(mac):
            raise ValueError(f"Invalid adapter MAC address in HA_ADAPTER_AREA_MAP: {raw_mac}")
        if not isinstance(raw_entry, dict):
            raise ValueError(f"Invalid HA_ADAPTER_AREA_MAP entry for {mac}")
        area_id = str(raw_entry.get("area_id") or "").strip()
        area_name = str(raw_entry.get("area_name") or "").strip()
        if not area_id:
            raise ValueError(f"HA_ADAPTER_AREA_MAP entry for {mac} must include area_id")
        normalized[mac] = {"area_id": area_id}
        if area_name:
            normalized[mac]["area_name"] = area_name
    return normalized


def _load_existing_config_for_validation() -> dict:
    """Read the current config file for compare-against-existing warnings."""
    if not CONFIG_FILE.exists():
        return {}
    try:
        with config_lock, open(CONFIG_FILE) as f:
            data = json.load(f)
    except Exception as exc:
        logger.debug("Could not read existing config for validation warnings: %s", exc)
        return {}
    return data if isinstance(data, dict) else {}


def _append_ma_duplicate_device_warnings(
    config: dict, warnings: list[dict[str, str]], *, existing_config: dict | None = None
) -> list[dict[str, str]]:
    """Warn when newly added MACs already appear in MA under the same player_id."""
    ma_url = str(config.get("MA_API_URL") or "").strip()
    ma_token = str(config.get("MA_API_TOKEN") or "").strip()
    if not ma_url or not ma_token:
        return warnings

    existing = existing_config if isinstance(existing_config, dict) else _load_existing_config_for_validation()
    existing_macs = {
        _normalize_device_mac(dev.get("mac"))
        for dev in existing.get("BLUETOOTH_DEVICES", [])
        if isinstance(dev, dict) and dev.get("mac")
    }
    candidates = [
        (index, _normalize_device_mac(dev.get("mac")))
        for index, dev in enumerate(config.get("BLUETOOTH_DEVICES", []))
        if isinstance(dev, dict)
    ]
    if not any(mac and mac not in existing_macs for _, mac in candidates):
        return warnings

    try:
        players = fetch_all_players_snapshot(ma_url, ma_token)
    except Exception as exc:
        logger.debug("Skipping MA duplicate-device warnings: %s", exc)
        return warnings

    players_by_id = {
        str(player.get("player_id") or "").strip(): str(player.get("display_name") or player.get("name") or "").strip()
        for player in players
        if isinstance(player, dict)
    }
    for index, mac in candidates:
        if not mac or mac in existing_macs:
            continue
        player_id = _player_id_from_mac(mac)
        existing_name = players_by_id.get(player_id)
        if not existing_name:
            continue
        warnings.append(
            {
                "field": f"BLUETOOTH_DEVICES[{index}].mac",
                "message": (
                    f"This device already appears in Music Assistant as '{existing_name}' and may belong "
                    "to another bridge. Disconnect or remove it there first to avoid conflicts."
                ),
            }
        )
    return warnings


def _update_channel_warning(channel: str) -> str | None:
    if channel == "beta":
        return "Beta channel tracks preview builds from the beta branch and may contain unfinished or unstable changes."
    if channel == "rc":
        return "RC channel tracks release candidates from main before stable publication and may still contain regressions."
    return None


def _docker_update_command(channel: str) -> str:
    image_tag = channel_image_tag(channel)
    return f"docker pull ghcr.io/trudenboy/sendspin-bt-bridge:{image_tag}"


def _docker_update_instructions(channel: str) -> str:
    command = _docker_update_command(channel)
    return f"Pull the matching container tag manually, e.g. `{command}` and redeploy your container or compose stack."


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


def _detect_ha_addon_delivery_channel_from_slug(slug: str) -> str | None:
    return detect_delivery_channel_from_slug(slug)


def _get_ha_addon_delivery_details() -> dict[str, str] | None:
    if _detect_runtime() != "ha_addon":
        return None
    token = os.environ.get("SUPERVISOR_TOKEN", "").strip()
    if not token:
        return None

    try:
        data = get_self_addon_info(timeout=10)
    except OSError as exc:
        logger.warning("Failed to query HA addon self info: %s", exc)
        return None

    if not isinstance(data, dict):
        return None

    slug = str(data.get("slug") or "")
    return {
        "slug": slug,
        "name": str(data.get("name") or ""),
        "channel": _detect_ha_addon_delivery_channel_from_slug(slug) or "",
    }


def _ha_addon_update_instructions(channel: str, delivery: dict[str, str] | None) -> str:
    delivery_channel = (delivery or {}).get("channel", "")
    addon_name = (delivery or {}).get("name") or "Sendspin Bluetooth Bridge"
    if delivery_channel:
        if channel != delivery_channel:
            return (
                f"Selected update channel is `{channel}`, but the installed Home Assistant addon track is "
                f"`{delivery_channel}` ({addon_name}). To actually switch tracks, install the matching addon "
                "variant from the Home Assistant store; saving the setting only changes prerelease preference "
                "inside the app."
            )
        return f"Update via Home Assistant → Add-ons → {addon_name} → Update."

    if channel == "stable":
        return "Update via Home Assistant → Add-ons → Sendspin Bluetooth Bridge → Update."
    return (
        "Install the matching prerelease addon variant from the Home Assistant store. Saving `update_channel` "
        "alone does not switch the installed addon track."
    )


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
            if d.get("room_id"):
                entry["room_id"] = d["room_id"]
            if d.get("room_name"):
                entry["room_name"] = d["room_name"]
            if d.get("handoff_mode"):
                entry["handoff_mode"] = d["handoff_mode"]
            sup_devices.append(entry)
        sup_adapters = [
            dict(
                {"id": a["id"], "mac": a.get("mac", "")},
                **({"name": a["name"]} if a.get("name") else {}),
            )
            for a in config.get("BLUETOOTH_ADAPTERS", [])
            if a.get("id")
        ]
        options = {
            "sendspin_server": config.get("SENDSPIN_SERVER", "auto"),
            "sendspin_port": int(config.get("SENDSPIN_PORT") or 9000),
            "bridge_name": config.get("BRIDGE_NAME", ""),
            "ha_area_name_assist_enabled": bool(config.get("HA_AREA_NAME_ASSIST_ENABLED", True)),
            "tz": config.get("TZ", ""),
            "pulse_latency_msec": int(config.get("PULSE_LATENCY_MSEC") or 200),
            "startup_banner_grace_seconds": int(config.get("STARTUP_BANNER_GRACE_SECONDS", 10)),
            "prefer_sbc_codec": bool(config.get("PREFER_SBC_CODEC", False)),
            "bt_check_interval": int(config.get("BT_CHECK_INTERVAL") or 10),
            "bt_max_reconnect_fails": int(config.get("BT_MAX_RECONNECT_FAILS") or 0),
            "auth_enabled": bool(config.get("AUTH_ENABLED", False)),
            "ma_auto_silent_auth": bool(config.get("MA_AUTO_SILENT_AUTH", True)),
            "bluetooth_devices": sup_devices,
            "bluetooth_adapters": sup_adapters,
        }
        if config.get("BASE_LISTEN_PORT") is not None:
            options["base_listen_port"] = int(config["BASE_LISTEN_PORT"])
        sup_opts = {"options": options}
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


@config_bp.route("/api/config/download")
def api_config_download():
    """Download a share-safe config export with sensitive tokens removed."""
    if not CONFIG_FILE.exists():
        return _error_response("No config file found", 404)
    try:
        with config_lock, open(CONFIG_FILE) as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError):
        logger.exception("Could not read config for download")
        return _error_response("Could not read config file", 500)
    raw = json.dumps(_sanitize_download_config(config), indent=2)
    bridge_name = config.get("BRIDGE_NAME", "").strip() or "Bridge"
    bridge_name = bridge_name.replace(" ", "_")
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{bridge_name}_SBB_Config_{ts}.json"
    return Response(
        raw,
        mimetype="application/json",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


_PRESERVED_KEYS = tuple(sorted(SENSITIVE_CONFIG_KEYS | {"MA_TOKEN_INSTANCE_HOSTNAME", "MA_TOKEN_LABEL"}))


@config_bp.route("/api/config/upload", methods=["POST"])
def api_config_upload():
    """Upload a config.json file to replace the current configuration.

    Preserves security-sensitive keys from the existing config.
    """
    f = request.files.get("file")
    if not f:
        return _error_response("No file uploaded")

    _MAX_CONFIG_SIZE = 1_000_000  # 1 MB
    try:
        raw = f.read(_MAX_CONFIG_SIZE + 1)
        if len(raw) > _MAX_CONFIG_SIZE:
            return _error_response("Config file too large (max 1 MB)", 413)
        uploaded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return _error_response(f"Invalid JSON: {exc}")

    if not isinstance(uploaded, dict):
        return _error_response("Config must be a JSON object")
    validation = validate_uploaded_config(uploaded, default_base_listen_port=resolve_base_listen_port())
    warnings = [{"field": issue.field, "message": issue.message} for issue in validation.warnings]
    if not validation.is_valid:
        errors = [{"field": issue.field, "message": issue.message} for issue in validation.errors]
        return _validation_error_response(errors, warnings)
    uploaded = validation.normalized_config
    warnings = _append_ma_duplicate_device_warnings(uploaded, warnings)

    # Preserve sensitive keys from existing config
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with config_lock:
        existing = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as ef:
                    existing = json.load(ef)
            except (json.JSONDecodeError, OSError):
                pass

        for key in _PRESERVED_KEYS:
            if key in existing:
                uploaded[key] = existing[key]

        # Remove sensitive keys that leaked into the uploaded file
        for key in _PRESERVED_KEYS:
            if key not in existing:
                uploaded.pop(key, None)

        write_config_file(uploaded, config_file=CONFIG_FILE, config_dir=CONFIG_FILE.parent)

    payload: dict[str, object] = {"success": True}
    if warnings:
        payload["validation"] = {"warnings": warnings}
    return jsonify(payload)


@config_bp.route("/api/config/validate", methods=["POST"])
def api_config_validate():
    """Validate a config payload without persisting it."""
    config = request.get_json()
    if not isinstance(config, dict):
        return _error_response("Invalid JSON body")

    validation = validate_uploaded_config(config, default_base_listen_port=resolve_base_listen_port())
    errors = [{"field": issue.field, "message": issue.message} for issue in validation.errors]
    warnings = [{"field": issue.field, "message": issue.message} for issue in validation.warnings]
    if validation.is_valid:
        warnings = _append_ma_duplicate_device_warnings(validation.normalized_config, warnings)
    status = 200 if validation.is_valid else 400
    return (
        jsonify(
            {
                "valid": validation.is_valid,
                "errors": errors,
                "warnings": warnings,
                "normalized_config": validation.normalized_config,
            }
        ),
        status,
    )


@config_bp.route("/api/config", methods=["GET", "POST"])
def api_config():
    """Read or write the service configuration."""
    if request.method == "GET":
        return _build_config_get_response()

    # POST
    config = request.get_json()
    if not isinstance(config, dict):
        return _error_response("Invalid JSON body")

    validation = validate_uploaded_config(config, default_base_listen_port=resolve_base_listen_port())
    warnings = [{"field": issue.field, "message": issue.message} for issue in validation.warnings]
    if not validation.is_valid:
        errors = [{"field": issue.field, "message": issue.message} for issue in validation.errors]
        return _validation_error_response(errors, warnings)
    config = validation.normalized_config
    warnings = _append_ma_duplicate_device_warnings(config, warnings)

    # Validate top-level string fields
    for str_key in ("SENDSPIN_SERVER", "BRIDGE_NAME", "TZ", "LOG_LEVEL", "UPDATE_CHANNEL"):
        val = config.get(str_key)
        if val is not None and not isinstance(val, str):
            return _error_response(f"{str_key} must be a string")
    if _detect_runtime() == "ha_addon":
        config["WEB_PORT"] = None
        config["UPDATE_CHANNEL"] = get_self_delivery_channel()
    else:
        config["UPDATE_CHANNEL"] = normalize_update_channel(config.get("UPDATE_CHANNEL", DEFAULT_UPDATE_CHANNEL))

    for bool_key in (
        "PREFER_SBC_CODEC",
        "AUTH_ENABLED",
        "BRUTE_FORCE_PROTECTION",
        "MA_AUTO_SILENT_AUTH",
        "MA_WEBSOCKET_MONITOR",
        "VOLUME_VIA_MA",
        "MUTE_VIA_MA",
        "HA_AREA_NAME_ASSIST_ENABLED",
        "SMOOTH_RESTART",
        "AUTO_UPDATE",
        "CHECK_UPDATES",
    ):
        val = config.get(bool_key)
        if val is not None and not isinstance(val, bool):
            return _error_response(f"{bool_key} must be true or false")

    # Validate BLUETOOTH_DEVICES entries
    bt_devices = config.get("BLUETOOTH_DEVICES", [])
    if not isinstance(bt_devices, list):
        return _error_response("BLUETOOTH_DEVICES must be an array")
    for dev in bt_devices:
        if not isinstance(dev, dict):
            return _error_response("Each device must be an object")
        mac = _normalize_device_mac(dev.get("mac"))
        if mac:
            dev["mac"] = mac
        if mac and not _MAC_RE.match(mac):
            return _error_response(f"Invalid MAC address: {mac}")

    # Validate BLUETOOTH_ADAPTERS entries
    bt_adapters = config.get("BLUETOOTH_ADAPTERS", [])
    if not isinstance(bt_adapters, list):
        return _error_response("BLUETOOTH_ADAPTERS must be an array")
    for adp in bt_adapters:
        if not isinstance(adp, dict):
            return _error_response("Each adapter must be an object")
        amac = str(adp.get("mac", ""))
        if amac and not _MAC_RE.match(amac):
            return _error_response(f"Invalid adapter MAC address: {amac}")

    try:
        config["HA_ADAPTER_AREA_MAP"] = _normalize_ha_adapter_area_map(config.get("HA_ADAPTER_AREA_MAP", {}))
    except ValueError as exc:
        return _error_response(str(exc))

    try:
        sendspin_port = _parse_optional_int(config.get("SENDSPIN_PORT"), "SENDSPIN_PORT", min_value=1, max_value=65535)
        if sendspin_port is not None:
            config["SENDSPIN_PORT"] = sendspin_port
        pulse_latency = _parse_optional_int(
            config.get("PULSE_LATENCY_MSEC"), "PULSE_LATENCY_MSEC", min_value=1, max_value=5000
        )
        if pulse_latency is not None:
            config["PULSE_LATENCY_MSEC"] = pulse_latency
        bt_check_interval = _parse_optional_int(
            config.get("BT_CHECK_INTERVAL"), "BT_CHECK_INTERVAL", min_value=1, max_value=3600
        )
        if bt_check_interval is not None:
            config["BT_CHECK_INTERVAL"] = bt_check_interval
        bt_max_reconnect_fails = _parse_optional_int(
            config.get("BT_MAX_RECONNECT_FAILS"), "BT_MAX_RECONNECT_FAILS", min_value=0, max_value=1000
        )
        if bt_max_reconnect_fails is not None:
            config["BT_MAX_RECONNECT_FAILS"] = bt_max_reconnect_fails
        web_port = _parse_optional_int(config.get("WEB_PORT"), "WEB_PORT", min_value=1, max_value=65535)
        config["WEB_PORT"] = web_port
        base_listen_port = _parse_optional_int(
            config.get("BASE_LISTEN_PORT"), "BASE_LISTEN_PORT", min_value=1, max_value=65535
        )
        config["BASE_LISTEN_PORT"] = base_listen_port
        for int_key, min_val, max_val in (
            ("SESSION_TIMEOUT_HOURS", 1, 168),
            ("BRUTE_FORCE_MAX_ATTEMPTS", 1, 50),
            ("BRUTE_FORCE_WINDOW_MINUTES", 1, 1440),
            ("BRUTE_FORCE_LOCKOUT_MINUTES", 1, 1440),
            ("STARTUP_BANNER_GRACE_SECONDS", 0, 300),
        ):
            value = _parse_optional_int(config.get(int_key), int_key, min_value=min_val, max_value=max_val)
            if value is not None:
                config[int_key] = value
    except ValueError as exc:
        return _error_response(str(exc))

    # Strip unknown top-level keys (whitelist)
    _ALLOWED_POST_KEYS = (
        CONFIG_ALLOWED_KEYS
        - {"AUTH_PASSWORD_HASH", "SECRET_KEY", "MA_ACCESS_TOKEN", "MA_REFRESH_TOKEN", "LAST_SINKS", "MA_AUTH_PROVIDER"}
    ) | {"_new_device_default_volume"}
    config = {k: v for k, v in config.items() if k in _ALLOWED_POST_KEYS}

    # Require password when enabling auth (except HA addon — uses HA login)
    if config.get("AUTH_ENABLED") and not os.environ.get("SUPERVISOR_TOKEN"):
        has_hash = False
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    has_hash = bool(json.load(f).get("AUTH_PASSWORD_HASH"))
            except (json.JSONDecodeError, OSError):
                pass
        if not has_hash:
            return _error_response("Set a password before enabling authentication")

    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with config_lock:
        existing = {}
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE) as f:
                    existing = json.load(f)
                # Preserve keys that are never submitted via the form
                for key in (
                    "LAST_VOLUMES",
                    "LAST_SINKS",
                    "HA_ADAPTER_AREA_MAP",
                    "AUTH_PASSWORD_HASH",
                    "SECRET_KEY",
                    "MA_AUTH_PROVIDER",
                    "MA_TOKEN_INSTANCE_HOSTNAME",
                    "MA_TOKEN_LABEL",
                    "MA_ACCESS_TOKEN",
                    "MA_REFRESH_TOKEN",
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

        client_adapter = {
            mac: getattr(getattr(client, "bt_manager", None), "_adapter_select", "")
            for mac, client in get_device_registry_snapshot().client_map_by_mac().items()
        }

        for mac, old_dev in old_devices.items():
            new_dev = new_devices.get(mac)
            adapter_changed = new_dev and new_dev.get("adapter") != old_dev.get("adapter")
            deleted = new_dev is None
            if deleted or adapter_changed:
                adapter_mac = client_adapter.get(mac) or ""
                _bt_remove_device(mac, adapter_mac)

        default_vol = config.pop("_new_device_default_volume", None)
        last_volumes = config.setdefault("LAST_VOLUMES", existing.get("LAST_VOLUMES", {}))
        if not isinstance(last_volumes, dict):
            last_volumes = {}
        if default_vol is not None:
            for mac in new_devices:
                if mac and mac not in last_volumes:
                    last_volumes[mac] = default_vol
        config["LAST_VOLUMES"] = _sanitize_last_volumes(last_volumes, set(new_devices))

        write_config_file(config, config_file=CONFIG_FILE, config_dir=CONFIG_FILE.parent)

    # Invalidate adapter name cache so next status poll picks up changes
    refresh_adapter_name_cache()

    _reload_volume_via_ma()
    _sync_ha_options(config)

    payload: dict[str, object] = {"success": True}
    if warnings:
        payload["validation"] = {"warnings": warnings}
    return jsonify(payload)


@config_bp.route("/api/ha/areas", methods=["POST"])
def api_ha_areas():
    """Fetch HA area suggestions using a transient Home Assistant token."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error_response("Invalid JSON body")

    ha_token = str(data.get("ha_token") or "").strip()
    if not ha_token:
        return _error_response("ha_token is required")

    adapters = data.get("adapters") or []
    if not isinstance(adapters, list):
        return _error_response("adapters must be an array")

    try:
        payload = fetch_ha_area_catalog(
            ha_token,
            include_devices=bool(data.get("include_devices")),
            adapters=adapters,
        )
    except HaCoreApiError as exc:
        return jsonify({"success": False, "error": str(exc), "areas": [], "bridge_name_suggestions": []}), 502

    payload["success"] = True
    return jsonify(payload)


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
    except (OSError, json.JSONDecodeError, ValueError):
        logger.exception("Could not persist auth password hash")
        return jsonify({"success": False, "error": "Could not save password"}), 500

    return jsonify({"success": True})


@config_bp.route("/api/settings/log_level", methods=["POST"])
def api_set_log_level():
    """Apply log level immediately (INFO or DEBUG) and persist to config.json."""
    data = request.get_json(force=True, silent=True) or {}
    level = str(data.get("level", "")).upper()
    if level not in ("INFO", "DEBUG"):
        return jsonify({"error": "level must be 'info' or 'debug'"}), 400

    # Persist to config.json
    try:
        update_config(lambda cfg: cfg.__setitem__("LOG_LEVEL", level))
    except (OSError, json.JSONDecodeError, ValueError):
        logger.exception("Could not persist log level %s", level)
        return jsonify({"success": False, "error": "Could not persist log level"}), 500

    # Apply to main process root logger immediately after persistence succeeds
    logging.getLogger().setLevel(getattr(logging, level))
    os.environ["LOG_LEVEL"] = level

    # Propagate to all running subprocesses via stdin IPC
    loop = get_main_loop()
    if loop is not None:
        cmd = {"cmd": "set_log_level", "level": level}
        for client in get_device_registry_snapshot().active_clients:
            if client.is_running():
                _submit_loop_coroutine(
                    loop,
                    client._send_subprocess_command(cmd),
                    description=f"set_log_level for {client.player_name}",
                )

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
        issue_summary = summarize_issue_logs(log_lines, tail_lines=20)
        return jsonify(
            {
                "logs": log_lines,
                "runtime": runtime,
                "has_recent_issues": issue_summary["has_issues"],
                "recent_issue_count": issue_summary["issue_count"],
                "recent_issue_level": issue_summary["highest_level"],
            }
        )
    except Exception:
        logger.exception("Error reading logs")
        return jsonify({"logs": ["Error reading logs"]}), 500


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
    except Exception:
        logger.exception("Error downloading logs")
        return Response("Error downloading logs", mimetype="text/plain", status=500)


@config_bp.route("/api/version")
def api_version():
    """Return git version information."""
    cwd = os.path.dirname(os.path.abspath(__file__))
    dependencies = get_runtime_dependency_versions()
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
                "version": git_desc or get_runtime_version(),
                "git_sha": git_sha or "unknown",
                "built_at": (git_date.split(" ")[0] if git_date else BUILD_DATE),
                "config_schema_version": CONFIG_SCHEMA_VERSION,
                "ipc_protocol_version": IPC_PROTOCOL_VERSION,
                "dependencies": dependencies,
            }
        )
    except Exception:
        return jsonify(
            {
                "version": get_runtime_version(),
                "git_sha": "unknown",
                "built_at": BUILD_DATE,
                "config_schema_version": CONFIG_SCHEMA_VERSION,
                "ipc_protocol_version": IPC_PROTOCOL_VERSION,
                "dependencies": dependencies,
            }
        )


# ---------------------------------------------------------------------------
# Update check & apply
# ---------------------------------------------------------------------------


def _run_update_check_job(job_id: str, channel: str, loop) -> None:
    """Resolve update availability in a background thread and store the result."""
    try:
        fut = asyncio.run_coroutine_threadsafe(check_latest_version(channel), loop)
        latest = fut.result(timeout=20)
        runtime_version = get_runtime_version()
        if not latest:
            finish_async_job(job_id, {"success": False, "error": "Could not reach GitHub API"})
            return
        if _is_newer_version(latest["tag"], runtime_version):
            latest["current_version"] = runtime_version
            set_update_available(latest)
            finish_async_job(job_id, {"success": True, "update_available": True, **latest})
            return
        set_update_available(None)
        finish_async_job(
            job_id,
            {
                "success": True,
                "update_available": False,
                "latest": latest["version"],
                "channel": channel,
            },
        )
    except Exception:
        logger.exception("Update check failed")
        finish_async_job(job_id, {"success": False, "error": "Internal error"})


@config_bp.route("/api/update/check", methods=["POST"])
def api_update_check():
    """Start an async version check against GitHub releases."""
    loop = get_main_loop()
    if not loop:
        return jsonify({"success": False, "error": "Event loop not available"}), 503
    payload = request.get_json(silent=True) or {}
    requested_channel = payload.get("channel") or request.args.get("channel")
    channel = normalize_update_channel(requested_channel or load_config().get("UPDATE_CHANNEL"))
    job_id = str(uuid.uuid4())
    create_async_job(job_id, "update-check")
    threading.Thread(
        target=_run_update_check_job,
        args=(job_id, channel, loop),
        daemon=True,
        name=f"update-check-{job_id[:8]}",
    ).start()
    return jsonify({"job_id": job_id, "status": "running", "channel": channel}), 202


@config_bp.route("/api/update/check/result/<job_id>", methods=["GET"])
def api_update_check_result(job_id: str):
    """Poll for async update-check result by job_id."""
    job = get_async_job(job_id)
    if job is None or job.get("job_type") != "update-check":
        return jsonify({"error": "Job not found"}), 404
    if job.get("status") == "running":
        return jsonify({"status": "running", "channel": job.get("channel")})
    return jsonify(job)


@config_bp.route("/api/update/info")
def api_update_info():
    """Return cached update availability information."""
    info = get_update_available()
    runtime = _detect_runtime()
    cfg = load_config()
    channel = normalize_update_channel(cfg.get("UPDATE_CHANNEL"))
    delivery = _get_ha_addon_delivery_details() if runtime == "ha_addon" else None
    result: dict = {
        "update_available": info is not None,
        "runtime": runtime,
        "auto_update": cfg.get("AUTO_UPDATE", False),
        "channel": channel,
        "channel_warning": _update_channel_warning(channel),
    }
    if delivery:
        result["delivery_channel"] = delivery.get("channel") or None
        result["delivery_slug"] = delivery.get("slug") or None
        result["delivery_name"] = delivery.get("name") or None
        result["channel_switch_required"] = bool(delivery.get("channel")) and delivery.get("channel") != channel
    if info:
        result.update(info)
    if runtime == "systemd":
        result["update_method"] = "one_click"
        result["instructions"] = "Click 'Update Now' to install the latest selected channel build automatically."
    elif runtime == "ha_addon":
        result["update_method"] = "ha_store"
        result["instructions"] = _ha_addon_update_instructions(channel, delivery)
    else:
        result["update_method"] = "manual"
        result["command"] = _docker_update_command(channel)
        result["instructions"] = _docker_update_instructions(channel)
    return jsonify(result)


@config_bp.route("/api/update/apply", methods=["POST"])
def api_update_apply():
    """Start upgrade.sh in a transient systemd unit (LXC/systemd only)."""
    runtime = _detect_runtime()
    channel = normalize_update_channel(
        (request.get_json(silent=True) or {}).get("channel") or load_config().get("UPDATE_CHANNEL")
    )
    if runtime != "systemd":
        methods = {
            "ha_addon": _ha_addon_update_instructions(channel, _get_ha_addon_delivery_details()),
            "docker": _docker_update_instructions(channel),
        }
        return jsonify({"success": False, "error": methods.get(runtime, "Unsupported runtime")}), 400

    try:
        payload = request.get_json(silent=True) or {}
        requested_ref = payload.get("tag") or payload.get("version")
        result = _start_upgrade_job(requested_ref)
        if result.get("success"):
            if result.get("already_running"):
                result["message"] = "Upgrade already in progress."
            else:
                result["message"] = "Upgrade started."
            return jsonify(result)
        if result.get("error") == "upgrade.sh not found":
            return jsonify(result), 404
        return jsonify(result), 500
    except Exception:
        logger.exception("Upgrade failed")
        return jsonify({"success": False, "error": "Internal error"}), 500
