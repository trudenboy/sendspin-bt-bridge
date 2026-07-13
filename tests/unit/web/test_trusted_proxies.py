"""Unit tests for ``web/trusted_proxies.py`` client-IP resolution."""

from __future__ import annotations

from sendspin_bridge.web.trusted_proxies import (
    TRUSTED_PROXY_DEFAULTS,
    peer_in_trust_set,
    resolve_client_ip,
)


def _trust():
    return set(TRUSTED_PROXY_DEFAULTS)


def test_untrusted_peer_forwarded_header_is_ignored():
    # A direct client that sets its own XFF must not be able to spoof the
    # rate-limit bucket — its real peer address wins.
    assert resolve_client_ip("8.8.8.8", "1.2.3.4", "", _trust()) == "8.8.8.8"


def test_trusted_proxy_forwards_real_client():
    assert resolve_client_ip("127.0.0.1", "203.0.113.9, 127.0.0.1", "", _trust()) == "203.0.113.9"


def test_spoofed_leftmost_hop_is_defeated_by_rightmost_untrusted():
    # Client injects an extra leftmost hop; the rightmost *untrusted* hop is
    # the true client.
    got = resolve_client_ip("127.0.0.1", "9.9.9.9, 203.0.113.9, 127.0.0.1", "", _trust())
    assert got == "203.0.113.9"


def test_cidr_proxy_in_hassio_network_is_trusted():
    # A hassio-network addon container (172.30.32.0/23) counts as a trusted
    # forwarding proxy.
    assert peer_in_trust_set("172.30.33.5", _trust()) is True
    assert resolve_client_ip("172.30.33.5", "203.0.113.9", "", _trust()) == "203.0.113.9"


def test_falls_back_to_x_real_ip_then_peer():
    # Trusted peer, XFF only lists trusted hops → use X-Real-IP.
    assert resolve_client_ip("127.0.0.1", "127.0.0.1", "198.51.100.4", _trust()) == "198.51.100.4"
    # Nothing usable → peer, then "unknown".
    assert resolve_client_ip("127.0.0.1", "127.0.0.1", "", _trust()) == "127.0.0.1"
    assert resolve_client_ip("", "", "", _trust()) == "unknown"
