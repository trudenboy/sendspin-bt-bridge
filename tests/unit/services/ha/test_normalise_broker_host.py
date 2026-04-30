"""Tests for ``normalise_broker_host``.

Operators paste broker hosts from heterogeneous sources — environment
variables (``mqtt://host:1883``), MA config (``mqtts://broker:8883``),
or just the bare hostname.  The helper accepts all of them and returns
a canonical shape so the broker host field can stay free-form without
the bridge silently sending broken connection strings to MQTT clients.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.services.ha.ha_addon import normalise_broker_host


@pytest.mark.parametrize(
    ("raw", "expected_host", "expected_port", "expected_tls"),
    [
        # Bare hostnames / IPs — pass through.
        ("homeassistant.local", "homeassistant.local", None, None),
        ("192.168.10.10", "192.168.10.10", None, None),
        ("auto", "auto", None, None),
        # host:port without scheme — port extracted, no TLS signal.
        ("192.168.10.10:1883", "192.168.10.10", 1883, None),
        ("broker.example.com:8883", "broker.example.com", 8883, None),
        # Plain mqtt:// scheme — TLS=False, port may or may not be there.
        ("mqtt://192.168.10.10", "192.168.10.10", None, False),
        ("mqtt://192.168.10.10:1883", "192.168.10.10", 1883, False),
        # mqtts:// → TLS=True
        ("mqtts://broker.example.com", "broker.example.com", None, True),
        ("mqtts://broker.example.com:8883", "broker.example.com", 8883, True),
        # Other TLS scheme aliases.
        ("ssl://broker.example.com:8883", "broker.example.com", 8883, True),
        ("tls://broker.example.com:8883", "broker.example.com", 8883, True),
        ("wss://broker.example.com", "broker.example.com", None, True),
        # http/https fall through too — same logic as MA URL.
        ("https://broker.example.com:443", "broker.example.com", 443, True),
        ("http://broker.example.com:8080", "broker.example.com", 8080, False),
        # Trailing path / query stripped.
        ("mqtt://192.168.10.10:1883/", "192.168.10.10", 1883, False),
        ("mqtts://broker/path?x=1", "broker", None, True),
        # Whitespace tolerated.
        ("  mqtt://192.168.10.10:1883  ", "192.168.10.10", 1883, False),
    ],
)
def test_normalise_strips_scheme_and_extracts_signals(raw, expected_host, expected_port, expected_tls):
    out = normalise_broker_host(raw)
    assert out["host"] == expected_host
    assert out["port"] == expected_port
    assert out["tls"] == expected_tls


def test_empty_input_returns_empty_host():
    out = normalise_broker_host("")
    assert out == {"host": "", "port": None, "tls": None, "stripped": False}


def test_whitespace_only_treated_as_empty():
    out = normalise_broker_host("   ")
    assert out["host"] == ""


def test_unknown_scheme_left_alone():
    """If it's not a scheme we recognise, don't mangle the input — let
    the operator see their typo rather than silently dropping it."""
    out = normalise_broker_host("foobar://broker.example.com")
    assert out["host"] == "foobar://broker.example.com"
    assert out["tls"] is None


def test_ipv6_in_brackets_with_port():
    out = normalise_broker_host("mqtt://[::1]:1883")
    assert out["host"] == "::1"
    assert out["port"] == 1883
    assert out["tls"] is False


def test_bare_hostname_preserves_stripped_false():
    """No scheme + no port = nothing was stripped."""
    out = normalise_broker_host("homeassistant.local")
    assert out["stripped"] is False


def test_scheme_present_marks_stripped():
    out = normalise_broker_host("mqtt://192.168.10.10")
    assert out["stripped"] is True


def test_port_extraction_does_not_misinterpret_bare_ipv6():
    """Bare IPv6 like ``::1`` has multiple colons → not extracted as
    ``host:port`` (must be in brackets to be unambiguous)."""
    out = normalise_broker_host("::1")
    assert out["host"] == "::1"
    assert out["port"] is None
