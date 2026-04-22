"""Tests for routes/api_bt.py scan classification and filter reject-log.

Verifies the ``_classify_audio_capability`` labeling and that
``_enrich_scan_device`` emits a reason when an unwanted device is dropped
by the ``audio_only`` filter. These diagnostics are what support reads
when users report "my speaker does not show up in scan".
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

from routes import api_bt

_CLASS_AUDIO = """Device AA:BB:CC:DD:EE:FF
\tName: Living Room Speaker
\tClass: 0x240404
\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)
"""

_CLASS_PHONE = """Device 11:22:33:44:55:66
\tName: Galaxy S21
\tClass: 0x5a020c
\tUUID: Serial Port                (00001101-0000-1000-8000-00805f9b34fb)
"""

_UUID_ONLY_AUDIO = """Device AA:BB:CC:DD:EE:FF
\tName: Mystery Speaker
\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)
"""

_UUID_ONLY_NON_AUDIO = """Device AA:BB:CC:DD:EE:FF
\tName: Fitness Tracker
\tUUID: Heart Rate                (0000180d-0000-1000-8000-00805f9b34fb)
"""

_NO_CLASS_NO_UUID = """Device AA:BB:CC:DD:EE:FF
\tName: Bare Device
"""


def test_classify_audio_class_of_device():
    """Major device-class 4 (audio/video) resolves as audio-capable."""
    ok, reason = api_bt._classify_audio_capability(_CLASS_AUDIO)
    assert ok is True
    assert reason == "audio_class_of_device"


def test_classify_non_audio_class_of_device():
    """Phone CoD (major class 2) resolves as non-audio with explicit reason."""
    ok, reason = api_bt._classify_audio_capability(_CLASS_PHONE)
    assert ok is False
    assert reason == "non_audio_class_of_device"


def test_classify_falls_back_to_audio_uuid():
    """No Class: but a known audio UUID advertised — treat as audio."""
    ok, reason = api_bt._classify_audio_capability(_UUID_ONLY_AUDIO)
    assert ok is True
    assert reason == "audio_uuid"


def test_classify_rejects_non_audio_uuid_without_class():
    """UUIDs advertised but none audio → rejected with `no_audio_class_no_uuid`."""
    ok, reason = api_bt._classify_audio_capability(_UUID_ONLY_NON_AUDIO)
    assert ok is False
    assert reason == "no_audio_class_no_uuid"


def test_classify_defaults_to_audio_when_info_is_empty():
    """Without Class: or UUID: lines we cannot decide — default to audio to be safe."""
    ok, reason = api_bt._classify_audio_capability(_NO_CLASS_NO_UUID)
    assert ok is True
    assert reason == "no_class_info_defaults_audio"


def test_enrich_scan_device_logs_and_returns_reason_when_dropped(caplog):
    """Non-audio device with audio_only=True → None + reason + INFO log with MAC."""
    caplog.set_level(logging.INFO, logger=api_bt.logger.name)

    mock_run = MagicMock(return_value=MagicMock(stdout=_CLASS_PHONE))
    with patch("routes.api_bt.subprocess.run", mock_run):
        device, reason = api_bt._enrich_scan_device("11:22:33:44:55:66", {}, audio_only=True)

    assert device is None
    assert reason == "non_audio_class_of_device"
    msgs = " | ".join(rec.getMessage() for rec in caplog.records)
    assert "11:22:33:44:55:66" in msgs
    assert "non_audio_class_of_device" in msgs


def test_enrich_scan_device_returns_audio_device_unfiltered():
    """Audio device must pass the audio_only filter with no drop reason."""
    mock_run = MagicMock(return_value=MagicMock(stdout=_CLASS_AUDIO))
    with patch("routes.api_bt.subprocess.run", mock_run):
        device, reason = api_bt._enrich_scan_device("AA:BB:CC:DD:EE:FF", {}, audio_only=True)

    assert device is not None
    assert device["audio_capable"] is True
    assert reason is None


def test_enrich_scan_device_keeps_non_audio_when_audio_only_disabled():
    """With audio_only=False, non-audio devices are returned (no reason)."""
    mock_run = MagicMock(return_value=MagicMock(stdout=_CLASS_PHONE))
    with patch("routes.api_bt.subprocess.run", mock_run):
        device, reason = api_bt._enrich_scan_device("11:22:33:44:55:66", {}, audio_only=False)

    assert device is not None
    assert device["audio_capable"] is False
    assert reason is None
