"""Build schema v6 configs from v5-style device lists in tests."""

import hashlib
import re

from sendspin_bridge.config.players import legacy_device_to_player

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")


def _valid_mac(mac: str) -> str:
    """Return ``mac`` if it is a real MAC, else a stable fake one for short test labels like ``"AA"``."""
    if _MAC_RE.match(mac):
        return mac
    digest = hashlib.sha1(mac.encode()).digest()[:6]
    return ":".join(f"{b:02X}" for b in digest)


def v6_players(devices: list[dict] | None = None) -> dict:
    """The ``players`` / ``PLAYER_STATE`` keys for a v5-style device list."""
    return {
        "players": [legacy_device_to_player({**d, "mac": _valid_mac(d.get("mac", ""))}) for d in (devices or [])],
        "PLAYER_STATE": {},
    }


def v6_config(devices: list[dict] | None = None, **extra) -> dict:
    return {"CONFIG_SCHEMA_VERSION": 6, **v6_players(devices), **extra}
