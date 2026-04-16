"""Unit tests for services.url_safety.is_safe_external_url."""

from __future__ import annotations

import socket

import pytest

from services import url_safety
from services.url_safety import is_safe_external_url


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


class TestPrivateAndLoopback:
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
            "169.254.169.254",  # AWS metadata, link-local
            "224.0.0.1",  # multicast
            "0.0.0.0",  # unspecified
        ],
    )
    def test_internal_addresses_rejected(self, monkeypatch, ip):
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"internal": [ip]}))
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        assert is_safe_external_url("http://internal/") is False

    def test_literal_loopback_rejected(self, monkeypatch):
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"127.0.0.1": ["127.0.0.1"]}))
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        assert is_safe_external_url("http://127.0.0.1:8095") is False

    def test_unresolvable_host_rejected(self, monkeypatch):
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({}))
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        assert is_safe_external_url("http://nowhere.invalid") is False


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

    def test_supervisor_hostname_rejected_when_not_addon(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"supervisor": ["172.30.32.2"]}))
        assert is_safe_external_url("http://supervisor/") is False

    def test_supervisor_proxy_net_allowed_in_addon(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"proxy.local": ["172.30.32.5"]}))
        assert is_safe_external_url("http://proxy.local/") is True

    def test_other_rfc1918_still_rejected_in_addon(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"lan.local": ["192.168.1.10"]}))
        assert is_safe_external_url("http://lan.local/") is False

    def test_loopback_still_rejected_in_addon(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        monkeypatch.setattr(url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"mine": ["127.0.0.1"]}))
        assert is_safe_external_url("http://mine/") is False


class TestMultiAnswer:
    def test_rejects_if_any_answer_is_private(self, monkeypatch):
        """DNS rebinding defence: reject if any resolved IP is private."""
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setattr(
            url_safety.socket, "getaddrinfo", _fake_getaddrinfo({"mixed": ["93.184.216.34", "10.0.0.1"]})
        )
        assert is_safe_external_url("http://mixed/") is False
