"""Unit tests for services.url_safety.is_safe_external_url."""

from __future__ import annotations

import socket

import pytest

from sendspin_bridge.services.infrastructure import url_safety
from sendspin_bridge.services.infrastructure.url_safety import is_safe_external_url


def _fake_getaddrinfo(addr_map: dict[str, list[str]]):
    """Return a replacement socket.getaddrinfo that serves from a dict.

    addr_map: hostname -> list of IP strings.  Missing host → raise gaierror.
    """

    def _resolver(host, port, *args, **kwargs):
        if host not in addr_map:
            raise socket.gaierror(f"unknown host {host}")
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip, 0)) for ip in addr_map[host]]

    return _resolver


class TestSchemes:
    def test_https_is_allowed(self, monkeypatch):
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"ex.com": ["93.184.216.34"]}))
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        assert is_safe_external_url("https://ex.com/path") is True

    def test_http_is_allowed(self, monkeypatch):
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"ex.com": ["93.184.216.34"]}))
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        assert is_safe_external_url("http://ex.com") is True

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/plain,hi",
            "ftp://ex.com/",
            "gopher://ex.com:70/",
            "",
        ],
    )
    def test_non_http_rejected(self, url):
        assert is_safe_external_url(url) is False

    def test_empty_hostname_rejected(self):
        assert is_safe_external_url("http:///path") is False


class TestAlwaysRejected:
    """Categories that are rejected in *both* default and strict mode."""

    @pytest.mark.parametrize(
        "ip",
        [
            "169.254.169.254",  # AWS/GCP/Azure metadata (link-local)
            "169.254.1.1",
            "224.0.0.1",  # multicast
            "239.255.255.250",  # SSDP multicast
            "0.0.0.0",  # unspecified
        ],
    )
    def test_dangerous_addresses_rejected(self, monkeypatch, ip):
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"internal": [ip]}))
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)
        assert is_safe_external_url("http://internal/") is False

    def test_unresolvable_host_rejected(self, monkeypatch):
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({}))
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        assert is_safe_external_url("http://nowhere.invalid") is False


class TestLanPermissiveDefault:
    """Loopback + RFC1918 are allowed by default (bridge lives on a LAN)."""

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "127.1.2.3",
            "::1",
            "10.0.0.1",
            "192.168.1.1",
            "172.16.0.1",
        ],
    )
    def test_lan_and_loopback_allowed_by_default(self, monkeypatch, ip):
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"home": [ip]}))
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)
        assert is_safe_external_url("http://home/") is True

    def test_literal_loopback_allowed_by_default(self, monkeypatch):
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"127.0.0.1": ["127.0.0.1"]}))
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)
        assert is_safe_external_url("http://127.0.0.1:8095") is True


class TestStrictMode:
    """Strict mode (SENDSPIN_STRICT_SSRF=1) also blocks loopback + RFC1918."""

    @pytest.mark.parametrize(
        "ip",
        [
            "127.0.0.1",
            "127.1.2.3",
            "::1",
            "10.0.0.1",
            "10.255.255.254",
            "172.16.0.1",
            "192.168.1.1",
        ],
    )
    def test_internal_addresses_rejected(self, monkeypatch, ip):
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"internal": [ip]}))
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")
        assert is_safe_external_url("http://internal/") is False

    def test_literal_loopback_rejected(self, monkeypatch):
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"127.0.0.1": ["127.0.0.1"]}))
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")
        assert is_safe_external_url("http://127.0.0.1:8095") is False

    def test_link_local_still_rejected_in_strict(self, monkeypatch):
        """Link-local stays blocked regardless of mode."""
        monkeypatch.setattr(
            url_safety.socket,
            "getaddrinfo",
            _fake_getaddrinfo({"metadata": ["169.254.169.254"]}),
        )
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")
        assert is_safe_external_url("http://metadata/") is False


class TestHomeAssistantAddon:
    def test_supervisor_hostname_allowed_in_addon(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        # No DNS lookup needed for allowlisted hostnames
        assert is_safe_external_url("http://supervisor/info") is True

    def test_hassio_hostname_allowed_in_addon(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        assert is_safe_external_url("http://hassio/addons") is True

    def test_homeassistant_hostname_allowed_in_addon(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        assert is_safe_external_url("http://homeassistant:8123/") is True

    def test_supervisor_hostname_rejected_when_not_addon_strict(self, monkeypatch):
        """Outside addon runtime, ``supervisor`` isn't special — and strict
        mode then rejects its 172.30.32.2 resolution."""
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"supervisor": ["172.30.32.2"]}))
        assert is_safe_external_url("http://supervisor/") is False

    def test_supervisor_proxy_net_allowed_in_addon(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"proxy.local": ["172.30.32.5"]}))
        assert is_safe_external_url("http://proxy.local/") is True

    def test_other_rfc1918_rejected_in_addon_strict(self, monkeypatch):
        """Even inside addon runtime, strict mode blocks non-Supervisor RFC1918."""
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"lan.local": ["192.168.1.10"]}))
        assert is_safe_external_url("http://lan.local/") is False

    def test_loopback_rejected_in_addon_strict(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"mine": ["127.0.0.1"]}))
        assert is_safe_external_url("http://mine/") is False


class TestMultiAnswer:
    def test_rejects_if_any_answer_is_private_in_strict(self, monkeypatch):
        """DNS rebinding defence (strict): reject if any resolved IP is private."""
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")
        monkeypatch.setattr(
            url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"mixed": ["93.184.216.34", "10.0.0.1"]})
        )
        assert is_safe_external_url("http://mixed/") is False

    def test_rejects_if_any_answer_is_link_local(self, monkeypatch):
        """Link-local is always blocked — even one bad answer is enough."""
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)
        monkeypatch.setattr(
            url_safety.socket,
            "getaddrinfo",
            _fake_getaddrinfo({"mixed": ["93.184.216.34", "169.254.169.254"]}),
        )
        assert is_safe_external_url("http://mixed/") is False
