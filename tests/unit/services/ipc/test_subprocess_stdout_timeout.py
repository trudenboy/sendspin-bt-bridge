"""Tests that _read_subprocess_output tolerates idle stdout without blocking.

Regression guard: before v2.58, ``async for line in proc.stdout`` could hang
forever when the daemon was alive but silent.  After hardening, the reader
wakes up every ``_STDOUT_IDLE_TIMEOUT_SECS`` and keeps polling — only a real
EOF (empty line) or a dead subprocess ends the loop.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sendspin_bridge.bridge.client import SendspinClient


class _FakeStdout:
    """Async stdout mock that never yields lines — simulates a silent daemon."""

    def __init__(self):
        self.read_calls = 0

    async def readline(self):
        self.read_calls += 1
        await asyncio.sleep(3600)
        return b""


class _FakeStdoutEof:
    """Async stdout mock that yields a single line then EOF."""

    def __init__(self, line: bytes):
        self._lines = [line, b""]

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""


def _make_client_with(proc) -> SendspinClient:
    """Build a SendspinClient stub that only wires what the reader needs."""
    client = SendspinClient.__new__(SendspinClient)
    client._daemon_proc = proc  # type: ignore[attr-defined]
    client.player_name = "test-player"
    client._ipc_service = MagicMock()
    client._ipc_service.parse_line.return_value = None
    return client


@pytest.mark.asyncio
async def test_reader_survives_idle_timeout(monkeypatch, caplog):
    """A silent but-alive daemon triggers one timeout log then keeps waiting."""
    stdout = _FakeStdout()
    proc = SimpleNamespace(stdout=stdout, returncode=None)
    client = _make_client_with(proc)
    monkeypatch.setattr(SendspinClient, "_STDOUT_IDLE_TIMEOUT_SECS", 0.05)

    with caplog.at_level(logging.DEBUG, logger="sendspin_bridge.bridge.client"):
        # Give the reader enough time for ≥1 timeout + 1 continued readline
        task = asyncio.create_task(client._read_subprocess_output())
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert stdout.read_calls >= 2, "reader must re-enter readline after timeout"
    assert any("subprocess idle" in rec.message for rec in caplog.records)


@pytest.mark.asyncio
async def test_reader_returns_when_process_dies(monkeypatch):
    """If the subprocess is dead and stdout stalls, the reader exits."""
    stdout = _FakeStdout()
    proc = SimpleNamespace(stdout=stdout, returncode=1)
    client = _make_client_with(proc)
    monkeypatch.setattr(SendspinClient, "_STDOUT_IDLE_TIMEOUT_SECS", 0.05)

    # Loop should terminate cleanly on first timeout since returncode != None
    await asyncio.wait_for(client._read_subprocess_output(), timeout=1.0)


@pytest.mark.asyncio
async def test_reader_stops_on_eof():
    """Empty line from readline → EOF → clean exit."""
    stdout = _FakeStdoutEof(b"")  # first readline returns b"" → EOF immediately
    proc = SimpleNamespace(stdout=stdout, returncode=None)
    client = _make_client_with(proc)

    await asyncio.wait_for(client._read_subprocess_output(), timeout=1.0)
