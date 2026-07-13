"""Canonical CIDR-aware trusted-proxy matching.

This lives in a dependency-free module so the auth gate, the ingress
middleware (``web/interface.py``) and the login rate-limiter
(``web/routes/auth.py``) all make identical trust decisions.  They
previously diverged: ``interface.py`` matched trusted peers by exact
string, silently ignoring any CIDR entry in the operator-managed
``TRUSTED_PROXIES`` config key.

Entries may be literal IPs (``127.0.0.1``) or CIDR networks
(``172.30.32.0/23`` — the whole HAOS ``hassio`` Docker network, where
Supervisor and every vetted addon container live).
"""

from __future__ import annotations

import ipaddress

#: Networks the bridge implicitly trusts as reverse proxies / ingress peers.
TRUSTED_PROXY_DEFAULTS: frozenset[str] = frozenset({"127.0.0.1", "::1", "172.30.32.0/23"})


def parse_trusted_entry(entry: str):
    """Return an ``ip_network`` for *entry* (single IP or CIDR), or None.

    Invalid entries are dropped rather than crashing the request, since
    they may come from the operator-managed ``TRUSTED_PROXIES`` key.
    """
    entry = (entry or "").strip()
    if not entry:
        return None
    try:
        # ``strict=False`` accepts both a CIDR with host bits and a bare IP.
        return ipaddress.ip_network(entry, strict=False)
    except ValueError:
        return None


def peer_in_trust_set(peer: str, trust_set) -> bool:
    """True when *peer* falls inside any IP / CIDR entry in *trust_set*."""
    if not peer:
        return False
    try:
        ip_obj = ipaddress.ip_address(peer)
    except ValueError:
        return False
    for entry in trust_set:
        net = parse_trusted_entry(entry)
        if net is not None and ip_obj in net:
            return True
    return False


def resolve_client_ip(remote_addr: str, forwarded_for: str, x_real_ip: str, trust_set) -> str:
    """Resolve the real client IP for rate-limiting / logging.

    ``X-Forwarded-For`` is only honoured when the immediate peer
    (*remote_addr*) is itself a trusted proxy — otherwise any client can
    spoof the header and evade a per-IP limit.  Among the forwarded hops the
    real client is the *rightmost* one that is not a trusted proxy (only
    proxies we trust may append to XFF).  Falls back to ``x_real_ip`` and
    finally to the peer address.
    """
    peer = (remote_addr or "").strip()
    if peer and peer_in_trust_set(peer, trust_set):
        hops = [p.strip() for p in (forwarded_for or "").split(",") if p.strip()]
        for hop in reversed(hops):
            if not peer_in_trust_set(hop, trust_set):
                return hop
        real = (x_real_ip or "").strip()
        if real:
            return real
    return peer or "unknown"
