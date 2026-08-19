"""Tests for scan classification and the filter reject-log.

The classifier itself now lives in ``services.bluetooth`` as
``classify_audio_capability(DeviceInfo)`` (batch 2 — merged from the
``routes/api_bt`` twin); it is exercised here through the real
``parse_device_info`` grammar so tests assert on parsed structure, not
on reimplemented logic.  ``_enrich_scan_device`` tests run on the
shared FakeBluez transport.

These diagnostics are what support reads when users report "my speaker
does not show up in scan".
"""

from __future__ import annotations

import logging

from sendspin_bridge.bluetooth.bluez import parse_device_info
from sendspin_bridge.services.bluetooth import classify_audio_capability
from sendspin_bridge.web.routes import api_bt

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


def _classify(text: str, mac: str = "AA:BB:CC:DD:EE:FF"):
    return classify_audio_capability(parse_device_info(text, mac))


def test_classify_audio_class_of_device():
    """Major device-class 4 (audio/video) resolves as audio-capable."""
    ok, reason = _classify(_CLASS_AUDIO)
    assert ok is True
    assert reason == "audio_class_of_device"


def test_classify_non_audio_class_of_device():
    """Phone CoD (major class 2) resolves as non-audio with explicit reason."""
    ok, reason = _classify(_CLASS_PHONE, mac="11:22:33:44:55:66")
    assert ok is False
    assert reason == "non_audio_class_of_device"


def test_classify_falls_back_to_audio_uuid():
    """No Class: but a known audio UUID advertised — treat as audio."""
    ok, reason = _classify(_UUID_ONLY_AUDIO)
    assert ok is True
    assert reason == "audio_uuid"


def test_classify_rejects_non_audio_uuid_without_class():
    """UUIDs advertised but none audio → rejected with `no_audio_class_no_uuid`."""
    ok, reason = _classify(_UUID_ONLY_NON_AUDIO)
    assert ok is False
    assert reason == "no_audio_class_no_uuid"


def test_classify_defaults_to_audio_when_info_is_empty():
    """Without Class: or UUID: lines we cannot decide — default to audio to be safe."""
    ok, reason = _classify(_NO_CLASS_NO_UUID)
    assert ok is True
    assert reason == "no_class_info_defaults_audio"


def test_enrich_scan_device_logs_and_returns_reason_when_dropped(caplog, installed_bluez):
    """Non-audio device with audio_only=True → None + reason + INFO log with MAC."""
    caplog.set_level(logging.INFO, logger=api_bt.logger.name)
    installed_bluez.on("info", stdout=_CLASS_PHONE)

    device, reason = api_bt._enrich_scan_device("11:22:33:44:55:66", {}, audio_only=True)

    assert device is None
    assert reason == "non_audio_class_of_device"
    msgs = " | ".join(rec.getMessage() for rec in caplog.records)
    assert "11:22:33:44:55:66" in msgs
    assert "non_audio_class_of_device" in msgs


def test_enrich_scan_device_returns_audio_device_unfiltered(installed_bluez):
    """Audio device must pass the audio_only filter with no drop reason."""
    installed_bluez.on("info", stdout=_CLASS_AUDIO)

    device, reason = api_bt._enrich_scan_device("AA:BB:CC:DD:EE:FF", {}, audio_only=True)

    assert device is not None
    assert device["audio_capable"] is True
    assert reason is None


def test_enrich_scan_device_keeps_non_audio_when_audio_only_disabled(installed_bluez):
    """With audio_only=False, non-audio devices are returned (no reason)."""
    installed_bluez.on("info", stdout=_CLASS_PHONE)

    device, reason = api_bt._enrich_scan_device("11:22:33:44:55:66", {}, audio_only=False)

    assert device is not None
    assert device["audio_capable"] is False
    assert reason is None


def test_enrich_scan_device_defaults_to_audio_on_transport_failure(installed_bluez):
    """Legacy contract: any bluetoothctl failure during enrichment must
    include the device (never silently drop a scannable speaker)."""
    installed_bluez.fail("info")

    device, reason = api_bt._enrich_scan_device("AA:BB:CC:DD:EE:FF", {}, audio_only=True)

    assert device == {"mac": "AA:BB:CC:DD:EE:FF", "name": "AA:BB:CC:DD:EE:FF", "audio_capable": True}
    assert reason is None


def test_enrich_scan_device_uses_short_timeout(installed_bluez):
    """The one per-call timeout override in phase 1: a 25-device scan must
    not gain +25 s, so enrichment runs ``info`` with timeout=4.0."""
    api_bt._enrich_scan_device("AA:BB:CC:DD:EE:FF", {}, audio_only=True)

    runs = [c for c in installed_bluez.commands if c.kind == "run"]
    assert runs and all(c.timeout == 4.0 for c in runs)


def test_enrich_scan_device_picks_up_name_from_info(installed_bluez):
    """A device first seen MAC-only in the scan stream gets its display
    name from the enrichment ``info`` read."""
    installed_bluez.on("info", stdout=_CLASS_AUDIO)
    names: dict[str, str] = {}

    device, _ = api_bt._enrich_scan_device("AA:BB:CC:DD:EE:FF", names, audio_only=True)

    assert device is not None
    assert device["name"] == "Living Room Speaker"
    assert names["AA:BB:CC:DD:EE:FF"] == "Living Room Speaker"


# ── _resolve_unnamed_devices: the bluetoothctl device-cache lookup ──────


def test_resolve_unnamed_devices_uses_devices_cache(installed_bluez):
    """Unnamed scan candidates get their display name from the BlueZ
    device cache (``devices``); MAC-shaped cache names stay unnamed."""
    installed_bluez.on(
        "devices",
        stdout=(
            "Device AA:BB:CC:DD:EE:01 Kitchen Speaker\n"
            "Device AA:BB:CC:DD:EE:02 AA-BB-CC-DD-EE-02\n"  # MAC-as-name stays unnamed
        ),
    )
    names: dict[str, str] = {}

    api_bt._resolve_unnamed_devices({"AA:BB:CC:DD:EE:01", "AA:BB:CC:DD:EE:02"}, names)

    assert names == {"AA:BB:CC:DD:EE:01": "Kitchen Speaker"}


def test_resolve_unnamed_devices_skips_cache_when_all_named(installed_bluez):
    """No unnamed candidates → no bluetoothctl round-trip at all."""
    api_bt._resolve_unnamed_devices({"AA:BB:CC:DD:EE:01"}, {"AA:BB:CC:DD:EE:01": "Known"})

    assert installed_bluez.commands == []
