"""Who this request is, decided once.

`web/trusted_proxies.py` exists so the auth gate, the ingress middleware and
the login rate-limiter make identical trust decisions — its own docstring
says exactly that, and records that they had previously diverged.  It was
wired into two of the three.

Four separate constructions of the trust set survived alongside it: a
frozenset of defaults, a snapshot taken at import time in `interface.py`
(stale after the first settings save, because `TRUSTED_PROXIES` is writable
through `POST /api/config`), a per-request `load_config()` in `auth.py`, and
an inline one in `api_status.py`.  Two identical forwarded-header resolvers
sat next to them.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.web.request_identity import TrustPolicy

INGRESS = "172.30.33.7"
CLIENT = "192.168.10.55"


def _policy(*entries) -> TrustPolicy:
    return TrustPolicy(entries or ("127.0.0.1", "172.30.32.0/23"))


# ── trust ────────────────────────────────────────────────────────────────


def test_a_literal_address_is_trusted():
    assert _policy("127.0.0.1").is_trusted("127.0.0.1") is True


def test_an_address_inside_a_trusted_range_is_trusted():
    """The Home Assistant ingress peer is never equal to the range itself."""
    assert _policy("172.30.32.0/23").is_trusted(INGRESS) is True


def test_an_address_outside_every_entry_is_not_trusted():
    assert _policy("172.30.32.0/23").is_trusted("10.0.0.9") is False


def test_a_malformed_entry_is_ignored_rather_than_fatal():
    """Operators edit TRUSTED_PROXIES by hand; one typo must not 500 the app."""
    policy = TrustPolicy(["not-an-address", "127.0.0.1"])

    assert policy.is_trusted("127.0.0.1") is True
    assert policy.is_trusted("10.0.0.9") is False


def test_a_hostname_is_not_an_address():
    assert _policy().is_trusted("localhost") is False


def test_an_empty_peer_is_not_trusted():
    assert _policy().is_trusted("") is False


# ── who the client is ────────────────────────────────────────────────────


def test_a_forwarded_header_from_a_trusted_proxy_is_honoured():
    ip = _policy().client_ip(remote_addr=INGRESS, forwarded_for=CLIENT, x_real_ip="")

    assert ip == CLIENT


def test_a_forwarded_header_from_an_untrusted_peer_is_ignored():
    """Otherwise any client could claim to be someone else and evade a limit."""
    ip = _policy().client_ip(remote_addr="10.0.0.9", forwarded_for=CLIENT, x_real_ip="")

    assert ip == "10.0.0.9"


def test_the_client_is_the_rightmost_hop_that_is_not_a_proxy():
    """Only proxies we trust may append, so trust runs right to left."""
    ip = _policy().client_ip(
        remote_addr=INGRESS,
        forwarded_for=f"203.0.113.5, {CLIENT}, {INGRESS}",
        x_real_ip="",
    )

    assert ip == CLIENT


def test_x_real_ip_is_the_fallback_when_there_is_no_forwarded_chain():
    ip = _policy().client_ip(remote_addr=INGRESS, forwarded_for="", x_real_ip=CLIENT)

    assert ip == CLIENT


def test_a_chain_of_nothing_but_proxies_falls_back_to_x_real_ip():
    ip = _policy().client_ip(remote_addr=INGRESS, forwarded_for=f"{INGRESS}, 127.0.0.1", x_real_ip=CLIENT)

    assert ip == CLIENT


def test_an_unknowable_client_is_named_rather_than_empty():
    assert _policy().client_ip(remote_addr="", forwarded_for="", x_real_ip="") == "unknown"


# ── where the trust set comes from ───────────────────────────────────────


def test_config_entries_extend_the_defaults():
    policy = TrustPolicy.from_config({"TRUSTED_PROXIES": ["10.0.0.0/8"]})

    assert policy.is_trusted("10.1.2.3") is True
    assert policy.is_trusted("127.0.0.1") is True, "a configured entry must not drop the defaults"


def test_config_without_the_key_still_trusts_the_defaults():
    policy = TrustPolicy.from_config({})

    assert policy.is_trusted("127.0.0.1") is True
    assert policy.is_trusted(INGRESS) is True


@pytest.mark.parametrize("bad", [None, "", "   ", 42, {"a": 1}, []])
def test_an_unusable_trusted_proxies_value_leaves_the_defaults_standing(bad):
    """Including an empty list: the defaults are not something config can drop.

    Losing the ingress network would lock the Home Assistant panel out of the
    bridge whose config is edited through that very panel.
    """
    policy = TrustPolicy.from_config({"TRUSTED_PROXIES": bad})

    assert policy.is_trusted("127.0.0.1") is True
    assert policy.is_trusted(INGRESS) is True


def test_a_comma_separated_string_is_accepted():
    """The HA addon translator writes this shape."""
    policy = TrustPolicy.from_config({"TRUSTED_PROXIES": "10.0.0.0/8"})

    assert policy.is_trusted("10.1.2.3") is True


# ── the whole point: one answer per request ──────────────────────────────


def test_two_policies_built_from_the_same_config_agree():
    config = {"TRUSTED_PROXIES": ["172.30.32.0/23"]}

    first = TrustPolicy.from_config(config)
    second = TrustPolicy.from_config(config)

    assert first.is_trusted(INGRESS) == second.is_trusted(INGRESS)
    assert first.client_ip(INGRESS, CLIENT, "") == second.client_ip(INGRESS, CLIENT, "")
