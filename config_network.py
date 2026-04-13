"""Network and port resolution helpers for the bridge."""

from __future__ import annotations

import logging
import os
import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

logger = logging.getLogger(__name__)

DEFAULT_WEB_PORT = 8080
DEFAULT_LISTEN_PORT_BASE = 8928

HA_ADDON_CHANNEL_DEFAULTS: dict[str, dict[str, int]] = {
    "stable": {"web_port": DEFAULT_WEB_PORT, "base_listen_port": DEFAULT_LISTEN_PORT_BASE},
    "rc": {"web_port": 8081, "base_listen_port": 9028},
    "beta": {"web_port": 8082, "base_listen_port": 9128},
}


def _coerce_port(raw_value: object, default: int) -> int:
    if isinstance(raw_value, bool):
        port = int(raw_value)
    elif isinstance(raw_value, (int, str)):
        try:
            port = int(raw_value)
        except ValueError:
            return default
    else:
        return default
    if 1 <= port <= 65535:
        return port
    return default


def _configured_port_override(config: dict | None, key: str, default: int) -> int | None:
    if not isinstance(config, dict):
        return None
    raw_value = config.get(key)
    if raw_value in (None, ""):
        return None
    return _coerce_port(raw_value, default)


def is_ha_addon_runtime(*, env: Mapping[str, str] | None = None) -> bool:
    from pathlib import Path

    environ = os.environ if env is None else env
    return bool(environ.get("SUPERVISOR_TOKEN")) or Path("/data/options.json").exists()


def detect_ha_addon_channel(*, env: Mapping[str, str] | None = None, hostname: str | None = None) -> str:
    """Infer the installed HA addon delivery channel from the container hostname."""
    from config_migration import DEFAULT_UPDATE_CHANNEL

    environ = os.environ if env is None else env
    if not is_ha_addon_runtime(env=environ):
        return DEFAULT_UPDATE_CHANNEL
    detected_hostname = (hostname or environ.get("HOSTNAME") or socket.gethostname()).strip().lower()
    if detected_hostname.endswith("-rc"):
        return "rc"
    if detected_hostname.endswith("-beta"):
        return "beta"
    return DEFAULT_UPDATE_CHANNEL


def resolve_web_port(*, env: Mapping[str, str] | None = None, hostname: str | None = None) -> int:
    environ = os.environ if env is None else env
    if not is_ha_addon_runtime(env=environ):
        explicit_port = environ.get("WEB_PORT")
        if explicit_port not in (None, ""):
            return _coerce_port(explicit_port, DEFAULT_WEB_PORT)
        from config import load_config  # late import to avoid circular dependency

        configured_port = _configured_port_override(load_config(), "WEB_PORT", DEFAULT_WEB_PORT)
        if configured_port is not None:
            return configured_port
    # HA addon: prefer dynamic INGRESS_PORT assigned by Supervisor (ingress_port: 0)
    ingress_port = environ.get("INGRESS_PORT")
    if ingress_port not in (None, ""):
        channel = detect_ha_addon_channel(env=environ, hostname=hostname)
        fallback = HA_ADDON_CHANNEL_DEFAULTS[channel]["web_port"]
        coerced = _coerce_port(ingress_port, fallback)
        if coerced != fallback:
            return coerced
    channel = detect_ha_addon_channel(env=environ, hostname=hostname)
    return HA_ADDON_CHANNEL_DEFAULTS[channel]["web_port"]


def resolve_additional_web_port(*, env: Mapping[str, str] | None = None, hostname: str | None = None) -> int | None:
    environ = os.environ if env is None else env
    if not is_ha_addon_runtime(env=environ):
        return None
    return None


def resolve_base_listen_port(*, env: Mapping[str, str] | None = None, hostname: str | None = None) -> int:
    environ = os.environ if env is None else env
    channel = detect_ha_addon_channel(env=environ, hostname=hostname)
    default_port = HA_ADDON_CHANNEL_DEFAULTS[channel]["base_listen_port"]
    explicit_port = environ.get("BASE_LISTEN_PORT")
    if explicit_port not in (None, ""):
        return _coerce_port(explicit_port, default_port)
    from config import load_config  # late import to avoid circular dependency

    configured_port = _configured_port_override(load_config(), "BASE_LISTEN_PORT", default_port)
    if configured_port is not None:
        return configured_port
    return default_port


def get_local_ip() -> str:
    """Return the primary local IP address via a UDP socket probe."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return ""
