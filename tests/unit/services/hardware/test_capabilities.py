"""Host capabilities: what this host can show, and the reason code when it cannot."""

from __future__ import annotations

import pytest

from sendspin_bridge.services.hardware.capabilities import (
    AUDIO_SERVERS,
    Capabilities,
    Capability,
    build_capabilities,
    classify_audio_server,
    probe_capture,
)


@pytest.mark.parametrize(
    ("server_name", "expected"),
    [
        ("PulseAudio (on PipeWire 1.0.5)", "pipewire"),
        ("PipeWire", "pipewire"),
        ("pipewire-pulse", "pipewire"),
        ("PulseAudio", "pulseaudio"),
        ("pulseaudio 17.0", "pulseaudio"),
        ("some-pa-compatible-server", "pulseaudio"),
        ("not available", "none"),
        ("", "none"),
        ("   ", "none"),
    ],
)
def test_classify_audio_server(server_name, expected):
    assert classify_audio_server(server_name) == expected
    assert expected in AUDIO_SERVERS


def test_everything_available_carries_no_reason():
    caps = build_capabilities(
        bluetooth=True,
        dbus=True,
        server_name="PulseAudio (on PipeWire 1.0.5)",
        capture=True,
        default_sink=True,
    )
    assert caps.audio_server == "pipewire"
    assert caps.bluetooth == Capability(available=True)
    assert caps.dbus == Capability(available=True)
    assert caps.capture == Capability(available=True)
    assert caps.default_sink == Capability(available=True)
    assert caps.reasons == {}


def test_each_unavailable_capability_has_a_reason_code():
    caps = build_capabilities(
        bluetooth=False,
        dbus=False,
        server_name="not available",
        capture=False,
        default_sink=False,
    )
    assert caps.audio_server == "none"
    assert caps.bluetooth == Capability(available=False, reason="bluetooth_unavailable")
    assert caps.dbus == Capability(available=False, reason="dbus_unavailable")
    assert caps.capture == Capability(available=False, reason="pulse_unavailable")
    assert caps.default_sink == Capability(available=False, reason="pulse_unavailable")
    assert caps.reasons == {
        "bluetooth": "bluetooth_unavailable",
        "dbus": "dbus_unavailable",
        "audio_server": "pulse_unavailable",
        "capture": "pulse_unavailable",
        "default_sink": "pulse_unavailable",
    }


def test_capture_is_the_only_reason_on_a_pipewire_host_without_it():
    caps = build_capabilities(
        bluetooth=True,
        dbus=True,
        server_name="PulseAudio (on PipeWire 1.0.5)",
        capture=False,
        default_sink=True,
    )
    assert isinstance(caps, Capabilities)
    assert caps.reasons == {"capture": "pipewire_required"}


def test_probe_capture_checks_for_the_pipewire_tool(monkeypatch):
    import sendspin_bridge.services.hardware.capabilities as capabilities

    monkeypatch.setattr(capabilities.shutil, "which", lambda name: "/usr/bin/wpctl" if name == "wpctl" else None)
    assert probe_capture("PulseAudio (on PipeWire 1.0.5)") is True
    monkeypatch.setattr(capabilities.shutil, "which", lambda name: None)
    assert probe_capture("PulseAudio (on PipeWire 1.0.5)") is False


def test_probe_capture_on_pulseaudio_needs_both_recording_tools(monkeypatch):
    import sendspin_bridge.services.hardware.capabilities as capabilities

    installed = {"pactl"}
    monkeypatch.setattr(capabilities.shutil, "which", lambda name: f"/usr/bin/{name}" if name in installed else None)

    assert probe_capture("PulseAudio") is False
    installed.add("parecord")
    assert probe_capture("PulseAudio") is True


def test_a_pulseaudio_host_without_tools_says_so():
    caps = build_capabilities(
        bluetooth=True,
        dbus=True,
        server_name="PulseAudio",
        capture=False,
        default_sink=True,
    )
    assert caps.reasons == {"capture": "pulse_tools_missing"}
