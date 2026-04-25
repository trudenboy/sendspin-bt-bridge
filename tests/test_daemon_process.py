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
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Stub external modules unavailable on Python 3.9 test environment
# ---------------------------------------------------------------------------
_STUB_MODULES = [
    "sendspin",
    "sendspin.audio",
    "sendspin.audio_devices",
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
    _emit_error,
    _emit_status,
    _filter_supported_daemon_args_kwargs,
    _patch_sendspin_audio_player_runtime_guards,
    _read_commands,
)
from services.ipc_protocol import IPC_PROTOCOL_VERSION  # noqa: E402


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
    """If settings_dir resolves outside /tmp, the fallback kicks in (mirrors daemon_process._run logic)."""
    from pathlib import Path

    client_id = "speaker1"
    safe_id = _sanitize_client_id(client_id)

    # Simulate the actual fallback logic from daemon_process._run (line 504-508)
    settings_dir = "/var/evil"
    resolved = str(Path(settings_dir).resolve())
    tmp_real = str(Path("/tmp").resolve())
    if not resolved.startswith(tmp_real + "/") and resolved != tmp_real:
        settings_dir = f"/tmp/sendspin-{safe_id}"
    assert settings_dir == f"/tmp/sendspin-{safe_id}"

    # Verify safe path is not overridden
    settings_dir_safe = f"/tmp/sendspin-{safe_id}"
    resolved_safe = str(Path(settings_dir_safe).resolve())
    assert resolved_safe.startswith(str(Path("/tmp").resolve()))


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
            "volume_controller": None,
            "use_hardware_volume": False,
        },
    )

    assert filtered == {
        "audio_device": "default",
        "client_id": "player-1",
        "use_mpris": False,
    }


def test_filter_supported_daemon_args_kwargs_preserves_volume_controller():
    """New sendspin >=5.5.0 uses volume_controller kwarg."""

    class FakeDaemonArgs:
        def __init__(self, audio_device, client_id, use_mpris=False, volume_controller=None):
            pass

    filtered = _filter_supported_daemon_args_kwargs(
        FakeDaemonArgs,
        {
            "audio_device": "default",
            "client_id": "player-1",
            "use_mpris": False,
            "volume_controller": None,
        },
    )

    assert filtered == {
        "audio_device": "default",
        "client_id": "player-1",
        "use_mpris": False,
        "volume_controller": None,
    }


def test_filter_supported_daemon_args_kwargs_preserves_legacy_hw_volume():
    """Old sendspin <5.5.0 uses use_hardware_volume kwarg."""

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


def test_patch_sendspin_audio_player_runtime_guards_resets_stale_last_frame(monkeypatch):
    class FakeAudioPlayer:
        def __init__(self) -> None:
            self._format = SimpleNamespace(frame_size=8)
            self._last_output_frame = b"bad"

        def set_format(self, audio_format, device):
            return (audio_format, device)

        def _audio_callback(self, outdata, frames, time, status):
            return self._last_output_frame

    fake_sendspin = ModuleType("sendspin")
    fake_audio = ModuleType("sendspin.audio")
    fake_audio.AudioPlayer = FakeAudioPlayer
    fake_sendspin.audio = fake_audio
    monkeypatch.setitem(sys.modules, "sendspin", fake_sendspin)
    monkeypatch.setitem(sys.modules, "sendspin.audio", fake_audio)

    _patch_sendspin_audio_player_runtime_guards()

    player = FakeAudioPlayer()
    result = player._audio_callback(None, 0, None, None)

    assert result == b"\x00" * 8
    assert player._last_output_frame == b"\x00" * 8


def test_patch_sendspin_audio_player_runtime_guards_resets_correction_state_on_format_change(monkeypatch):
    class FakeAudioPlayer:
        def __init__(self) -> None:
            self._last_output_frame = b"12345678"
            self._insert_every_n_frames = 4
            self._drop_every_n_frames = 5
            self._frames_until_next_insert = 2
            self._frames_until_next_drop = 3
            self.seen_state: tuple[bytes, int, int, int, int] | None = None

        def set_format(self, audio_format, device):
            self.seen_state = (
                self._last_output_frame,
                self._insert_every_n_frames,
                self._drop_every_n_frames,
                self._frames_until_next_insert,
                self._frames_until_next_drop,
            )
            return (audio_format, device)

        def _audio_callback(self, outdata, frames, time, status):
            return None

    fake_sendspin = ModuleType("sendspin")
    fake_audio = ModuleType("sendspin.audio")
    fake_audio.AudioPlayer = FakeAudioPlayer
    fake_sendspin.audio = fake_audio
    monkeypatch.setitem(sys.modules, "sendspin", fake_sendspin)
    monkeypatch.setitem(sys.modules, "sendspin.audio", fake_audio)

    _patch_sendspin_audio_player_runtime_guards()

    player = FakeAudioPlayer()
    result = player.set_format("fmt", "device")

    assert player.seen_state == (b"", 0, 0, 0, 0)
    assert result == ("fmt", "device")


# ── _emit_status dedup ──────────────────────────────────────────────────


def test_emit_status_dedup(capsys):
    """Identical status dicts should only emit once."""
    status = {"player_name": "test", "connected": False}
    _emit_status(status)
    _emit_status(status)
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["protocol_version"] == IPC_PROTOCOL_VERSION


def test_emit_status_different(capsys):
    """Different status dicts should both emit."""
    _emit_status({"player_name": "a", "connected": False})
    _emit_status({"player_name": "b", "connected": True})
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["player_name"] == "a"
    assert json.loads(lines[1])["player_name"] == "b"


def test_emit_error_structured_envelope(capsys):
    _emit_error("audio_output_missing", "No audio output device found")

    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["type"] == "error"
    assert payload["error_code"] == "audio_output_missing"
    assert payload["message"] == "No audio output device found"
    assert payload["protocol_version"] == IPC_PROTOCOL_VERSION
    assert "at" in payload["details"]


# ── _read_commands integration (async) ───────────────────────────────────


@pytest.mark.asyncio
async def test_read_commands_set_volume():
    """set_volume command should clamp and update daemon bridge_status."""
    daemon = MagicMock()
    daemon._bridge_status = {"volume": 50}
    daemon._notify = MagicMock()
    daemon._client = MagicMock()
    daemon._client.connected = True
    daemon_ref = [daemon]
    stop_event = asyncio.Event()

    # Feed a volume command then EOF
    cmd_line = json.dumps({"cmd": "set_volume", "value": 120, "protocol_version": IPC_PROTOCOL_VERSION}) + "\n"

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
