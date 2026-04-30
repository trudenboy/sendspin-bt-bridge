"""Tests for ``derive_mqtt_broker_from_ma_url``.

The helper exists so a standalone bridge (no Supervisor) can offer a
sensible default Mosquitto host when the operator has already
configured Music Assistant — the common topology where MA runs as an
HA add-on and the bridge talks to MA from another machine.
"""

from __future__ import annotations

import pytest

from sendspin_bridge.services.ha.ha_addon import derive_mqtt_broker_from_ma_url


@pytest.mark.parametrize(
    ("url", "expected_host"),
    [
        ("http://192.168.10.10:8095", "192.168.10.10"),
        ("http://homeassistant.local:8095", "homeassistant.local"),
        ("https://ma.example.com:8443", "ma.example.com"),
        # No port — host still extracted.
        ("http://192.168.10.10/", "192.168.10.10"),
        # Trailing whitespace tolerated.
        ("  http://192.168.10.10:8095  ", "192.168.10.10"),
    ],
)
def test_derive_returns_ma_host_for_realistic_inputs(url, expected_host):
    result = derive_mqtt_broker_from_ma_url(url)
    assert result is not None
    assert result["host"] == expected_host
    assert result["port"] == 1883
    assert result["username"] == ""
    assert result["password"] == ""
    assert result["ssl"] is False
    assert result["source"] == "ma_url"


@pytest.mark.parametrize("url", ["", "   ", "not-a-url", "://no-scheme"])
def test_derive_returns_none_for_unparseable_input(url):
    assert derive_mqtt_broker_from_ma_url(url) is None
