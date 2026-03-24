"""
Bluetooth service helpers for sendspin-bt-bridge.

Low-level operations shared between web_interface.py routes and other modules.
"""

import json
import logging
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path

from config import CONFIG_FILE as _CONFIG_FILE
from config import config_lock as _config_lock

logger = logging.getLogger(__name__)
_OPTIONS_FILE = Path("/data/options.json")

__all__ = [
    "bt_remove_device",
    "extract_pair_failure_reason",
    "is_audio_device",
    "list_bt_adapters",
    "persist_device_enabled",
    "persist_device_released",
]

_AUDIO_UUIDS = {
    "0000110b",  # A2DP Sink
    "0000110a",  # A2DP Source
    "0000110e",  # AV Remote Control
    "0000110c",  # AV Remote Control Target
    "0000111e",  # Hands-Free
}

_ADAPTER_RE = re.compile(r"Controller\s+([\dA-F:]{17})\s", re.IGNORECASE)
_MAC_RE = re.compile(r"^[\dA-Fa-f]{2}(:[\dA-Fa-f]{2}){5}$")
_BLUEZ_ERROR_RE = re.compile(r"org\.bluez\.Error\.[A-Za-z]+")


def is_valid_mac(mac: str) -> bool:
    """Return True if *mac* looks like a valid colon-separated MAC address."""
    return bool(_MAC_RE.match(mac))


def list_bt_adapters(timeout: int = 5) -> list[str]:
    """Return list of BT adapter MAC addresses from ``bluetoothctl list``."""
    try:
        result = subprocess.run(
            ["bluetoothctl", "list"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return _ADAPTER_RE.findall(result.stdout)
    except (subprocess.SubprocessError, OSError):
        return []


def extract_pair_failure_reason(output: str, *, tail_chars: int = 400) -> str:
    """Extract the most useful bluetoothctl pairing failure reason from output."""
    text = str(output or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for line in reversed(lines):
        if "failed to pair:" in line.lower():
            return line

    for line in reversed(lines):
        match = _BLUEZ_ERROR_RE.search(line)
        if match:
            return match.group(0)

    failure_tokens = (
        "error",
        "failed",
        "timed out",
        "authentication",
        "rejected",
        "canceled",
        "not available",
    )
    for line in reversed(lines):
        lowered = line.lower()
        if any(token in lowered for token in failure_tokens):
            return line

    return text[-tail_chars:].strip()


def bt_remove_device(mac: str, adapter_mac: str = "") -> None:
    """Remove device from BT stack (disconnect + unpair). Fire-and-forget."""
    if not _MAC_RE.match(mac):
        logger.warning("Invalid MAC: %s", mac)
        return
    if adapter_mac and not _MAC_RE.match(adapter_mac):
        logger.warning("Invalid adapter MAC: %s", adapter_mac)
        return

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


def _match_player_name(config_name: str, runtime_name: str) -> bool:
    """Match config player_name against runtime name (which may include ' @ bridge' suffix)."""
    return runtime_name == config_name or runtime_name.startswith(config_name + " @ ")


def _update_bound_config_file(mutator) -> None:
    """Atomically mutate the config file bound into this module."""
    with _config_lock:
        existing: dict = {}
        if _CONFIG_FILE.exists():
            with open(_CONFIG_FILE) as f:
                existing = json.load(f)
        mutator(existing)
        _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp_f = tempfile.NamedTemporaryFile(  # noqa: SIM115
            dir=str(_CONFIG_FILE.parent),
            delete=False,
            mode="w",
            suffix=".tmp",
        )
        try:
            json.dump(existing, tmp_f, indent=2)
            tmp_f.flush()
            os.fsync(tmp_f.fileno())
            tmp_f.close()
            os.replace(tmp_f.name, str(_CONFIG_FILE))
        except BaseException:
            tmp_f.close()
            try:
                os.unlink(tmp_f.name)
            except OSError:
                pass
            raise


def persist_device_enabled(player_name: str, enabled: bool) -> None:
    """Persist the enabled flag to config.json and (in HA mode) to options.json."""
    if not _CONFIG_FILE.exists():
        return

    def _set_enabled(cfg: dict) -> None:
        for dev in cfg.get("BLUETOOTH_DEVICES", []):
            if _match_player_name(dev.get("player_name", ""), player_name):
                dev["enabled"] = enabled
                break

    try:
        _update_bound_config_file(_set_enabled)
    except Exception as e:
        logger.warning("Could not persist enabled flag for '%s': %s", player_name, e)

    # Sync to options.json so the HA addon config page reflects the change
    if _OPTIONS_FILE.exists():
        try:
            with _config_lock:
                with open(_OPTIONS_FILE) as f:
                    opts = json.load(f)
                for dev in opts.get("bluetooth_devices", []):
                    if _match_player_name(dev.get("player_name", ""), player_name):
                        dev["enabled"] = enabled
                        break
                tmp = str(_OPTIONS_FILE) + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(opts, f, indent=2)
                os.replace(tmp, str(_OPTIONS_FILE))
            logger.debug("Synced enabled=%s for '%s' to options.json", enabled, player_name)
        except Exception as e:
            logger.debug("Could not sync enabled flag to options.json: %s", e)


def persist_device_released(player_name: str, released: bool) -> None:
    """Persist the released (bt_management_enabled=False) flag to config.json."""
    if not _CONFIG_FILE.exists():
        return

    def _set_released(cfg: dict) -> None:
        for dev in cfg.get("BLUETOOTH_DEVICES", []):
            if _match_player_name(dev.get("player_name", ""), player_name):
                dev["released"] = released
                break

    try:
        _update_bound_config_file(_set_released)
    except Exception as e:
        logger.warning("Could not persist released flag for '%s': %s", player_name, e)

    if _OPTIONS_FILE.exists():
        try:
            with _config_lock:
                with open(_OPTIONS_FILE) as f:
                    opts = json.load(f)
                for dev in opts.get("bluetooth_devices", []):
                    if _match_player_name(dev.get("player_name", ""), player_name):
                        dev["released"] = released
                        break
                tmp = str(_OPTIONS_FILE) + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(opts, f, indent=2)
                os.replace(tmp, str(_OPTIONS_FILE))
            logger.debug("Synced released=%s for '%s' to options.json", released, player_name)
        except Exception as e:
            logger.debug("Could not sync released flag to options.json: %s", e)


def is_audio_device(mac: str) -> bool:
    """Return True if the BT device is an audio device (A2DP/HFP)."""
    if not _MAC_RE.match(mac):
        return False
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
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        logger.debug("is_audio_device(%s) check failed: %s", mac, exc)
        return True  # on error, include
