"""Reject server-side fetches to dangerous network targets.

This project runs on home LANs, HAOS boxes, Proxmox LXC containers and
similar deployments where Music Assistant / Home Assistant legitimately
live on RFC1918 addresses or on ``localhost`` (host-networked Docker /
HAOS).  A pure "block all private" SSRF guard would reject those
legitimate configurations, so we operate in two modes:

**Default (LAN-permissive)** — blocks:
  * non-http(s) schemes (``file://``, ``javascript:``, ``data:`` …)
  * link-local (169.254.0.0/16) — covers AWS/GCP/Azure IMDS and APIPA
  * multicast, reserved and the unspecified ``0.0.0.0``

  Allows loopback and private RFC1918 ranges because the bridge
  normally *lives* on such a network and routinely has to talk to LAN
  services.

**Strict mode** — enabled by setting ``SENDSPIN_STRICT_SSRF=1`` — also
blocks loopback and RFC1918 addresses, except the Home Assistant
Supervisor proxy net (172.30.32.0/23) and the supervisor/hassio/
homeassistant internal hostnames when running as an HA add-on.
Intended for deployments where the bridge is exposed on an untrusted
network.

Two layers of protection are provided:

1. ``is_safe_external_url(url)`` — pre-flight DNS check used to reject
   obviously-bad URLs before any socket work.
2. ``safe_urlopen(...)`` / ``safe_build_opener(...)`` — wrappers around
   ``urllib.request`` whose underlying ``HTTPConnection`` re-checks the
   peer IP after the socket connects.  This defeats DNS-rebinding /
   TOCTOU attacks where the validator-time resolution returns a public
   IP but the connect-time resolution returns 127.0.0.1 or an RFC1918
   address.
"""

from __future__ import annotations

import http.client
import ipaddress
import logging
import os
import socket
import urllib.parse as _up
import urllib.request as _ur

_HA_ADDON_PROXY_NET = ipaddress.ip_network("172.30.32.0/23")
_HA_ADDON_INTERNAL_HOSTS = frozenset({"supervisor", "hassio", "homeassistant"})

logger = logging.getLogger(__name__)


class UnsafePeerError(OSError):
    """Raised when a socket connects to a disallowed peer address."""


def _is_ha_addon_runtime() -> bool:
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


def _is_strict_mode() -> bool:
    """``SENDSPIN_STRICT_SSRF=1`` opts in to blocking loopback + RFC1918."""
    return os.environ.get("SENDSPIN_STRICT_SSRF", "").strip() == "1"


def _is_ip_safe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, is_ha_addon: bool) -> bool:
    # Loopback is handled first because IPv6 ``::1`` is classified both as
    # loopback *and* reserved; we don't want the reserved-blocklist below
    # to catch it.  Loopback follows the strict-mode rule.
    if ip.is_loopback:
        return not _is_strict_mode()
    # Always block these — link-local covers AWS/GCP/Azure metadata
    # endpoints and APIPA; multicast/reserved/unspecified have no
    # legitimate reason to be the target of an HTTP fetch.
    if ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return False
    if ip.is_private:
        # HA Supervisor proxy net is always allowed in addon mode.
        if is_ha_addon and ip in _HA_ADDON_PROXY_NET:
            return True
        # In LAN-permissive default mode, RFC1918 is OK; strict rejects.
        return not _is_strict_mode()
    return True


def is_safe_external_url(url: str) -> bool:
    """Return True if ``url`` is a safe target for a server-side HTTP request.

    Always rejects:
      * non-http(s) schemes (file://, javascript:, data:, ftp:, …)
      * URLs without a hostname
      * hostnames that resolve to link-local (169.254.0.0/16 — cloud
        metadata + APIPA), multicast, reserved or the unspecified
        ``0.0.0.0``
      * hostnames that do not resolve at all

    Additionally rejected in strict mode (``SENDSPIN_STRICT_SSRF=1``):
      * loopback (``127.0.0.0/8``, ``::1``)
      * RFC1918 private ranges (except the HA Supervisor proxy net
        when running as an HA addon)

    Note: this is a *pre-flight* check.  Callers that actually fetch the
    URL must also use ``safe_urlopen`` / ``safe_build_opener`` so that
    the connect-time peer IP is verified (defence against DNS rebinding).
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
        if not _is_ip_safe(ip, is_ha_addon=is_ha_addon):
            return False
    return True


def _verify_peer_safe(sock: socket.socket, host: str) -> None:
    """Verify that ``sock``'s connected peer is a safe target.

    Closes the socket and raises :class:`UnsafePeerError` on failure.
    Host ``supervisor`` / ``hassio`` / ``homeassistant`` are always
    accepted when running as an HA add-on (they resolve to the
    Supervisor proxy net by design).
    """
    is_ha_addon = _is_ha_addon_runtime()
    if is_ha_addon and host in _HA_ADDON_INTERNAL_HOSTS:
        return
    try:
        peer = sock.getpeername()
    except OSError as exc:
        sock.close()
        raise UnsafePeerError(f"getpeername() failed: {exc}") from exc
    try:
        ip = ipaddress.ip_address(peer[0])
    except (ValueError, IndexError) as exc:
        sock.close()
        raise UnsafePeerError(f"unparseable peer address: {peer!r}") from exc
    if not _is_ip_safe(ip, is_ha_addon=is_ha_addon):
        sock.close()
        raise UnsafePeerError(f"refusing connection: peer {ip} is disallowed for host {host!r}")


class SafeHTTPConnection(http.client.HTTPConnection):
    """``http.client.HTTPConnection`` that refuses unsafe peer IPs after connect."""

    def connect(self) -> None:  # type: ignore[override]
        super().connect()
        _verify_peer_safe(self.sock, self.host)


class SafeHTTPSConnection(http.client.HTTPSConnection):
    """``http.client.HTTPSConnection`` that refuses unsafe peer IPs after connect."""

    def connect(self) -> None:  # type: ignore[override]
        super().connect()
        _verify_peer_safe(self.sock, self.host)


class _SafeHTTPHandler(_ur.HTTPHandler):
    def http_open(self, req):  # type: ignore[override]
        return self.do_open(SafeHTTPConnection, req)


class _SafeHTTPSHandler(_ur.HTTPSHandler):
    def https_open(self, req):  # type: ignore[override]
        return self.do_open(SafeHTTPSConnection, req)


def safe_build_opener(*extra_handlers: _ur.BaseHandler | type[_ur.BaseHandler]) -> _ur.OpenerDirector:
    """Return an ``OpenerDirector`` whose HTTP/HTTPS connections verify
    the connected peer IP against the SSRF allowlist.

    Extra handlers (e.g., a no-redirect handler) can be passed and will
    be installed alongside the safe HTTP(S) handlers.
    """
    return _ur.build_opener(_SafeHTTPHandler, _SafeHTTPSHandler, *extra_handlers)


def safe_urlopen(url_or_req, *, data=None, timeout: float | None = None):
    """SSRF-safe replacement for ``urllib.request.urlopen``.

    Performs a pre-flight ``is_safe_external_url`` check, then opens the
    request through a handler that re-verifies the peer IP after the
    socket connects (guards against DNS rebinding).
    """
    if isinstance(url_or_req, _ur.Request):
        url = url_or_req.full_url
    else:
        url = str(url_or_req)
    if not is_safe_external_url(url):
        raise UnsafePeerError(f"URL is not safe for server-side fetch: {url!r}")
    opener = safe_build_opener()
    if timeout is None:
        return opener.open(url_or_req, data=data)
    return opener.open(url_or_req, data=data, timeout=timeout)
