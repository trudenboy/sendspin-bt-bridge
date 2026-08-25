"""Who a request is, decided once.

Three parts of the web layer need the same answer: the auth gate, the ingress
middleware and the login rate-limiter.  ``web/trusted_proxies.py`` exists
because they had diverged before — but it supplied only the *matcher*, not
the data, so four separate constructions of the trust set grew around it: a
frozenset of defaults, a snapshot taken at import time (stale after the
first settings save, since ``TRUSTED_PROXIES`` is writable through the
config API), a ``load_config()`` on every call, and one built inline.  Two
identical forwarded-header resolvers sat alongside them.

A :class:`TrustPolicy` carries both halves.  Built once per request from the
live config, it gives every consumer the same answer for the life of that
request — which is the property the divergence kept breaking.
"""

from __future__ import annotations

import ipaddress
from typing import TYPE_CHECKING

from sendspin_bridge.config import load_config
from sendspin_bridge.web.trusted_proxies import (
    TRUSTED_PROXY_DEFAULTS,
    parse_trusted_entry,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["TrustPolicy", "current_trust_policy", "trust_policy_for_environ"]


class TrustPolicy:
    """Which peers may speak for someone else, and who that someone is."""

    def __init__(self, trust_set: Iterable[str]):
        self._entries = tuple(str(entry).strip() for entry in trust_set if str(entry).strip())
        # Parsed once: a request may ask about several hops.
        self._networks = tuple(net for net in (parse_trusted_entry(e) for e in self._entries) if net is not None)

    @classmethod
    def from_config(cls, config) -> TrustPolicy:
        """Build from the defaults plus whatever ``TRUSTED_PROXIES`` adds.

        The defaults — loopback and the whole hassio ``172.30.32.0/23``
        network — are always trusted, and configured entries extend them.
        They are not replaceable on purpose: dropping the ingress network
        would lock the Home Assistant panel out of its own bridge, and the
        config that did it is edited *through* that panel.
        """
        raw = (config or {}).get("TRUSTED_PROXIES")
        if isinstance(raw, str):
            raw = [part for part in raw.split(",") if part.strip()]
        extra = raw if isinstance(raw, (list, tuple, set, frozenset)) else ()
        entries = set(TRUSTED_PROXY_DEFAULTS)
        entries.update(str(value).strip() for value in extra if str(value).strip())
        return cls(entries)

    @property
    def entries(self) -> tuple[str, ...]:
        """The configured entries, as written."""
        return self._entries

    def is_trusted(self, peer: str) -> bool:
        """True when *peer* falls inside any trusted address or range."""
        if not peer:
            return False
        try:
            address = ipaddress.ip_address(peer.strip())
        except ValueError:
            return False
        return any(address in net for net in self._networks)

    def client_ip(self, remote_addr: str, forwarded_for: str = "", x_real_ip: str = "") -> str:
        """The address to attribute this request to.

        ``X-Forwarded-For`` counts only when the immediate peer is itself
        trusted — otherwise any client could name someone else and evade a
        per-address limit.  Within the chain the client is the rightmost hop
        that is not a proxy, because only proxies we trust may append.
        """
        peer = (remote_addr or "").strip()
        if peer and self.is_trusted(peer):
            hops = [hop.strip() for hop in (forwarded_for or "").split(",") if hop.strip()]
            for hop in reversed(hops):
                if not self.is_trusted(hop):
                    return hop
            real = (x_real_ip or "").strip()
            if real:
                return real
        return peer or "unknown"


#: Where the per-request policy is cached.  The WSGI environ rather than
#: ``flask.g``: the ingress middleware runs before Flask pushes a request
#: context and still has to make a trust decision, and the environ is the one
#: object both it and the request see.
_ENVIRON_KEY = "sendspin.trust_policy"


def trust_policy_for_environ(environ: dict) -> TrustPolicy:
    """The policy for the request this environ belongs to, built once.

    Usable before a request context exists, which is where the ingress
    middleware asks.
    """
    policy = environ.get(_ENVIRON_KEY)
    if policy is None:
        policy = TrustPolicy.from_config(load_config())
        environ[_ENVIRON_KEY] = policy
    return policy


def current_trust_policy() -> TrustPolicy:
    """The trust policy for the request in flight.

    Built once per request from the live config and cached on its environ, so
    every consumer gets the same answer for that request's lifetime — the
    property the import-time snapshot kept breaking after a settings save.
    Outside a request context (startup, background tasks) it is built fresh.
    """
    try:
        from flask import has_request_context, request
    except ImportError:  # pragma: no cover — Flask is a hard dependency
        return TrustPolicy.from_config(load_config())

    if not has_request_context():
        return TrustPolicy.from_config(load_config())

    return trust_policy_for_environ(request.environ)
