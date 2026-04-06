"""Audio sink discovery and SBC codec forcing for Bluetooth devices.

Extracted from ``bluetooth_manager.py`` — contains ``_force_sbc_codec()``
and the standalone ``configure_bluetooth_audio()`` function that discovers
the PulseAudio/PipeWire sink for a connected BT device, restores saved
volume, and caches the sink name.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from typing import TYPE_CHECKING

from config import CONFIG_FILE, save_device_sink
from config import config_lock as config_lock
from services.pulse import get_sink_volume, list_sinks, set_sink_mute, set_sink_volume

if TYPE_CHECKING:
    from collections.abc import Callable

    from bt_types import BluetoothManagerHost

logger = logging.getLogger(__name__)

# Timing constants (mirrors values in bluetooth_manager.py)
_A2DP_PROFILE_DELAY = 3.0
_SINK_RETRY_DELAY = 3.0
_SINK_RETRY_COUNT = int(os.environ.get("SINK_RETRY_COUNT", "5"))


def _force_sbc_codec(pa_mac: str) -> None:
    """Attempt to force SBC codec on the BlueZ card for this device.

    SBC is the simplest mandatory A2DP codec — least CPU for the PA encoder.
    Silently ignores failures (older PA, device already on SBC, or PA 14 that
    lacks the send-message routing).
    """
    card_prefix = f"bluez_card.{pa_mac}"
    try:
        cards = subprocess.check_output(["pactl", "list", "short", "cards"], text=True, timeout=5)
        for line in cards.splitlines():
            if card_prefix in line:
                card_name = line.split()[1]
                result = subprocess.run(
                    [
                        "pactl",
                        "send-message",
                        f"/card/{card_name}/bluez5/set_codec",
                        "a2dp_sink",
                        "SBC",
                    ],
                    timeout=5,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if result.returncode == 0:
                    logger.info("✓ Forced SBC codec on %s", card_name)
                else:
                    logger.debug("SBC force failed for %s: %s", card_name, result.stderr.strip())
                return
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("SBC codec force skipped: %s", e)


def configure_bluetooth_audio(
    mac_address: str,
    prefer_sbc: bool,
    on_sink_found: Callable[[str, int | None], None] | None,
    host: BluetoothManagerHost | None,
    wait_with_cancel: Callable[[float], bool],
    *,
    logger: logging.Logger = logger,
) -> bool:
    """Configure PipeWire/PulseAudio to use a Bluetooth device as audio output.

    This is the standalone version of ``BluetoothManager.configure_bluetooth_audio``.
    The *wait_with_cancel* callback should sleep for the given duration while
    checking for reconnect cancellation; it returns ``True`` when the full
    duration elapsed normally, ``False`` if cancelled.
    """
    try:
        pa_mac = mac_address.replace(":", "_")

        # Try cached sink name first — avoids 3s A2DP delay on service restart
        cached_sink = None
        try:
            if CONFIG_FILE.exists():
                with config_lock, open(CONFIG_FILE) as f:
                    cfg = json.load(f)
                cached_sink = cfg.get("LAST_SINKS", {}).get(mac_address)
        except (OSError, json.JSONDecodeError, ValueError):
            pass

        if cached_sink and get_sink_volume(cached_sink) is not None:
            logger.info("✓ Using cached sink: %s (skipped A2DP delay)", cached_sink)
            configured_sink = cached_sink
            success = True
        else:
            if cached_sink:
                logger.debug("Cached sink %s not available, falling back to discovery", cached_sink)
            # Wait for PipeWire/PulseAudio to register the device.
            if not wait_with_cancel(_A2DP_PROFILE_DELAY):
                return False

            # Log available sinks for diagnostics
            sinks = list_sinks()
            logger.info("Available audio sinks: %s", [s["name"] for s in sinks])

            # Find the Bluetooth sink
            # CRITICAL: Audio routing — sink discovery with bounded retries (_SINK_RETRY_COUNT).
            # If no sink found after retries, BT speaker will connect but play no audio.
            # Sink naming differs between PipeWire and PulseAudio — order matters.
            sink_names = [
                f"bluez_output.{pa_mac}.1",  # PipeWire format
                f"bluez_output.{pa_mac}.a2dp-sink",
                f"bluez_sink.{pa_mac}.a2dp_sink",  # Legacy PulseAudio format
                f"bluez_sink.{pa_mac}",
            ]
            known_names = {s["name"] for s in sinks}

            success = False
            configured_sink = None
            for attempt in range(_SINK_RETRY_COUNT):
                for sink_name in sink_names:
                    if sink_name in known_names or get_sink_volume(sink_name) is not None:
                        logger.info("✓ Found audio sink: %s", sink_name)
                        configured_sink = sink_name
                        success = True
                        break
                    else:
                        logger.debug("Sink %s not found, trying next...", sink_name)
                if success:
                    break
                if attempt < _SINK_RETRY_COUNT - 1:
                    logger.info(
                        "Sink not yet available, retrying in 3s... (attempt %s/%s)",
                        attempt + 1,
                        _SINK_RETRY_COUNT,
                    )
                    if not wait_with_cancel(_SINK_RETRY_DELAY):
                        return False
                    sinks = list_sinks()
                    known_names = {s["name"] for s in sinks}

        if success and configured_sink:
            if prefer_sbc:
                _force_sbc_codec(pa_mac)

            # Ensure sink is unmuted (PulseAudio may mute on BT reconnect)
            set_sink_mute(configured_sink, False)

            # Resolve last saved volume for this device
            restored_volume = None
            try:
                if CONFIG_FILE.exists():
                    with config_lock, open(CONFIG_FILE) as f:
                        cfg = json.load(f)
                    volumes = cfg.get("LAST_VOLUMES", {})
                    last_volume = volumes.get(mac_address)
                    if last_volume is not None and isinstance(last_volume, int) and 0 <= last_volume <= 100:
                        if set_sink_volume(configured_sink, last_volume):
                            logger.info("✓ Restored volume to %s%% for %s", last_volume, mac_address)
                            restored_volume = last_volume
            except (OSError, json.JSONDecodeError, ValueError) as e:
                logger.warning("Could not restore volume: %s", e)

            if restored_volume is None:
                logger.info("No saved volume to restore, will use current volume")

            # Notify caller via callback (decoupled) or fall back to direct host mutation
            if on_sink_found:
                on_sink_found(configured_sink, restored_volume)
            elif host:
                host.bluetooth_sink_name = configured_sink
                logger.info("Stored Bluetooth sink for volume sync: %s", configured_sink)
                if restored_volume is not None:
                    host.update_status({"volume": restored_volume})

            # Cache sink name for faster restart
            save_device_sink(mac_address, configured_sink)
        elif not success:
            logger.warning("Could not find Bluetooth sink for %s", mac_address)
            logger.warning("Audio may play from default device instead of Bluetooth")
            _warn_pipewire_session(known_names)

        return success

    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as e:
        logger.error("Error configuring Bluetooth audio: %s", e)
        return False


def _warn_pipewire_session(known_sink_names: set[str]) -> None:
    """Log a targeted warning when sink discovery fails on PipeWire.

    On PipeWire, Bluetooth audio sinks are managed by WirePlumber which
    runs as a user-session service (``systemd --user``).  After a host
    reboot without an active login session, WirePlumber may not be running
    and PipeWire will never create the ``bluez_output.*`` sink node.

    Even when WirePlumber *is* running, its ``with-logind`` option
    (enabled by default) causes continuous A2DP endpoint churn on headless
    systems — endpoints register then unregister every ~10 s, preventing
    any stable Bluetooth connection.

    This helper checks the audio backend and emits clear remediation hints
    so the operator doesn't have to dig through generic "sink not found" logs.
    """
    try:
        from services.pulse import get_server_name

        server = get_server_name()
    except Exception:
        return

    if not server or "pipewire" not in str(server).lower():
        return

    has_bt_sink = any(n.startswith(("bluez_output.", "bluez_sink.")) for n in known_sink_names)
    if has_bt_sink:
        return

    logger.warning(
        "PipeWire detected but no Bluetooth audio sinks are visible. "
        "WirePlumber (the Bluetooth policy manager) may not be running."
    )
    logger.warning(
        "On headless/server systems, run 'loginctl enable-linger <user>' "
        "so PipeWire + WirePlumber start at boot without a login session. "
        "See https://trudenboy.github.io/sendspin-bt-bridge/installation/docker/"
    )
    _warn_wireplumber_logind()


def _warn_wireplumber_logind() -> None:
    """Warn about WirePlumber ``with-logind`` causing A2DP endpoint churn.

    On headless PipeWire systems, WirePlumber's logind integration
    continuously re-registers and unregisters A2DP media endpoints with
    BlueZ (~every 10 s) because there is no active graphical seat.  This
    prevents any Bluetooth audio connection from stabilizing — the speaker
    connects briefly then drops with ``a2dp-sink profile connect failed:
    Protocol not available`` in bluetoothd logs.

    The fix is to disable ``with-logind`` in WirePlumber's bluetooth
    config.  This function detects the condition and logs the remedy.
    """
    logind_active = _is_wireplumber_logind_active()
    if logind_active is None:
        return  # cannot determine — stay silent
    if not logind_active:
        return  # already disabled — nothing to warn about

    logger.warning(
        "WirePlumber 'with-logind' is enabled — on headless systems this "
        "causes A2DP endpoint churn that prevents Bluetooth connections "
        "from stabilizing."
    )
    logger.warning(
        "Fix: create ~/.config/wireplumber/bluetooth.lua.d/51-disable-logind.lua "
        'containing:  bluez_monitor.properties["with-logind"] = false  — '
        "then restart WirePlumber. "
        "See https://trudenboy.github.io/sendspin-bt-bridge/installation/docker/"
    )


def _is_wireplumber_logind_active(
    *,
    _override_dirs: list | None = None,
    _default_cfg_path: str | os.PathLike[str] | None = None,
) -> bool | None:
    """Check whether WirePlumber's ``with-logind`` is active.

    Returns ``True`` if the default config has ``with-logind = true`` and
    no user override disables it.  Returns ``None`` when the check cannot
    be performed (e.g. config files unreadable).
    """
    import pathlib

    # User override takes precedence (WirePlumber merges lua.d in order)
    if _override_dirs is None:
        _override_dirs = [
            pathlib.Path.home() / ".config" / "wireplumber" / "bluetooth.lua.d",
            pathlib.Path("/etc/wireplumber/bluetooth.lua.d"),
        ]
    for d in _override_dirs:
        try:
            for f in sorted(pathlib.Path(d).glob("*.lua")):
                try:
                    content = f.read_text()
                except OSError:
                    continue
                # Look for explicit with-logind = false
                if "with-logind" in content and "false" in content:
                    return False
        except OSError:
            continue

    # Check default config
    if _default_cfg_path is None:
        _default_cfg_path = pathlib.Path("/usr/share/wireplumber/bluetooth.lua.d/50-bluez-config.lua")
    try:
        content = pathlib.Path(_default_cfg_path).read_text()
    except OSError:
        return None

    # Default WirePlumber ships with-logind = true
    if "with-logind" in content and "true" in content:
        return True

    return None
