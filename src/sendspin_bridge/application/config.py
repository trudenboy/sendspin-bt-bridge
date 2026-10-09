"""Configuration, service logs, version and software updates."""

from __future__ import annotations

import functools
import json
import logging
import os
import subprocess
from datetime import datetime, timezone

from sendspin_bridge.application.diagnostics import invalidate_preflight_probe
from sendspin_bridge.application.errors import UseCaseError, config_write_error
from sendspin_bridge.application.jobs import Job, JobContext, jobs
from sendspin_bridge.config import (
    BUILD_DATE,
    CONFIG_ALLOWED_KEYS,
    CONFIG_FILE,
    CONFIG_SCHEMA_VERSION,
    DEFAULT_UPDATE_CHANNEL,
    RUNTIME_STATE_CONFIG_KEYS,
    SENSITIVE_CONFIG_KEYS,
    _player_id_from_mac,
    config_lock,
    detect_ha_addon_channel,
    get_runtime_version,
    load_config,
    normalize_update_channel,
    resolve_base_listen_port,
    resolve_web_port,
    write_config_file,
)
from sendspin_bridge.services import (
    bt_remove_device as _bt_remove_device,
)
from sendspin_bridge.services.bluetooth import _MAC_RE
from sendspin_bridge.services.bluetooth.adapter_names import refresh_adapter_name_cache
from sendspin_bridge.services.bluetooth.device_registry import get_device_registry_snapshot
from sendspin_bridge.services.diagnostics.compatibility_capabilities import get_compatibility_capabilities
from sendspin_bridge.services.diagnostics.log_analysis import summarize_issue_logs
from sendspin_bridge.services.diagnostics.sendspin_compat import get_runtime_dependency_versions
from sendspin_bridge.services.diagnostics.update_checker import (
    _is_newer_version,
    _start_upgrade_job,
    channel_image_tag,
    check_latest_version,
)
from sendspin_bridge.services.ha.ha_addon import (
    detect_delivery_channel_from_slug,
    get_self_addon_info,
    get_self_delivery_channel,
)
from sendspin_bridge.services.ha.ha_core_api import HaCoreApiError, fetch_ha_area_catalog
from sendspin_bridge.services.infrastructure.config_diff import diff_configs
from sendspin_bridge.services.infrastructure.config_validation import validate_uploaded_config
from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION
from sendspin_bridge.services.lifecycle.async_job_state import (
    get_update_available,
    set_update_available,
)
from sendspin_bridge.services.lifecycle.bridge_runtime_state import get_activation_context, get_main_loop
from sendspin_bridge.services.lifecycle.reconfig_orchestrator import ReconfigOrchestrator
from sendspin_bridge.services.lifecycle.status_snapshot import build_device_snapshot
from sendspin_bridge.services.music_assistant.ma_client import fetch_all_players_snapshot

logger = logging.getLogger(__name__)


# Keys accepted via POST /api/config.  Sensitive keys (passwords, tokens,
# secrets) and internal runtime-state keys are intentionally excluded so
# they cannot be overwritten by the web form.
_ALLOWED_POST_CONFIG_KEYS = (
    CONFIG_ALLOWED_KEYS - SENSITIVE_CONFIG_KEYS - RUNTIME_STATE_CONFIG_KEYS - {"MA_AUTH_PROVIDER"}
) | {
    "LAST_VOLUMES",
    "MA_API_TOKEN",
    # ``HA_INTEGRATION`` round-trips through the Settings → Home Assistant
    # tab; it's in SENSITIVE_CONFIG_KEYS only because it nests an MQTT
    # password.  POSTing the block back is fine because the password
    # is redacted at download time (see ``_sanitize_download_config``).
    "HA_INTEGRATION",
    "_new_device_default_volume",
}


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
    # HA_INTEGRATION nests an MQTT password — redact only that leaf so the
    # rest of the block survives a config-share/diagnostic dump.
    ha_integration = sanitized.get("HA_INTEGRATION")
    if isinstance(ha_integration, dict):
        cloned = {k: (dict(v) if isinstance(v, dict) else v) for k, v in ha_integration.items()}
        mqtt = cloned.get("mqtt")
        if isinstance(mqtt, dict) and mqtt.get("password"):
            mqtt["password"] = "***REDACTED***"
        sanitized["HA_INTEGRATION"] = cloned
    return sanitized


def _error(message: str, status: int = 400, code: str = "invalid_config") -> UseCaseError:
    return UseCaseError(status, code, message)


def _validation_error(errors: list[dict[str, str]], warnings: list[dict[str, str]] | None = None) -> UseCaseError:
    """400 with every field error, and warnings alongside."""
    return UseCaseError(
        400,
        "invalid_config",
        errors[0]["message"] if errors else "Validation failed",
        errors=errors,
        extra={"warnings": warnings} if warnings else None,
    )


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


def get_config() -> dict:
    """The config for the settings screen: secrets removed, effective ports and live device facts added."""
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
    # HA_INTEGRATION carries an MQTT password — surface its presence
    # without leaking the value.  Mirrors the MA_API_TOKEN pattern.
    ha_integration = config.get("HA_INTEGRATION")
    if isinstance(ha_integration, dict):
        cloned = {k: (dict(v) if isinstance(v, dict) else v) for k, v in ha_integration.items()}
        mqtt = cloned.get("mqtt")
        if isinstance(mqtt, dict):
            mqtt["password"] = "***REDACTED***" if mqtt.get("password") else ""
        config["HA_INTEGRATION"] = cloned
    # AUTH_TOKENS is sensitive — never include in GET response (the
    # /api/auth/tokens endpoint returns the public form separately).
    config.pop("AUTH_TOKENS", None)
    config["_password_set"] = has_password
    if runtime == "ha_addon":
        config["WEB_PORT"] = None
        config["_effective_web_port"] = resolve_web_port()
        config["_delivery_channel"] = detect_ha_addon_channel()
    config["_effective_base_listen_port"] = resolve_base_listen_port()
    config["_compatibility_capabilities"] = get_compatibility_capabilities()

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

    return config


def _normalize_device_mac(raw_mac) -> str:
    """Return a canonical MAC string for config payload validation."""
    return str(raw_mac or "").strip().upper()


def _normalize_device_adapter(raw_adapter) -> str:
    """Return a canonical adapter identity for config comparisons.

    Empty, missing, or explicit ``default`` adapter values are equivalent.
    """
    adapter = str(raw_adapter or "").strip()
    if not adapter or adapter.lower() == "default":
        return ""
    return adapter.upper() if ":" in adapter else adapter.lower()


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
    return "docker compose pull && docker compose up -d"


def _docker_update_instructions(channel: str) -> tuple[str, str]:
    image_tag = channel_image_tag(channel)
    image = f"ghcr.io/trudenboy/sendspin-bt-bridge:{image_tag}"
    return (
        "Make sure your docker-compose.yml uses this image, then run the command below in the same directory.",
        image,
    )


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
            for latency_key in ("required_lead_time_ms", "min_buffer_ms"):
                if d.get(latency_key) is not None:
                    entry[latency_key] = int(d[latency_key])
            for metadata_key in ("static_delay_source", "static_delay_calibrated_at", "static_delay_codec"):
                if d.get(metadata_key):
                    entry[metadata_key] = d[metadata_key]
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
            "sendspin_port": int(config.get("SENDSPIN_PORT") or 8927),
            "bridge_name": config.get("BRIDGE_NAME", ""),
            "ha_area_name_assist_enabled": bool(config.get("HA_AREA_NAME_ASSIST_ENABLED", True)),
            "tz": config.get("TZ", ""),
            "pulse_latency_msec": int(config.get("PULSE_LATENCY_MSEC") or 600),
            "startup_banner_grace_seconds": int(config.get("STARTUP_BANNER_GRACE_SECONDS", 5)),
            "recovery_banner_grace_seconds": int(config.get("RECOVERY_BANNER_GRACE_SECONDS", 15)),
            "prefer_sbc_codec": bool(config.get("PREFER_SBC_CODEC", False)),
            "disable_pa_rescue_streams": bool(config.get("DISABLE_PA_RESCUE_STREAMS", False)),
            "bt_check_interval": int(config.get("BT_CHECK_INTERVAL") or 10),
            "bt_max_reconnect_fails": int(config.get("BT_MAX_RECONNECT_FAILS") or 0),
            "auth_enabled": bool(config.get("AUTH_ENABLED", False)),
            "ma_auto_silent_auth": bool(config.get("MA_AUTO_SILENT_AUTH", True)),
            "bluetooth_devices": sup_devices,
            "bluetooth_adapters": sup_adapters,
        }
        if config.get("BASE_LISTEN_PORT") is not None:
            options["base_listen_port"] = int(config["BASE_LISTEN_PORT"])

        # Mirror the HA_INTEGRATION block back to Supervisor in the flat
        # shape the addon options schema expects (translate_ha_config does
        # the inverse on addon startup).  Without this the HAOS
        # Configuration tab keeps showing stale values after operators
        # change them in the bridge web UI, and the next addon restart
        # writes those stale values back over the live config.
        ha_integration = config.get("HA_INTEGRATION") or {}
        if isinstance(ha_integration, dict):
            raw_mqtt = ha_integration.get("mqtt")
            mqtt_block: dict = raw_mqtt if isinstance(raw_mqtt, dict) else {}
            raw_rest = ha_integration.get("rest")
            rest_block: dict = raw_rest if isinstance(raw_rest, dict) else {}
            options["ha_integration"] = {
                "enabled": bool(ha_integration.get("enabled", False)),
                "mode": str(ha_integration.get("mode") or "off"),
                "mqtt_broker": str(mqtt_block.get("broker") or "auto"),
                "mqtt_port": int(mqtt_block.get("port") or 1883),
                "mqtt_username": str(mqtt_block.get("username") or ""),
                "mqtt_password": str(mqtt_block.get("password") or ""),
                "mqtt_discovery_prefix": str(mqtt_block.get("discovery_prefix") or "homeassistant"),
                "mqtt_tls": bool(mqtt_block.get("tls", False)),
                "advertise_mdns": bool(rest_block.get("advertise_mdns", True)),
                "supervisor_pair": bool(rest_block.get("supervisor_pair", True)),
            }

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


def export_config() -> tuple[str, str]:
    """A share-safe export (tokens and secrets removed) and its file name."""
    if not CONFIG_FILE.exists():
        raise _error("No config file found", 404, "not_found")
    try:
        with config_lock, open(CONFIG_FILE) as f:
            config = json.load(f)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.exception("Could not read config for download")
        raise _error("Could not read config file", 500, "config_unreadable") from exc
    raw = json.dumps(_sanitize_download_config(config), indent=2)
    bridge_name = (config.get("BRIDGE_NAME", "").strip() or "Bridge").replace(" ", "_")
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S")
    return raw, f"{bridge_name}_SBB_Config_{ts}.json"


_PRESERVED_KEYS = tuple(sorted(SENSITIVE_CONFIG_KEYS | {"MA_TOKEN_INSTANCE_HOSTNAME", "MA_TOKEN_LABEL"}))


MAX_CONFIG_UPLOAD_BYTES = 1_000_000


def _require_password_for_auth(config: dict) -> None:
    """Turning authentication on without a password would lock everyone out
    (in the HA add-on Home Assistant signs people in instead)."""
    if not config.get("AUTH_ENABLED") or os.environ.get("SUPERVISOR_TOKEN"):
        return
    try:
        stored = json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    except (json.JSONDecodeError, OSError):
        stored = {}
    if not (isinstance(stored, dict) and stored.get("AUTH_PASSWORD_HASH")):
        raise _error("Set a password before enabling authentication")


def import_config(raw: bytes) -> dict:
    """Replace the configuration with an uploaded file; secrets stay as stored.

    Returns validation warnings, if any.
    """
    if len(raw) > MAX_CONFIG_UPLOAD_BYTES:
        raise _error("Config file too large (max 1 MB)", 413, "payload_too_large")
    try:
        uploaded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise _error(f"Invalid JSON: {exc}") from exc
    if not isinstance(uploaded, dict):
        raise _error("Config must be a JSON object")
    validation = validate_uploaded_config(uploaded, default_base_listen_port=resolve_base_listen_port())
    warnings = [{"field": issue.field, "message": issue.message} for issue in validation.warnings]
    if not validation.is_valid:
        errors = [{"field": issue.field, "message": issue.message} for issue in validation.errors]
        raise _validation_error(errors, warnings)
    uploaded = validation.normalized_config
    warnings = _append_ma_duplicate_device_warnings(uploaded, warnings)
    _require_password_for_auth(uploaded)

    # The sensitive keys belong to the stored file, not the upload. The store
    # refuses rather than writes when the existing file cannot be read.
    from sendspin_bridge.config.store import ConfigStore

    try:
        ConfigStore(CONFIG_FILE).replace(uploaded, preserve=(), owned=_PRESERVED_KEYS)
    except ValueError as exc:
        logger.error("Refusing to save uploaded config: %s is unreadable (%s)", CONFIG_FILE, exc)
        raise _error(
            "The existing configuration is corrupted; refusing to overwrite it", 500, "config_corrupted"
        ) from exc
    except OSError as exc:
        raise config_write_error(exc, context="Cannot save uploaded config") from exc
    return {"warnings": warnings}


def sendspin_test(server: str | None = None, port: int | str | None = None) -> dict:
    """Probe the Sendspin endpoint (given values, or the saved ones) before saving them (#291)."""
    from sendspin_bridge.services.diagnostics.operator_check_runner import run_safe_check

    if server is None and port is None:
        cfg = load_config()
        probe_cfg = {"SENDSPIN_SERVER": cfg.get("SENDSPIN_SERVER"), "SENDSPIN_PORT": cfg.get("SENDSPIN_PORT")}
    else:
        probe_cfg = {"SENDSPIN_SERVER": server, "SENDSPIN_PORT": port}
    result = run_safe_check("sendspin_connection", config=probe_cfg)
    if result.get("reason_code") == "config_invalid":
        raise UseCaseError(
            400, "config_invalid", result.get("summary") or "Invalid Sendspin server settings", extra={"check": result}
        )
    return result


def validate_config(config: dict) -> dict:
    """Validate without saving: errors, warnings and the normalized config."""
    validation = validate_uploaded_config(config, default_base_listen_port=resolve_base_listen_port())
    errors = [{"field": issue.field, "message": issue.message} for issue in validation.errors]
    warnings = [{"field": issue.field, "message": issue.message} for issue in validation.warnings]
    if validation.is_valid:
        warnings = _append_ma_duplicate_device_warnings(validation.normalized_config, warnings)
    return {
        "valid": validation.is_valid,
        "errors": errors,
        "warnings": warnings,
        "normalized_config": validation.normalized_config,
    }


def save_config(config: dict) -> dict:
    """Validate, persist and apply a config — hot where possible, restarts listed where not.

    Returns ``{"warnings": [...], "reconfig": {...}}``.
    """
    if not isinstance(config, dict):
        raise _error("Invalid JSON body")

    # Preserve the caller's delay intent across schema normalization.  The
    # migration layer labels an explicit source-less static_delay_ms as
    # ``legacy``; for a newly registered device we need to distinguish that
    # explicit value from a device which omitted delay altogether.
    incoming_delay_intent = {
        _normalize_device_mac(device.get("mac")): {
            "has_delay": "static_delay_ms" in device,
            "has_source": bool(device.get("static_delay_source")),
        }
        for device in config.get("BLUETOOTH_DEVICES", [])
        if isinstance(device, dict) and _normalize_device_mac(device.get("mac"))
    }

    # The settings form submits a partial config that never includes
    # CONFIG_SCHEMA_VERSION.  Carry the on-disk schema version into the payload
    # so the one-shot schema migrations (e.g. the BT_MAX_RECONNECT_FAILS 0 → 5
    # default flip) don't re-fire on every save and silently overwrite the
    # operator's choice (#332).
    if "CONFIG_SCHEMA_VERSION" not in config and CONFIG_FILE.exists():
        try:
            with config_lock, open(CONFIG_FILE) as _schema_fh:
                existing_schema = json.load(_schema_fh).get("CONFIG_SCHEMA_VERSION")
            if existing_schema is not None:
                config["CONFIG_SCHEMA_VERSION"] = existing_schema
        except (json.JSONDecodeError, OSError):
            pass

    validation = validate_uploaded_config(config, default_base_listen_port=resolve_base_listen_port())
    warnings = [{"field": issue.field, "message": issue.message} for issue in validation.warnings]
    if not validation.is_valid:
        errors = [{"field": issue.field, "message": issue.message} for issue in validation.errors]
        raise _validation_error(errors, warnings)
    config = validation.normalized_config
    config = {k: v for k, v in config.items() if k in _ALLOWED_POST_CONFIG_KEYS}
    warnings = _append_ma_duplicate_device_warnings(config, warnings)

    # Validate top-level string fields
    for str_key in ("SENDSPIN_SERVER", "BRIDGE_NAME", "TZ", "LOG_LEVEL", "UPDATE_CHANNEL"):
        val = config.get(str_key)
        if val is not None and not isinstance(val, str):
            raise _error(f"{str_key} must be a string")
    if _detect_runtime() == "ha_addon":
        config["WEB_PORT"] = None
        config["UPDATE_CHANNEL"] = get_self_delivery_channel()
    else:
        config["UPDATE_CHANNEL"] = normalize_update_channel(config.get("UPDATE_CHANNEL", DEFAULT_UPDATE_CHANNEL))

    for bool_key in (
        "PREFER_SBC_CODEC",
        "DISABLE_PA_RESCUE_STREAMS",
        "AUTH_ENABLED",
        "BRUTE_FORCE_PROTECTION",
        "MA_AUTO_SILENT_AUTH",
        "MA_WEBSOCKET_MONITOR",
        "HA_AREA_NAME_ASSIST_ENABLED",
        "SMOOTH_RESTART",
        "AUTO_UPDATE",
        "CHECK_UPDATES",
    ):
        val = config.get(bool_key)
        if val is not None and not isinstance(val, bool):
            raise _error(f"{bool_key} must be true or false")

    # Validate BLUETOOTH_DEVICES entries
    bt_devices = config.get("BLUETOOTH_DEVICES", [])
    if not isinstance(bt_devices, list):
        raise _error("BLUETOOTH_DEVICES must be an array")
    for dev in bt_devices:
        if not isinstance(dev, dict):
            raise _error("Each device must be an object")
        mac = _normalize_device_mac(dev.get("mac"))
        if mac:
            dev["mac"] = mac
        if mac and not _MAC_RE.match(mac):
            raise _error(f"Invalid MAC address: {mac}")

    # Validate BLUETOOTH_ADAPTERS entries
    bt_adapters = config.get("BLUETOOTH_ADAPTERS", [])
    if not isinstance(bt_adapters, list):
        raise _error("BLUETOOTH_ADAPTERS must be an array")
    for adp in bt_adapters:
        if not isinstance(adp, dict):
            raise _error("Each adapter must be an object")
        amac = str(adp.get("mac", ""))
        if amac and not _MAC_RE.match(amac):
            raise _error(f"Invalid adapter MAC address: {amac}")

    try:
        config["HA_ADAPTER_AREA_MAP"] = _normalize_ha_adapter_area_map(config.get("HA_ADAPTER_AREA_MAP", {}))
    except ValueError as exc:
        raise _error(str(exc))

    # HA_INTEGRATION broker URL normalisation runs pre-lock because it has no
    # dependency on the existing config — operators paste broker URLs like
    # ``mqtt://host:1883`` / ``mqtts://broker.example.com`` and we strip the
    # scheme + promote port/TLS hints to dedicated fields.  Password merge
    # (which DOES depend on the existing on-disk value) is deferred until
    # after we read ``existing`` under ``config_lock`` so a parallel save
    # can't slip a different password in between our pre-lock read and write.
    incoming_ha = config.get("HA_INTEGRATION")
    if isinstance(incoming_ha, dict):
        incoming_mqtt = incoming_ha.get("mqtt")
        if isinstance(incoming_mqtt, dict):
            from sendspin_bridge.services.ha.ha_addon import normalise_broker_host

            broker_raw = incoming_mqtt.get("broker")
            if isinstance(broker_raw, str) and broker_raw.strip():
                normalised = normalise_broker_host(broker_raw)
                if normalised["host"]:
                    incoming_mqtt["broker"] = normalised["host"]
                # URL-derived signals always win — operators who paste a
                # full URI (``mqtts://broker:8883``) are giving the
                # strongest possible intent.  Frontend's blur handler
                # syncs the port / TLS form fields so the UI stays in
                # sync; backend defense-in-depth applies the same rule
                # for direct ``POST /api/config`` callers.  An explicit
                # operator-typed port in a non-URL broker string is
                # preserved (``normalised["port"]`` is None then).
                if normalised["port"] is not None:
                    incoming_mqtt["port"] = normalised["port"]
                if normalised["tls"] is not None:
                    incoming_mqtt["tls"] = normalised["tls"]

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
            ("RECOVERY_BANNER_GRACE_SECONDS", 0, 300),
        ):
            value = _parse_optional_int(config.get(int_key), int_key, min_value=min_val, max_value=max_val)
            if value is not None:
                config[int_key] = value
    except ValueError as exc:
        raise _error(str(exc))

    # Require password when enabling auth (except HA addon — uses HA login)
    _require_password_for_auth(config)

    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with config_lock:
        existing: dict = {}
        if CONFIG_FILE.exists():
            # The preserve loop below is the only thing carrying the password
            # hash, the session secret and the MA tokens across a settings
            # save, and ``write_config_file`` replaces the file wholesale.  A
            # swallowed read error therefore used to mean "silently erase the
            # operator's credentials and answer 200" — fail the request
            # instead and leave the file untouched.
            from sendspin_bridge.config.store import ConfigStore

            try:
                # Backed up before we refuse: the operator is left with a copy
                # of whatever their settings had become, rather than a file
                # they cannot save over and cannot read either.
                loaded = ConfigStore(CONFIG_FILE).read_stored(backup_corrupt=True)
            except OSError as exc:
                logger.error("Refusing to save config: %s is unreadable (%s)", CONFIG_FILE, exc)
                raise _error(
                    "Cannot read the existing configuration; refusing to overwrite it", 500, "config_unreadable"
                ) from exc
            except ValueError as exc:
                logger.error("Refusing to save config: %s is corrupted (%s)", CONFIG_FILE, exc)
                raise _error(
                    "The existing configuration is corrupted; refusing to overwrite it", 500, "config_corrupted"
                ) from exc
            existing = loaded or {}

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

        # HA_INTEGRATION password round-trip semantics — under the lock so a
        # parallel ``POST /api/config`` from another Waitress thread can't
        # change the on-disk password between our existing-read and write.
        # The GET response surfaces an existing password as ``***REDACTED***``
        # and the UI puts that marker into the input field on load.  Three cases:
        #   * ``***REDACTED***`` (operator didn't touch the field) — keep existing
        #   * ``""`` (operator cleared the field) — explicit clear, broker no-auth
        #   * anything else — overwrite with the typed plaintext
        # ``None``/whitespace inherit "keep existing" so a clumsy client
        # submitting an unset field doesn't accidentally clear the password,
        # but explicit ``""`` is honoured.
        incoming_ha_locked = config.get("HA_INTEGRATION")
        if isinstance(incoming_ha_locked, dict):
            incoming_mqtt_locked = incoming_ha_locked.get("mqtt")
            if isinstance(incoming_mqtt_locked, dict):
                existing_ha = existing.get("HA_INTEGRATION") or {}
                stored_mqtt = existing_ha.get("mqtt")
                existing_mqtt: dict = stored_mqtt if isinstance(stored_mqtt, dict) else {}
                pw = incoming_mqtt_locked.get("password")
                if pw == "***REDACTED***" or pw is None:
                    incoming_mqtt_locked["password"] = existing_mqtt.get("password", "")
                elif isinstance(pw, str) and pw.strip() == "" and pw != "":
                    # Whitespace-only — treat as untouched / unset, not clear.
                    incoming_mqtt_locked["password"] = existing_mqtt.get("password", "")
                # else: explicit "" → preserved as "" (clear), or non-empty → overwrite

        # Normalize MA_API_URL: add http:// scheme if missing
        ma_url = config.get("MA_API_URL", "").strip()
        if ma_url and "://" not in ma_url:
            config["MA_API_URL"] = f"http://{ma_url}"

        old_devices = {d["mac"]: d for d in existing.get("BLUETOOTH_DEVICES", []) if d.get("mac")}
        new_devices = {d["mac"]: d for d in config.get("BLUETOOTH_DEVICES", []) if d.get("mac")}

        # A delay recommendation is a one-shot starting value, never an
        # ongoing policy.  Only brand-new devices without an explicit delay
        # enter auto_pending.  The telemetry loop consumes that marker after
        # its first usable BlueZ report or codec fallback.  An explicit value
        # supplied through the API is user intent and therefore manual.
        for mac, new_device in new_devices.items():
            if mac in old_devices:
                continue
            intent = incoming_delay_intent.get(mac, {})
            if intent.get("has_source"):
                continue
            if intent.get("has_delay"):
                new_device["static_delay_source"] = "manual"
            else:
                new_device["static_delay_ms"] = 0
                new_device["static_delay_source"] = "auto_pending"

        client_adapter = {
            mac: getattr(getattr(client, "bt_manager", None), "_adapter_select", "")
            for mac, client in get_device_registry_snapshot().client_map_by_mac().items()
        }

        # Which speakers this save orphans.  The unpairing itself waits until
        # the new config is on disk: it used to run first, so a failed write
        # answered 500 while the speaker had already been unpaired from its
        # controller — a change reported as failed that the operator then had
        # to undo by hand.
        macs_to_unpair = []
        for mac, old_dev in old_devices.items():
            new_dev = new_devices.get(mac)
            adapter_changed = (
                bool(new_dev)
                and new_dev is not None
                and _normalize_device_adapter(new_dev.get("adapter"))
                != _normalize_device_adapter(old_dev.get("adapter"))
            )
            deleted = new_dev is None
            if deleted or adapter_changed:
                macs_to_unpair.append((mac, client_adapter.get(mac) or ""))

        # What the bridge runs on now: the file plus the defaults it does not
        # spell out. Diffing against the raw file read every default as a
        # change and restarted every speaker on an untouched save.
        from sendspin_bridge.config.store import ConfigStore

        effective_before = ConfigStore(CONFIG_FILE).load() if existing else {}

        default_vol = config.pop("_new_device_default_volume", None)
        last_volumes = config.setdefault("LAST_VOLUMES", existing.get("LAST_VOLUMES", {}))
        if not isinstance(last_volumes, dict):
            last_volumes = {}
        if default_vol is not None:
            for mac in new_devices:
                if mac and mac not in last_volumes:
                    last_volumes[mac] = default_vol
        config["LAST_VOLUMES"] = _sanitize_last_volumes(last_volumes, set(new_devices))

        try:
            write_config_file(config, config_file=CONFIG_FILE, config_dir=CONFIG_FILE.parent)
        except OSError as exc:
            raise config_write_error(exc, context="Cannot save bridge config") from exc

        # Compute reconfig actions from the on-disk "before" snapshot to the
        # just-persisted "after" snapshot while the lock is still held.
        reconfig_actions = diff_configs(effective_before or existing, ConfigStore(CONFIG_FILE).load())

        # The save stands; now let the orphaned speakers go.
        for mac, adapter_mac in macs_to_unpair:
            _bt_remove_device(mac, adapter_mac)

    # Invalidate adapter name cache so next status poll picks up changes
    refresh_adapter_name_cache()
    # A saved setting can change what the host looks like to the bridge — a
    # different adapter, a different audio target.  The operator is watching
    # the screen they just saved from, so the next build measures rather than
    # reporting the sample taken before the save.
    invalidate_preflight_probe()

    _sync_ha_options(config)

    # Apply on-line reconfiguration: hot-update fields via IPC, warm-restart
    # subprocesses where needed, record fields that still require a full
    # bridge restart.
    reconfig_summary_dict: dict[str, object] = {}
    if reconfig_actions:
        orchestrator = ReconfigOrchestrator(
            get_main_loop(),
            get_device_registry_snapshot(),
            activation_context=get_activation_context(),
        )
        reconfig_summary_dict = orchestrator.apply(reconfig_actions).to_dict()

    return {"warnings": warnings, "reconfig": reconfig_summary_dict}


def ha_areas(ha_token: str, adapters: list | None = None, *, include_devices: bool = False) -> dict:
    """Home Assistant areas (and suggestions per adapter), read with a transient HA token."""
    ha_token = (ha_token or "").strip()
    if not ha_token:
        raise _error("ha_token is required", code="ha_token_required")
    try:
        return fetch_ha_area_catalog(ha_token, include_devices=include_devices, adapters=list(adapters or []))
    except HaCoreApiError as exc:
        raise UseCaseError(502, "ha_unavailable", str(exc)) from exc


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
        # Inside a Docker container the docker CLI is typically not
        # available. Read straight from the in-memory ring buffer the
        # bridge attaches to the root logger at startup — same source
        # the SSE log stream uses, so `/api/logs` and the live stream
        # show the same lines. The docker CLI is only attempted as a
        # last resort for hosts that bind-mount /var/run/docker.sock.
        log_lines = []
        try:
            from sendspin_bridge.bridge.client import _ring_log_handler

            log_lines = list(_ring_log_handler.records)[-lines:]
        except Exception:
            log_lines = []
        if not log_lines:
            try:
                result = subprocess.run(
                    ["docker", "logs", "--tail", str(lines), "sendspin-client"],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                log_lines = (result.stdout + result.stderr).splitlines()
            except FileNotFoundError:
                pass
        if not log_lines:
            log_lines = ["(no log lines yet — bridge just started)"]

    return log_lines or ["(No logs available)"]


def service_logs(lines: int = 150) -> dict:
    """The service's own log (journalctl, Supervisor, or the in-memory ring)."""
    lines = max(1, min(int(lines), 500))
    runtime = _detect_runtime()
    log_lines = _read_log_lines(runtime, lines)
    issue_summary = summarize_issue_logs(log_lines, tail_lines=20)
    return {
        "logs": log_lines,
        "runtime": runtime,
        "has_recent_issues": issue_summary["has_issues"],
        "recent_issue_count": issue_summary["issue_count"],
        "recent_issue_level": issue_summary["highest_level"],
    }


def service_logs_text() -> tuple[str, str]:
    """The last 500 log lines as a text file, and its name."""
    log_lines = _read_log_lines(_detect_runtime(), 500)
    ts = datetime.now(tz=timezone.utc).strftime("%Y%m%d-%H%M%S")
    return "\n".join(log_lines), f"sendspin-logs-{ts}.txt"


def version_info() -> dict:
    """Version, git revision when running from a checkout, contract versions, dependencies."""
    cwd = os.path.dirname(os.path.abspath(__file__))
    info = {
        "version": get_runtime_version(),
        "git_sha": "unknown",
        "built_at": BUILD_DATE,
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "ipc_protocol_version": IPC_PROTOCOL_VERSION,
        "dependencies": get_runtime_dependency_versions(),
    }

    def _git(*args: str) -> str:
        return subprocess.run(["git", *args], capture_output=True, text=True, timeout=3, cwd=cwd).stdout.strip()

    try:
        sha, desc, date = (
            _git("rev-parse", "--short", "HEAD"),
            _git("describe", "--tags", "--always"),
            _git("log", "-1", "--format=%ci"),
        )
    except Exception:
        return info
    info["version"] = desc or info["version"]
    info["git_sha"] = sha or "unknown"
    info["built_at"] = date.split(" ")[0] if date else BUILD_DATE
    return info


# ---------------------------------------------------------------------------
# Update check & apply
# ---------------------------------------------------------------------------


def start_update_check(channel: str | None = None) -> Job:
    """Ask GitHub for the newest release on *channel* (default: the configured one)."""
    from sendspin_bridge.application.runtime import run_on_loop

    resolved = normalize_update_channel(channel or load_config().get("UPDATE_CHANNEL"))

    def _work(_ctx: JobContext):
        latest = run_on_loop(check_latest_version(resolved), timeout=20, description="update check")
        if not latest:
            raise UseCaseError(502, "github_unreachable", "Could not reach GitHub API")
        runtime_version = get_runtime_version()
        if _is_newer_version(latest["tag"], runtime_version):
            latest["current_version"] = runtime_version
            set_update_available(latest)
            return {"update_available": True, **latest}
        set_update_available(None)
        return {"update_available": False, "latest": latest["version"], "channel": resolved}

    return jobs.start("updates.check", _work, subject=resolved, progress={"channel": resolved})


def update_info() -> dict:
    """The cached update availability and how to update on this runtime."""
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
        instructions_text, docker_image = _docker_update_instructions(channel)
        result["instructions"] = instructions_text
        result["docker_image"] = docker_image
    return result


def apply_update(*, channel: str | None = None, ref: str | None = None) -> dict:
    """Start ``upgrade.sh`` in a transient systemd unit (LXC / bare metal only)."""
    runtime = _detect_runtime()
    resolved = normalize_update_channel(channel or load_config().get("UPDATE_CHANNEL"))
    if runtime != "systemd":
        if runtime == "ha_addon":
            message = _ha_addon_update_instructions(resolved, _get_ha_addon_delivery_details())
        else:
            message = " ".join(_docker_update_instructions(resolved))
        raise UseCaseError(409, "manual_update", message)
    result = _start_upgrade_job(ref)
    if result.get("success"):
        result["message"] = "Upgrade already in progress." if result.get("already_running") else "Upgrade started."
        return result
    if result.get("error") == "upgrade.sh not found":
        raise UseCaseError(404, "upgrade_script_missing", "upgrade.sh not found")
    raise UseCaseError(500, "upgrade_failed", str(result.get("error") or "Upgrade failed"))


#: Public name for the Supervisor options mirror.
sync_ha_options = _sync_ha_options
