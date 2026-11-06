"""Is this device deliberately the Source feed's active input?

A player switched to source mode has its daemon stopped and its card on HFP.
No A2DP sink is the expected shape of that state, not a missing output: the
guidance builders must not offer a reconnect for it.  This is the one place
that answers the question for them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sendspin_bridge.services.ipc.device_facts import DeviceFacts
from sendspin_bridge.services.source.inputs import active_headset_mac

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["device_mac", "owns_active_headset_input", "source_mode_macs"]


def device_mac(device: Any) -> str | None:
    """MAC from a normalised state model, a device snapshot, or its fact bag."""
    state_model = getattr(device, "state_model", None)
    if isinstance(state_model, dict):
        bluetooth = state_model.get("bluetooth")
        if isinstance(bluetooth, dict):
            mac = bluetooth.get("mac")
            if isinstance(mac, str) and mac:
                return mac
    bluetooth = getattr(device, "bluetooth", None)
    if isinstance(bluetooth, dict):
        mac = bluetooth.get("mac")
        if isinstance(mac, str) and mac:
            return mac
    return DeviceFacts(device).bluetooth_mac


def owns_active_headset_input(device: Any, config: Mapping[str, Any] | None) -> bool:
    """Does *device*'s microphone own the active source input?"""
    mac = active_headset_mac(config)
    if mac is None:
        return False
    device_address = device_mac(device)
    return bool(device_address) and str(device_address).upper() == mac.upper()


def source_mode_macs(devices: Any, config: Mapping[str, Any] | None) -> frozenset[str]:
    """The MACs of every device whose microphone owns the active source input."""
    mac = active_headset_mac(config)
    if mac is None:
        return frozenset()
    wanted = str(mac).upper()
    return frozenset(
        str(address).upper()
        for device in devices or ()
        if (address := device_mac(device)) and str(address).upper() == wanted
    )
