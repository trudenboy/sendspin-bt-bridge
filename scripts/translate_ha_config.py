#!/usr/bin/env python3
"""Translate /data/options.json (HA Supervisor) to /data/config.json.

Called by entrypoint.sh when running as a Home Assistant addon.
Produces a config.json that the Python application reads uniformly,
regardless of whether it runs as a Docker container or HA addon.

All field types are validated and coerced here so the rest of the app
can trust the types it receives from config.json.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def _load_config_helpers() -> Callable[[], str]:
    repo_root = Path(__file__).resolve().parents[1]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from config import CONFIG_SCHEMA_VERSION
    from services.ha_addon import get_self_delivery_channel

    globals()["CONFIG_SCHEMA_VERSION"] = CONFIG_SCHEMA_VERSION
    return get_self_delivery_channel


get_self_delivery_channel = _load_config_helpers()
CONFIG_SCHEMA_VERSION = globals()["CONFIG_SCHEMA_VERSION"]

logger = logging.getLogger(__name__)

OPTIONS_FILE = os.getenv("SENDSPIN_HA_OPTIONS_FILE", "/data/options.json")
CONFIG_FILE = os.getenv("SENDSPIN_HA_CONFIG_FILE", "/data/config.json")


def _mac_to_hci(mac: str) -> str:
    """Return hciN interface name for a BT adapter MAC using sysfs, or empty string.

    Thin wrapper over :func:`services.bluetooth.resolve_hci_for_mac` to keep
    a single sysfs-walking implementation.  Imported lazily so the script
    keeps working when invoked outside the bridge runtime.
    """
    try:
        from services.bluetooth import resolve_hci_for_mac
    except Exception as exc:
        logger.debug("sysfs adapter lookup helper import failed: %s", exc)
        return ""
    return resolve_hci_for_mac(mac)


def _detect_adapters() -> list[dict]:
    """Return list of {id, mac, name} dicts for detected BT controllers."""
    detected: list[dict] = []
    try:
        out = subprocess.check_output(["bluetoothctl", "list"], stderr=subprocess.DEVNULL, timeout=5).decode()
        for line in out.strip().splitlines():
            m = re.search(r"Controller\s+([0-9A-Fa-f:]{17})\s+(.*?)(\s+\[default\])?$", line)
            if m:
                mac = m.group(1)
                hci_name = _mac_to_hci(mac) or f"hci{len(detected)}"
                detected.append(
                    {
                        "id": hci_name,
                        "mac": mac,
                        "name": m.group(2).strip() or hci_name,
                    }
                )
    except Exception as exc:
        logger.debug("detect adapters via bluetoothctl failed: %s", exc)
    return detected


def _merge_adapters(detected: list[dict], raw_adapters: list[dict]) -> list[dict]:
    """Merge user-supplied adapter options with detected hardware adapters."""
    existing_macs = {a["mac"]: a for a in detected if a.get("mac")}
    existing_ids = {a["id"]: a for a in detected if a.get("id")}
    for a in raw_adapters:
        opt_name = (a.get("name") or "").strip()
        if a.get("mac") and a["mac"] in existing_macs:
            if opt_name:
                existing_macs[a["mac"]]["name"] = opt_name
        elif a.get("id") and a["id"] in existing_ids:
            if opt_name:
                existing_ids[a["id"]]["name"] = opt_name
        elif a.get("mac") and a["mac"] not in existing_macs:
            detected.append({"id": a.get("id", ""), "mac": a["mac"], "name": opt_name or a.get("id", "")})
        elif a.get("id") and a["id"] not in existing_ids:
            detected.append({"id": a["id"], "mac": a.get("mac", ""), "name": opt_name or a["id"]})
    return detected


def _int_opt(opts: dict, key: str, default: int) -> int:
    """Return opts[key] as int, falling back to *default* only when absent/None."""
    v = opts.get(key)
    return int(v) if v is not None else default


def _optional_int_opt(opts: dict, key: str) -> int | None:
    """Return opts[key] as int, or None when absent/blank."""
    v = opts.get(key)
    if v in (None, ""):
        return None
    return int(v)


def main() -> None:
    if not os.path.exists(OPTIONS_FILE):
        print(f"[translate_ha_config] {OPTIONS_FILE} not found — nothing to do")
        sys.exit(0)

    with open(OPTIONS_FILE) as f:
        opts: dict = json.load(f)

    tz: str = (opts.get("tz") or "").strip() or os.environ.get("TZ", "") or "UTC"

    raw_adapters: list[dict] = opts.get("bluetooth_adapters", []) or []
    detected = _detect_adapters()
    adapters = _merge_adapters(detected, raw_adapters)

    config: dict = {
        "CONFIG_SCHEMA_VERSION": CONFIG_SCHEMA_VERSION,
        "SENDSPIN_SERVER": str(opts.get("sendspin_server") or "auto"),
        "SENDSPIN_PORT": _int_opt(opts, "sendspin_port", 9000),
        "WEB_PORT": None,
        "BASE_LISTEN_PORT": _optional_int_opt(opts, "base_listen_port"),
        "BRIDGE_NAME": str(opts.get("bridge_name") or ""),
        "HA_AREA_NAME_ASSIST_ENABLED": bool(opts.get("ha_area_name_assist_enabled", True)),
        "BLUETOOTH_DEVICES": list(opts.get("bluetooth_devices") or []),
        "BLUETOOTH_ADAPTERS": adapters,
        "TZ": tz,
        "PULSE_LATENCY_MSEC": _int_opt(opts, "pulse_latency_msec", 600),
        "STARTUP_BANNER_GRACE_SECONDS": _int_opt(opts, "startup_banner_grace_seconds", 5),
        "RECOVERY_BANNER_GRACE_SECONDS": _int_opt(opts, "recovery_banner_grace_seconds", 15),
        "PREFER_SBC_CODEC": bool(opts.get("prefer_sbc_codec", False)),
        "DISABLE_PA_RESCUE_STREAMS": bool(opts.get("disable_pa_rescue_streams", False)),
        "BT_CHECK_INTERVAL": _int_opt(opts, "bt_check_interval", 10),
        "BT_MAX_RECONNECT_FAILS": _int_opt(opts, "bt_max_reconnect_fails", 0),
        "LOG_LEVEL": (opts.get("log_level") or "info").upper(),
        "MA_API_URL": opts.get("ma_api_url") or "",
        "MA_API_TOKEN": opts.get("ma_api_token") or "",
        "MA_AUTO_SILENT_AUTH": bool(opts.get("ma_auto_silent_auth", True)),
        "DUPLICATE_DEVICE_CHECK": bool(opts.get("duplicate_device_check", True)),
        "UPDATE_CHANNEL": get_self_delivery_channel(),
    }

    # Normalize: devices without explicit 'enabled' field default to True
    for dev in config["BLUETOOTH_DEVICES"]:
        if isinstance(dev, dict):
            dev.setdefault("enabled", True)
            raw_delay = dev.get("static_delay_ms")
            if raw_delay is not None:
                try:
                    normalized_delay = int(float(raw_delay))
                except (TypeError, ValueError):
                    normalized_delay = 0
                dev["static_delay_ms"] = max(0, min(5000, normalized_delay))

    # Preserve runtime state from previous config
    try:
        with open(CONFIG_FILE) as f:
            existing: dict = json.load(f)
        if "LAST_VOLUMES" in existing:
            config["LAST_VOLUMES"] = existing["LAST_VOLUMES"]
        if "LAST_SINKS" in existing:
            config["LAST_SINKS"] = existing["LAST_SINKS"]
        if "HA_AREA_NAME_ASSIST_ENABLED" in existing and "ha_area_name_assist_enabled" not in opts:
            config["HA_AREA_NAME_ASSIST_ENABLED"] = bool(existing["HA_AREA_NAME_ASSIST_ENABLED"])
        if "HA_ADAPTER_AREA_MAP" in existing:
            config["HA_ADAPTER_AREA_MAP"] = existing["HA_ADAPTER_AREA_MAP"]
        for key in ("AUTH_PASSWORD_HASH", "SECRET_KEY"):
            if key in existing:
                config[key] = existing[key]
        for key in ("MA_ACCESS_TOKEN", "MA_REFRESH_TOKEN"):
            if existing.get(key):
                config[key] = existing[key]
        # Preserve MA API credentials if not overridden via HA addon options
        for key in ("MA_API_URL", "MA_API_TOKEN"):
            if not config.get(key) and existing.get(key):
                config[key] = existing[key]
        preserved_optional_int_fields = {
            "BASE_LISTEN_PORT": "base_listen_port",
            "STARTUP_BANNER_GRACE_SECONDS": "startup_banner_grace_seconds",
            "RECOVERY_BANNER_GRACE_SECONDS": "recovery_banner_grace_seconds",
        }
        for key, option_name in preserved_optional_int_fields.items():
            if opts.get(option_name) in (None, "") and existing.get(key) not in (None, ""):
                config[key] = int(existing[key])
        # Preserve per-device web UI settings (e.g. keepalive) not present in options.json
        existing_devs = {
            d["mac"]: d for d in existing.get("BLUETOOTH_DEVICES", []) if isinstance(d, dict) and d.get("mac")
        }
        for dev in config["BLUETOOTH_DEVICES"]:
            mac = dev.get("mac") if isinstance(dev, dict) else None
            if mac and mac in existing_devs:
                for field in (
                    "keepalive_silence",
                    "keepalive_interval",
                    "room_id",
                    "room_name",
                    "idle_disconnect_minutes",
                    "idle_mode",
                    "power_save_delay_minutes",
                ):
                    if field not in dev and field in existing_devs[mac]:
                        dev[field] = existing_devs[mac][field]
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.debug("preserve existing device settings failed: %s", exc)

    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)

    print(
        f"[translate_ha_config] Generated {CONFIG_FILE} with "
        f"{len(config['BLUETOOTH_DEVICES'])} device(s), "
        f"TZ={config['TZ']}, "
        f"{len(config['BLUETOOTH_ADAPTERS'])} adapter(s)"
    )


if __name__ == "__main__":
    main()
