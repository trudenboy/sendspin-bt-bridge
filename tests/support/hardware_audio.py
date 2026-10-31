"""One audio world for the hardware tests, parsed by the real parsers.

The fixtures under ``tests/fixtures/pactl`` are the source of truth; this
module only wires them into the ``AudioFacts`` the reader produces, so the
inventory, application and API tests all describe the same host.
"""

from __future__ import annotations

from pathlib import Path

from sendspin_bridge.services.audio.sink_inventory import (
    LocalCard,
    LocalSink,
    LocalSource,
    parse_pactl_cards,
    parse_pactl_sources,
)
from sendspin_bridge.services.hardware import inventory

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "pactl"

ANALOG_CARD = "alsa_card.pci-0000_00_1f.3"
BLUEZ_CARD = "bluez_card.FC_58_FA_EB_08_6C"
USB_CARD = "alsa_card.usb-Generic_USB_DAC_0000A1-00"

ANALOG_SINK = "alsa_output.pci-0000_00_1f.3.analog-stereo"
ANALOG_SOURCE = "alsa_input.pci-0000_00_1f.3.analog-stereo"
MONITOR_SOURCE = "alsa_output.pci-0000_00_1f.3.analog-stereo.monitor"
USB_SINK = "alsa_output.usb-Generic_USB_DAC_0000A1-00.analog-stereo"
BLUEZ_SINK = "bluez_sink.FC_58_FA_EB_08_6C.a2dp_sink"
BLUEZ_SOURCE = "bluez_source.FC_58_FA_EB_08_6C"
SPEAKER_MAC = "FC:58:FA:EB:08:6C"
CAPTURE_NODE = "bluez_input.FC_58_FA_EB_08_6C.2"
INTERNAL_SINK = "sendspin_fallback"


def sinks() -> list[LocalSink]:
    return [
        LocalSink(
            name=ANALOG_SINK,
            description="Built-in Audio Analog Stereo",
            state="running",
            sample_spec="s16le 2ch 44100Hz",
            muted=False,
            volume=100,
            active_port="analog-output-speaker",
            active_port_available="availability unknown",
            identifiers={"bus_path": "pci-0000:00:1f.3"},
            index=41,
            owner_module=7,
        ),
        LocalSink(
            name=BLUEZ_SINK,
            description="ENEBY20",
            state="idle",
            sample_spec="s16le 2ch 44100Hz",
            muted=False,
            volume=80,
            active_port="headset-output",
            active_port_available="available",
            identifiers={},
            index=70,
            owner_module=25,
        ),
        LocalSink(
            name=USB_SINK,
            description="USB DAC Analog Stereo",
            state="suspended",
            sample_spec="s24le 2ch 96000Hz",
            muted=True,
            volume=0,
            active_port="analog-output",
            active_port_available="not available",
            identifiers={"serial": "Generic_USB_DAC_0000A1", "bus_path": "pci-0000:00:14.0-usb-0:2:1.0"},
            index=72,
            owner_module=9,
        ),
        LocalSink(
            name=INTERNAL_SINK,
            description="Sendspin_Fallback",
            state="idle",
            sample_spec="float32le 2ch 44100Hz",
            muted=False,
            volume=100,
            active_port=None,
            active_port_available=None,
            identifiers={},
            index=73,
            owner_module=18,
        ),
    ]


def sources() -> list[LocalSource]:
    return parse_pactl_sources((FIXTURES / "sources.txt").read_text())


def cards(sink_list: list[LocalSink] | None = None, source_list: list[LocalSource] | None = None) -> list[LocalCard]:
    resolved_sinks = sink_list if sink_list is not None else sinks()
    resolved_sources = source_list if source_list is not None else sources()
    return parse_pactl_cards(
        (FIXTURES / "cards.txt").read_text(),
        resolved_sinks,
        resolved_sources,
    )


def capture() -> inventory.CaptureFacts:
    return inventory.CaptureFacts(kind="pipewire_node", node=CAPTURE_NODE)


def facts(
    *, sink_list: list[LocalSink] | None = None, source_list: list[LocalSource] | None = None
) -> inventory.AudioFacts:
    resolved_sinks = sink_list if sink_list is not None else sinks()
    resolved_sources = source_list if source_list is not None else sources()
    return inventory.build_audio_facts(
        cards(resolved_sinks, resolved_sources), resolved_sinks, resolved_sources, capture()
    )
