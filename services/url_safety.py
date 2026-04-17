"""Reject server-side fetches to private/internal networks.

Used to guard route handlers that resolve an arbitrary user-supplied URL
and then perform a server-side HTTP request to it — preventing SSRF
against internal services (127.0.0.1, 169.254.169.254, RFC1918 ranges).

In HA-addon mode (when SUPERVISOR_TOKEN is set) a small allowlist of
internal hostnames and the Supervisor proxy network (172.30.32.0/23) is
permitted so legitimate MA / HA discovery keeps working.

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


def _is_ip_safe(ip: ipaddress.IPv4Address | ipaddress.IPv6Address, *, is_ha_addon: bool) -> bool:
    if ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
        return False
    if ip.is_private:
        return bool(is_ha_addon and ip in _HA_ADDON_PROXY_NET)
    return True


def is_safe_external_url(url: str) -> bool:
    """Return True if ``url`` is a safe target for a server-side HTTP request.

    Rejects:
      * non-http(s) schemes (file://, javascript:, data:, ftp:, …)
      * URLs without a hostname
      * hostnames that resolve to loopback/link-local/reserved/multicast
      * hostnames that resolve to private RFC1918 ranges
        (except the HA Supervisor proxy net when running as an HA addon)
      * hostnames that do not resolve at all

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
