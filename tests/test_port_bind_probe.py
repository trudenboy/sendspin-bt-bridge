"""Tests for services.port_bind_probe."""

from __future__ import annotations

import contextlib
import socket

import pytest

from services.port_bind_probe import (
    DEFAULT_MAX_ATTEMPTS,
    find_available_bind_port,
    is_port_available,
)

_TEST_HOST = "127.0.0.1"


def _pick_free_port() -> int:
    """Return a port number that was free at call time.

    Used to seed tests; callers should re-probe since the port may be reused.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((_TEST_HOST, 0))
        return sock.getsockname()[1]


@contextlib.contextmanager
def _occupy_port(port: int):
    """Hold a listening socket on ``(127.0.0.1, port)`` for the duration of the block."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((_TEST_HOST, port))
    sock.listen(1)
    try:
        yield sock
    finally:
        sock.close()


def test_is_port_available_true_for_free_port():
    port = _pick_free_port()
    assert is_port_available(port, host=_TEST_HOST) is True


def test_is_port_available_false_when_bound():
    port = _pick_free_port()
    with _occupy_port(port):
        assert is_port_available(port, host=_TEST_HOST) is False


def test_finds_start_port_when_free():
    port = _pick_free_port()
    result = find_available_bind_port(port, host=_TEST_HOST, max_attempts=5)
    assert result == port


def test_shifts_when_start_port_taken():
    start = _pick_free_port()
    with _occupy_port(start):
        result = find_available_bind_port(start, host=_TEST_HOST, max_attempts=5)
    assert result is not None
    assert result > start
    assert result < start + 5


def test_returns_none_when_all_taken():
    start = _pick_free_port()
    # Occupy a contiguous window that fully covers max_attempts.
    ports_to_occupy = [start, start + 1, start + 2]
    sockets = []
    try:
        for p in ports_to_occupy:
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind((_TEST_HOST, p))
            except OSError:
                # Port got snatched by the OS between picks; skip this iteration.
                s.close()
                pytest.skip("could not reserve contiguous port range for test")
            s.listen(1)
            sockets.append(s)
        result = find_available_bind_port(start, host=_TEST_HOST, max_attempts=3)
    finally:
        for s in sockets:
            s.close()
    assert result is None


def test_returns_none_when_max_attempts_zero():
    port = _pick_free_port()
    assert find_available_bind_port(port, host=_TEST_HOST, max_attempts=0) is None


def test_default_max_attempts_constant():
    assert DEFAULT_MAX_ATTEMPTS == 10
