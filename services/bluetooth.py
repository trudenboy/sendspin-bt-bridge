"""
Bluetooth service helpers for sendspin-bt-bridge.

Low-level operations shared between web_interface.py routes and other modules.
"""

import json
import logging
import os
import re
import subprocess
import threading
from pathlib import Path

from config import CONFIG_FILE as _CONFIG_FILE
from config import config_lock as _config_lock

logger = logging.getLogger(__name__)
_OPTIONS_FILE = Path("/data/options.json")

_AUDIO_UUIDS = {
    "0000110b",  # A2DP Sink
    "0000110a",  # A2DP Source
    "0000110e",  # AV Remote Control
    "0000110c",  # AV Remote Control Target
    "0000111e",  # Hands-Free
}


def bt_remove_device(mac: str, adapter_mac: str = "") -> None:
    """Remove device from BT stack (disconnect + unpair). Fire-and-forget."""

    def _run():
        cmds = []
        if adapter_mac:
            cmds.append(f"select {adapter_mac}")
        cmds.append(f"remove {mac}")
        cmd_str = "\n".join(cmds) + "\n"
        try:
            subprocess.run(
                ["bluetoothctl"],
                input=cmd_str,
                capture_output=True,
                text=True,
                timeout=10,
            )
            logger.info("BT stack: removed %s (adapter: %s)", mac, adapter_mac or "default")
        except Exception as e:
            logger.warning("BT stack cleanup failed for %s: %s", mac, e)

    threading.Thread(target=_run, daemon=True).start()


def persist_device_enabled(player_name: str, enabled: bool) -> None:
    """Persist the enabled flag to config.json and (in HA mode) to options.json."""
    if not _CONFIG_FILE.exists():
        return
    try:
        with _config_lock:
            with open(_CONFIG_FILE) as f:
                cfg = json.load(f)
            for dev in cfg.get("BLUETOOTH_DEVICES", []):
                if dev.get("player_name") == player_name:
                    dev["enabled"] = enabled
                    break
            tmp = str(_CONFIG_FILE) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(cfg, f, indent=2)
            os.replace(tmp, str(_CONFIG_FILE))
    except Exception as e:
        logger.warning("Could not persist enabled flag for '%s': %s", player_name, e)

    # Sync to options.json so the HA addon config page reflects the change
    if _OPTIONS_FILE.exists():
        try:
            with open(_OPTIONS_FILE) as f:
                opts = json.load(f)
            for dev in opts.get("bluetooth_devices", []):
                if dev.get("player_name") == player_name:
                    dev["enabled"] = enabled
                    break
            tmp = str(_OPTIONS_FILE) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(opts, f, indent=2)
            os.replace(tmp, str(_OPTIONS_FILE))
            logger.debug("Synced enabled=%s for '%s' to options.json", enabled, player_name)
        except Exception as e:
            logger.debug("Could not sync enabled flag to options.json: %s", e)


def is_audio_device(mac: str) -> bool:
    """Return True if the BT device is an audio device (A2DP/HFP)."""
    try:
        r = subprocess.run(["bluetoothctl", "info", mac], capture_output=True, text=True, timeout=4)
        out = r.stdout
        out_lower = out.lower()
        # Check Class field: major class 4 = Audio/Video
        class_m = re.search(r"Class:\s+(0x[0-9A-Fa-f]+)", out)
        if class_m:
            cls = int(class_m.group(1), 16)
            major = (cls >> 8) & 0x1F
            return major == 4
        # No Class — check for any audio profile UUID
        if any(u in out_lower for u in _AUDIO_UUIDS):
            return True
        # BlueZ has UUID info but no audio profile → definitely not audio
        if "UUID:" in out:
            return False
        # Name only (no Class, no UUID) — device may be in pairing mode, include cautiously
        return True
    except Exception:
        return True  # on error, include
