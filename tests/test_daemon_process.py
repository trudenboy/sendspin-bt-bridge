"""Unit tests for services/daemon_process.py.

Tests validation logic: volume clamping, log-level validation,
client_id sanitisation, and settings_dir path safety.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub external modules unavailable on Python 3.9 test environment
# ---------------------------------------------------------------------------
_STUB_MODULES = [
    "sendspin",
    "sendspin.audio",
    "sendspin.daemon",
    "sendspin.daemon.daemon",
    "sendspin.settings",
    "sendspin.client_settings",
    "sendspin.models",
    "sendspin.models.player_command",
    "aiosendspin",
    "aiosendspin.models",
    "aiosendspin.models.types",
    "pulsectl",
    "pulsectl_asyncio",
    "dbus",
    "dbus.mainloop",
    "dbus.mainloop.glib",
    "gi",
    "gi.repository",
    "services.bridge_daemon",
    "services.pulse",
]

for _mod in _STUB_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from services.daemon_process import (  # noqa: E402
    _VALID_LOG_LEVELS,
    _emit_status,
    _filter_supported_daemon_args_kwargs,
    _read_commands,
)


@pytest.fixture(autouse=True)
def _reset_last_status():
    """Clear the module-level dedup cache between tests."""
    import services.daemon_process as dp

    dp._last_status_json = ""
    yield
    dp._last_status_json = ""


# ── Volume validation helpers ────────────────────────────────────────────


def _clamp_volume(raw_value):
    """Reproduce the volume clamping logic from _read_commands."""
    return max(0, min(100, int(raw_value)))


def test_set_volume_clamps_above_100():
    assert _clamp_volume(150) == 100


def test_set_volume_clamps_below_0():
    assert _clamp_volume(-20) == 0


def test_set_volume_clamps_normal():
    assert _clamp_volume(75) == 75


def test_set_volume_clamps_boundary():
    assert _clamp_volume(0) == 0
    assert _clamp_volume(100) == 100


def test_set_volume_invalid_value():
    """Non-numeric value should raise ValueError, matching the try/except in _read_commands."""
    with pytest.raises((ValueError, TypeError)):
        _clamp_volume("loud")


# ── Log-level validation ─────────────────────────────────────────────────


def test_set_log_level_valid():
    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        assert level in _VALID_LOG_LEVELS


def test_set_log_level_invalid():
    for bad in ("VERBOSE", "TRACE", "quiet", ""):
        assert bad not in _VALID_LOG_LEVELS


# ── client_id sanitisation ───────────────────────────────────────────────

_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9_:-]")


def _sanitize_client_id(client_id: str) -> str:
    """Reproduce the sanitisation regex from _run()."""
    return _SANITIZE_RE.sub("_", client_id)


def test_client_id_sanitization():
    """Path-traversal characters should be replaced with underscores."""
    dangerous = "../../etc/foo"
    safe = _sanitize_client_id(dangerous)
    assert ".." not in safe
    assert "/" not in safe
    assert safe == "______etc_foo"


def test_client_id_sanitization_preserves_safe():
    assert _sanitize_client_id("my-speaker_1:ok") == "my-speaker_1:ok"


def test_client_id_path_stays_under_tmp():
    """Resolved settings_dir must always be under /tmp/."""
    client_id = "../../etc/passwd"
    safe_id = _sanitize_client_id(client_id)
    settings_dir = f"/tmp/sendspin-{safe_id}"
    resolved = str(Path(settings_dir).resolve())
    # macOS resolves /tmp → /private/tmp; accept both
    tmp_real = str(Path("/tmp").resolve())
    assert resolved.startswith(tmp_real), f"Resolved path escaped {tmp_real}: {resolved}"


def test_client_id_path_fallback_on_escape():
    """If someone manually sets settings_dir outside /tmp, the fallback kicks in."""
    client_id = "speaker1"
    safe_id = _sanitize_client_id(client_id)
    settings_dir = "/var/evil"
    resolved = str(Path(settings_dir).resolve())
    if not resolved.startswith("/tmp/"):
        settings_dir = f"/tmp/sendspin-{safe_id}"
    assert settings_dir.startswith("/tmp/")


def test_filter_supported_daemon_args_kwargs_drops_unknown_fields():
    class FakeDaemonArgs:
        def __init__(self, audio_device, client_id, use_mpris=False):
            pass

    filtered = _filter_supported_daemon_args_kwargs(
        FakeDaemonArgs,
        {
            "audio_device": "default",
            "client_id": "player-1",
            "use_mpris": False,
            "use_hardware_volume": False,
        },
    )

    assert filtered == {
        "audio_device": "default",
        "client_id": "player-1",
        "use_mpris": False,
    }


def test_filter_supported_daemon_args_kwargs_preserves_supported_fields():
    class FakeDaemonArgs:
        def __init__(self, audio_device, client_id, use_mpris=False, use_hardware_volume=False):
            pass

    filtered = _filter_supported_daemon_args_kwargs(
        FakeDaemonArgs,
        {
            "audio_device": "default",
            "client_id": "player-1",
            "use_mpris": False,
            "use_hardware_volume": False,
        },
    )

    assert filtered["use_hardware_volume"] is False


# ── _emit_status dedup ──────────────────────────────────────────────────


def test_emit_status_dedup(capsys):
    """Identical status dicts should only emit once."""
    status = {"player_name": "test", "connected": False}
    _emit_status(status)
    _emit_status(status)
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1


def test_emit_status_different(capsys):
    """Different status dicts should both emit."""
    _emit_status({"player_name": "a", "connected": False})
    _emit_status({"player_name": "b", "connected": True})
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["player_name"] == "a"
    assert json.loads(lines[1])["player_name"] == "b"


# ── _read_commands integration (async) ───────────────────────────────────


@pytest.mark.asyncio
async def test_read_commands_set_volume():
    """set_volume command should clamp and update daemon bridge_status."""
    daemon = MagicMock()
    daemon._bridge_status = {"volume": 50}
    daemon._sync_bt_sink_volume = MagicMock()
    daemon._notify = MagicMock()
    daemon._client = MagicMock()
    daemon._client.connected = True
    daemon_ref = [daemon]
    stop_event = asyncio.Event()

    # Feed a volume command then EOF
    cmd_line = json.dumps({"cmd": "set_volume", "value": 120}) + "\n"

    async def _fake_connect_read_pipe(protocol_factory, pipe):
        protocol = protocol_factory()
        protocol.connection_made(MagicMock())
        protocol.data_received(cmd_line.encode())
        protocol.eof_received()

    with pytest.MonkeyPatch.context() as mp:
        loop = asyncio.get_running_loop()
        mp.setattr(loop, "connect_read_pipe", _fake_connect_read_pipe)
        await asyncio.wait_for(_read_commands(daemon_ref, stop_event), timeout=2.0)

    assert daemon._bridge_status["volume"] == 100  # clamped
    daemon._sync_bt_sink_volume.assert_called_once_with(100)
    daemon._notify.assert_called_once()


@pytest.mark.asyncio
async def test_read_commands_set_log_level_valid():
    """Valid log level should be accepted and applied."""
    daemon_ref = []
    stop_event = asyncio.Event()

    cmd_line = json.dumps({"cmd": "set_log_level", "level": "DEBUG"}) + "\n"

    async def _fake_connect_read_pipe(protocol_factory, pipe):
        protocol = protocol_factory()
        protocol.connection_made(MagicMock())
        protocol.data_received(cmd_line.encode())
        protocol.eof_received()

    with pytest.MonkeyPatch.context() as mp:
        loop = asyncio.get_running_loop()
        mp.setattr(loop, "connect_read_pipe", _fake_connect_read_pipe)
        await asyncio.wait_for(_read_commands(daemon_ref, stop_event), timeout=2.0)

    assert logging.getLogger().level == logging.DEBUG


@pytest.mark.asyncio
async def test_read_commands_set_log_level_invalid():
    """Invalid log level should be rejected (logged as warning, not crash)."""
    daemon_ref = []
    stop_event = asyncio.Event()
    logging.getLogger().setLevel(logging.INFO)

    cmd_line = json.dumps({"cmd": "set_log_level", "level": "VERBOSE"}) + "\n"

    async def _fake_connect_read_pipe(protocol_factory, pipe):
        protocol = protocol_factory()
        protocol.connection_made(MagicMock())
        protocol.data_received(cmd_line.encode())
        protocol.eof_received()

    with pytest.MonkeyPatch.context() as mp:
        loop = asyncio.get_running_loop()
        mp.setattr(loop, "connect_read_pipe", _fake_connect_read_pipe)
        await asyncio.wait_for(_read_commands(daemon_ref, stop_event), timeout=2.0)

    # Level should remain unchanged
    assert logging.getLogger().level == logging.INFO


@pytest.mark.asyncio
async def test_read_commands_invalid_volume_no_crash():
    """Non-numeric volume value should be logged, not crash the reader."""
    daemon = MagicMock()
    daemon._bridge_status = {"volume": 50}
    daemon._client = MagicMock()
    daemon._client.connected = True
    daemon_ref = [daemon]
    stop_event = asyncio.Event()

    cmd_line = json.dumps({"cmd": "set_volume", "value": "loud"}) + "\n"

    async def _fake_connect_read_pipe(protocol_factory, pipe):
        protocol = protocol_factory()
        protocol.connection_made(MagicMock())
        protocol.data_received(cmd_line.encode())
        protocol.eof_received()

    with pytest.MonkeyPatch.context() as mp:
        loop = asyncio.get_running_loop()
        mp.setattr(loop, "connect_read_pipe", _fake_connect_read_pipe)
        # Should complete without raising
        await asyncio.wait_for(_read_commands(daemon_ref, stop_event), timeout=2.0)

    # Volume should remain unchanged
    assert daemon._bridge_status["volume"] == 50
