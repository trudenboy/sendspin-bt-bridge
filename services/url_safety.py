"""Reject server-side fetches to private/internal networks.

Used to guard route handlers that resolve an arbitrary user-supplied URL
and then perform a server-side HTTP request to it — preventing SSRF
against internal services (127.0.0.1, 169.254.169.254, RFC1918 ranges).

In HA-addon mode (when SUPERVISOR_TOKEN is set) a small allowlist of
internal hostnames and the Supervisor proxy network (172.30.32.0/23) is
permitted so legitimate MA / HA discovery keeps working.
"""

from __future__ import annotations

import ipaddress
import os
import socket
import urllib.parse as _up

_HA_ADDON_PROXY_NET = ipaddress.ip_network("172.30.32.0/23")
_HA_ADDON_INTERNAL_HOSTS = frozenset({"supervisor", "hassio", "homeassistant"})


def _is_ha_addon_runtime() -> bool:
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


def is_safe_external_url(url: str) -> bool:
    """Return True if ``url`` is a safe target for a server-side HTTP request.

    Rejects:
      * non-http(s) schemes (file://, javascript:, data:, ftp:, …)
      * URLs without a hostname
      * hostnames that resolve to loopback/link-local/reserved/multicast
      * hostnames that resolve to private RFC1918 ranges
        (except the HA Supervisor proxy net when running as an HA addon)
      * hostnames that do not resolve at all
    """
    try:
        parsed = _up.urlparse(str(url or "").strip())
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False

    is_ha_addon = _is_ha_addon_runtime()
    if is_ha_addon and host in _HA_ADDON_INTERNAL_HOSTS:
        return True

    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return False
    if not infos:
        return False

    for *_, sockaddr in infos:
        try:
            ip = ipaddress.ip_address(sockaddr[0])
        except (ValueError, IndexError):
            return False
        if ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False
        if ip.is_private:
            if is_ha_addon and ip in _HA_ADDON_PROXY_NET:
                continue
            return False
    return True
