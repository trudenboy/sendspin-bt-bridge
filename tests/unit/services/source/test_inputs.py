"""The source-input read model: Bluetooth bonds, saved locals and candidates."""

from __future__ import annotations

from dataclasses import dataclass

from sendspin_bridge.services.source import inputs
from sendspin_bridge.services.source.state import SourceDevice

PHONE = "34:C7:E9:25:A3:A3"


@dataclass
class AudioSource:
    name: str
    description: str = ""
    monitor_of: str | None = None


def test_bluetooth_and_local_profiles_are_normalized():
    config = {
        "SENDSPIN_SOURCES": [
            {"id": "ignored", "kind": "local", "node": "alsa_input.turntable", "name": "Turntable"},
            {"node": "alsa_input.turntable", "name": "duplicate"},
            {"node": "", "name": "blank"},
        ]
    }

    profiles = inputs.local_profiles(config)

    assert profiles == [
        {"id": "local:alsa_input.turntable", "kind": "local", "node": "alsa_input.turntable", "name": "Turntable"}
    ]


def test_configured_inputs_mark_the_active_one_and_use_the_description():
    config = {"SENDSPIN_SOURCES": [{"node": "alsa_input.line", "name": ""}]}
    devices = [SourceDevice(mac=PHONE, name="Pixel", connected=True)]

    found = inputs.build_inputs(
        config,
        active_input="local:alsa_input.line",
        bluetooth=devices,
        audio_sources=[AudioSource(name="alsa_input.line", description="Line In")],
    )

    assert [entry.id for entry in found] == ["bluetooth:" + PHONE, "local:alsa_input.line"]
    assert found[0].active is False and found[0].connected is True
    assert found[1].active is True and found[1].name == "Line In"


def test_candidates_exclude_internal_bluetooth_and_monitors():
    audio = [
        AudioSource("alsa_input.line", "Line In"),
        AudioSource("bluez_source.34_C7_E9_25_A3_A3", "Phone"),
        AudioSource("sendspin_fallback.monitor", "Bridge"),
        AudioSource("alsa_output.speaker.monitor", "Speaker monitor", monitor_of="alsa_output.speaker"),
    ]

    found = [entry.id for entry in inputs.local_candidates(audio)]

    assert found == ["local:alsa_input.line"]


def test_monitors_are_offered_only_when_requested():
    audio = [AudioSource("alsa_output.speaker.monitor", "Speaker monitor", monitor_of="alsa_output.speaker")]

    assert inputs.local_candidates(audio) == []
    assert inputs.local_candidates(audio, include_monitors=True)[0].monitor is True
