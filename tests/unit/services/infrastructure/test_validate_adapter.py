"""Tests for validate_adapter – guards against command injection via adapter param."""

import pytest

from sendspin_bridge.web.routes._helpers import validate_adapter

# ---------------------------------------------------------------------------
# Valid inputs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("hci0", "hci0"),
        ("hci1", "hci1"),
        ("hci12", "hci12"),
        ("C0:FB:F9:62:D6:9D", "C0:FB:F9:62:D6:9D"),
        ("c0:fb:f9:62:d6:9d", "c0:fb:f9:62:d6:9d"),
        ("  hci0  ", "hci0"),  # whitespace stripped
    ],
)
def test_valid_adapter(value, expected):
    assert validate_adapter(value) == expected


# ---------------------------------------------------------------------------
# None / empty → empty string (no adapter selection)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, "", "   ", "\n"])
def test_none_or_empty_returns_empty(value):
    assert validate_adapter(value) == ""


# ---------------------------------------------------------------------------
# Injection attempts → ValueError
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "hci0\npower off",
        "hci0\nremove AA:BB:CC:DD:EE:FF",
        "hci0; rm -rf /",
        "hci0\r\npower off",
        "../../etc/passwd",
        "hci0 && echo pwned",
        "select hci0",
        "hci",  # missing digit
        "hci-1",
        "hci0 hci1",
        "XX:XX:XX:XX:XX:XX",
    ],
)
def test_injection_rejected(value):
    with pytest.raises(ValueError, match="Invalid adapter identifier"):
        validate_adapter(value)
