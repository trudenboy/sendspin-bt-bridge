"""The hardware use cases: the read model plus the config it is joined with."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from sendspin_bridge.application import hardware as hardware_uc
from sendspin_bridge.application.errors import UseCaseError
from sendspin_bridge.application.models.hardware import RoleEvidence
from sendspin_bridge.services.hardware import inventory
from sendspin_bridge.services.source import state as source_state
from tests.support import hardware_audio

ADAPTER = "C0:FB:F9:62:D7:D6"
DEVICE = "6C:5C:3D:35:17:99"
UNPAIRED = "AA:BB:CC:DD:EE:FF"

INFO = (
    f"Device {DEVICE} (public)\n"
    "\tName: ENEBY Portable\n"
    "\tAlias: ENEBY Portable\n"
    "\tPaired: yes\n"
    "\tBonded: yes\n"
    "\tTrusted: yes\n"
    "\tBlocked: no\n"
    "\tConnected: no\n"
    "\tUUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)\n"
    "\tModalias: bluetooth:v0059p0001d0001\n"
)


@pytest.fixture(autouse=True)
def _configured(monkeypatch, tmp_config):
    inventory.clear_cache()
    monkeypatch.setattr(inventory, "probe_audio_server", lambda: "PulseAudio")
    monkeypatch.setattr(inventory, "probe_capture", lambda _name=None: True)
    tmp_config.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "players": [
                    {
                        "id": "eneby",
                        "name": "ENEBY",
                        "backend": {"type": "bluetooth_a2dp", "mac": DEVICE, "adapter": "hci0"},
                    }
                ],
                "HA_ADAPTER_AREA_MAP": {ADAPTER: {"area_id": "living", "area_name": "Living room"}},
            }
        )
    )
    yield
    inventory.clear_cache()


@pytest.fixture(autouse=True)
def _fresh_source_state():
    """The source snapshot is a process-wide singleton; no test inherits another's phone."""
    source_state.reset()
    yield
    source_state.reset()


async def test_snapshot_joins_player_id_and_adapter_area(installed_bluez):
    installed_bluez.on(lambda text: f"info {DEVICE}" in text, stdout=INFO)

    snapshot = await hardware_uc.snapshot()

    controller = snapshot.controllers[0]
    assert controller.mac == ADAPTER
    assert controller.area is not None
    assert controller.area.id == "living"
    assert controller.area.name == "Living room"
    assert controller.device_count == 2

    by_mac = {device.mac: device for device in snapshot.bluetooth_devices}
    assert by_mac[DEVICE].player_id == "eneby"
    assert by_mac[DEVICE].battery_pct is None
    assert by_mac[DEVICE].rssi is None
    assert by_mac[DEVICE].source_phone is False
    assert by_mac[DEVICE].roles == []

    assert snapshot.capabilities.audio_server == "pulseaudio"
    assert snapshot.capabilities.dbus is False
    assert any(section.code == "dbus_unavailable" for section in snapshot.degraded)
    assert snapshot.stale is False
    assert snapshot.stale_after_s == 2


async def test_controller_detail_lists_devices_and_address_forms(installed_bluez):
    detail = await hardware_uc.controller("hci0")

    assert detail.id == "hci0"
    assert detail.id_synthetic is True
    assert {device.mac for device in detail.devices} == {DEVICE, "30:21:0E:0A:AE:5A"}
    assert detail.address_forms.colons == ADAPTER
    assert detail.address_forms.underscores == "C0_FB_F9_62_D7_D6"
    assert detail.address_forms.bare == "C0FBF962D7D6"


async def test_controller_detail_can_be_addressed_by_mac(installed_bluez):
    detail = await hardware_uc.controller(ADAPTER.lower())
    assert detail.id == "hci0"


async def test_unknown_controller_is_entity_not_found(installed_bluez):
    with pytest.raises(UseCaseError) as exc:
        await hardware_uc.controller("hci9")

    assert exc.value.status == 404
    assert exc.value.code == "entity_not_found"


async def test_bluetooth_device_detail_has_uuids_manufacturer_and_bonds(installed_bluez):
    installed_bluez.on(lambda text: f"info {DEVICE}" in text, stdout=INFO)

    detail = await hardware_uc.bluetooth_device(DEVICE.lower())

    assert detail.mac == DEVICE
    assert detail.player_id == "eneby"
    assert detail.uuids == ["0000110b-0000-1000-8000-00805f9b34fb"]
    assert detail.modalias == "bluetooth:v0059p0001d0001"
    assert detail.manufacturer == "Nordic Semiconductor"
    assert detail.bonds == [ADAPTER]
    assert detail.services_resolved is None


async def test_unknown_mac_is_entity_not_found(installed_bluez):
    with pytest.raises(UseCaseError) as exc:
        await hardware_uc.bluetooth_device(UNPAIRED)

    assert exc.value.status == 404
    assert exc.value.code == "entity_not_found"


async def test_malformed_mac_is_entity_not_found(installed_bluez):
    with pytest.raises(UseCaseError) as exc:
        await hardware_uc.bluetooth_device("not-a-mac")

    assert exc.value.status == 404
    assert exc.value.code == "entity_not_found"


async def test_a_host_that_cannot_answer_is_hardware_unavailable(installed_bluez):
    installed_bluez.fail("list")
    installed_bluez.fail("--version")

    with pytest.raises(UseCaseError) as exc:
        await hardware_uc.snapshot()

    assert exc.value.status == 503
    assert exc.value.code == "hardware_unavailable"


# ---------------------------------------------------------------------------
# The audio half
# ---------------------------------------------------------------------------


def _use_audio(monkeypatch) -> None:
    async def fake_audio(server_name: str | None = None) -> inventory.AudioFacts:
        del server_name
        return hardware_audio.facts()

    monkeypatch.setattr(inventory, "_read_audio_async", fake_audio)


def _write_config(tmp_config, players: list[dict]) -> None:
    tmp_config.write_text(json.dumps({"CONFIG_SCHEMA_VERSION": 6, "players": players}))


async def test_snapshot_has_cards_sinks_and_sources_with_owners(installed_bluez, monkeypatch, tmp_config):
    _use_audio(monkeypatch)
    _write_config(
        tmp_config,
        [
            {"id": "eneby", "name": "ENEBY", "backend": {"type": "bluetooth_a2dp", "mac": DEVICE}},
            {"id": "dac", "name": "DAC", "backend": {"type": "pulse_sink", "sink": hardware_audio.USB_SINK}},
        ],
    )

    snapshot = await hardware_uc.snapshot()

    audio = snapshot.audio
    by_card = {card.name: card for card in audio.cards}
    assert set(by_card) == {hardware_audio.ANALOG_CARD, hardware_audio.BLUEZ_CARD, hardware_audio.USB_CARD}
    assert by_card[hardware_audio.ANALOG_CARD].profile_count == 4
    assert by_card[hardware_audio.ANALOG_CARD].port_count == 3
    assert [ref.name for ref in by_card[hardware_audio.ANALOG_CARD].endpoint_refs] == [
        hardware_audio.ANALOG_SINK,
        hardware_audio.ANALOG_SOURCE,
        hardware_audio.MONITOR_SOURCE,
    ]
    # A Bluetooth sink's owner is the link, but it still belongs to its bluez_card.
    assert [ref.name for ref in by_card[hardware_audio.BLUEZ_CARD].endpoint_refs] == [
        hardware_audio.BLUEZ_SINK,
        hardware_audio.BLUEZ_SOURCE,
    ]

    by_name = {sink.name: sink for sink in audio.sinks}
    assert set(by_name) == {
        hardware_audio.ANALOG_SINK,
        hardware_audio.BLUEZ_SINK,
        hardware_audio.USB_SINK,
        hardware_audio.INTERNAL_SINK,
    }
    assert by_name[hardware_audio.ANALOG_SINK].owner is not None
    assert by_name[hardware_audio.ANALOG_SINK].owner.card == hardware_audio.ANALOG_CARD
    assert by_name[hardware_audio.BLUEZ_SINK].owner is not None
    assert by_name[hardware_audio.BLUEZ_SINK].owner.bluetooth == hardware_audio.SPEAKER_MAC
    assert by_name[hardware_audio.INTERNAL_SINK].owner is not None
    assert by_name[hardware_audio.INTERNAL_SINK].owner.internal is True
    assert by_name[hardware_audio.USB_SINK].in_use_by == "dac"
    assert by_name[hardware_audio.ANALOG_SINK].in_use_by is None
    assert by_name[hardware_audio.USB_SINK].active_port_available == "not available"

    by_name = {source.name: source for source in audio.sources}
    assert set(by_name) == {
        hardware_audio.ANALOG_SOURCE,
        hardware_audio.MONITOR_SOURCE,
        hardware_audio.BLUEZ_SOURCE,
    }
    assert by_name[hardware_audio.BLUEZ_SOURCE].owner is not None
    assert by_name[hardware_audio.BLUEZ_SOURCE].owner.bluetooth == hardware_audio.SPEAKER_MAC
    assert by_name[hardware_audio.ANALOG_SOURCE].muted is None and by_name[hardware_audio.ANALOG_SOURCE].volume is None
    assert by_name[hardware_audio.ANALOG_SOURCE].in_use_by is None

    # The Bluetooth config join is unchanged by the audio section.
    by_mac = {device.mac: device for device in snapshot.bluetooth_devices}
    assert by_mac[DEVICE].player_id == "eneby"


async def test_card_detail_lists_profiles_ports_and_owned_endpoints(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    detail = await hardware_uc.audio_card(hardware_audio.ANALOG_CARD)

    assert detail.name == hardware_audio.ANALOG_CARD
    assert [profile.name for profile in detail.profiles] == [
        "output:analog-stereo",
        "output:analog-stereo+input:analog-stereo",
        "input:analog-stereo",
        "off",
    ]
    headphones = next(port for port in detail.ports if port.name == "analog-output-headphones")
    assert headphones.available is False and headphones.active is False
    speaker = next(port for port in detail.ports if port.name == "analog-output-speaker")
    assert speaker.available is None and speaker.active is True
    assert speaker.profiles == ["output:analog-stereo", "output:analog-stereo+input:analog-stereo"]
    assert {endpoint.name for endpoint in detail.endpoints} == {
        hardware_audio.ANALOG_SINK,
        hardware_audio.ANALOG_SOURCE,
        hardware_audio.MONITOR_SOURCE,
    }


async def test_card_detail_lists_the_bluetooth_endpoints_the_summary_lists(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    detail = await hardware_uc.audio_card(hardware_audio.BLUEZ_CARD)

    assert {endpoint.name for endpoint in detail.endpoints} == {
        hardware_audio.BLUEZ_SINK,
        hardware_audio.BLUEZ_SOURCE,
    }
    assert {endpoint.kind for endpoint in detail.endpoints} == {"sink", "source"}
    by_name = {endpoint.name: endpoint for endpoint in detail.endpoints}
    assert by_name[hardware_audio.BLUEZ_SINK].owner is not None
    assert by_name[hardware_audio.BLUEZ_SINK].owner.bluetooth == hardware_audio.SPEAKER_MAC


async def test_sink_detail_carries_identifiers_and_the_owning_cards_ports(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    detail = await hardware_uc.audio_sink(hardware_audio.ANALOG_SINK)

    assert detail.kind == "sink"
    assert detail.owner is not None and detail.owner.card == hardware_audio.ANALOG_CARD
    assert detail.identifiers["bus_path"] == "pci-0000:00:1f.3"
    assert {port.name for port in detail.ports} == {
        "analog-output-speaker",
        "analog-output-headphones",
        "analog-input-mic",
    }
    assert detail.monitor_of is None


async def test_source_detail_names_the_sink_a_monitor_source_watches(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    detail = await hardware_uc.audio_source(hardware_audio.MONITOR_SOURCE)

    assert detail.kind == "source"
    assert detail.monitor_of == hardware_audio.ANALOG_SINK
    assert detail.identifiers == {}


async def test_source_detail_carries_its_active_port(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    detail = await hardware_uc.audio_source(hardware_audio.ANALOG_SOURCE)

    assert detail.active_port == "analog-input-headset-mic"
    assert detail.active_port_available == "not available"


async def test_unknown_audio_entities_are_entity_not_found(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    for lookup in (hardware_uc.audio_card, hardware_uc.audio_sink, hardware_uc.audio_source):
        with pytest.raises(UseCaseError) as exc:
            await lookup("no.such.entity")
        assert exc.value.status == 404
        assert exc.value.code == "entity_not_found"


async def test_a_name_outside_the_grammar_is_a_400_before_any_lookup(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    for lookup in (hardware_uc.audio_card, hardware_uc.audio_sink, hardware_uc.audio_source):
        with pytest.raises(UseCaseError) as exc:
            await lookup("not a name")
        assert exc.value.status == 400
        assert exc.value.code == "invalid_audio_name"


async def test_surrounding_spaces_are_not_silently_stripped(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    with pytest.raises(UseCaseError) as exc:
        await hardware_uc.audio_sink(" name ")

    assert exc.value.status == 400
    assert exc.value.code == "invalid_audio_name"


async def test_no_audio_server_means_an_empty_audio_section(installed_bluez, monkeypatch):
    monkeypatch.setattr(inventory, "probe_audio_server", lambda: "not available")

    snapshot = await hardware_uc.snapshot()

    assert snapshot.audio.cards == [] and snapshot.audio.sinks == [] and snapshot.audio.sources == []


# ---------------------------------------------------------------------------
# The source feed
# ---------------------------------------------------------------------------

PHONE_NODE = "bluez_input.6C_5C_3D_35_17_99.1"


def _use_audio_facts(monkeypatch, audio: inventory.AudioFacts) -> None:
    async def fake_audio(server_name: str | None = None) -> inventory.AudioFacts:
        del server_name
        return audio

    monkeypatch.setattr(inventory, "_read_audio_async", fake_audio)


def _write_source_config(tmp_config) -> None:
    tmp_config.write_text(
        json.dumps(
            {
                "CONFIG_SCHEMA_VERSION": 6,
                "SENDSPIN_SOURCE": True,
                "players": [
                    {
                        "id": "eneby",
                        "name": "ENEBY",
                        "backend": {"type": "bluetooth_a2dp", "mac": DEVICE, "adapter": "hci0"},
                    }
                ],
            }
        )
    )


async def test_no_source_feed_when_the_feature_is_off(installed_bluez, monkeypatch):
    _use_audio(monkeypatch)

    snapshot = await hardware_uc.snapshot()

    assert snapshot.source is None


async def test_the_source_feed_is_built_from_the_runtime_and_the_capture_facts(
    installed_bluez, monkeypatch, tmp_config
):
    _write_source_config(tmp_config)
    _use_audio(monkeypatch)
    source_state.publish(
        enabled=True,
        state="waiting",
        phone=PHONE_NODE,
        phone_mac=DEVICE,
        server="ws://ma:8927/sendspin",
        ma_connected=True,
        ma_paired=True,
    )

    snapshot = await hardware_uc.snapshot()

    feed = snapshot.source
    assert feed is not None
    assert (feed.enabled, feed.state, feed.phone_mac) == (True, "waiting", DEVICE)
    assert (feed.capture.kind, feed.capture.node) == ("pipewire_node", hardware_audio.CAPTURE_NODE)
    assert (feed.ma.connected, feed.ma.paired) == (True, True)
    assert feed.error is None

    by_mac = {device.mac: device for device in snapshot.bluetooth_devices}
    assert by_mac[DEVICE].source_phone is True
    assert RoleEvidence(role="input", evidence=f"source_feed:{DEVICE}") in by_mac[DEVICE].roles


async def test_a_streaming_source_reports_streaming(installed_bluez, monkeypatch, tmp_config):
    _write_source_config(tmp_config)
    _use_audio(monkeypatch)
    source_state.publish(enabled=True, state="streaming", phone=PHONE_NODE, streaming=True)

    snapshot = await hardware_uc.snapshot()

    assert snapshot.source is not None and snapshot.source.state == "streaming"


async def test_a_pairing_error_does_not_make_a_streaming_source_an_error(installed_bluez, monkeypatch, tmp_config):
    _write_source_config(tmp_config)
    _use_audio(monkeypatch)
    source_state.publish(enabled=True, state="streaming", phone=PHONE_NODE, streaming=True, pairing_error="no_adapter")

    snapshot = await hardware_uc.snapshot()

    assert snapshot.source is not None
    assert snapshot.source.state == "streaming"
    assert snapshot.source.error is None


async def test_an_env_started_source_is_reported_without_the_config_key(installed_bluez, monkeypatch):
    from sendspin_bridge.services.source import manager as source_manager

    _use_audio(monkeypatch)
    monkeypatch.setattr(source_manager, "running", lambda: True)

    snapshot = await hardware_uc.snapshot()

    assert snapshot.source is not None
    assert (snapshot.source.enabled, snapshot.source.state) == (False, "off")


async def test_only_a_known_device_is_marked_as_the_source_phone(installed_bluez, monkeypatch, tmp_config):
    _write_source_config(tmp_config)
    _use_audio(monkeypatch)
    unknown_node = f"bluez_input.{UNPAIRED.replace(':', '_')}.3"
    source_state.publish(enabled=True, state="streaming", phone=unknown_node, streaming=True)

    snapshot = await hardware_uc.snapshot()

    assert snapshot.source is not None
    assert snapshot.source.phone_mac is None
    assert all(not device.source_phone for device in snapshot.bluetooth_devices)


async def test_a_stopped_source_is_off(installed_bluez, monkeypatch, tmp_config):
    _write_source_config(tmp_config)
    _use_audio(monkeypatch)

    snapshot = await hardware_uc.snapshot()

    assert snapshot.source is not None
    assert (snapshot.source.enabled, snapshot.source.state, snapshot.source.phone_mac) == (False, "off", None)


# ---------------------------------------------------------------------------
# The audio server's default sink
# ---------------------------------------------------------------------------


async def test_exactly_the_audio_servers_default_sink_is_marked(installed_bluez, monkeypatch):
    _use_audio_facts(monkeypatch, replace(hardware_audio.facts(), default_sink=hardware_audio.USB_SINK))

    snapshot = await hardware_uc.snapshot()

    defaults = [sink.name for sink in snapshot.audio.sinks if sink.is_default]
    assert defaults == [hardware_audio.USB_SINK]
    assert all(source.is_default is False for source in snapshot.audio.sources)


async def test_no_endpoint_is_default_when_the_server_cannot_say(installed_bluez, monkeypatch):
    _use_audio_facts(monkeypatch, replace(hardware_audio.facts(), default_sink=None))

    snapshot = await hardware_uc.snapshot()

    assert all(not endpoint.is_default for endpoint in [*snapshot.audio.sinks, *snapshot.audio.sources])


async def test_the_sink_detail_carries_the_same_default_flag(installed_bluez, monkeypatch):
    _use_audio_facts(monkeypatch, replace(hardware_audio.facts(), default_sink=hardware_audio.USB_SINK))

    default = await hardware_uc.audio_sink(hardware_audio.USB_SINK)
    other = await hardware_uc.audio_sink(hardware_audio.ANALOG_SINK)
    source = await hardware_uc.audio_source(hardware_audio.ANALOG_SOURCE)

    assert default.is_default is True
    assert other.is_default is False
    assert source.is_default is False
