"""Tests for the connect-time peer-IP verification in services.url_safety.

Validators that only resolve DNS once are vulnerable to DNS rebinding (the
attacker returns a public IP on the first resolution and a private IP on
the second).  The safe HTTP(S) connection wrappers re-check the actual
connected peer after the socket is established.

These tests use a fake ``socket`` whose ``getpeername`` we control to
verify the pass/fail behaviour without opening real sockets.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from services import url_safety
from services.url_safety import (
    SafeHTTPConnection,
    UnsafePeerError,
    _verify_peer_safe,
    safe_urlopen,
)


class _FakeSock:
    def __init__(self, peer_ip: str) -> None:
        self._peer = (peer_ip, 0)
        self.closed = False

    def getpeername(self):
        return self._peer

    def close(self):
        self.closed = True


class TestVerifyPeerSafeAlwaysBlocks:
    """Categories blocked regardless of strict-mode setting."""

    def test_accepts_public_ipv4(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)
        sock = _FakeSock("93.184.216.34")
        _verify_peer_safe(sock, "example.com")
        assert sock.closed is False

    @pytest.mark.parametrize(
        "ip",
        [
            "169.254.169.254",  # cloud metadata
            "169.254.1.1",
            "224.0.0.1",  # multicast
            "239.255.255.250",  # SSDP multicast
            "0.0.0.0",  # unspecified
        ],
    )
    def test_always_rejects_dangerous_addresses(self, monkeypatch, ip):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)
        sock = _FakeSock(ip)
        with pytest.raises(UnsafePeerError):
            _verify_peer_safe(sock, "attacker.example")
        assert sock.closed is True


class TestVerifyPeerSafeStrictMode:
    """Loopback + RFC1918 are only rejected when SENDSPIN_STRICT_SSRF=1."""

    @pytest.mark.parametrize(
        "ip",
        ["127.0.0.1", "::1", "10.0.0.5", "172.16.5.5", "192.168.1.1"],
    )
    def test_allowed_in_default_mode(self, monkeypatch, ip):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)
        sock = _FakeSock(ip)
        _verify_peer_safe(sock, "lan.host")
        assert sock.closed is False

    @pytest.mark.parametrize(
        "ip",
        ["127.0.0.1", "::1", "10.0.0.5", "172.16.5.5", "192.168.1.1"],
    )
    def test_rejected_in_strict_mode(self, monkeypatch, ip):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")
        sock = _FakeSock(ip)
        with pytest.raises(UnsafePeerError):
            _verify_peer_safe(sock, "attacker.example")
        assert sock.closed is True


class TestVerifyPeerSafeHaAddon:
    def test_ha_addon_supervisor_hostname_skips_check(self, monkeypatch):
        """``supervisor``/``hassio``/``homeassistant`` pass-through in addon mode."""
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")
        sock = _FakeSock("172.30.32.2")
        _verify_peer_safe(sock, "supervisor")
        assert sock.closed is False

    def test_ha_addon_proxy_net_allowed_in_strict(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")
        sock = _FakeSock("172.30.32.17")
        _verify_peer_safe(sock, "proxy.local")
        assert sock.closed is False

    def test_ha_addon_other_private_rejected_in_strict(self, monkeypatch):
        """Strict mode blocks non-Supervisor RFC1918 even inside addon."""
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")
        sock = _FakeSock("192.168.1.100")
        with pytest.raises(UnsafePeerError):
            _verify_peer_safe(sock, "lan.local")


class TestSafeHTTPConnectionDnsRebinding:
    def test_connect_rejects_link_local_peer_even_if_validator_passed(self, monkeypatch):
        """TOCTOU defence (always-on category): validator saw a public IP,
        connect lands on link-local cloud-metadata address."""
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)

        fake_sock = _FakeSock("169.254.169.254")

        def _fake_super_connect(self):
            self.sock = fake_sock

        with patch.object(url_safety.http.client.HTTPConnection, "connect", _fake_super_connect):
            conn = SafeHTTPConnection("rebinder.example", 80, timeout=1)
            with pytest.raises(UnsafePeerError):
                conn.connect()

        assert fake_sock.closed is True

    def test_connect_rejects_loopback_in_strict(self, monkeypatch):
        """TOCTOU defence (strict-only): loopback rebinding blocked when
        the operator opts into strict mode."""
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.setenv("SENDSPIN_STRICT_SSRF", "1")

        fake_sock = _FakeSock("127.0.0.1")

        def _fake_super_connect(self):
            self.sock = fake_sock

        with patch.object(url_safety.http.client.HTTPConnection, "connect", _fake_super_connect):
            conn = SafeHTTPConnection("rebinder.example", 80, timeout=1)
            with pytest.raises(UnsafePeerError):
                conn.connect()

        assert fake_sock.closed is True


class TestSafeUrlopenPreFlight:
    def test_rejects_metadata_url_before_any_network_call(self, monkeypatch):
        """Link-local is always unsafe — no opener should be built."""
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        monkeypatch.delenv("SENDSPIN_STRICT_SSRF", raising=False)

        def _should_not_be_called(*args, **kwargs):  # pragma: no cover - guard
            raise AssertionError("safe_urlopen must not open an opener for unsafe URLs")

        with (
            patch.object(url_safety, "safe_build_opener", _should_not_be_called),
            pytest.raises(UnsafePeerError),
        ):
            safe_urlopen("http://169.254.169.254/latest/meta-data/")

    def test_rejects_non_http_scheme(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        with pytest.raises(UnsafePeerError):
            safe_urlopen("file:///etc/passwd")
