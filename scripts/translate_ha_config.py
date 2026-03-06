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

logger = logging.getLogger(__name__)

OPTIONS_FILE = "/data/options.json"
CONFIG_FILE = "/data/config.json"


def _mac_to_hci(mac: str) -> str:
    """Return hciN interface name for a BT adapter MAC using sysfs, or empty string."""
    mac_norm = mac.upper().replace(":", "").lower()  # e.g. "aabbccddeeff"
    import pathlib

    bt_sysfs = pathlib.Path("/sys/class/bluetooth")
    try:
        for hci in sorted(bt_sysfs.iterdir()):
            addr_file = hci / "address"
            if addr_file.exists():
                addr = addr_file.read_text().strip().replace(":", "").lower()
                if addr == mac_norm:
                    return hci.name  # e.g. "hci0"
    except Exception as exc:
        logger.debug("sysfs adapter lookup failed: %s", exc)
    return ""


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
        "SENDSPIN_SERVER": str(opts.get("sendspin_server") or "auto"),
        "SENDSPIN_PORT": int(opts.get("sendspin_port") or 9000),
        "BRIDGE_NAME": str(opts.get("bridge_name") or ""),
        "BRIDGE_NAME_SUFFIX": bool(opts.get("bridge_name_suffix", False)),
        "BLUETOOTH_DEVICES": list(opts.get("bluetooth_devices") or []),
        "BLUETOOTH_ADAPTERS": adapters,
        "TZ": tz,
        "PULSE_LATENCY_MSEC": int(opts.get("pulse_latency_msec") or 200),
        "PREFER_SBC_CODEC": bool(opts.get("prefer_sbc_codec", False)),
        "BT_CHECK_INTERVAL": int(opts.get("bt_check_interval") or 10),
        "BT_MAX_RECONNECT_FAILS": int(opts.get("bt_max_reconnect_fails") or 0),
        "AUTH_ENABLED": bool(opts.get("auth_enabled", False)),
        "LOG_LEVEL": (opts.get("log_level") or "info").upper(),
        "MA_API_URL": opts.get("ma_api_url") or "",
        "MA_API_TOKEN": opts.get("ma_api_token") or "",
        "VOLUME_VIA_MA": bool(opts.get("volume_via_ma", True)),
    }

    # Normalize: devices without explicit 'enabled' field default to True
    for dev in config["BLUETOOTH_DEVICES"]:
        if isinstance(dev, dict):
            dev.setdefault("enabled", True)

    # Preserve runtime state from previous config
    try:
        with open(CONFIG_FILE) as f:
            existing: dict = json.load(f)
        if "LAST_VOLUMES" in existing:
            config["LAST_VOLUMES"] = existing["LAST_VOLUMES"]
        elif "LAST_VOLUME" in existing:
            config["LAST_VOLUME"] = existing["LAST_VOLUME"]
        for key in ("AUTH_PASSWORD_HASH", "SECRET_KEY"):
            if key in existing:
                config[key] = existing[key]
        # Preserve MA API credentials if not overridden via HA addon options
        for key in ("MA_API_URL", "MA_API_TOKEN"):
            if not config.get(key) and existing.get(key):
                config[key] = existing[key]
        # Preserve per-device web UI settings (e.g. keepalive) not present in options.json
        existing_devs = {
            d["mac"]: d for d in existing.get("BLUETOOTH_DEVICES", []) if isinstance(d, dict) and d.get("mac")
        }
        for dev in config["BLUETOOTH_DEVICES"]:
            mac = dev.get("mac") if isinstance(dev, dict) else None
            if mac and mac in existing_devs:
                for field in ("keepalive_silence", "keepalive_interval"):
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
