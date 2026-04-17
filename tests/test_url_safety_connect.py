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


class TestVerifyPeerSafe:
    def test_accepts_public_ipv4(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        sock = _FakeSock("93.184.216.34")
        _verify_peer_safe(sock, "example.com")
        assert sock.closed is False

    def test_rejects_loopback_and_closes_socket(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        sock = _FakeSock("127.0.0.1")
        with pytest.raises(UnsafePeerError):
            _verify_peer_safe(sock, "attacker.example")
        assert sock.closed is True

    @pytest.mark.parametrize(
        "ip",
        [
            "10.0.0.5",
            "172.16.5.5",
            "192.168.1.1",
            "169.254.169.254",
            "224.0.0.1",
            "0.0.0.0",
            "::1",
        ],
    )
    def test_rejects_internal_addresses(self, monkeypatch, ip):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        sock = _FakeSock(ip)
        with pytest.raises(UnsafePeerError):
            _verify_peer_safe(sock, "attacker.example")
        assert sock.closed is True

    def test_ha_addon_supervisor_hostname_skips_check(self, monkeypatch):
        """``supervisor``/``hassio``/``homeassistant`` pass-through in addon mode."""
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        sock = _FakeSock("172.30.32.2")
        _verify_peer_safe(sock, "supervisor")
        assert sock.closed is False

    def test_ha_addon_proxy_net_allowed(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        sock = _FakeSock("172.30.32.17")
        _verify_peer_safe(sock, "proxy.local")
        assert sock.closed is False

    def test_ha_addon_other_private_still_rejected(self, monkeypatch):
        monkeypatch.setenv("SUPERVISOR_TOKEN", "x")
        sock = _FakeSock("192.168.1.100")
        with pytest.raises(UnsafePeerError):
            _verify_peer_safe(sock, "lan.local")


class TestSafeHTTPConnectionDnsRebinding:
    def test_connect_rejects_private_peer_even_if_validator_passed(self, monkeypatch):
        """TOCTOU defence: validator saw a public IP, connect lands on private."""
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

        # Capture the fake socket we return so we can assert it was closed
        fake_sock = _FakeSock("127.0.0.1")

        def _fake_super_connect(self):
            self.sock = fake_sock

        with patch.object(url_safety.http.client.HTTPConnection, "connect", _fake_super_connect):
            conn = SafeHTTPConnection("rebinder.example", 80, timeout=1)
            with pytest.raises(UnsafePeerError):
                conn.connect()

        assert fake_sock.closed is True


class TestSafeUrlopenPreFlight:
    def test_rejects_private_url_before_any_network_call(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)

        def _should_not_be_called(*args, **kwargs):  # pragma: no cover - guard
            raise AssertionError("safe_urlopen must not open an opener for private URLs")

        with (
            patch.object(url_safety, "safe_build_opener", _should_not_be_called),
            pytest.raises(UnsafePeerError),
        ):
            safe_urlopen("http://127.0.0.1:22")

    def test_rejects_non_http_scheme(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        with pytest.raises(UnsafePeerError):
            safe_urlopen("file:///etc/passwd")
