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
    "COMMON_BT_PAIR_PINS",
    "bt_remove_device",
    "build_hci_map",
    "describe_pair_failure",
    "extract_pair_failure_reason",
    "get_adapter_alias",
    "is_audio_device",
    "list_bt_adapters",
    "persist_device_enabled",
    "persist_device_released",
    "resolve_hci_for_mac",
]

# /sys/class/bluetooth/hciN/address is the canonical kernel mapping from
# adapter MAC to interface name.  Override for tests.
_BT_SYSFS_DIR: Path = Path("/sys/class/bluetooth")

# Ordered list of PINs the bridge tries when a device asks for one. `0000`
# is the BlueZ/consumer default; the rest are the most common fallbacks
# shipped by BT audio vendors (user guides for Anker, JBL, HMDX, Harman,
# no-name OEM speakers). Kept short intentionally — every extra attempt
# adds ~20 s to total pair time because BlueZ auth-fails only surface
# after a timeout.
COMMON_BT_PAIR_PINS: tuple[str, ...] = ("0000", "1234", "1111", "8888", "1212", "9999")

_AUTH_FAIL_MARKERS = (
    "authenticationfailed",
    "authentication failed",
    "failed to pair: authentication",
)

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

# BlueZ stores per-adapter device cache files at /var/lib/bluetooth/<adapter>/cache/<device>.
# `bluetoothctl remove` does NOT delete these; stale ServiceRecords/Endpoints in that file
# cause org.bluez.Error.Failed "Protocol not available" on the next A2DP pair attempt
# (bluez/bluez#191, #348, #698). Tests inject a temp path via monkeypatch.
_BLUEZ_LIB_DIR: Path = Path("/var/lib/bluetooth")


def _clean_bluez_cache(adapter_mac: str, device_mac: str) -> None:
    """Best-effort removal of the stale BlueZ cache file for *device_mac*
    under *adapter_mac*. Silent on ``FileNotFoundError`` (already gone);
    warns on other OS errors but never raises — the caller runs in a
    daemon thread and must not die."""
    cache_file = _BLUEZ_LIB_DIR / adapter_mac / "cache" / device_mac
    try:
        cache_file.unlink()
        logger.info("BlueZ cache: removed stale %s", cache_file)
    except FileNotFoundError:
        pass
    except OSError as e:
        logger.warning("BlueZ cache cleanup failed for %s: %s", cache_file, e)


def is_valid_mac(mac: str) -> bool:
    """Return True if *mac* looks like a valid colon-separated MAC address."""
    return bool(_MAC_RE.match(mac))


def build_hci_map() -> dict[str, str]:
    """Return a ``{normalised_mac: hciN}`` map by scanning sysfs once.

    Use this instead of calling :func:`resolve_hci_for_mac` in a loop —
    one sysfs walk per request keeps the endpoint O(n) in the number of
    adapters and avoids redundant filesystem I/O on hosts with several
    BT controllers.

    Keys are uppercase, colon-stripped MACs (matching what
    ``resolve_hci_for_mac`` compares internally).  Returns an empty dict
    when sysfs is unreadable.
    """
    mapping: dict[str, str] = {}
    try:
        entries = sorted(_BT_SYSFS_DIR.iterdir())
    except OSError as exc:
        logger.debug("sysfs adapter lookup failed: %s", exc)
        return mapping
    for hci in entries:
        addr_file = hci / "address"
        if not addr_file.exists():
            continue
        try:
            addr = addr_file.read_text().strip().upper().replace(":", "")
        except OSError:
            continue
        if addr:
            mapping[addr] = hci.name
    return mapping


def resolve_hci_for_mac(mac: str) -> str:
    """Return the kernel ``hciN`` interface name for a BT adapter MAC.

    Reads ``/sys/class/bluetooth/hciN/address`` — the canonical mapping
    BlueZ honours.  ``bluetoothctl list`` enumerates controllers in
    BlueZ's internal registration order, which is **not** guaranteed to
    match the kernel ``hciN`` numbering (issue #193: a Pi with built-in
    BT plus a hot-plugged USB stick).  Callers that surface adapter
    labels to the user must resolve via sysfs to stay consistent with
    ``hciconfig`` / ``bluetoothctl info``.

    Returns an empty string when sysfs is unreadable (non-Linux dev box
    or container without ``/sys`` mounted) so callers can fall back to
    a synthetic ``hci{i}`` label.

    For multi-MAC lookups in the same request, prefer :func:`build_hci_map`
    + dict access to avoid an O(n²) sysfs scan.
    """
    if not mac:
        return ""
    target = mac.upper().replace(":", "")
    return build_hci_map().get(target, "")


def get_adapter_alias(mac: str, *, timeout: int = 5) -> tuple[str, bool]:
    """Return ``(alias, powered)`` for *mac* via ``bluetoothctl show <MAC>``.

    Uses the explicit ``show <MAC>`` form rather than ``select <MAC>;
    show`` — the select-then-show recipe is unreliable in piped-stdin
    mode (the ``select`` D-Bus call may not propagate before ``show``
    runs, and the resulting stdout often contains the **default**
    controller's ``Alias:`` line first plus the selected controller's
    block second; the original frontend parser picked the first match
    and surfaced the wrong alias — issue #193).

    Returns ``("", False)`` on any subprocess / parse failure so callers
    can fall back to a synthetic label.
    """
    if not mac:
        return "", False
    try:
        result = subprocess.run(
            ["bluetoothctl"],
            input=f"show {mac}\n",
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        logger.debug("get_adapter_alias(%s) subprocess failed: %s", mac, exc)
        return "", False
    stdout = result.stdout or ""

    # Anchor on lines that look like "<whitespace>Alias: <value>" — bluetoothctl
    # nests the property lines under the ``Controller`` block with a leading tab.
    # Discovery / async events ("[CHG] Controller ... Pairable: yes") never
    # match because they don't have the bare ``Alias:`` token at the start of
    # the trimmed line.
    alias = ""
    powered = False
    for raw_line in stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("Alias:"):
            value = line.split("Alias:", 1)[1].strip()
            if value and not alias:
                alias = value
        elif line.startswith("Powered:"):
            powered = "yes" in line.split("Powered:", 1)[1].lower()
    return alias, powered


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


def is_pin_rejection(output: str) -> bool:
    """Return True when bluetoothctl output indicates the device rejected
    the PIN we supplied (AuthenticationFailed variants). Non-auth failures
    like ConnectionAttemptFailed or ProtocolError are not PIN-related."""
    lowered = str(output or "").lower()
    return any(marker in lowered for marker in _AUTH_FAIL_MARKERS)


def describe_pair_failure(output: str, *, pin_attempted: bool = False, pin_used: str = "") -> str:
    """Return a human-readable pairing failure reason with a PIN hint.

    When ``pin_attempted`` is True and the captured output contains an
    authentication-failure marker, appends an explicit note that the
    device rejected the PIN. That way operators see the root cause in
    the log without grepping for ``AuthenticationFailed``.
    """
    base = extract_pair_failure_reason(output)
    if not pin_attempted or not base:
        return base
    if not is_pin_rejection(base):
        return base
    pin_label = pin_used or "0000"
    return f"{base} — device rejected PIN {pin_label}"


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
            result = subprocess.run(
                ["bluetoothctl"],
                input=cmd_str,
                capture_output=True,
                text=True,
                timeout=10,
            )
            # `bluetoothctl` returns 0 even when `remove <mac>` fails with
            # "Device not available" (device not in the BlueZ object tree).
            # Rely on the stdout marker instead of returncode.
            out = (result.stdout or "") + (result.stderr or "")
            if "not available" in out.lower() or "failed to remove" in out.lower():
                logger.warning(
                    "BT stack: remove %s reported failure (adapter: %s): %s",
                    mac,
                    adapter_mac or "default",
                    out.strip() or "no output",
                )
            else:
                logger.info("BT stack: removed %s (adapter: %s)", mac, adapter_mac or "default")
        except Exception as e:
            logger.warning("BT stack cleanup failed for %s: %s", mac, e)
        # Cache cleanup is intentionally independent of the remove outcome:
        # stale cache files survive even when bluetoothctl reports "not
        # available" (device already gone from the tree but its
        # /var/lib/bluetooth/<adapter>/cache/<device> file lingers and
        # still causes the next-pair Protocol-not-available regression).
        if adapter_mac:
            _clean_bluez_cache(adapter_mac, mac)

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
