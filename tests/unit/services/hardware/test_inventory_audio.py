"""The audio half of the hardware reader: cards, endpoints and the capture node."""

from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from sendspin_bridge.services.audio.sink_inventory import LocalCard, LocalSource
from sendspin_bridge.services.hardware import inventory
from sendspin_bridge.services.hardware.roles import RoleEvidence
from tests.support import hardware_audio

#: The real composition, captured before the autouse stub replaces the name.
_REAL_READ_AUDIO_ASYNC = inventory._read_audio_async

ADAPTER = "C0:FB:F9:62:D7:D6"
DEVICE = "6C:5C:3D:35:17:99"
DEVICE_UNDERSCORES = DEVICE.replace(":", "_")
PIPEWIRE_CARD = "alsa_card.pci-0000_00_1b.0"
PIPEWIRE_SINK = "alsa_output.pci-0000_00_1b.0.analog-stereo"


@pytest.fixture(autouse=True)
def _fresh_reader(monkeypatch):
    inventory.clear_cache()
    monkeypatch.setattr(inventory, "probe_audio_server", lambda: "PulseAudio")
    monkeypatch.setattr(inventory, "probe_capture", lambda _name=None: True)
    yield
    inventory.clear_cache()


@pytest.fixture
def clock(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(inventory, "_monotonic", lambda: now[0])
    return now


def _use_audio(monkeypatch, audio: inventory.AudioFacts) -> list[int]:
    calls: list[int] = []

    async def fake_audio(server_name: str | None = None) -> inventory.AudioFacts:
        del server_name
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

    analog_source = next(endpoint for endpoint in facts.sources if endpoint.name == hardware_audio.ANALOG_SOURCE)
    assert analog_source.active_port == "analog-input-headset-mic"
    assert analog_source.active_port_available == "not available"

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


def test_a_pipewire_card_owns_a_sink_by_its_device_name_property():
    from sendspin_bridge.services.audio.sink_inventory import parse_pactl_cards, parse_pactl_sinks

    sinks = parse_pactl_sinks((hardware_audio.FIXTURES / "analog_and_hdmi_one_card.txt").read_text())
    card = parse_pactl_cards((hardware_audio.FIXTURES / "pipewire_cards.txt").read_text())[0]

    facts = inventory.build_audio_facts([card], sinks, [], inventory.CaptureFacts())

    analog = next(endpoint for endpoint in facts.sinks if endpoint.name == PIPEWIRE_SINK)
    assert analog.owner == inventory.EndpointOwnerFacts(kind="card", card=PIPEWIRE_CARD)


def test_a_monitor_source_is_owned_through_the_sink_it_monitors():
    card = LocalCard(PIPEWIRE_CARD, "", None, (), ())
    sink = replace(hardware_audio.sinks()[0], name=PIPEWIRE_SINK, owner_module=None, device_name=PIPEWIRE_CARD)
    source = LocalSource(
        name=f"{PIPEWIRE_SINK}.monitor",
        description="Monitor",
        state="idle",
        sample_spec="s16le 2ch 48000Hz",
        muted=False,
        volume=100,
        monitor_of=PIPEWIRE_SINK,
    )

    facts = inventory.build_audio_facts([card], [sink], [source], inventory.CaptureFacts())

    assert facts.sources[0].owner == inventory.EndpointOwnerFacts(kind="card", card=PIPEWIRE_CARD)


def test_two_cards_with_the_same_device_name_leave_the_owner_unknown():
    shared = "alsa_card.shared"
    sink = replace(hardware_audio.sinks()[0], name=PIPEWIRE_SINK, owner_module=None, device_name=shared)
    cards = [LocalCard(shared, "", None, (), ()), LocalCard(shared, "", None, (), ())]

    facts = inventory.build_audio_facts(cards, [sink], [], inventory.CaptureFacts())

    assert facts.sinks[0].owner is None


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


def test_a_wpctl_probe_that_did_not_run_is_not_the_phone_not_streaming():
    sources = [source for source in hardware_audio.sources() if not source.name.startswith("bluez_source")]

    failed = inventory.classify_capture(None, sources, wpctl_available=True)
    no_node = inventory.classify_capture("Audio\n", sources, wpctl_available=True)

    assert failed.kind == "unavailable" and failed.node is None
    assert failed.reason and "wpctl" in failed.reason
    assert "not streaming" not in failed.reason
    assert no_node.reason and "not streaming" in no_node.reason
    assert failed.reason != no_node.reason


def test_capture_facts_carries_a_wpctl_timeout_distinctly(monkeypatch):
    sources = [source for source in hardware_audio.sources() if not source.name.startswith("bluez_source")]

    async def timed_out() -> None:
        return None

    async def ran_with_no_node() -> str:
        return "Audio\n"

    monkeypatch.setattr(inventory, "_wpctl_available", lambda: True)
    monkeypatch.setattr(inventory, "_wpctl_status", timed_out)
    timeout = asyncio.run(inventory._capture_facts(sources, "PulseAudio"))
    monkeypatch.setattr(inventory, "_wpctl_status", ran_with_no_node)
    no_node = asyncio.run(inventory._capture_facts(sources, "PulseAudio"))

    assert timeout.kind == "unavailable" and timeout.reason
    assert "wpctl" in timeout.reason and "not streaming" not in timeout.reason
    assert no_node.reason and "not streaming" in no_node.reason


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


def test_a_successful_empty_audio_read_is_empty_not_unavailable(installed_bluez, monkeypatch):
    from sendspin_bridge.services.audio import sink_inventory

    async def empty() -> str:
        return ""

    async def no_pulsectl():
        return None

    async def no_wpctl() -> str:
        return ""

    monkeypatch.setattr(sink_inventory, "_list_all_sinks_pulsectl", no_pulsectl)
    monkeypatch.setattr(sink_inventory, "_pactl_list_sinks_text", empty)
    monkeypatch.setattr(sink_inventory, "_pactl_list_sources_text", empty)
    monkeypatch.setattr(sink_inventory, "_pactl_list_cards_text", empty)
    monkeypatch.setattr(inventory, "_wpctl_status", no_wpctl)
    monkeypatch.setattr(inventory, "_read_audio_async", _REAL_READ_AUDIO_ASYNC)

    audio = inventory._read_audio("PulseAudio")

    assert audio.cards == () and audio.sinks == () and audio.sources == ()
    assert audio.unavailable == ()

    read = inventory._assemble([], [], dbus=True, bluetooth=True, degraded=[], audio=audio, server_name="PulseAudio")
    assert not any(section.code == "pulse_unavailable" for section in read.degraded)


def test_a_failed_list_read_is_pulse_unavailable_not_empty(installed_bluez, monkeypatch):
    from sendspin_bridge.services.audio import sink_inventory

    async def empty() -> str:
        return ""

    async def failed():
        return None

    async def no_pulsectl():
        return None

    async def no_wpctl() -> str:
        return ""

    monkeypatch.setattr(sink_inventory, "_list_all_sinks_pulsectl", no_pulsectl)
    monkeypatch.setattr(sink_inventory, "_pactl_list_sinks_text", empty)
    monkeypatch.setattr(sink_inventory, "_pactl_list_sources_text", failed)
    monkeypatch.setattr(sink_inventory, "_pactl_list_cards_text", empty)
    monkeypatch.setattr(inventory, "_wpctl_status", no_wpctl)
    monkeypatch.setattr(inventory, "_read_audio_async", _REAL_READ_AUDIO_ASYNC)

    audio = inventory._read_audio("PulseAudio")

    assert audio.sources == ()
    assert audio.unavailable
    assert any("source" in reason for reason in audio.unavailable)

    read = inventory._assemble([], [], dbus=True, bluetooth=True, degraded=[], audio=audio, server_name="PulseAudio")
    entry = next(section for section in read.degraded if section.code == "pulse_unavailable")
    assert entry.section == "audio"
    assert "source" in entry.detail


def test_the_audio_composition_reads_sinks_sources_and_cards(installed_bluez, monkeypatch):
    from sendspin_bridge.services.audio import sink_inventory

    async def no_pulsectl():
        return None

    async def sinks_text() -> str:
        return (hardware_audio.FIXTURES / "analog_and_hdmi_one_card.txt").read_text()

    async def sources_text() -> str:
        return (hardware_audio.FIXTURES / "sources.txt").read_text()

    async def cards_text() -> str:
        return (hardware_audio.FIXTURES / "pipewire_cards.txt").read_text()

    async def no_wpctl() -> str:
        return ""

    monkeypatch.setattr(sink_inventory, "_list_all_sinks_pulsectl", no_pulsectl)
    monkeypatch.setattr(sink_inventory, "_pactl_list_sinks_text", sinks_text)
    monkeypatch.setattr(sink_inventory, "_pactl_list_sources_text", sources_text)
    monkeypatch.setattr(sink_inventory, "_pactl_list_cards_text", cards_text)
    monkeypatch.setattr(inventory, "_wpctl_status", no_wpctl)

    facts = asyncio.run(_REAL_READ_AUDIO_ASYNC("PulseAudio"))

    assert [card.name for card in facts.cards] == [PIPEWIRE_CARD]
    assert {sink.name for sink in facts.sinks} == {
        PIPEWIRE_SINK,
        "alsa_output.pci-0000_00_1b.0.hdmi-stereo",
    }
    assert {source.name for source in facts.sources} == {
        hardware_audio.ANALOG_SOURCE,
        hardware_audio.MONITOR_SOURCE,
        hardware_audio.BLUEZ_SOURCE,
    }
    analog = next(sink for sink in facts.sinks if sink.name == PIPEWIRE_SINK)
    assert analog.owner == inventory.EndpointOwnerFacts(kind="card", card=PIPEWIRE_CARD)


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

    async def fake_audio(server_name: str | None = None) -> inventory.AudioFacts:
        del server_name
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


# -- the audio server's default sink --------------------------------------------------------------


def test_build_audio_facts_carries_the_default_sink():
    facts = inventory.build_audio_facts(
        hardware_audio.cards(),
        hardware_audio.sinks(),
        hardware_audio.sources(),
        hardware_audio.capture(),
        default_sink=hardware_audio.USB_SINK,
    )

    assert facts.default_sink == hardware_audio.USB_SINK


def test_default_sink_capability_needs_a_successful_default_read(installed_bluez, monkeypatch):
    _use_audio(monkeypatch, hardware_audio.facts())

    read = _read()

    assert read.capabilities.default_sink.available is False


def test_default_sink_capability_is_true_after_a_successful_read(installed_bluez, monkeypatch):
    _use_audio(monkeypatch, replace(hardware_audio.facts(), default_sink=hardware_audio.USB_SINK))

    read = _read()

    assert read.capabilities.default_sink.available is True


def test_the_composition_reads_the_default_sink_once(installed_bluez, monkeypatch):
    from sendspin_bridge.services.audio import pulse, sink_inventory

    calls: list[int] = []

    async def sinks(**kwargs) -> list:
        return []

    async def sources(**kwargs) -> list:
        return []

    async def cards(**kwargs) -> list:
        return []

    async def no_wpctl() -> str:
        return ""

    async def default_sink() -> str | None:
        calls.append(1)
        return hardware_audio.USB_SINK

    monkeypatch.setattr(sink_inventory, "list_all_sinks", sinks)
    monkeypatch.setattr(sink_inventory, "list_local_sources", sources)
    monkeypatch.setattr(sink_inventory, "list_local_cards", cards)
    monkeypatch.setattr(inventory, "_wpctl_status", no_wpctl)
    monkeypatch.setattr(pulse, "aget_default_sink_name", default_sink)

    facts = asyncio.run(_REAL_READ_AUDIO_ASYNC("PulseAudio"))

    assert facts.default_sink == hardware_audio.USB_SINK
    assert calls == [1]
