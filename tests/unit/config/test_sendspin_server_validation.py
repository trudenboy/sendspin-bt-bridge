"""Tests for strict SENDSPIN_SERVER validation (added in #291 follow-up)."""

from __future__ import annotations

import pytest

from sendspin_bridge.services.infrastructure.config_validation import (
    is_valid_sendspin_host,
    resolve_sendspin_url,
    validate_sendspin_server_format,
    validate_uploaded_config,
)

# ---------------------------------------------------------------------------
# validate_sendspin_server_format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "auto",
        "AUTO",
        "discover",
        "192.168.1.11",
        "10.0.0.1",
        "music-assistant.local",
        "ma",
        "  192.168.1.11  ",  # trimmed
    ],
)
def test_validate_sendspin_server_format_accepts_valid(value):
    assert validate_sendspin_server_format(value) is None


@pytest.mark.parametrize(
    "value,fragment",
    [
        ("http://192.168.1.11", "scheme"),
        ("https://192.168.1.11", "scheme"),
        ("ws://192.168.1.11", "scheme"),
        ("HTTP://192.168.1.11", "scheme"),
    ],
)
def test_validate_sendspin_server_format_rejects_scheme(value, fragment):
    issue = validate_sendspin_server_format(value)
    assert issue is not None
    assert issue.field == "SENDSPIN_SERVER"
    assert fragment in issue.message.lower()


@pytest.mark.parametrize("value", ["192.168.1.11:8095", "ma.local:8927"])
def test_validate_sendspin_server_format_rejects_port_suffix(value):
    issue = validate_sendspin_server_format(value)
    assert issue is not None
    assert issue.field == "SENDSPIN_SERVER"
    assert "port" in issue.message.lower()


@pytest.mark.parametrize("value", ["192.168.1.11/", "ma/sendspin", "host/path"])
def test_validate_sendspin_server_format_rejects_slash(value):
    issue = validate_sendspin_server_format(value)
    assert issue is not None
    assert issue.field == "SENDSPIN_SERVER"
    assert "slash" in issue.message.lower() or "path" in issue.message.lower()


@pytest.mark.parametrize("value", ["192 168 1 11", "host name", "ma\tserver"])
def test_validate_sendspin_server_format_rejects_whitespace(value):
    issue = validate_sendspin_server_format(value)
    assert issue is not None
    assert issue.field == "SENDSPIN_SERVER"
    assert "whitespace" in issue.message.lower()


def test_validate_sendspin_server_format_compound_url_is_rejected():
    """Regression for issue #291 — full URL pasted into SENDSPIN_SERVER."""
    issue = validate_sendspin_server_format("http://192.168.1.11:8095")
    assert issue is not None
    # The scheme check fires first; users see 'scheme' first, but any of the
    # three rules is sufficient — the user can self-correct after the first fix.
    assert issue.field == "SENDSPIN_SERVER"


# ---------------------------------------------------------------------------
# is_valid_sendspin_host
# ---------------------------------------------------------------------------


def test_is_valid_sendspin_host_returns_bool():
    assert is_valid_sendspin_host("192.168.1.11") is True
    assert is_valid_sendspin_host("auto") is True
    assert is_valid_sendspin_host("") is True
    assert is_valid_sendspin_host(None) is True
    assert is_valid_sendspin_host("http://x") is False
    assert is_valid_sendspin_host("x:80") is False


# ---------------------------------------------------------------------------
# resolve_sendspin_url
# ---------------------------------------------------------------------------


def test_resolve_sendspin_url_builds_canonical_form():
    assert resolve_sendspin_url("192.168.1.11", 8927) == "ws://192.168.1.11:8927/sendspin"


def test_resolve_sendspin_url_strips_whitespace():
    assert resolve_sendspin_url("  192.168.1.11  ", 8927) == "ws://192.168.1.11:8927/sendspin"


@pytest.mark.parametrize("value", [None, "", "auto", "AUTO", "discover"])
def test_resolve_sendspin_url_returns_none_for_auto_modes(value):
    assert resolve_sendspin_url(value, 8927) is None


# ---------------------------------------------------------------------------
# validate_uploaded_config integration
# ---------------------------------------------------------------------------


def test_validate_uploaded_config_rejects_malformed_sendspin_server():
    """The full-URL paste pattern from issue #291 must fail validation."""
    result = validate_uploaded_config(
        {
            "CONFIG_SCHEMA_VERSION": 1,
            "SENDSPIN_SERVER": "http://192.168.1.11:8095",
            "SENDSPIN_PORT": 8927,
            "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}],
        }
    )
    assert result.is_valid is False
    server_errors = [e for e in result.errors if e.field == "SENDSPIN_SERVER"]
    assert len(server_errors) == 1


def test_validate_uploaded_config_accepts_bare_ip_sendspin_server():
    result = validate_uploaded_config(
        {
            "CONFIG_SCHEMA_VERSION": 1,
            "SENDSPIN_SERVER": "192.168.1.11",
            "SENDSPIN_PORT": 8927,
            "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}],
        }
    )
    assert result.is_valid is True
    assert result.normalized_config["SENDSPIN_SERVER"] == "192.168.1.11"


def test_validate_uploaded_config_accepts_auto_sendspin_server():
    result = validate_uploaded_config(
        {
            "CONFIG_SCHEMA_VERSION": 1,
            "SENDSPIN_SERVER": "auto",
            "SENDSPIN_PORT": 8927,
            "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}],
        }
    )
    assert result.is_valid is True
    assert result.normalized_config["SENDSPIN_SERVER"] == "auto"


def test_validate_uploaded_config_does_not_coerce_malformed_value():
    """Strict mode — we must not silently strip scheme/port."""
    result = validate_uploaded_config(
        {
            "CONFIG_SCHEMA_VERSION": 1,
            "SENDSPIN_SERVER": "http://192.168.1.11:8095",
            "SENDSPIN_PORT": 8927,
            "BLUETOOTH_DEVICES": [{"mac": "AA:BB:CC:DD:EE:FF"}],
        }
    )
    # The raw value is preserved so the user can see what they entered.
    assert result.normalized_config["SENDSPIN_SERVER"] == "http://192.168.1.11:8095"
