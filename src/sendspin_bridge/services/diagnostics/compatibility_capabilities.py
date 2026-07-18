"""Runtime capability detection for opt-in Bluetooth recovery workarounds."""

from __future__ import annotations

import importlib.util
import os
import platform
import subprocess
from typing import Any

_CAP_NET_ADMIN = 12
_CAP_NET_RAW = 13


def _effective_linux_capabilities() -> int:
    try:
        with open("/proc/self/status", encoding="utf-8") as status_file:
            for line in status_file:
                if line.startswith("CapEff:"):
                    return int(line.split(":", 1)[1].strip(), 16)
    except (OSError, ValueError):
        pass
    return 0


def _has_bluetooth_capabilities() -> bool:
    effective = _effective_linux_capabilities()
    required = (1 << _CAP_NET_ADMIN) | (1 << _CAP_NET_RAW)
    return effective & required == required


def _loaded_pactl_modules() -> set[str]:
    try:
        result = subprocess.run(
            ["pactl", "list", "modules", "short"],
            capture_output=True,
            text=True,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return set()
    if result.returncode != 0:
        return set()
    modules: set[str] = set()
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            modules.add(parts[1].strip())
    return modules


def detect_compatibility_capabilities(
    *,
    server_name: str,
    loaded_modules: set[str],
    is_linux: bool,
    adapter_library_available: bool,
    has_bt_capabilities: bool,
    usb_access: bool,
    rfkill_access: bool,
) -> dict[str, Any]:
    """Build a serializable capability snapshot from already-probed facts."""
    server_text = str(server_name or "").strip()
    lower_server = server_text.lower()
    if "pipewire" in lower_server:
        audio_backend = "pipewire"
    elif server_text and lower_server != "not available":
        audio_backend = "pulseaudio"
    else:
        audio_backend = "unavailable"

    discover_loaded = "module-bluez5-discover" in loaded_modules
    pa_reload_available = audio_backend == "pulseaudio" and discover_loaded
    if audio_backend == "pipewire":
        pa_reason = "PipeWire does not load PulseAudio module-bluez5-discover."
    elif audio_backend == "unavailable":
        pa_reason = "The PulseAudio-compatible server is unavailable."
    elif not discover_loaded:
        pa_reason = "PulseAudio module-bluez5-discover is not loaded."
    else:
        pa_reason = "Classic PulseAudio module-bluez5-discover is available."

    adapter_available = bool(is_linux and adapter_library_available and has_bt_capabilities)
    if not is_linux:
        adapter_reason = "Adapter recovery is available on Linux only."
    elif not adapter_library_available:
        adapter_reason = "bluetooth-auto-recovery is not installed."
    elif not has_bt_capabilities:
        adapter_reason = "NET_ADMIN and NET_RAW capabilities are required."
    elif usb_access:
        adapter_reason = "MGMT power-cycle and USB reset are available."
    else:
        adapter_reason = "MGMT power-cycle is available; USB reset is unavailable."

    return {
        "audio_backend": audio_backend,
        "pa_module_reload": {
            "available": pa_reload_available,
            "module_loaded": discover_loaded,
            "reason": pa_reason,
        },
        "adapter_auto_recovery": {
            "available": adapter_available,
            "level": "full" if adapter_available and usb_access else (
                "power_cycle_only" if adapter_available else "unavailable"
            ),
            "usb_reset_available": bool(adapter_available and usb_access),
            "rfkill_available": bool(adapter_available and rfkill_access),
            "reason": adapter_reason,
        },
    }


def get_compatibility_capabilities() -> dict[str, Any]:
    """Probe the running bridge process and audio server."""
    try:
        from sendspin_bridge.services.audio.pulse import get_server_name

        server_name = get_server_name()
    except Exception:
        server_name = "not available"
    is_linux = platform.system() == "Linux"
    try:
        library_available = importlib.util.find_spec("bluetooth_auto_recovery") is not None
    except (ImportError, ValueError):
        library_available = False
    usb_path = "/dev/bus/usb"
    rfkill_path = "/dev/rfkill"
    return detect_compatibility_capabilities(
        server_name=server_name,
        loaded_modules=_loaded_pactl_modules(),
        is_linux=is_linux,
        adapter_library_available=library_available,
        has_bt_capabilities=_has_bluetooth_capabilities() if is_linux else False,
        usb_access=os.path.exists(usb_path) and os.access(usb_path, os.R_OK | os.W_OK),
        rfkill_access=os.path.exists(rfkill_path) and os.access(rfkill_path, os.R_OK | os.W_OK),
    )


__all__ = ["detect_compatibility_capabilities", "get_compatibility_capabilities"]
