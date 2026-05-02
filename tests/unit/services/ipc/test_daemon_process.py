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
    "sendspin_bridge.services.ipc.bridge_daemon",
    "sendspin_bridge.services.audio.pulse",
]

for _mod in _STUB_MODULES:
    if _mod not in sys.modules:
        sys.modules[_mod] = MagicMock()

from sendspin_bridge.services.ipc.daemon_process import (  # noqa: E402
    _VALID_LOG_LEVELS,
    _emit_error,
    _emit_status,
    _filter_supported_daemon_args_kwargs,
    _patch_sendspin_audio_player_runtime_guards,
    _read_commands,
)
from sendspin_bridge.services.ipc.ipc_protocol import IPC_PROTOCOL_VERSION  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_last_status():
    """Clear the module-level dedup cache between tests."""
    import sendspin_bridge.services.ipc.daemon_process as dp

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


# ── set_static_delay_ms IPC branch (issue #237) ──────────────────────────


def _make_static_delay_daemon(*, connected: bool = True, setter=None, raise_on_setter: bool = False):
    """Build a MagicMock daemon for set_static_delay_ms tests.

    The local IPC path calls daemon._client.set_static_delay_ms(N) and then,
    on success, awaits daemon._client.send_player_state(...). Tests assert
    against the mocked client.
    """

    class _AsyncMock:
        def __init__(self):
            self.calls: list[dict] = []

        async def __call__(self, **kwargs):
            self.calls.append(kwargs)

    if setter is None:
        applied: list[float] = []

        def _setter(value):
            if raise_on_setter:
                raise RuntimeError("simulated setter failure")
            applied.append(value)

        setter = _setter
        applied_ref = applied
    else:
        applied_ref = []

    send_state = _AsyncMock()
    daemon = MagicMock()
    daemon._client = SimpleNamespace(
        connected=connected,
        set_static_delay_ms=setter,
        send_player_state=send_state,
    )
    daemon._audio_handler = SimpleNamespace(volume=42, muted=False)
    daemon._last_player_state = "synchronized-sentinel"
    daemon._static_delay_ms = 0.0
    return daemon, send_state, applied_ref


async def _run_one_command(daemon, cmd_payload):
    daemon_ref = [daemon]
    stop_event = asyncio.Event()
    cmd_line = json.dumps(cmd_payload) + "\n"

    async def _fake_connect_read_pipe(protocol_factory, pipe):
        protocol = protocol_factory()
        protocol.connection_made(MagicMock())
        protocol.data_received(cmd_line.encode())
        protocol.eof_received()

    with pytest.MonkeyPatch.context() as mp:
        loop = asyncio.get_running_loop()
        mp.setattr(loop, "connect_read_pipe", _fake_connect_read_pipe)
        await asyncio.wait_for(_read_commands(daemon_ref, stop_event), timeout=2.0)


@pytest.mark.asyncio
async def test_read_commands_set_static_delay_applies_and_pushes_to_ma():
    """Happy path: setter applies, daemon caches new value, send_player_state pushes to MA."""
    daemon, send_state, applied = _make_static_delay_daemon(connected=True)

    await _run_one_command(daemon, {"cmd": "set_static_delay_ms", "value": 750})

    assert applied == [750.0]
    assert daemon._static_delay_ms == 750.0
    assert len(send_state.calls) == 1
    pushed = send_state.calls[0]
    assert pushed["volume"] == 42
    assert pushed["muted"] is False
    # Reuses daemon's tracked _last_player_state instead of hard-coding.
    assert pushed["state"] == "synchronized-sentinel"


@pytest.mark.asyncio
async def test_read_commands_set_static_delay_clamps_above_5000():
    daemon, send_state, applied = _make_static_delay_daemon(connected=True)
    await _run_one_command(daemon, {"cmd": "set_static_delay_ms", "value": 9999})
    assert applied == [5000.0]
    assert daemon._static_delay_ms == 5000.0
    assert len(send_state.calls) == 1


@pytest.mark.asyncio
async def test_read_commands_set_static_delay_clamps_below_zero():
    daemon, send_state, applied = _make_static_delay_daemon(connected=True)
    await _run_one_command(daemon, {"cmd": "set_static_delay_ms", "value": -250})
    assert applied == [0.0]
    assert daemon._static_delay_ms == 0.0
    assert len(send_state.calls) == 1


@pytest.mark.asyncio
async def test_read_commands_set_static_delay_skips_push_when_disconnected():
    """If the aiosendspin client isn't connected, no client/state push is attempted."""
    daemon, send_state, applied = _make_static_delay_daemon(connected=False)

    await _run_one_command(daemon, {"cmd": "set_static_delay_ms", "value": 500})

    # Local apply still happens (so subsequent reconnect picks it up via the cache)
    assert applied == [500.0]
    assert daemon._static_delay_ms == 500.0
    # but the bridge→MA push is gated on connected=True
    assert send_state.calls == []


@pytest.mark.asyncio
async def test_read_commands_set_static_delay_skips_cache_when_setter_raises():
    """Regression: a failed apply must NOT update daemon._static_delay_ms.

    The cache feeds _create_client(self._static_delay_ms) on the next server
    reconnect; updating it after a failed apply would silently retry the broken
    value across reconnects, contradicting the "failed" log line.
    """
    daemon, send_state, _applied = _make_static_delay_daemon(connected=True, raise_on_setter=True)

    await _run_one_command(daemon, {"cmd": "set_static_delay_ms", "value": 600})

    # Cache untouched — next reconnect uses the prior value, not the failed one.
    assert daemon._static_delay_ms == 0.0
    # And MA isn't told about a value the local setter rejected.
    assert send_state.calls == []


@pytest.mark.asyncio
async def test_read_commands_set_static_delay_skips_cache_when_setter_unsupported():
    """Older sendspin without set_static_delay_ms must not poison the cache.

    Logged as 'not supported — value ignored'; reconnects rebuild the same
    incompatible client, so caching the user's value would just re-emit the
    same warning forever. Keep the cache stable instead.
    """
    daemon = MagicMock()
    daemon._client = SimpleNamespace(connected=True)  # no set_static_delay_ms attribute
    daemon._audio_handler = SimpleNamespace(volume=42, muted=False)
    daemon._last_player_state = "synchronized-sentinel"
    daemon._static_delay_ms = 100.0  # prior value

    await _run_one_command(daemon, {"cmd": "set_static_delay_ms", "value": 800})

    assert daemon._static_delay_ms == 100.0  # untouched


@pytest.mark.asyncio
async def test_read_commands_set_static_delay_invalid_value_no_crash_no_push():
    """Non-numeric value should warn + skip; no setter, no push, no cache write."""
    daemon, send_state, applied = _make_static_delay_daemon(connected=True)

    await _run_one_command(daemon, {"cmd": "set_static_delay_ms", "value": "loud"})

    assert applied == []
    assert send_state.calls == []
    assert daemon._static_delay_ms == 0.0  # untouched


@pytest.mark.asyncio
async def test_read_commands_set_static_delay_falls_back_when_state_attr_missing(monkeypatch):
    """Older daemon instances without _last_player_state still emit a SYNCHRONIZED push.

    Uses monkeypatch.setattr so the PlayerStateType stub mutation is reverted
    automatically after the test — the aiosendspin.models.types MagicMock is
    shared across the entire test suite, so leaking attributes here would
    make later tests order-dependent.
    """
    daemon, send_state, applied = _make_static_delay_daemon(connected=True)
    # Simulate an old daemon that hasn't been initialised with the attribute.
    del daemon._last_player_state

    fake_pst = SimpleNamespace(SYNCHRONIZED="synchronized-fallback")
    monkeypatch.setattr(
        sys.modules["aiosendspin.models.types"],
        "PlayerStateType",
        fake_pst,
        raising=False,
    )

    await _run_one_command(daemon, {"cmd": "set_static_delay_ms", "value": 300})

    assert applied == [300.0]
    assert len(send_state.calls) == 1
    assert send_state.calls[0]["state"] == "synchronized-fallback"


# ── transport IPC parametric coverage (Track 2B controller audit) ─────────


def _stub_media_command(monkeypatch):
    """Provide a real-enum-shaped MediaCommand stub for the transport handler.

    The daemon's transport branch does
        ``_TRANSPORT_MAP = {mc.value: mc for mc in MediaCommand}``
    so the stub must be iterable with ``.value`` per entry. The bare MagicMock
    from the module-level stub block is neither.
    """
    from enum import Enum

    class FakeMediaCommand(str, Enum):
        PLAY = "play"
        PAUSE = "pause"
        STOP = "stop"
        NEXT = "next"
        PREVIOUS = "previous"
        VOLUME = "volume"
        MUTE = "mute"
        REPEAT_OFF = "repeat_off"
        REPEAT_ONE = "repeat_one"
        REPEAT_ALL = "repeat_all"
        SHUFFLE = "shuffle"
        UNSHUFFLE = "unshuffle"

    monkeypatch.setattr(
        sys.modules["aiosendspin.models.types"],
        "MediaCommand",
        FakeMediaCommand,
        raising=False,
    )
    return FakeMediaCommand


def _make_transport_daemon():
    """Build a daemon mock whose send_group_command captures call args.

    The dispatcher fires the coroutine via ``asyncio.ensure_future``, so the
    coroutine body may not run before the test asserts. We record args at
    sync call time (when ``ensure_future(send_group_command(...))`` evaluates
    the inner expression) and return a no-op coroutine — that way the call
    is observable the moment the dispatcher reaches it.
    """

    class _CapturingCallable:
        def __init__(self):
            self.calls: list[tuple[object, dict]] = []

        def __call__(self, mc, **kwargs):
            self.calls.append((mc, dict(kwargs)))

            async def _noop():
                return None

            return _noop()

    send_group = _CapturingCallable()
    daemon = MagicMock()
    daemon._client = SimpleNamespace(connected=True, send_group_command=send_group)
    return daemon, send_group


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "action",
    [
        "play",
        "pause",
        "stop",
        "next",
        "previous",
        "repeat_off",
        "repeat_one",
        "repeat_all",
        "shuffle",
        "unshuffle",
    ],
)
async def test_read_commands_transport_dispatches_all_media_commands(monkeypatch, action):
    """Track 2B audit: every MediaCommand value MA's SUPPORTED_GROUP_COMMANDS
    advertises must round-trip through the transport IPC handler.

    Locks in the wiring so a future refactor of `_TRANSPORT_MAP` doesn't
    silently drop a command MA's group UI relies on.
    """
    fake_mc = _stub_media_command(monkeypatch)
    daemon, send_group = _make_transport_daemon()

    await _run_one_command(daemon, {"cmd": "transport", "action": action})

    assert len(send_group.calls) == 1, f"action={action!r} did not dispatch"
    dispatched_mc, kwargs = send_group.calls[0]
    assert dispatched_mc == fake_mc(action)
    # Pure transport actions don't pass volume/mute kwargs.
    assert kwargs == {}


@pytest.mark.asyncio
async def test_read_commands_transport_volume_passes_clamped_value(monkeypatch):
    fake_mc = _stub_media_command(monkeypatch)
    daemon, send_group = _make_transport_daemon()

    await _run_one_command(daemon, {"cmd": "transport", "action": "volume", "value": 150})

    assert len(send_group.calls) == 1
    dispatched_mc, kwargs = send_group.calls[0]
    assert dispatched_mc == fake_mc.VOLUME
    assert kwargs == {"volume": 100}  # clamped from 150


@pytest.mark.asyncio
async def test_read_commands_transport_mute_passes_bool(monkeypatch):
    fake_mc = _stub_media_command(monkeypatch)
    daemon, send_group = _make_transport_daemon()

    await _run_one_command(daemon, {"cmd": "transport", "action": "mute", "value": True})

    assert len(send_group.calls) == 1
    dispatched_mc, kwargs = send_group.calls[0]
    assert dispatched_mc == fake_mc.MUTE
    assert kwargs == {"mute": True}


@pytest.mark.asyncio
async def test_read_commands_transport_unknown_action_no_dispatch(monkeypatch):
    """Unknown action must warn + skip; no crash, no spurious dispatch."""
    _stub_media_command(monkeypatch)
    daemon, send_group = _make_transport_daemon()

    await _run_one_command(daemon, {"cmd": "transport", "action": "fast_forward"})

    assert send_group.calls == []


@pytest.mark.asyncio
async def test_read_commands_transport_ignored_when_client_disconnected(monkeypatch):
    """No dispatch attempted if the aiosendspin client isn't connected — the
    transport handler short-circuits with a 'client not connected' warning."""
    _stub_media_command(monkeypatch)
    daemon, send_group = _make_transport_daemon()
    daemon._client = SimpleNamespace(connected=False, send_group_command=send_group)

    await _run_one_command(daemon, {"cmd": "transport", "action": "play"})

    assert send_group.calls == []
