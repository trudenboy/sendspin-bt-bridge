"""The audio half of the hardware reader: cards, endpoints and the capture node."""

from __future__ import annotations

from dataclasses import replace

import pytest

from sendspin_bridge.services.audio.sink_inventory import LocalCard, LocalSource
from sendspin_bridge.services.hardware import inventory
from sendspin_bridge.services.hardware.roles import RoleEvidence
from tests.support import hardware_audio

ADAPTER = "C0:FB:F9:62:D7:D6"
DEVICE = "6C:5C:3D:35:17:99"
DEVICE_UNDERSCORES = DEVICE.replace(":", "_")


@pytest.fixture(autouse=True)
def _fresh_reader(monkeypatch):
    inventory.clear_cache()
    monkeypatch.setattr(inventory, "probe_audio_server", lambda: "PulseAudio")
    monkeypatch.setattr(inventory, "probe_capture", lambda: True)
    yield
    inventory.clear_cache()


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(inventory, "_monotonic", lambda: now[0])
    return now


def _use_audio(monkeypatch, audio: inventory.AudioFacts) -> list[int]:
    calls: list[int] = []

    async def fake_audio() -> inventory.AudioFacts:
        calls.append(1)
        return audio

    monkeypatch.setattr(inventory, "_read_audio_async", fake_audio)
    return calls


def _read() -> inventory.InventoryRead:
    return inventory.read_inventory()


# -- owner classification -----------------------------------------------------


def test_audio_facts_classify_card_bluetooth_and_internal_owners():
    facts = hardware_audio.facts()

    by_name = {endpoint.name: endpoint for endpoint in facts.sinks}
    assert by_name[hardware_audio.ANALOG_SINK].owner == inventory.EndpointOwnerFacts(
        kind="card", card=hardware_audio.ANALOG_CARD
    )
    assert by_name[hardware_audio.BLUEZ_SINK].owner == inventory.EndpointOwnerFacts(
        kind="bluetooth", bluetooth=hardware_audio.SPEAKER_MAC
    )
    assert by_name[hardware_audio.INTERNAL_SINK].owner == inventory.EndpointOwnerFacts(kind="internal", internal=True)

    by_name = {endpoint.name: endpoint for endpoint in facts.sources}
    assert by_name[hardware_audio.ANALOG_SOURCE].owner == inventory.EndpointOwnerFacts(
        kind="card", card=hardware_audio.ANALOG_CARD
    )
    assert by_name[hardware_audio.BLUEZ_SOURCE].owner == inventory.EndpointOwnerFacts(
        kind="bluetooth", bluetooth=hardware_audio.SPEAKER_MAC
    )


def test_audio_facts_keep_state_identifiers_and_ports():
    facts = hardware_audio.facts()
    usb = next(endpoint for endpoint in facts.sinks if endpoint.name == hardware_audio.USB_SINK)

    assert usb.state == "suspended"
    assert usb.muted is True and usb.volume == 0
    assert usb.active_port == "analog-output"
    assert usb.active_port_available == "not available"
    assert usb.identifiers["serial"] == "Generic_USB_DAC_0000A1"
    assert usb.monitor_of is None

    monitor = next(endpoint for endpoint in facts.sources if endpoint.name == hardware_audio.MONITOR_SOURCE)
    assert monitor.monitor_of == hardware_audio.ANALOG_SINK
    assert monitor.volume is None and monitor.muted is None


def test_an_endpoint_no_card_claims_has_no_owner():
    sink = hardware_audio.sinks()[0]
    facts = inventory.build_audio_facts(
        [LocalCard("other_card", "", None, (), ())],
        [sink],
        [],
        inventory.CaptureFacts(),
    )

    assert facts.sinks[0].owner is None


def test_owner_falls_back_to_the_single_card_loaded_by_the_same_module():
    sink = hardware_audio.sinks()[0]
    card = LocalCard("alsa_card.card-7", "", None, (), (), owner_module=7)
    facts = inventory.build_audio_facts([card], [sink], [], inventory.CaptureFacts())

    assert facts.sinks[0].owner == inventory.EndpointOwnerFacts(kind="card", card="alsa_card.card-7")


# -- capture detection --------------------------------------------------------


def test_capture_prefers_the_pipewire_node():
    capture = inventory.classify_capture(
        f"Audio\n ├─ Sources:\n │  *   42. {hardware_audio.CAPTURE_NODE}\n",
        hardware_audio.sources(),
        wpctl_available=True,
    )

    assert capture.kind == "pipewire_node"
    assert capture.node == hardware_audio.CAPTURE_NODE


def test_capture_falls_back_to_a_pactl_bluez_source():
    capture = inventory.classify_capture(None, hardware_audio.sources(), wpctl_available=True)

    assert capture.kind == "pulse_source"
    assert capture.node == hardware_audio.BLUEZ_SOURCE


def test_capture_absence_is_unavailable_with_a_reason():
    sources = [source for source in hardware_audio.sources() if not source.name.startswith("bluez_source")]

    no_node = inventory.classify_capture("Audio\n", sources, wpctl_available=True)
    no_wpctl = inventory.classify_capture(None, sources, wpctl_available=False)

    assert no_node.kind == "unavailable" and no_node.node is None
    assert no_node.reason
    assert "bluez_input" in no_node.reason
    assert no_wpctl.kind == "unavailable"
    assert no_wpctl.reason and "wpctl" in no_wpctl.reason


# -- roles --------------------------------------------------------------------


def test_a_bluez_source_gives_the_device_an_input_role(installed_bluez, monkeypatch):
    source = LocalSource(
        name=f"bluez_source.{DEVICE_UNDERSCORES}",
        description="ENEBY Portable",
        state="running",
        sample_spec="s16le 2ch 44100Hz",
        muted=False,
        volume=100,
    )
    _use_audio(monkeypatch, inventory.build_audio_facts([], [], [source], inventory.CaptureFacts()))

    device = next(item for item in _read().devices if item.mac == DEVICE)

    assert device.roles == (RoleEvidence(role="input", evidence=f"source:bluez_source.{DEVICE_UNDERSCORES}"),)


def test_a_pipewire_capture_node_gives_the_device_an_input_role(installed_bluez, monkeypatch):
    node = f"bluez_input.{DEVICE_UNDERSCORES}.2"
    _use_audio(
        monkeypatch,
        inventory.build_audio_facts([], [], [], inventory.CaptureFacts(kind="pipewire_node", node=node)),
    )

    device = next(item for item in _read().devices if item.mac == DEVICE)

    assert device.roles == (RoleEvidence(role="input", evidence=f"capture:{node}"),)


def test_a_suspended_bluez_sink_claims_no_output_role(installed_bluez, monkeypatch):
    sink = replace(hardware_audio.sinks()[1], name=f"bluez_sink.{DEVICE_UNDERSCORES}.a2dp_sink", state="suspended")
    _use_audio(monkeypatch, inventory.build_audio_facts([], [sink], [], inventory.CaptureFacts()))

    device = next(item for item in _read().devices if item.mac == DEVICE)

    assert device.roles == ()


# -- reads, cache and degradation --------------------------------------------


def test_the_audio_half_is_read_once_per_ttl(installed_bluez, monkeypatch, clock):
    calls = _use_audio(monkeypatch, hardware_audio.facts())

    _read()
    clock[0] += 1.9
    _read()
    assert len(calls) == 1

    clock[0] += 0.2
    _read()
    assert len(calls) == 2


def test_the_read_model_carries_the_audio_facts(installed_bluez, monkeypatch):
    expected = hardware_audio.facts()
    _use_audio(monkeypatch, expected)

    read = _read()

    assert read.audio == expected
    assert [card.name for card in read.audio.cards] == [
        hardware_audio.ANALOG_CARD,
        hardware_audio.BLUEZ_CARD,
        hardware_audio.USB_CARD,
    ]


def test_capture_absence_is_a_degraded_audio_section(installed_bluez, monkeypatch):
    facts = hardware_audio.facts()
    audio = inventory.AudioFacts(
        cards=facts.cards,
        sinks=facts.sinks,
        sources=facts.sources,
        capture=inventory.CaptureFacts(kind="unavailable", reason="No bluez_input node is present."),
    )
    _use_audio(monkeypatch, audio)

    read = _read()

    entry = next(section for section in read.degraded if section.code == "capture_unavailable")
    assert entry.section == "audio"
    assert "No bluez_input" in entry.detail


def test_no_audio_server_reads_no_audio(monkeypatch):
    async def _boom() -> inventory.AudioFacts:
        raise AssertionError("no server answered; nothing may be read")

    monkeypatch.setattr(inventory, "_read_audio_async", _boom)

    assert inventory._read_audio("not available") == inventory.AudioFacts()


def test_a_server_that_answers_is_read_through_one_event_loop(monkeypatch):
    expected = hardware_audio.facts()

    async def fake_audio() -> inventory.AudioFacts:
        return expected

    monkeypatch.setattr(inventory, "_read_audio_async", fake_audio)

    assert inventory._read_audio("PulseAudio (on PipeWire 1.0.5)") == expected


def test_no_audio_server_is_pulse_unavailable_and_not_capture_unavailable(installed_bluez, monkeypatch):
    monkeypatch.setattr(inventory, "probe_audio_server", lambda: "not available")

    read = _read()

    assert read.audio == inventory.AudioFacts()
    codes = {(section.section, section.code) for section in read.degraded}
    assert ("audio", "pulse_unavailable") in codes
    assert ("audio", "capture_unavailable") not in codes
