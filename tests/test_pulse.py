"""Tests for services/pulse.py — PulseAudio helpers."""

import asyncio
import sys
from unittest.mock import MagicMock, patch

# Stub pulsectl libraries before importing the module under test.
sys.modules.setdefault("pulsectl", MagicMock())
sys.modules.setdefault("pulsectl_asyncio", MagicMock())

# Another test file (test_daemon_process) may stub services.pulse with a
# MagicMock at module-import time.  Force-reload the real module here.
sys.modules.pop("services.pulse", None)

import services.pulse as _pulse_mod  # noqa: E402
from services.pulse import _fallback_set_volume  # noqa: E402

# ---------------------------------------------------------------------------
# _fallback_set_volume clamping
# ---------------------------------------------------------------------------


def test_fallback_set_volume_clamps_high():
    """Volume above 100 should be clamped to 100."""
    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=0)
        result = _fallback_set_volume("test_sink", 150)
        assert result is True
        cmd = mock_sub.run.call_args[0][0]
        assert cmd == ["pactl", "set-sink-volume", "test_sink", "100%"]


def test_fallback_set_volume_clamps_low():
    """Negative volume should be clamped to 0."""
    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=0)
        result = _fallback_set_volume("test_sink", -10)
        assert result is True
        cmd = mock_sub.run.call_args[0][0]
        assert cmd == ["pactl", "set-sink-volume", "test_sink", "0%"]


def test_fallback_set_volume_normal():
    """Normal volume value should pass through unchanged."""
    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=0)
        result = _fallback_set_volume("test_sink", 50)
        assert result is True
        cmd = mock_sub.run.call_args[0][0]
        assert cmd == ["pactl", "set-sink-volume", "test_sink", "50%"]


def test_fallback_set_volume_failure():
    """Non-zero returncode should return False."""
    with patch.object(_pulse_mod, "subprocess") as mock_sub:
        mock_sub.run.return_value = MagicMock(returncode=1)
        assert _fallback_set_volume("bad_sink", 50) is False


# ---------------------------------------------------------------------------
# _cleanup_loops
# ---------------------------------------------------------------------------


def test_cleanup_loops_closes_open_loops():
    """_cleanup_loops should close all tracked loops and clear the list."""
    loop1 = asyncio.new_event_loop()
    loop2 = asyncio.new_event_loop()
    with _pulse_mod._thread_loops_lock:
        _pulse_mod._thread_loops.append(loop1)
        _pulse_mod._thread_loops.append(loop2)
    try:
        _pulse_mod._cleanup_loops()
        assert loop1.is_closed()
        assert loop2.is_closed()
        with _pulse_mod._thread_loops_lock:
            assert len(_pulse_mod._thread_loops) == 0
    finally:
        if not loop1.is_closed():
            loop1.close()
        if not loop2.is_closed():
            loop2.close()


def test_cleanup_loops_skips_already_closed():
    """_cleanup_loops should not error on already-closed loops."""
    loop = asyncio.new_event_loop()
    loop.close()
    with _pulse_mod._thread_loops_lock:
        _pulse_mod._thread_loops.append(loop)
    _pulse_mod._cleanup_loops()  # should not raise
    with _pulse_mod._thread_loops_lock:
        assert len(_pulse_mod._thread_loops) == 0
